# -*- coding: utf-8 -*-
"""
Finance Data Toolkit - 重试调度器

提供统一的重试调度能力，支持：
1. 同步/异步重试调度
2. 多种退避算法（指数退避、固定间隔、线性退避、全抖动）
3. 错误分类与条件重试
4. 并发重试控制
5. 重试统计与监控

使用示例：
    from finance_toolkit.retry_scheduler import RetryScheduler, BackoffAlgorithm
    
    scheduler = RetryScheduler(
        max_retries=3,
        algorithm=BackoffAlgorithm.EXPONENTIAL,
        base_delay=1.0,
    )
    result = scheduler.run(fetch_func, data_type='quote', source='akshare')
"""

import asyncio
import functools
import logging
import random
import time
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    Union,
)

from .error_capture import ErrorCapture, ErrorType
from .exceptions import (
    CircuitBreakerError,
    SourceUnavailableError,
    TimeoutError as FinanceTimeoutError,
)

logger = logging.getLogger(__name__)


# ============== 退避算法枚举 ==============

class BackoffAlgorithm(Enum):
    """退避算法类型"""
    EXPONENTIAL = "exponential"           # 指数退避: delay = base * factor^(attempt-1)
    FIXED = "fixed"                        # 固定间隔: delay = base
    LINEAR = "linear"                      # 线性退避: delay = base * attempt
    FULL_JITTER = "full_jitter"            # 全抖动: delay = random(0, base * factor^(attempt-1))
    DECORRELATED = "decorrelated"          # 关联退避: delay = min(cap, random(base, last*2))


# ============== 重试策略基类 ==============

class RetryPolicy(ABC):
    """重试策略抽象基类"""

    @abstractmethod
    def get_delay(self, attempt: int, error: Exception) -> float:
        """计算重试延迟（秒）"""
        pass

    @abstractmethod
    def should_retry(self, attempt: int, error: Exception) -> bool:
        """判断是否应该重试"""
        pass

    @abstractmethod
    def on_success(self) -> None:
        """记录成功"""
        pass

    @abstractmethod
    def on_failure(self) -> None:
        """记录失败"""
        pass


# ============== 指数退避策略 ==============

class ExponentialBackoffPolicy(RetryPolicy):
    """
    指数退避策略

    delay = min(base_delay * backoff_factor^(attempt-1), max_delay)
    """

    def __init__(
        self,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        backoff_factor: float = 2.0,
        jitter: bool = True,
        retryable_errors: Optional[List[ErrorType]] = None,
    ):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor
        self.jitter = jitter
        self.retryable_errors = retryable_errors or [
            ErrorType.NETWORK_TIMEOUT,
            ErrorType.NETWORK_CONNECTION,
            ErrorType.NETWORK_DNS,
            ErrorType.NETWORK_SSL,
            ErrorType.HTTP_4XX,
            ErrorType.HTTP_5XX,
        ]
        self._consecutive_failures = 0
        self._consecutive_successes = 0

    def get_delay(self, attempt: int, error: Exception) -> float:
        # 特殊处理：限流错误使用 retry_after
        if hasattr(error, 'retry_after') and error.retry_after is not None:
            return min(float(error.retry_after), self.max_delay)
        if isinstance(error, SourceUnavailableError):
            retry_after = getattr(error, 'retry_after', None)
            if retry_after is not None:
                return min(float(retry_after), self.max_delay)

        delay = self.base_delay * (self.backoff_factor ** (attempt - 1))
        delay = min(delay, self.max_delay)

        if self.jitter:
            delay = delay * (0.5 + random.random() * 0.5)

        return delay

    def should_retry(self, attempt: int, error: Exception) -> bool:
        if attempt > self._get_max_retries():
            return False
        if self._consecutive_failures > 10:
            logger.error("连续失败次数过多，停止重试")
            return False
        error_type = _classify_exception(error)
        if error_type == ErrorType.DATA_QUALITY:
            return False
        if self.retryable_errors and error_type not in self.retryable_errors:
            return False
        return True

    def _get_max_retries(self) -> int:
        return getattr(self, '_max_retries', 3)

    def set_max_retries(self, max_retries: int) -> None:
        self._max_retries = max_retries

    def on_success(self) -> None:
        self._consecutive_failures = max(0, self._consecutive_failures - 1)
        self._consecutive_successes += 1

    def on_failure(self) -> None:
        self._consecutive_failures += 1
        self._consecutive_successes = 0


