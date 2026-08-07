"""
enhanced_retry.py - 增强型重试与请求策略模块

在 retry_handler.py 基础上增强：
- 按错误类型动态调整重试参数（超时错误增加等待时间）
- 请求级超时控制（per-operation timeout）
- 代理轮换集成（失败时自动切换代理）
- 熔断器在评估流程中启用
- 重试成功率追踪与自适应调整
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


class ErrorCategory(Enum):
    """错误分类（更细粒度）"""
    TIMEOUT = "timeout"
    CONNECTION_REFUSED = "connection_refused"
    CONNECTION_RESET = "connection_reset"
    SELECTOR_NOT_FOUND = "selector_not_found"
    JS_EXECUTION_ERROR = "js_execution_error"
    CDP_SESSION_ERROR = "cdp_session_error"
    PAGE_CRASHED = "page_crashed"
    RATE_LIMITED = "rate_limited"
    BLOCKED_BY_SITE = "blocked_by_site"
    UNKNOWN = "unknown"


@dataclass
class RetryStrategy:
    """重试策略配置"""
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    exponential_base: float = 2.0
    jitter_factor: float = 0.3

    # 按错误类型的重试策略
    error_strategies: Dict[ErrorCategory, "RetryStrategy"] = field(default_factory=dict)

    def __post_init__(self):
        if not self.error_strategies:
            self.error_strategies = {
                ErrorCategory.TIMEOUT: RetryStrategy(
                    max_attempts=5, base_delay=2.0, max_delay=60.0
                ),
                ErrorCategory.CONNECTION_REFUSED: RetryStrategy(
                    max_attempts=3, base_delay=1.0, max_delay=10.0
                ),
                ErrorCategory.SELECTOR_NOT_FOUND: RetryStrategy(
                    max_attempts=2, base_delay=0.5, max_delay=5.0
                ),
                ErrorCategory.RATE_LIMITED: RetryStrategy(
                    max_attempts=3, base_delay=5.0, max_delay=30.0
                ),
                ErrorCategory.BLOCKED_BY_SITE: RetryStrategy(
                    max_attempts=1, base_delay=0.0, max_delay=0.0
                ),
            }


class AdaptiveRetryHandler:
    """
    自适应重试处理器

    特性：
    - 根据错误类型动态选择重试策略
    - 失败时自动切换代理
    - 追踪成功率并动态调整参数
    - 熔断器保护
    """

    def __init__(
        self,
        strategy: RetryStrategy = None,
        proxy_pool: Any = None,
        circuit_breaker_threshold: int = 3,
        circuit_breaker_timeout: float = 60.0,
    ):
        self.strategy = strategy or RetryStrategy()
        self.proxy_pool = proxy_pool
        self._circuit_breaker_threshold = circuit_breaker_threshold
        self._circuit_breaker_timeout = circuit_breaker_timeout
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._success_count = 0
        self._retry_history: List[dict] = []
        self._adaptive_config: Dict[str, Any] = {
            "max_attempts": 5,  # 优化：从3增加到5
            "base_delay": 2.0,  # 优化：从1.0增加到2.0
        }

    def _classify_error(self, exc: Exception) -> ErrorCategory:
        """根据异常消息分类错误"""
        exc_str = str(exc).lower()

        if "timeout" in exc_str:
            return ErrorCategory.TIMEOUT
        elif "connection refused" in exc_str or "connect" in exc_str:
            return ErrorCategory.CONNECTION_REFUSED
        elif "connection reset" in exc_str or "disconnect" in exc_str:
            return ErrorCategory.CONNECTION_REFUSED
        elif "selector" in exc_str or "not found" in exc_str:
            return ErrorCategory.SELECTOR_NOT_FOUND
        elif "js" in exc_str or "illegal invocation" in exc_str:
            return ErrorCategory.JS_EXECUTION_ERROR
        elif "cdp" in exc_str or "websocket" in exc_str:
            return ErrorCategory.CDP_SESSION_ERROR
        elif "crash" in exc_str or "crashed" in exc_str:
            return ErrorCategory.PAGE_CRASHED
        elif "429" in exc_str or "rate limit" in exc_str:
            return ErrorCategory.RATE_LIMITED
        elif "blocked" in exc_str or "captcha" in exc_str or "verify" in exc_str:
            return ErrorCategory.BLOCKED_BY_SITE
        else:
            return ErrorCategory.UNKNOWN

    def _should_retry(self, error: ErrorCategory, attempt: int) -> bool:
        """判断是否应该重试"""
        if attempt >= self._adaptive_config["max_attempts"]:
            return False

        # 被网站拦截的错误不重试
        if error == ErrorCategory.BLOCKED_BY_SITE:
            return False

        # 选择器未找到错误最多重试1次
        if error == ErrorCategory.SELECTOR_NOT_FOUND and attempt > 1:
            return False

        return True

    def _get_delay(self, error: ErrorCategory, attempt: int) -> float:
        """根据错误类型和尝试次数计算延迟"""
        error_strategy = self.strategy.error_strategies.get(
            error, self.strategy
        )

        delay = error_strategy.base_delay * (
            error_strategy.exponential_base ** (attempt - 1)
        )

        # 添加随机抖动
        jitter = 1.0 - error_strategy.jitter_factor + random.random() * error_strategy.jitter_factor * 2
        delay *= jitter

        # 自适应调整：根据历史成功率增加延迟
        total_attempts = self._success_count + self._failure_count
        if total_attempts > 10:
            success_rate = self._success_count / total_attempts
            if success_rate < 0.5:
                delay *= 1.5  # 成功率低时增加等待时间

        return min(delay, error_strategy.max_delay)

    def _switch_proxy(self) -> bool:
        """切换到下一个代理（增强版）"""
        if self.proxy_pool:
            try:
                proxy = self.proxy_pool.get_next_proxy()
                if proxy:
                    logger.info(f"切换到代理: {proxy.url} (健康度: {proxy.health_score:.2f})")
                    return True
                else:
                    logger.warning("代理池无可用代理，降级策略：等待重试")
                    return False
            except Exception as e:
                logger.warning(f"代理切换失败: {e}")
                return False
        return False

    def _check_circuit_breaker(self) -> bool:
        """检查熔断器状态"""
        if self._failure_count >= self._circuit_breaker_threshold:
            elapsed = time.time() - self._last_failure_time
            if elapsed < self._circuit_breaker_timeout:
                remaining = self._circuit_breaker_timeout - elapsed
                logger.warning(
                    f"熔断器开启，等待 {remaining:.1f}s 后重试"
                )
                return False
        return True

    def _record_success(self):
        """记录成功"""
        self._success_count += 1
        self._failure_count = 0
        self._retry_history.append({
            "timestamp": time.time(),
            "success": True,
        })

    def _record_failure(self, error: ErrorCategory):
        """记录失败"""
        self._failure_count += 1
        self._last_failure_time = time.time()
        self._retry_history.append({
            "timestamp": time.time(),
            "success": False,
            "error": error.value,
        })

    async def execute(
        self,
        func: Callable,
        *args,
        timeout: float = 30.0,
        operation_name: str = "operation",
        **kwargs
    ) -> Any:
        """
        带自适应重试的执行器

        Args:
            func: 要执行的异步函数
            timeout: 单次操作超时时间（秒）
            operation_name: 操作名称（用于日志）

        Returns:
            函数执行结果

        Raises:
            Exception: 所有重试失败后抛出最后一次异常
        """
        last_exception = None
        attempt = 0

        while attempt < self._adaptive_config["max_attempts"]:
            attempt += 1

            # 检查熔断器
            if not self._check_circuit_breaker():
                await asyncio.sleep(
                    self._circuit_breaker_timeout - (time.time() - self._last_failure_time)
                )

            try:
                # 执行函数（带超时）
                result = await asyncio.wait_for(
                    func(*args, **kwargs),
                    timeout=timeout
                )

                self._record_success()
                logger.debug(
                    f"[{operation_name}] 执行成功 (attempt {attempt})"
                )
                return result

            except asyncio.TimeoutError as e:
                last_exception = asyncio.TimeoutError(
                    f"[{operation_name}] 超时 ({timeout}s)"
                )
                last_exception.__cause__ = e
                error_category = ErrorCategory.TIMEOUT

            except Exception as e:
                last_exception = e
                error_category = self._classify_error(e)

            # 记录失败
            self._record_failure(error_category)

            logger.warning(
                f"[{operation_name}] 执行失败 (attempt {attempt}/{self._adaptive_config['max_attempts']}), "
                f"原因: {error_category.value}, 错误: {str(last_exception)[:100]}"
            )

            # 判断是否应该重试
            if not self._should_retry(error_category, attempt):
                logger.info(f"[{operation_name}] 不重试，错误类型: {error_category.value}")
                break

            # 切换代理（如果是连接类错误）
            if error_category in [
                ErrorCategory.TIMEOUT,
                ErrorCategory.CONNECTION_REFUSED,
                ErrorCategory.CDP_SESSION_ERROR,
            ]:
                self._switch_proxy()

            # 计算延迟
            delay = self._get_delay(error_category, attempt)
            logger.info(f"[{operation_name}] 等待 {delay:.2f}s 后重试...")
            await asyncio.sleep(delay)

        # 所有重试失败
        logger.error(f"[{operation_name}] 所有重试失败，共 {attempt} 次")
        raise last_exception

    def get_stats(self) -> Dict[str, Any]:
        """获取重试统计信息"""
        total = self._success_count + self._failure_count
        return {
            "total_attempts": total,
            "success_count": self._success_count,
            "failure_count": self._failure_count,
            "success_rate": self._success_count / total if total > 0 else 0,
            "consecutive_failures": self._failure_count,
            "recent_history": self._retry_history[-10:],
        }

    def reset(self):
        """重置状态"""
        self._failure_count = 0
        self._success_count = 0
        self._retry_history.clear()


# 便捷函数
class RequestPolicy:
    """
    请求策略管理器

    统一管理超时、重试、代理轮换
    """

    def __init__(
        self,
        default_timeout: float = 30.0,
        max_retries: int = 3,
        proxy_pool: Any = None,
    ):
        self.default_timeout = default_timeout
        self.proxy_pool = proxy_pool
        self._handlers: Dict[str, AdaptiveRetryHandler] = {}

    def get_handler(self, operation: str) -> AdaptiveRetryHandler:
        """获取指定操作的处理器"""
        if operation not in self._handlers:
            self._handlers[operation] = AdaptiveRetryHandler(
                proxy_pool=self.proxy_pool,
            )
        return self._handlers[operation]

    async def execute_with_policy(
        self,
        operation: str,
        func: Callable,
        *args,
        timeout: float = None,
        **kwargs
    ) -> Any:
        """
        使用策略执行操作

        Args:
            operation: 操作名称
            func: 要执行的函数
            timeout: 超时时间（默认使用配置值）
        """
        handler = self.get_handler(operation)
        return await handler.execute(
            func,
            *args,
            timeout=timeout or self.default_timeout,
            operation_name=operation,
            **kwargs
        )

    def get_all_stats(self) -> Dict[str, Any]:
        """获取所有操作的统计信息"""
        return {
            op: handler.get_stats()
            for op, handler in self._handlers.items()
        }


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
