# -*- coding: utf-8 -*-
"""
增强重试框架

基于分析结果优化的重试机制，支持：
- 按错误类型动态选择退避策略
- 智能熔断器（带试探恢复）
- 不可恢复错误快速失败
- 重试统计和监控
"""

import asyncio
import random
import time
import logging
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from .error import (
    ReliabilityError,
    CDPConnectionLostError,
    CDPCommandTimeoutError,
    ElementNotFoundError,
    NavigationTimeoutError,
    CaptchaDetectedError,
    BlockedByAntiBotError,
    is_retryable,
    categorize_error,
    ErrorCategory,
)

logger = logging.getLogger(__name__)


class BackoffStrategy(Enum):
    """退避策略枚举"""
    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    EXPONENTIAL_JITTER = "exponential_jitter"


class RetryStats:
    """重试统计信息"""
    
    def __init__(self):
        self.total_attempts = 0
        self.success_attempts = 0
        self.failure_attempts = 0
        self.retry_counts: Dict[str, int] = {}
        self.duration_sum = 0.0
        self.duration_count = 0
    
    def record_success(self, duration: float = 0.0):
        self.success_attempts += 1
        self.total_attempts += 1
        self.duration_sum += duration
        self.duration_count += 1
    
    def record_failure(self, error_type: str = "unknown"):
        self.failure_attempts += 1
        self.total_attempts += 1
        self.retry_counts[error_type] = self.retry_counts.get(error_type, 0) + 1
    
    def get_success_rate(self) -> float:
        if self.total_attempts == 0:
            return 0.0
        return self.success_attempts / self.total_attempts
    
    def get_avg_duration(self) -> float:
        if self.duration_count == 0:
            return 0.0
        return self.duration_sum / self.duration_count
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_attempts": self.total_attempts,
            "success_attempts": self.success_attempts,
            "failure_attempts": self.failure_attempts,
            "success_rate": self.get_success_rate(),
            "avg_duration": self.get_avg_duration(),
            "retry_counts": self.retry_counts,
        }