# ============== 固定间隔策略 ==============

class FixedIntervalPolicy(RetryPolicy):
    """固定间隔重试策略"""

    def __init__(
        self,
        base_delay: float = 1.0,
        max_retries: int = 3,
        retryable_errors: Optional[List[ErrorType]] = None,
    ):
        self.base_delay = base_delay
        self.max_retries = max_retries
        self.retryable_errors = retryable_errors or []
        self._consecutive_failures = 0

    def get_delay(self, attempt: int, error: Exception) -> float:
        return self.base_delay

    def should_retry(self, attempt: int, error: Exception) -> bool:
        if attempt > self.max_retries:
            return False
        return True

    def on_success(self) -> None:
        self._consecutive_failures = 0

    def on_failure(self) -> None:
        self._consecutive_failures += 1


# ============== 线性退避策略 ==============

class LinearBackoffPolicy(RetryPolicy):
    """线性退避策略：delay = base_delay * attempt"""

    def __init__(
        self,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        max_retries: int = 3,
        retryable_errors: Optional[List[ErrorType]] = None,
    ):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.retryable_errors = retryable_errors or []

    def get_delay(self, attempt: int, error: Exception) -> float:
        delay = self.base_delay * attempt
        return min(delay, self.max_delay)

    def should_retry(self, attempt: int, error: Exception) -> bool:
        return attempt <= self.max_retries

    def on_success(self) -> None:
        pass

    def on_failure(self) -> None:
        pass


# ============== 全抖动策略 ==============

class FullJitterPolicy(RetryPolicy):
    """全抖动策略：delay = random(0, base * factor^(attempt-1))"""

    def __init__(
        self,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        backoff_factor: float = 2.0,
        max_retries: int = 3,
        retryable_errors: Optional[List[ErrorType]] = None,
    ):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor
        self.max_retries = max_retries
        self.retryable_errors = retryable_errors or []

    def get_delay(self, attempt: int, error: Exception) -> float:
        delay = self.base_delay * (self.backoff_factor ** (attempt - 1))
        delay = min(delay, self.max_delay)
        return random.uniform(0, delay)

    def should_retry(self, attempt: int, error: Exception) -> bool:
        return attempt <= self.max_retries

    def on_success(self) -> None:
        pass

    def on_failure(self) -> None:
        pass


# ============== 关联退避策略 ==============

class DecorrelatedJitterPolicy(RetryPolicy):
    """
    AWS 关联退避策略
    delay = min(cap, random(base, last_attempt_delay * 2))
    """

    def __init__(
        self,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        max_retries: int = 3,
        retryable_errors: Optional[List[ErrorType]] = None,
    ):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.retryable_errors = retryable_errors or []
        self._last_delay = base_delay

    def get_delay(self, attempt: int, error: Exception) -> float:
        upper = min(self._last_delay * 2, self.max_delay * 2)
        delay = random.uniform(self.base_delay, upper)
        delay = min(delay, self.max_delay)
        self._last_delay = delay
        return delay

    def should_retry(self, attempt: int, error: Exception) -> bool:
        return attempt <= self.max_retries

    def on_success(self) -> None:
        self._last_delay = self.base_delay

    def on_failure(self) -> None:
        pass


# ============== 重试统计 ==============

@dataclass
class RetryStats:
    """重试统计信息"""
    total_attempts: int = 0
    successful_attempts: int = 0
    failed_attempts: int = 0
    total_delay_seconds: float = 0.0
    errors_by_type: Dict[str, int] = field(default_factory=dict)
    last_error: Optional[str] = None
    last_error_type: Optional[str] = None

    @property
    def success_rate(self) -> float:
        if self.total_attempts == 0:
            return 100.0
        return (self.successful_attempts / self.total_attempts) * 100

    def to_dict(self) -> Dict[str, Any]:
        return {
            'total_attempts': self.total_attempts,
            'successful_attempts': self.successful_attempts,
            'failed_attempts': self.failed_attempts,
            'success_rate': round(self.success_rate, 2),
            'total_delay_seconds': round(self.total_delay_seconds, 2),
            'errors_by_type': self.errors_by_type,
            'last_error': self.last_error,
            'last_error_type': self.last_error_type,
        }


