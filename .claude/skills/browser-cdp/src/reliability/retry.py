"""
统一重试框架

提供同步和异步重试包装器，支持多种退避策略和熔断器。
整合同步 retry_operation 和异步 retry_operation_async，
解决原有同步/异步重试框架分离的问题。
"""

import asyncio
import random
import time
import logging
from enum import Enum
from functools import wraps
from typing import Any, Callable, Optional, Tuple, Type

from .error import (
    ReliabilityError,
    CDPConnectionLostError,
    CDPCommandTimeoutError,
    ElementNotFoundError,
    is_retryable,
)

logger = logging.getLogger(__name__)


class BackoffStrategy(Enum):
    """退避策略枚举"""
    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    EXPONENTIAL_JITTER = "exponential_jitter"


class RetryConfig:
    """重试配置"""

    # 默认配置
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_BASE_DELAY = 1.0
    DEFAULT_MAX_DELAY = 30.0
    DEFAULT_BACKOFF = BackoffStrategy.EXPONENTIAL_JITTER

    # 操作类型默认配置
    OPERATION_DEFAULTS = {
        "cdp_command": {"max_retries": 5, "base_delay": 1.0, "circuit_breaker": True},
        "element_find": {"max_retries": 3, "base_delay": 0.5, "circuit_breaker": False},
        "navigation": {"max_retries": 3, "base_delay": 2.0, "circuit_breaker": True},
        "screenshot": {"max_retries": 2, "base_delay": 1.0, "circuit_breaker": False},
        "input_click": {"max_retries": 3, "base_delay": 1.0, "circuit_breaker": False},
    }

    def __init__(
        self,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_strategy: BackoffStrategy = DEFAULT_BACKOFF,
        base_delay: float = DEFAULT_BASE_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
        retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
        circuit_breaker: bool = False,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_recovery: float = 30.0,
        on_retry: Optional[Callable[[int, Exception, float], None]] = None,
        on_exhausted: Optional[Callable[[Exception], None]] = None,
    ):
        self.max_retries = max_retries
        self.backoff_strategy = backoff_strategy
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.retryable_exceptions = retryable_exceptions or (ReliabilityError, CDPConnectionLostError, ElementNotFoundError)
        self.circuit_breaker = circuit_breaker
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.circuit_breaker_recovery = circuit_breaker_recovery
        self.on_retry = on_retry
        self.on_exhausted = on_exhausted

    @classmethod
    def for_operation(cls, operation: str, **overrides) -> "RetryConfig":
        """根据操作类型创建默认配置"""
        defaults = cls.OPERATION_DEFAULTS.get(operation, {})
        merged = {**defaults, **overrides}
        return cls(**merged)


class CircuitBreaker:
    """
    熔断器：连续失败 N 次后熔断，等待 M 秒后半开试探。

    状态转换：
    closed → open（连续失败达到阈值）
    open → half_open（等待恢复超时）
    half_open → closed（试探成功）
    half_open → open（试探失败）
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.state = "closed"  # closed | open | half_open
        self.success_count_after_half_open = 0

    def can_execute(self) -> bool:
        """检查是否允许执行"""
        if self.state == "closed":
            return True
        if self.state == "open":
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = "half_open"
                self.success_count_after_half_open = 0
                logger.info("Circuit breaker: half-open, allowing probe request")
                return True
            return False
        # half_open: 只允许一次试探
        return self.success_count_after_half_open == 0

    def record_success(self):
        """记录成功"""
        if self.state == "half_open":
            self.state = "closed"
            logger.info("Circuit breaker: closed after successful probe")
        self.failure_count = 0
        self.success_count_after_half_open = 0

    def record_failure(self):
        """记录失败"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.state == "half_open":
            self.state = "open"
            logger.warning("Circuit breaker: open after failed probe")
        elif self.failure_count >= self.failure_threshold:
            self.state = "open"
            logger.warning(
                f"Circuit breaker: open after {self.failure_count} consecutive failures"
            )

    def get_status(self) -> dict:
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "time_since_last_failure": time.time() - self.last_failure_time if self.last_failure_time else 0,
        }

    def reset(self):
        """手动重置熔断器"""
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.state = "closed"
        self.success_count_after_half_open = 0


def _calculate_delay(strategy: BackoffStrategy, attempt: int, base: float, max_delay: float) -> float:
    """计算退避延迟"""
    if strategy == BackoffStrategy.FIXED:
        return min(base, max_delay)
    elif strategy == BackoffStrategy.LINEAR:
        return min(base * attempt, max_delay)
    elif strategy == BackoffStrategy.EXPONENTIAL:
        return min(base ** attempt, max_delay)
    elif strategy == BackoffStrategy.EXPONENTIAL_JITTER:
        delay = min(base ** attempt, max_delay)
        return delay * (0.5 + random.random())  # 50%~150% jitter
    return min(base * attempt, max_delay)


