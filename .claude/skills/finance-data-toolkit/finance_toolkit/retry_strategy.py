# -*- coding: utf-8 -*-
"""
智能重试策略模块

提供多种重试策略实现：
- 指数退避重试
- 固定间隔重试
- 自定义条件重试
- 带熔断保护的重试

使用示例：
    from finance_toolkit.retry_strategy import (
        exponential_backoff_retry,
        fixed_interval_retry,
        retry_with_circuit_breaker,
    )
    
    # 指数退避重试
    @exponential_backoff_retry(max_retries=3, base_delay=1)
    def fetch_data():
        ...
    
    # 带熔断保护的重试
    retry_with_circuit_breaker(fetch_func, source='akshare')
"""

import asyncio
import time
import logging
from typing import Callable, Any, Optional, List, Tuple, Type
from functools import wraps

from .exceptions import (
    SourceUnavailableError,
    SourceRateLimitedError,
    CircuitBreakerError,
    DataQualityError,
    DataValidationError,
    TimeoutError,
    ConnectionError,
)

logger = logging.getLogger(__name__)


# ============== 重试策略基类 ==============

class RetryStrategy:
    """重试策略基类"""
    
    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
    
    def should_retry(self, attempt: int, exception: Exception) -> bool:
        """判断是否应该重试"""
        raise NotImplementedError
    
    def get_delay(self, attempt: int, exception: Exception) -> float:
        """获取重试延迟时间（秒）"""
        raise NotImplementedError
    
    def execute(self, func: Callable, *args, **kwargs) -> Any:
        """执行带重试的函数"""
        last_exception = None
        
        for attempt in range(self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                
                if not self.should_retry(attempt, e):
                    logger.error(f"重试策略判断不应重试: {type(e).__name__}: {str(e)[:100]}")
                    raise
                
                if attempt < self.max_retries:
                    delay = self.get_delay(attempt, e)
                    logger.warning(
                        f"第 {attempt + 1} 次尝试失败，{delay:.1f}s 后重试: "
                        f"{type(e).__name__}: {str(e)[:80]}"
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"已达最大重试次数 {self.max_retries}")
        
        raise last_exception


class AsyncRetryStrategy:
    """异步重试策略基类"""
    
    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
    
    def should_retry(self, attempt: int, exception: Exception) -> bool:
        raise NotImplementedError
    
    def get_delay(self, attempt: int, exception: Exception) -> float:
        raise NotImplementedError
    
    async def execute(self, func: Callable, *args, **kwargs) -> Any:
        last_exception = None
        
        for attempt in range(self.max_retries + 1):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                
                if not self.should_retry(attempt, e):
                    logger.error(f"重试策略判断不应重试: {type(e).__name__}: {str(e)[:100]}")
                    raise
                
                if attempt < self.max_retries:
                    delay = self.get_delay(attempt, e)
                    logger.warning(
                        f"第 {attempt + 1} 次尝试失败，{delay:.1f}s 后重试: "
                        f"{type(e).__name__}: {str(e)[:80]}"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"已达最大重试次数 {self.max_retries}")
        
        raise last_exception


# ============== 指数退避重试 ==============

class ExponentialBackoffRetry(RetryStrategy):
    """
    指数退避重试策略
    
    延迟计算公式：delay = base_delay * (factor ** attempt)
    最大延迟不超过 max_delay
    """
    
    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        factor: float = 2.0,
        max_delay: float = 60.0,
        jitter: bool = True
    ):
        super().__init__(max_retries)
        self.base_delay = base_delay
        self.factor = factor
        self.max_delay = max_delay
        self.jitter = jitter
    
    def should_retry(self, attempt: int, exception: Exception) -> bool:
        """可重试的异常类型"""
        retryable = (
            SourceUnavailableError,
            SourceRateLimitedError,
            TimeoutError,
            ConnectionError,
        )
        # 数据质量问题不重试
        if isinstance(exception, (DataQualityError, DataValidationError)):
            return False
        return isinstance(exception, retryable) or attempt < self.max_retries
    
    def get_delay(self, attempt: int, exception: Exception) -> float:
        """计算延迟时间"""
        # 如果是限流，使用 retry_after
        if isinstance(exception, SourceRateLimitedError):
            retry_after = exception.details.get('retry_after_seconds')
            if retry_after:
                return min(float(retry_after), self.max_delay)
        
        delay = self.base_delay * (self.factor ** attempt)
        delay = min(delay, self.max_delay)
        
        # 添加随机抖动
        if self.jitter:
            import random
            delay = delay * (0.5 + random.random() * 0.5)
        
        return delay


class AsyncExponentialBackoffRetry(AsyncRetryStrategy):
    """异步指数退避重试"""
    
    def __init__(self, max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 60.0, **kwargs):
        super().__init__(max_retries=max_retries)
        self.strategy = ExponentialBackoffRetry(
            max_retries=max_retries,
            base_delay=base_delay,
            max_delay=max_delay
        )
    
    def should_retry(self, attempt: int, exception: Exception) -> bool:
        return self.strategy.should_retry(attempt, exception)
    
    def get_delay(self, attempt: int, exception: Exception) -> float:
        return self.strategy.get_delay(attempt, exception)


# ============== 固定间隔重试 ==============