# ============== 重试结果 ==============

@dataclass
class RetryResult:
    """重试执行结果"""
    success: bool
    result: Any
    stats: RetryStats
    error: Optional[Exception] = None

    def __bool__(self) -> bool:
        return self.success


# ============== 错误分类辅助 ==============

def _classify_exception(exception: Exception) -> ErrorType:
    """根据异常分类错误类型"""
    from .error_capture import _ERROR_CLASSIFICATION_RULES

    msg = str(exception)
    exc_type = type(exception).__name__
    combined = f"{exc_type}: {msg}"

    for pattern, error_type in _ERROR_CLASSIFICATION_RULES:
        if pattern.search(combined):
            return error_type

    # 检查金融异常类型
    if isinstance(exception, FinanceTimeoutError):
        return ErrorType.NETWORK_TIMEOUT
    if isinstance(exception, SourceUnavailableError):
        return ErrorType.NETWORK_CONNECTION

    # 检查 HTTP 状态码
    if hasattr(exception, 'response'):
        status = getattr(exception.response, 'status_code', None)
        if status:
            if status == 429:
                return ErrorType.HTTP_4XX
            elif 400 <= status < 500:
                return ErrorType.HTTP_4XX
            elif 500 <= status < 600:
                return ErrorType.HTTP_5XX

    return ErrorType.UNKNOWN


# ============== 重试调度器 ==============