def retry_operation(
    func: Callable,
    config: Optional[RetryConfig] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
    operation: str = "unknown",
    *args,
    **kwargs,
) -> Any:
    """
    同步重试包装器。

    Args:
        func: 要执行的函数
        config: 重试配置
        circuit_breaker: 熔断器实例（可选，默认自动创建）
        operation: 操作类型名称（用于日志）
        *args: 传递给 func 的位置参数
        **kwargs: 传递给 func 的关键字参数

    Returns:
        func 的返回值

    Raises:
        最后一次异常（重试耗尽后）
    """
    config = config or RetryConfig.for_operation(operation)
    cb = circuit_breaker
    if cb is None and config.circuit_breaker:
        cb = CircuitBreaker(
            failure_threshold=config.circuit_breaker_threshold,
            recovery_timeout=config.circuit_breaker_recovery,
        )

    last_exception = None

    for attempt in range(config.max_retries + 1):
        # 检查熔断器
        if cb and not cb.can_execute():
            remaining = cb.recovery_timeout - (time.time() - cb.last_failure_time)
            raise CDPConnectionLostError(
                details={
                    "operation": operation,
                    "circuit_breaker_state": cb.get_status(),
                    "remaining_retry_seconds": round(remaining, 1),
                }
            )

        try:
            result = func(*args, **kwargs)
            if cb:
                cb.record_success()
            return result
        except config.retryable_exceptions as e:
            last_exception = e
            if attempt < config.max_retries:
                delay = _calculate_delay(
                    config.backoff_strategy, attempt, config.base_delay, config.max_delay
                )
                logger.warning(
                    f"[{operation}] Retry {attempt+1}/{config.max_retries} after {delay:.1f}s: {e}"
                )
                if config.on_retry:
                    config.on_retry(attempt + 1, e, delay)
                time.sleep(delay)
            if cb:
                cb.record_failure()
        except Exception as e:
            # 非预期异常，不重试
            logger.error(f"[{operation}] Non-retryable error: {e}")
            raise

    # 重试耗尽
    logger.error(f"[{operation}] All {config.max_retries} retries exhausted")
    if config.on_exhausted:
        config.on_exhausted(last_exception)
    raise last_exception


async def retry_operation_async(
    func: Callable,
    config: Optional[RetryConfig] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
    operation: str = "unknown",
    *args,
    **kwargs,
) -> Any:
    """
    异步重试包装器。

    Args:
        func: 要执行的异步函数
        config: 重试配置
        circuit_breaker: 熔断器实例（可选，默认自动创建）
        operation: 操作类型名称（用于日志）
        *args: 传递给 func 的位置参数
        **kwargs: 传递给 func 的关键字参数

    Returns:
        func 的返回值

    Raises:
        最后一次异常（重试耗尽后）
    """
    config = config or RetryConfig.for_operation(operation)
    cb = circuit_breaker
    if cb is None and config.circuit_breaker:
        cb = CircuitBreaker(
            failure_threshold=config.circuit_breaker_threshold,
            recovery_timeout=config.circuit_breaker_recovery,
        )

    last_exception = None

    for attempt in range(config.max_retries + 1):
        # 检查熔断器
        if cb and not cb.can_execute():
            remaining = cb.recovery_timeout - (time.time() - cb.last_failure_time)
            raise CDPConnectionLostError(
                details={
                    "operation": operation,
                    "circuit_breaker_state": cb.get_status(),
                    "remaining_retry_seconds": round(remaining, 1),
                }
            )

        try:
            result = await func(*args, **kwargs)
            if cb:
                cb.record_success()
            return result
        except config.retryable_exceptions as e:
            last_exception = e
            if attempt < config.max_retries:
                delay = _calculate_delay(
                    config.backoff_strategy, attempt, config.base_delay, config.max_delay
                )
                logger.warning(
                    f"[{operation}] Retry {attempt+1}/{config.max_retries} after {delay:.1f}s: {e}"
                )
                if config.on_retry:
                    config.on_retry(attempt + 1, e, delay)
                await asyncio.sleep(delay)
            if cb:
                cb.record_failure()
        except Exception as e:
            # 非预期异常，不重试
            logger.error(f"[{operation}] Non-retryable error: {e}")
            raise

    # 重试耗尽
    logger.error(f"[{operation}] All {config.max_retries} retries exhausted")
    if config.on_exhausted:
        config.on_exhausted(last_exception)
    raise last_exception


def with_retry(
    max_retries: int = 3,
    backoff: BackoffStrategy = BackoffStrategy.EXPONENTIAL_JITTER,
    base_delay: float = 1.0,
    operation: str = "unknown",
    circuit_breaker: bool = False,
):
    """
    装饰器：为同步函数添加重试能力。

    Usage:
        @with_retry(max_retries=3, operation="my_operation")
        def my_func(x):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            config = RetryConfig(
                max_retries=max_retries,
                backoff_strategy=backoff,
                base_delay=base_delay,
                circuit_breaker=circuit_breaker,
            )
            return retry_operation(
                func,
                config=config,
                operation=operation,
                *args,
                **kwargs,
            )
        return wrapper
    return decorator


def with_retry_async(
    max_retries: int = 3,
    backoff: BackoffStrategy = BackoffStrategy.EXPONENTIAL_JITTER,
    base_delay: float = 1.0,
    operation: str = "unknown",
    circuit_breaker: bool = False,
):
    """
    装饰器：为异步函数添加重试能力。

    Usage:
        @with_retry_async(max_retries=3, operation="my_operation")
        async def my_func(x):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            config = RetryConfig(
                max_retries=max_retries,
                backoff_strategy=backoff,
                base_delay=base_delay,
                circuit_breaker=circuit_breaker,
            )
            return await retry_operation_async(
                func,
                config=config,
                operation=operation,
                *args,
                **kwargs,
            )
        return wrapper
    return decorator


# 预定义的常用重试配置
RETRY_CONFIGS = {
    "cdp_command": RetryConfig.for_operation("cdp_command"),
    "element_find": RetryConfig.for_operation("element_find"),
    "navigation": RetryConfig.for_operation("navigation"),
    "screenshot": RetryConfig.for_operation("screenshot"),
    "input_click": RetryConfig.for_operation("input_click"),
}


def get_retry_config(operation: str) -> RetryConfig:
    """获取预定义的重试配置"""
    return RETRY_CONFIGS.get(operation, RetryConfig.for_operation(operation))
