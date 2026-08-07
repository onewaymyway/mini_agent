"""
retry_handler.py - 重试与恢复模块

提供自动重试、指数退避、熔断器机制，增强抓取稳定性。

核心功能：
- 指数退避重试（exponential backoff with jitter）
- 熔断器模式（circuit breaker）
- 异常分类与策略匹配
- 重试历史记录
"""
from __future__ import annotations

import asyncio
import random
import time
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Callable, Any, Optional, List, Type
import functools

logger = logging.getLogger(__name__)


class FailureReason(Enum):
    """失败原因枚举"""
    TIMEOUT = "timeout"
    CONNECTION_LOST = "connection_lost"
    PAGE_CRASHED = "page_crashed"
    SELECTOR_NOT_FOUND = "selector_not_found"
    NETWORK_ERROR = "network_error"
    HTTP_ERROR = "http_error"
    UNKNOWN = "unknown"


@dataclass
class RetryConfig:
    """重试配置"""
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    exponential_base: float = 2.0
    jitter_factor: float = 0.5  # 随机抖动因子
    
    # 熔断器配置
    circuit_breaker_threshold: int = 5  # 连续失败次数触发熔断
    circuit_breaker_timeout: float = 60.0  # 熔断恢复时间（秒）
    
    # 重试策略
    retry_on: List[FailureReason] = field(default_factory=lambda: list(FailureReason))
    
    # 回调
    on_retry: Optional[Callable[[int, Exception, float], None]] = None
    on_exhausted: Optional[Callable[[Exception], None]] = None