class RetryConfig:
    """重试配置 - 按错误类型优化"""
    
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_BASE_DELAY = 1.0
    DEFAULT_MAX_DELAY = 30.0
    DEFAULT_BACKOFF = BackoffStrategy.EXPONENTIAL_JITTER
    
    ERROR_TYPE_CONFIGS = {
        ErrorCategory.CONNECTION: {
            "max_retries": 5, "base_delay": 1.0, "max_delay": 30.0,
            "backoff": BackoffStrategy.EXPONENTIAL_JITTER,
            "circuit_breaker": True, "circuit_breaker_threshold": 5,
            "circuit_breaker_recovery": 30.0,
        },
        ErrorCategory.TIMEOUT: {
            "max_retries": 3, "base_delay": 2.0, "max_delay": 30.0,
            "backoff": BackoffStrategy.EXPONENTIAL,
            "circuit_breaker": True, "circuit_breaker_threshold": 3,
            "circuit_breaker_recovery": 20.0,
        },
        ErrorCategory.ELEMENT: {
            "max_retries": 3, "base_delay": 0.5, "max_delay": 10.0,
            "backoff": BackoffStrategy.LINEAR, "circuit_breaker": False,
        },
        ErrorCategory.NAVIGATION: {
            "max_retries": 3, "base_delay": 2.0, "max_delay": 30.0,
            "backoff": BackoffStrategy.EXPONENTIAL_JITTER,
            "circuit_breaker": True, "circuit_breaker_threshold": 3,
            "circuit_breaker_recovery": 20.0,
        },
        ErrorCategory.CONTENT: {
            "max_retries": 0, "base_delay": 0.0, "backoff": BackoffStrategy.FIXED,
            "circuit_breaker": False,
        },
        ErrorCategory.PERMISSION: {
            "max_retries": 0, "base_delay": 0.0, "backoff": BackoffStrategy.FIXED,
            "circuit_breaker": False,
        },
        ErrorCategory.UNKNOWN: {
            "max_retries": 1, "base_delay": 1.0, "backoff": BackoffStrategy.FIXED,
            "circuit_breaker": False,
        },
    }
    
    OPERATION_DEFAULTS = {
        "cdp_command": {"max_retries": 5, "base_delay": 1.0, "circuit_breaker": True},
        "element_find": {"max_retries": 3, "base_delay": 0.5, "circuit_breaker": False},
        "navigation": {"max_retries": 3, "base_delay": 2.0, "circuit_breaker": True},
        "screenshot": {"max_retries": 2, "base_delay": 1.0, "circuit_breaker": False},
        "input_click": {"max_retries": 3, "base_delay": 1.0, "circuit_breaker": False},
        "search": {"max_retries": 3, "base_delay": 1.5, "circuit_breaker": True},
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
        error_category: Optional[ErrorCategory] = None,
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
        self.error_category = error_category
    
    @classmethod
    def for_error_category(cls, category: ErrorCategory) -> "RetryConfig":
        """根据错误类型创建配置"""
        config_data = cls.ERROR_TYPE_CONFIGS.get(category, cls.ERROR_TYPE_CONFIGS[ErrorCategory.UNKNOWN])
        return cls(
            max_retries=config_data["max_retries"],
            backoff_strategy=config_data.get("backoff", BackoffStrategy.FIXED),
            base_delay=config_data.get("base_delay", 1.0),
            max_delay=config_data.get("max_delay", 30.0),
            circuit_breaker=config_data.get("circuit_breaker", False),
            circuit_breaker_threshold=config_data.get("circuit_breaker_threshold", 5),
            circuit_breaker_recovery=config_data.get("circuit_breaker_recovery", 30.0),
            error_category=category,
        )
    
    @classmethod
    def for_operation(cls, operation: str, **overrides) -> "RetryConfig":
        """根据操作类型创建默认配置"""
        defaults = cls.OPERATION_DEFAULTS.get(operation, {})
        merged = {**defaults, **overrides}
        return cls(**merged)
    
    @classmethod
    def adaptive(cls, error: Exception) -> "RetryConfig":
        """根据异常动态创建配置"""
        category = categorize_error(error)
        return cls.for_error_category(category)


class CircuitBreaker:
    """
    熔断器：连续失败 N 次后熔断，等待 M 秒后半开试探。
    
    状态转换：
    closed → open（连续失败达到阈值）
    open → half_open（等待恢复超时）
    half_open → closed（试探成功）
    half_open → open（试探失败）
    """
    
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.state = "closed"
        self.success_count_after_half_open = 0
        self.trip_count = 0
    
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
            self.trip_count += 1
            logger.warning(f"Circuit breaker: open after {self.failure_count} consecutive failures")
    
    def get_status(self) -> dict:
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "trip_count": self.trip_count,
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
        return delay * (0.5 + random.random())
    return min(base * attempt, max_delay)


def retry_operation(
    func: Callable,
    config: Optional[RetryConfig] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
    operation: str = "unknown",
    *args,
    **kwargs,
) -> Any:
    """同步重试包装器（增强版）"""
    config = config or RetryConfig.for_operation(operation)
    cb = circuit_breaker
    if cb is None and config.circuit_breaker:
        cb = CircuitBreaker(
            failure_threshold=config.circuit_breaker_threshold,
            recovery_timeout=config.circuit_breaker_recovery,
        )
    
    stats = RetryStats()
    last_exception = None
    start_time = time.time()
    
    for attempt in range(config.max_retries + 1):
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
            duration = time.time() - start_time
            stats.record_success(duration)
            if cb:
                cb.record_success()
            logger.debug(f"[{operation}] Success after {attempt + 1} attempt(s), duration={duration:.2f}s")
            return result
            
        except config.retryable_exceptions as e:
            last_exception = e
            category = categorize_error(e)
            
            if hasattr(e, 'recoverable') and not e.recoverable:
                logger.error(f"[{operation}] Non-recoverable error ({category.value}), stopping: {e}")
                stats.record_failure(category.value)
                raise
            
            stats.record_failure(category.value)
            
            if attempt < config.max_retries:
                delay = _calculate_delay(config.backoff_strategy, attempt, config.base_delay, config.max_delay)
                logger.warning(f"[{operation}] Retry {attempt+1}/{config.max_retries} after {delay:.1f}s: {category.value} - {e}")
                if config.on_retry:
                    config.on_retry(attempt + 1, e, delay)
                time.sleep(delay)
            
            if cb:
                cb.record_failure()
                
        except Exception as e:
            logger.error(f"[{operation}] Non-retryable error: {type(e).__name__}: {e}")
            stats.record_failure("unexpected")
            raise
    
    logger.error(f"[{operation}] All {config.max_retries} retries exhausted, stats={stats.to_dict()}")
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
    """异步重试包装器（增强版）"""
    config = config or RetryConfig.for_operation(operation)
    cb = circuit_breaker
    if cb is None and config.circuit_breaker:
        cb = CircuitBreaker(
            failure_threshold=config.circuit_breaker_threshold,
            recovery_timeout=config.circuit_breaker_recovery,
        )
    
    stats = RetryStats()
    last_exception = None
    start_time = time.time()
    
    for attempt in range(config.max_retries + 1):
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
            duration = time.time() - start_time
            stats.record_success(duration)
            if cb:
                cb.record_success()
            logger.debug(f"[{operation}] Success after {attempt + 1} attempt(s), duration={duration:.2f}s")
            return result
            
        except config.retryable_exceptions as e:
            last_exception = e
            category = categorize_error(e)
            
            if hasattr(e, 'recoverable') and not e.recoverable:
                logger.error(f"[{operation}] Non-recoverable error ({category.value}), stopping: {e}")
                stats.record_failure(category.value)
                raise
            
            stats.record_failure(category.value)
            
            if attempt < config.max_retries:
                delay = _calculate_delay(config.backoff_strategy, attempt, config.base_delay, config.max_delay)
                logger.warning(f"[{operation}] Retry {attempt+1}/{config.max_retries} after {delay:.1f}s: {category.value} - {e}")
                if config.on_retry:
                    config.on_retry(attempt + 1, e, delay)
                await asyncio.sleep(delay)
            
            if cb:
                cb.record_failure()
                
        except Exception as e:
            logger.error(f"[{operation}] Non-retryable error: {type(e).__name__}: {e}")
            stats.record_failure("unexpected")
            raise
    
    logger.error(f"[{operation}] All {config.max_retries} retries exhausted, stats={stats.to_dict()}")
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
    """装饰器：为同步函数添加重试能力"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            config = RetryConfig(
                max_retries=max_retries,
                backoff_strategy=backoff,
                base_delay=base_delay,
                circuit_breaker=circuit_breaker,
            )
            return retry_operation(func, config=config, operation=operation, *args, **kwargs)
        return wrapper
    return decorator


def with_retry_async(
    max_retries: int = 3,
    backoff: BackoffStrategy = BackoffStrategy.EXPONENTIAL_JITTER,
    base_delay: float = 1.0,
    operation: str = "unknown",
    circuit_breaker: bool = False,
):
    """装饰器：为异步函数添加重试能力"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            config = RetryConfig(
                max_retries=max_retries,
                backoff_strategy=backoff,
                base_delay=base_delay,
                circuit_breaker=circuit_breaker,
            )
            return await retry_operation_async(func, config=config, operation=operation, *args, **kwargs)
        return wrapper
    return decorator


RETRY_CONFIGS = {
    "cdp_command": RetryConfig.for_operation("cdp_command"),
    "element_find": RetryConfig.for_operation("element_find"),
    "navigation": RetryConfig.for_operation("navigation"),
    "screenshot": RetryConfig.for_operation("screenshot"),
    "input_click": RetryConfig.for_operation("input_click"),
    "search": RetryConfig.for_operation("search"),
}


def get_retry_config(operation: str) -> RetryConfig:
    """获取预定义的重试配置"""
    return RETRY_CONFIGS.get(operation, RetryConfig.for_operation(operation))


def get_config_for_error(error: Exception) -> RetryConfig:
    """根据错误类型获取最优重试配置"""
    category = categorize_error(error)
    return RetryConfig.for_error_category(category)