class FixedIntervalRetry(RetryStrategy):
    """固定间隔重试策略

    支持 max_delay 参数（保持接口一致性），但实际延迟为固定值 interval。
    若 interval > max_delay，则自动限制为 max_delay。
    """

    def __init__(
        self,
        max_retries: int = 3,
        interval: float = 5.0,
        max_delay: float = 60.0,
    ):
        super().__init__(max_retries)
        self.interval = min(interval, max_delay)
        self.max_delay = max_delay

    def should_retry(self, attempt: int, exception: Exception) -> bool:
        retryable = (SourceUnavailableError, SourceRateLimitedError, TimeoutError)
        if isinstance(exception, (DataQualityError, DataValidationError)):
            return False
        return isinstance(exception, retryable) or attempt < self.max_retries

    def get_delay(self, attempt: int, exception: Exception) -> float:
        return self.interval

    def get_max_delay(self) -> float:
        """返回最大延迟（保持与 ExponentialBackoffRetry 接口一致）"""
        return self.max_delay


# ============== 自定义条件重试 ==============

class ConditionalRetry(RetryStrategy):
    """
    自定义条件重试策略
    
    允许用户自定义重试条件和延迟计算
    """
    
    def __init__(
        self,
        max_retries: int = 3,
        should_retry_func: Optional[Callable[[int, Exception], bool]] = None,
        delay_func: Optional[Callable[[int, Exception], float]] = None
    ):
        super().__init__(max_retries)
        self.should_retry_func = should_retry_func or (lambda a, e: a < self.max_retries)
        self.delay_func = delay_func or (lambda a, e: 1.0)
    
    def should_retry(self, attempt: int, exception: Exception) -> bool:
        return self.should_retry_func(attempt, exception)
    
    def get_delay(self, attempt: int, exception: Exception) -> float:
        return self.delay_func(attempt, exception)


# ============== 装饰器实现 ==============

def retry_with_backoff(
    max_retries: int = 3,
    backoff_factors: Optional[List[float]] = None,
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,)
):
    """
    带指数退避的重试装饰器
    
    参数：
        max_retries: 最大重试次数
        backoff_factors: 退避因子列表 [1, 2, 5] 表示等待 1s, 2s, 5s
        retryable_exceptions: 需要重试的异常类型
    
    使用示例：
        @retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
        async def fetch_data():
            ...
        
        @retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
        def fetch_data_sync():
            ...
    """
    if backoff_factors is None:
        backoff_factors = [1, 2, 5]
    
    def decorator(func):
        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                last_exception = None
                
                for attempt in range(max_retries + 1):
                    try:
                        return await func(*args, **kwargs)
                    except retryable_exceptions as e:
                        last_exception = e
                        
                        if attempt < max_retries:
                            wait_time = backoff_factors[min(attempt, len(backoff_factors) - 1)]
                            logger.warning(
                                f"{func.__name__} 第 {attempt + 1} 次失败，"
                                f"等待 {wait_time}s 后重试：{str(e)[:100]}"
                            )
                            await asyncio.sleep(wait_time)
                        else:
                            logger.error(f"{func.__name__} 重试 {max_retries} 次后仍失败")
                
                raise last_exception
            
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                last_exception = None
                
                for attempt in range(max_retries + 1):
                    try:
                        return func(*args, **kwargs)
                    except retryable_exceptions as e:
                        last_exception = e
                        
                        if attempt < max_retries:
                            wait_time = backoff_factors[min(attempt, len(backoff_factors) - 1)]
                            logger.warning(
                                f"{func.__name__} 第 {attempt + 1} 次失败，"
                                f"等待 {wait_time}s 后重试：{str(e)[:100]}"
                            )
                            time.sleep(wait_time)
                        else:
                            logger.error(f"{func.__name__} 重试 {max_retries} 次后仍失败")
                
                raise last_exception
            
            return sync_wrapper
    
    return decorator


# ============== 便捷函数 ==============

def exponential_backoff_retry(func=None, **kwargs):
    """指数退避重试装饰器（别名）"""
    if func is None:
        return retry_with_backoff(**kwargs)
    return retry_with_backoff(**kwargs)(func)


def fixed_retry(func=None, **kwargs):
    """固定间隔重试装饰器"""
    strategy = FixedIntervalRetry(**kwargs)
    
    if func is None:
        def decorator(f):
            @wraps(f)
            def wrapper(*args, **kwargs):
                return strategy.execute(f, *args, **kwargs)
            return wrapper
        return decorator
    
    return strategy.execute(func)


# ============== 默认重试策略实例 ==============

DEFAULT_RETRY_STRATEGY = ExponentialBackoffRetry(
    max_retries=3,
    base_delay=1.0,
    factor=2.0,
    max_delay=30.0
)

ASYNC_DEFAULT_RETRY_STRATEGY = AsyncExponentialBackoffRetry(
    max_retries=3,
    base_delay=1.0,
    factor=2.0,
    max_delay=30.0
)


if __name__ == '__main__':
    # 测试
    logger.info("测试重试策略...")
    
    # 测试指数退避
    strategy = ExponentialBackoffRetry(max_retries=3, base_delay=1)
    
    call_count = 0

    def failing_func():
        global call_count
        call_count += 1
        if call_count < 3:
            raise SourceUnavailableError("test", "连接超时")
        return "success"
    
    result = strategy.execute(failing_func)
    logger.info(f"结果: {result}, 调用次数: {call_count}")
    
    # 测试装饰器
    @retry_with_backoff(max_retries=2, backoff_factors=[1, 2])
    def decorated_func():
        return "decorated_success"
    
    result = decorated_func()
    logger.info(f"装饰器结果: {result}")