class RetryScheduler:
    """
    重试调度器 - 统一重试入口

    功能：
    1. 同步/异步重试调度
    2. 多种退避算法支持
    3. 基于错误类型的条件重试
    4. 重试统计与监控
    5. 并发重试控制

    使用示例：
        scheduler = RetryScheduler(
            max_retries=3,
            algorithm=BackoffAlgorithm.EXPONENTIAL,
            base_delay=1.0,
        )
        result = scheduler.run(fetch_func, data_type='quote', source='akshare')
        if result.success:
            print(result.result)
        else:
            print(f"失败: {result.stats.last_error}")
    """

    _POLICY_MAP = {
        BackoffAlgorithm.EXPONENTIAL: ExponentialBackoffPolicy,
        BackoffAlgorithm.FIXED: FixedIntervalPolicy,
        BackoffAlgorithm.LINEAR: LinearBackoffPolicy,
        BackoffAlgorithm.FULL_JITTER: FullJitterPolicy,
        BackoffAlgorithm.DECORRELATED: DecorrelatedJitterPolicy,
    }

    def __init__(
        self,
        max_retries: int = 3,
        algorithm: BackoffAlgorithm = BackoffAlgorithm.EXPONENTIAL,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        backoff_factor: float = 2.0,
        jitter: bool = True,
        retryable_errors: Optional[List[ErrorType]] = None,
        error_capture: Optional[ErrorCapture] = None,
        circuit_breaker: Optional[Any] = None,
    ):
        self.max_retries = max_retries
        self.algorithm = algorithm
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor
        self.jitter = jitter
        self.retryable_errors = retryable_errors
        self.error_capture = error_capture
        self.circuit_breaker = circuit_breaker
        self._stats = RetryStats()
        self._lock = threading.Lock()

        # 创建策略实例
        self._policy = self._create_policy()

    def _create_policy(self) -> RetryPolicy:
        """根据算法创建策略实例"""
        policy_class = self._POLICY_MAP.get(self.algorithm)
        if policy_class is None:
            raise ValueError(f"不支持的退避算法: {self.algorithm}")

        kwargs = {
            'base_delay': self.base_delay,
            'max_delay': self.max_delay,
            'backoff_factor': self.backoff_factor,
            'jitter': self.jitter,
            'retryable_errors': self.retryable_errors,
        }
        if self.algorithm in (BackoffAlgorithm.FIXED, BackoffAlgorithm.LINEAR,
                               BackoffAlgorithm.FULL_JITTER, BackoffAlgorithm.DECORRELATED):
            kwargs['max_retries'] = self.max_retries

        policy = policy_class(**kwargs)
        policy.set_max_retries(self.max_retries)
        return policy

    # ============== 同步执行 ==============

    def run(
        self,
        func: Callable,
        *args,
        data_type: str = "unknown",
        source: str = "unknown",
        symbol: Optional[str] = None,
        **kwargs,
    ) -> RetryResult:
        """
        同步执行带重试的函数

        Args:
            func: 要执行的函数
            data_type: 数据类型
            source: 数据源
            symbol: 标的代码

        Returns:
            RetryResult: 执行结果
        """
        # 检查熔断器
        if self.circuit_breaker and self.circuit_breaker.is_open():
            self._record_failure(CircuitBreakerError(source, 0, 0))
            return RetryResult(
                success=False,
                result=None,
                stats=self._stats,
                error=CircuitBreakerError(source, 0, 0),
            )

        # 初始化错误捕获
        capture = self.error_capture or ErrorCapture(
            source=source,
            data_type=data_type,
            symbol=symbol,
            max_retry=self.max_retries,
        )

        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 2):  # 首次 + 重试
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                duration_ms = (time.time() - start_time) * 1000
                capture.record_success(duration_ms)
                self._record_success()
                self._policy.on_success()
                return RetryResult(success=True, result=result, stats=self._stats)

            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                error_type = capture.classify_error(e)
                last_error = capture.record_failure(
                    error_type, e, attempt=attempt, duration_ms=duration_ms
                )
                last_error.__dict__['_original_exception'] = e
                self._record_failure(e, error_type)
                self._policy.on_failure()

                # 检查是否继续重试
                if attempt <= self.max_retries and self._policy.should_retry(attempt, e):
                    delay = self._policy.get_delay(attempt, e)
                    logger.warning(
                        f"[{source}] {error_type.value}: 第 {attempt} 次失败，"
                        f"等待 {delay:.2f}s 后重试"
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"[{source}] {error_type.value}: 停止重试")
                    break

        return RetryResult(
            success=False,
            result=None,
            stats=self._stats,
            error=last_error or Exception("Unknown retry error"),
        )

    # ============== 异步执行 ==============

    async def run_async(
        self,
        func: Callable,
        *args,
        data_type: str = "unknown",
        source: str = "unknown",
        symbol: Optional[str] = None,
        **kwargs,
    ) -> RetryResult:
        """
        异步执行带重试的函数

        Args:
            func: 要执行的异步函数
            data_type: 数据类型
            source: 数据源
            symbol: 标的代码

        Returns:
            RetryResult: 执行结果
        """
        # 检查熔断器
        if self.circuit_breaker and self.circuit_breaker.is_open():
            self._record_failure(CircuitBreakerError(source, 0, 0))
            return RetryResult(
                success=False,
                result=None,
                stats=self._stats,
                error=CircuitBreakerError(source, 0, 0),
            )

        capture = self.error_capture or ErrorCapture(
            source=source,
            data_type=data_type,
            symbol=symbol,
            max_retry=self.max_retries,
        )

        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 2):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                duration_ms = (time.time() - start_time) * 1000
                capture.record_success(duration_ms)
                self._record_success()
                self._policy.on_success()
                return RetryResult(success=True, result=result, stats=self._stats)

            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                error_type = capture.classify_error(e)
                last_error = capture.record_failure(
                    error_type, e, attempt=attempt, duration_ms=duration_ms
                )
                last_error.__dict__['_original_exception'] = e
                self._record_failure(e, error_type)
                self._policy.on_failure()

                if attempt <= self.max_retries and self._policy.should_retry(attempt, e):
                    delay = await self._get_async_delay(attempt, e)
                    logger.warning(
                        f"[{source}] {error_type.value}: 异步重试第 {attempt} 次，"
                        f"等待 {delay:.2f}s"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"[{source}] {error_type.value}: 停止重试")
                    break

        return RetryResult(
            success=False,
            result=None,
            stats=self._stats,
            error=last_error or Exception("Unknown retry error"),
        )

    async def _get_async_delay(self, attempt: int, error: Exception) -> float:
        """异步获取延迟（兼容异步策略）"""
        if hasattr(self._policy, 'get_delay_async'):
            return await self._policy.get_delay_async(attempt, error)
        return self._policy.get_delay(attempt, error)

    # ============== 并发重试 ==============

    def run_concurrent(
        self,
        tasks: List[Tuple[Callable, Dict[str, Any]]],
        max_concurrent: int = 5,
    ) -> List[RetryResult]:
        """
        并发执行多个带重试的任务

        Args:
            tasks: 任务列表，每个元素为 (func, kwargs) 元组
            max_concurrent: 最大并发数

        Returns:
            结果列表
        """
        import concurrent.futures

        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = {
                executor.submit(self.run, func, **kwargs): idx
                for idx, (func, kwargs) in enumerate(tasks)
            }
            for future in concurrent.futures.as_completed(futures):
                idx = futures[future]
                try:
                    results.append((idx, future.result()))
                except Exception as e:
                    results.append((idx, RetryResult(
                        success=False,
                        result=None,
                        stats=self._stats,
                        error=e,
                    )))

        results.sort(key=lambda x: x[0])
        return [r for _, r in results]

    # ============== 统计管理 ==============

    def _record_success(self) -> None:
        """记录成功"""
        with self._lock:
            self._stats.total_attempts += 1
            self._stats.successful_attempts += 1

    def _record_failure(self, error: Exception, error_type: Optional[ErrorType] = None) -> None:
        """记录失败"""
        et = error_type or _classify_exception(error)
        with self._lock:
            self._stats.total_attempts += 1
            self._stats.failed_attempts += 1
            self._stats.last_error = str(error)[:200]
            self._stats.last_error_type = et.value
            self._stats.errors_by_type[et.value] = self._stats.errors_by_type.get(et.value, 0) + 1

    def get_stats(self) -> Dict[str, Any]:
        """获取当前统计信息"""
        return self._stats.to_dict()

    def reset_stats(self) -> None:
        """重置统计"""
        with self._lock:
            self._stats = RetryStats()

    # ============== 配置更新 ==============

    def update_config(
        self,
        max_retries: Optional[int] = None,
        algorithm: Optional[BackoffAlgorithm] = None,
        base_delay: Optional[float] = None,
    ) -> None:
        """动态更新配置"""
        if max_retries is not None:
            self.max_retries = max_retries
        if algorithm is not None:
            self.algorithm = algorithm
        if base_delay is not None:
            self.base_delay = base_delay
        self._policy = self._create_policy()
        logger.info(f"重试配置已更新: algorithm={self.algorithm.value}, max_retries={self.max_retries}")


