"""
request_policy.py - 请求策略管理器

统一管理超时、重试、代理轮换，集成到评估流程中。

核心功能：
- 按操作类型配置差异化超时
- 自适应重试（根据错误类型动态调整）
- 代理轮换（连接失败时自动切换）
- 熔断器保护（连续失败时暂停）
- 成功率追踪与报告
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class OperationType(Enum):
    """操作类型"""
    NAVIGATION = "navigation"
    SEARCH = "search"
    EXTRACT = "extract"
    CLICK = "click"
    INPUT = "input"
    WAIT = "wait"
    SCREENSHOT = "screenshot"


@dataclass
class OperationConfig:
    """单个操作的配置"""
    timeout: float = 30.0
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    enable_circuit_breaker: bool = True
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: float = 60.0
    retry_on_timeout: bool = True
    retry_on_selector_error: bool = False  # 选择器错误不重试


# 预定义的操作配置
DEFAULT_OPERATION_CONFIGS: Dict[OperationType, OperationConfig] = {
    OperationType.NAVIGATION: OperationConfig(
        timeout=30.0,
        max_retries=3,
        base_delay=2.0,
        max_delay=60.0,
        enable_circuit_breaker=True,
    ),
    OperationType.SEARCH: OperationConfig(
        timeout=20.0,
        max_retries=3,
        base_delay=1.5,
        max_delay=30.0,
        enable_circuit_breaker=True,
    ),
    OperationType.EXTRACT: OperationConfig(
        timeout=15.0,
        max_retries=2,
        base_delay=1.0,
        max_delay=20.0,
        enable_circuit_breaker=False,
    ),
    OperationType.CLICK: OperationConfig(
        timeout=10.0,
        max_retries=3,
        base_delay=0.5,
        max_delay=10.0,
        enable_circuit_breaker=False,
    ),
    OperationType.INPUT: OperationConfig(
        timeout=10.0,
        max_retries=3,
        base_delay=0.5,
        max_delay=10.0,
        enable_circuit_breaker=False,
    ),
    OperationType.WAIT: OperationConfig(
        timeout=30.0,
        max_retries=2,
        base_delay=1.0,
        max_delay=20.0,
        enable_circuit_breaker=False,
    ),
    OperationType.SCREENSHOT: OperationConfig(
        timeout=15.0,
        max_retries=2,
        base_delay=1.0,
        max_delay=20.0,
        enable_circuit_breaker=False,
    ),
}


class CircuitBreaker:
    """熔断器"""

    def __init__(self, threshold: int = 5, timeout: float = 60.0):
        self.threshold = threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.state = "closed"  # closed, open, half_open

    def can_execute(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            if time.time() - self.last_failure_time > self.timeout:
                self.state = "half_open"
                logger.info("熔断器: open -> half_open")
                return True
            return False
        return True  # half_open 允许一个测试请求

    def record_success(self):
        if self.state == "half_open":
            self.state = "closed"
            logger.info("熔断器: half_open -> closed")
        self.failure_count = 0

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.threshold:
            self.state = "open"
            logger.warning(f"熔断器触发: 连续失败 {self.failure_count} 次")

    def reset(self):
        self.failure_count = 0
        self.state = "closed"


class RequestStats:
    """请求统计"""

    def __init__(self):
        self.total_requests = 0
        self.success_requests = 0
        self.failed_requests = 0
        self.timeout_requests = 0
        self.retry_requests = 0
        self.operation_stats: Dict[str, Dict[str, int]] = {}
        self._start_time = time.time()

    def record_success(self, operation: str):
        self.total_requests += 1
        self.success_requests += 1
        self._record_operation(operation, "success")

    def record_failure(self, operation: str, error_type: str = "unknown"):
        self.total_requests += 1
        self.failed_requests += 1
        self._record_operation(operation, "failure", error_type)
        if "timeout" in error_type.lower():
            self.timeout_requests += 1

    def record_retry(self, operation: str):
        self.retry_requests += 1
        self._record_operation(operation, "retry")

    def _record_operation(self, operation: str, event: str, detail: str = ""):
        if operation not in self.operation_stats:
            self.operation_stats[operation] = {"success": 0, "failure": 0, "retry": 0}
        self.operation_stats[operation][event] = (
            self.operation_stats[operation].get(event, 0) + 1
        )
        if detail:
            self.operation_stats[operation][f"{event}_detail"] = (
                self.operation_stats[operation].get(f"{event}_detail", 0) + 1
            )

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.success_requests / self.total_requests

    @property
    def retry_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.retry_requests / self.total_requests

    def get_summary(self) -> Dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "success_requests": self.success_requests,
            "failed_requests": self.failed_requests,
            "timeout_requests": self.timeout_requests,
            "retry_requests": self.retry_requests,
            "success_rate": round(self.success_rate * 100, 2),
            "retry_rate": round(self.retry_rate * 100, 2),
            "operation_stats": self.operation_stats,
            "elapsed_time": round(time.time() - self._start_time, 2),
        }


class RequestPolicy:
    """
    请求策略管理器

    统一管理超时、重试、代理轮换
    """

    def __init__(
        self,
        operation_configs: Dict[OperationType, OperationConfig] = None,
        proxy_pool: Any = None,
    ):
        self.operation_configs = operation_configs or DEFAULT_OPERATION_CONFIGS
        self.proxy_pool = proxy_pool
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.stats = RequestStats()
        self._adaptive_adjustments: Dict[str, Dict[str, Any]] = {}

    def get_config(self, operation: OperationType) -> OperationConfig:
        return self.operation_configs.get(operation, OperationConfig())

    def get_circuit_breaker(self, operation: str) -> CircuitBreaker:
        if operation not in self._circuit_breakers:
            config = self.get_config_by_name(operation)
            self._circuit_breakers[operation] = CircuitBreaker(
                threshold=config.circuit_breaker_threshold,
                timeout=config.circuit_breaker_timeout,
            )
        return self._circuit_breakers[operation]

    def get_config_by_name(self, operation_name: str) -> OperationConfig:
        """根据操作名称获取配置"""
        try:
            op_type = OperationType(operation_name)
            return self.operation_configs.get(op_type, OperationConfig())
        except ValueError:
            return OperationConfig()

    def adjust_config_adaptive(self, operation: str, success_rate: float):
        """根据成功率自适应调整配置"""
        if operation not in self._adaptive_adjustments:
            self._adaptive_adjustments[operation] = {}

        current = self._adaptive_adjustments[operation]
        if success_rate < 0.5:
            # 成功率低，增加超时和重试次数
            current["timeout_multiplier"] = current.get("timeout_multiplier", 1.0) + 0.5
            current["retry_multiplier"] = current.get("retry_multiplier", 1.0) + 0.5
            logger.info(f"[{operation}] 成功率 {success_rate:.1%} 过低，调整配置")
        elif success_rate > 0.9:
            # 成功率高，可以适当减少超时
            current["timeout_multiplier"] = max(0.8, current.get("timeout_multiplier", 1.0) - 0.1)
            current["retry_multiplier"] = max(0.8, current.get("retry_multiplier", 1.0) - 0.1)

    async def execute_with_policy(
        self,
        operation: str,
        func: Callable,
        *args,
        timeout: float = None,
        **kwargs,
    ) -> Any:
        """
        使用策略执行操作

        Args:
            operation: 操作名称
            func: 要执行的函数
            timeout: 超时时间（可选，默认使用配置值）
        """
        config = self.get_config_by_name(operation)
        cb = self.get_circuit_breaker(operation) if config.enable_circuit_breaker else None

        # 应用自适应调整
        timeout_mult = self._adaptive_adjustments.get(operation, {}).get("timeout_multiplier", 1.0)
        retry_mult = self._adaptive_adjustments.get(operation, {}).get("retry_multiplier", 1.0)
        effective_timeout = (timeout or config.timeout) * timeout_mult
        effective_max_retries = int(config.max_retries * retry_mult)

        last_exception = None

        for attempt in range(1, effective_max_retries + 1):
            # 检查熔断器
            if cb and not cb.can_execute():
                remaining = config.circuit_breaker_timeout - (
                    time.time() - cb.last_failure_time
                )
                raise TimeoutError(f"熔断器开启，等待 {remaining:.1f}s")

            try:
                # 执行操作（带超时）
                result = await asyncio.wait_for(
                    func(*args, **kwargs),
                    timeout=effective_timeout,
                )

                if cb:
                    cb.record_success()
                self.stats.record_success(operation)
                logger.debug(f"[{operation}] 成功 (attempt {attempt})")
                return result

            except asyncio.TimeoutError:
                last_exception = asyncio.TimeoutError(
                    f"[{operation}] 超时 ({effective_timeout}s)"
                )
                self.stats.record_failure(operation, "timeout")
                logger.warning(f"[{operation}] 超时 (attempt {attempt})")

            except Exception as e:
                last_exception = e
                error_type = type(e).__name__
                self.stats.record_failure(operation, error_type)
                logger.warning(f"[{operation}] 失败 (attempt {attempt}): {str(e)[:100]}")

            # 判断是否应该重试
            if attempt >= effective_max_retries:
                break

            # 选择器错误不重试
            if "selector" in str(last_exception).lower() and not config.retry_on_selector_error:
                logger.info(f"[{operation}] 选择器错误，不重试")
                break

            # 记录重试
            self.stats.record_retry(operation)

            # 切换代理（如果是连接类错误）
            if self.proxy_pool and any(
                keyword in str(last_exception).lower()
                for keyword in ["timeout", "connection", "network"]
            ):
                try:
                    await self.proxy_pool.get_next_proxy()
                    logger.info(f"[{operation}] 切换代理")
                except Exception as e:
                    logger.warning(f"[{operation}] 代理切换失败: {e}")

            # 计算退避时间
            delay = config.base_delay * (2 ** (attempt - 1))
            delay = min(delay, config.max_delay)
            delay *= (0.5 + random.random())  # 随机抖动
            logger.info(f"[{operation}] 等待 {delay:.2f}s 后重试...")
            await asyncio.sleep(delay)

        # 所有重试失败
        if cb:
            cb.record_failure()
        logger.error(f"[{operation}] 所有重试失败，共 {effective_max_retries} 次")
        raise last_exception

    def get_operation_stats(self, operation: str) -> Dict[str, Any]:
        """获取单个操作的统计"""
        return self.stats.operation_stats.get(operation, {})

    def reset_stats(self):
        """重置统计"""
        self.stats = RequestStats()
        self._adaptive_adjustments.clear()

    def reset_circuit_breakers(self):
        """重置所有熔断器"""
        for cb in self._circuit_breakers.values():
            cb.reset()


# 全局请求策略实例
_request_policy: Optional[RequestPolicy] = None


def get_request_policy(proxy_pool: Any = None) -> RequestPolicy:
    """获取全局请求策略单例"""
    global _request_policy
    if _request_policy is None:
        _request_policy = RequestPolicy(proxy_pool=proxy_pool)
    return _request_policy


def reset_request_policy():
    """重置全局请求策略"""
    global _request_policy
    _request_policy = None
    logger.debug("重置全局请求策略")


def with_request_policy(
    operation: str,
    timeout: float = None,
):
    """
    装饰器：为函数添加请求策略

    Usage:
        @with_request_policy("search", timeout=20.0)
        async def search_website(query):
            ...
    """
    def decorator(func: Callable) -> Callable:
        policy = get_request_policy()

        async def wrapper(*args, **kwargs):
            return await policy.execute_with_policy(
                operation, func, *args, timeout=timeout, **kwargs
            )

        return wrapper
    return decorator