class CircuitBreaker:
    """
    熔断器：连续失败后暂停，避免雪崩效应
    
    状态转换：
    closed -> open (连续失败达到阈值)
    open -> half-open (超时后允许测试请求)
    half-open -> closed (测试成功)
    half-open -> open (测试失败)
    """
    
    def __init__(self, threshold: int = 5, timeout: float = 60.0):
        self.threshold = threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.state = "closed"  # closed, open, half-open
    
    def can_execute(self) -> bool:
        """检查是否允许执行"""
        if self.state == "closed":
            return True
        
        if self.state == "open":
            if time.time() - self.last_failure_time > self.timeout:
                self.state = "half-open"
                logger.info("熔断器状态转换: open -> half-open")
                return True
            return False
        
        # half-open 状态允许一个测试请求
        return True
    
    def record_success(self):
        """记录成功"""
        if self.state == "half-open":
            self.state = "closed"
            logger.info("熔断器状态转换: half-open -> closed")
        self.failure_count = 0
    
    def record_failure(self):
        """记录失败"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.threshold:
            self.state = "open"
            logger.warning(f"熔断器触发: 连续失败 {self.failure_count} 次")
    
    def reset(self):
        """重置熔断器"""
        self.failure_count = 0
        self.state = "closed"


class RetryHandler:
    """
    重试处理器
    
    支持：
    - 指数退避重试
    - 熔断器保护
    - 异常分类
    - 重试历史追踪
    """
    
    def __init__(self, config: RetryConfig = None):
        self.config = config or RetryConfig()
        self.circuit_breaker = CircuitBreaker(
            self.config.circuit_breaker_threshold,
            self.config.circuit_breaker_timeout
        )
        self._retry_history: List[dict] = []
    
    async def execute(
        self,
        func: Callable,
        *args,
        **kwargs
    ) -> Any:
        """
        带重试的执行器
        
        Args:
            func: 要执行的异步函数
            *args: 位置参数
            **kwargs: 关键字参数
        
        Returns:
            函数执行结果
        
        Raises:
            Exception: 所有重试失败后抛出最后一次异常
        """
        last_exception = None
        
        for attempt in range(1, self.config.max_attempts + 1):
            # 检查熔断器
            if not self.circuit_breaker.can_execute():
                from ..reliability.error import CircuitBreakerOpenError
                raise CircuitBreakerOpenError(
                    timeout=self.config.circuit_breaker_timeout
                )
            
            try:
                # 执行函数
                result = await func(*args, **kwargs)
                
                # 记录成功
                self._record_attempt(attempt, success=True, result=result)
                self.circuit_breaker.record_success()
                
                logger.debug(f"执行成功: {func.__name__} (attempt {attempt})")
                return result
            
            except Exception as e:
                last_exception = e
                failure_reason = self._classify_exception(e)
                
                # 记录失败
                self._record_attempt(
                    attempt, 
                    success=False, 
                    exception=e, 
                    reason=failure_reason
                )
                
                logger.warning(
                    f"执行失败: {func.__name__} (attempt {attempt}/{self.config.max_attempts}), "
                    f"原因: {failure_reason.value}"
                )
                
                # 判断是否应该重试
                if failure_reason not in self.config.retry_on:
                    logger.error(f"不重试，失败原因: {failure_reason.value}")
                    raise
                
                # 调用重试回调
                if self.config.on_retry:
                    delay = self._calculate_delay(attempt)
                    self.config.on_retry(attempt, e, delay)
                
                # 最后一次尝试，直接抛出
                if attempt == self.config.max_attempts:
                    break
                
                # 计算退避时间
                delay = self._calculate_delay(attempt)
                logger.info(f"等待 {delay:.2f}s 后重试...")
                await asyncio.sleep(delay)
        
        # 所有重试失败
        self.circuit_breaker.record_failure()
        
        # 调用耗尽回调
        if self.config.on_exhausted:
            self.config.on_exhausted(last_exception)
        
        raise last_exception
    
    def _calculate_delay(self, attempt: int) -> float:
        """
        计算退避时间（指数退避 + 随机抖动）
        
        delay = base_delay * (exponential_base ^ (attempt - 1)) * (0.5 + random())
        capped at max_delay
        """
        delay = self.config.base_delay * (
            self.config.exponential_base ** (attempt - 1)
        )
        
        # 添加随机抖动（0.5 ~ 1.5 倍）
        jitter = 1.0 - self.config.jitter_factor + random.random() * self.config.jitter_factor * 2
        delay *= jitter
        
        # 限制最大延迟
        return min(delay, self.config.max_delay)
    
    def _classify_exception(self, exc: Exception) -> FailureReason:
        """
        异常分类
        
        根据异常消息判断失败原因
        """
        exc_str = str(exc).lower()
        
        if "timeout" in exc_str:
            return FailureReason.TIMEOUT
        elif "connection" in exc_str or "disconnect" in exc_str:
            return FailureReason.CONNECTION_LOST
        elif "crash" in exc_str or "crashed" in exc_str:
            return FailureReason.PAGE_CRASHED
        elif "selector" in exc_str or "not found" in exc_str:
            return FailureReason.SELECTOR_NOT_FOUND
        elif "network" in exc_str or "http" in exc_str:
            return FailureReason.NETWORK_ERROR
        else:
            return FailureReason.UNKNOWN
    
    def _record_attempt(
        self,
        attempt: int,
        success: bool,
        result: Any = None,
        exception: Exception = None,
        reason: FailureReason = None
    ):
        """记录重试历史"""
        entry = {
            "attempt": attempt,
            "success": success,
            "timestamp": time.time(),
        }
        
        if result is not None:
            entry["result"] = str(result)[:200]  # 截断长结果
        
        if exception is not None:
            entry["exception"] = str(exception)[:500]
            entry["reason"] = reason.value if reason else None
        
        self._retry_history.append(entry)
    
    def get_history(self, limit: int = 10) -> List[dict]:
        """获取重试历史（最近 limit 条）"""
        return self._retry_history[-limit:]
    
    def reset_history(self):
        """清空重试历史"""
        self._retry_history.clear()
    
    def reset_circuit_breaker(self):
        """重置熔断器"""
        self.circuit_breaker.reset()


def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_on: Optional[List[FailureReason]] = None,
    **kwargs
):
    """
    装饰器：为异步函数添加重试能力
    
    Usage:
        @retry(max_attempts=3, base_delay=0.5)
        async def fetch_page(url):
            ...
    """
    def decorator(func: Callable) -> Callable:
        handler = RetryHandler(RetryConfig(
            max_attempts=max_attempts,
            base_delay=base_delay,
            max_delay=max_delay,
            retry_on=retry_on or list(FailureReason),
            **kwargs
        ))
        
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await handler.execute(func, *args, **kwargs)
        
        return wrapper
    return decorator


# 便捷函数
async def retry_async(func, *args, config: RetryConfig = None, **kwargs):
    """异步重试便捷函数"""
    handler = RetryHandler(config)
    return await handler.execute(func, *args, **kwargs)