# ============== 便捷函数 ==============

def retry_run(
    func: Callable,
    *args,
    max_retries: int = 3,
    algorithm: BackoffAlgorithm = BackoffAlgorithm.EXPONENTIAL,
    base_delay: float = 1.0,
    data_type: str = "unknown",
    source: str = "unknown",
    **kwargs,
) -> Any:
    """
    便捷重试函数

    使用示例：
        data = retry_run(fetch_quotes, symbols=['000001'], source='akshare')
    """
    scheduler = RetryScheduler(
        max_retries=max_retries,
        algorithm=algorithm,
        base_delay=base_delay,
    )
    result = scheduler.run(func, *args, data_type=data_type, source=source, **kwargs)
    if not result.success:
        raise result.error or Exception("Retry exhausted")
    return result.result


def async_retry_run(
    func: Callable,
    *args,
    max_retries: int = 3,
    algorithm: BackoffAlgorithm = BackoffAlgorithm.EXPONENTIAL,
    base_delay: float = 1.0,
    data_type: str = "unknown",
    source: str = "unknown",
    **kwargs,
) -> asyncio.Task:
    """
    便捷异步重试函数

    使用示例：
        task = async_retry_run(async_fetch, source='akshare')
        data = await task
    """
    scheduler = RetryScheduler(
        max_retries=max_retries,
        algorithm=algorithm,
        base_delay=base_delay,
    )
    return asyncio.create_task(
        scheduler.run_async(func, *args, data_type=data_type, source=source, **kwargs)
    )


# ============== 默认调度器实例 ==============

DEFAULT_RETRY_SCHEDULER = RetryScheduler()


__all__ = [
    'RetryScheduler',
    'RetryPolicy',
    'ExponentialBackoffPolicy',
    'FixedIntervalPolicy',
    'LinearBackoffPolicy',
    'FullJitterPolicy',
    'DecorrelatedJitterPolicy',
    'RetryStats',
    'RetryResult',
    'BackoffAlgorithm',
    'retry_run',
    'async_retry_run',
    'DEFAULT_RETRY_SCHEDULER',
]
