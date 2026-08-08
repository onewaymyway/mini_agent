# -*- coding: utf-8 -*-
"""
Finance Data Toolkit - 容错机制

提供熔断器、降级策略、重试机制等容错功能。

使用示例：
    from finance_toolkit.resilience import CircuitBreaker, FallbackManager, retry_with_backoff
    
    # 熔断器示例
    cb = CircuitBreaker("akshare", failure_threshold=5, reset_timeout=60)
    try:
        with cb.guard():
            data = fetch_data()
    except CircuitBreakerError:
        print("使用备用数据源")
    
    # 降级策略示例
    fallback = FallbackManager([
        ("akshare", fetch_from_akshare),
        ("eastmoney", fetch_from_eastmoney),
        ("sina", fetch_from_sina),
    ])
    result = fallback.fetch(symbols=['600000.SH'])
"""

import asyncio
import time
import logging
from typing import List, Dict, Callable, Any, Optional, Tuple
from functools import wraps

from .exceptions import (
    CircuitBreakerError,
    SourceUnavailableError,
    FallbackError,
    SourceRateLimitedError,
)

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """
    熔断器实现
    
    状态机：
    - CLOSED（正常）：请求正常通过，失败计数累加
    - OPEN（熔断）：直接拒绝请求，等待重置时间后进入 HALF_OPEN
    - HALF_OPEN（半开）：允许少量请求试探，成功则恢复 CLOSED，失败则继续 OPEN
    
    参数：
        source: 数据源名称
        failure_threshold: 失败阈值（达到此次数后触发熔断）
        reset_timeout: 重置超时时间（秒），OPEN 状态持续时间
        half_open_max_calls: 半开状态下的最大试探调用次数
    """
    
    def __init__(
        self,
        source: str,
        failure_threshold: int = 5,
        reset_timeout: int = 60,
        half_open_max_calls: int = 3
    ):
        self.source = source
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.half_open_max_calls = half_open_max_calls
        
        self._state = "CLOSED"
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._half_open_calls = 0
        self._lock = asyncio.Lock() if asyncio.get_event_loop().is_running() else None
    
    @property
    def state(self) -> str:
        """获取当前状态"""
        if self._state == "OPEN" and self._last_failure_time:
            if time.time() - self._last_failure_time >= self.reset_timeout:
                return "HALF_OPEN"
        return self._state
    
    @property
    def failure_count(self) -> int:
        return self._failure_count
    
    async def _acquire_lock(self):
        if self._lock:
            await self._lock.acquire()
    
    def _release_lock(self):
        if self._lock and self._lock.locked():
            self._lock.release()
    
    async def guard(self):
        """
        上下文管理器，保护被熔断器监控的代码块
        
        使用示例：
            async with cb.guard():
                result = await fetch_data()
        """
        await self._acquire_lock()
        try:
            current_state = self.state
            
            if current_state == "OPEN":
                raise CircuitBreakerError(
                    self.source,
                    self._failure_count,
                    int(self.reset_timeout - (time.time() - self._last_failure_time))
                )
            
            if current_state == "HALF_OPEN":
                if self._half_open_calls >= self.half_open_max_calls:
                    raise CircuitBreakerError(self.source, self._failure_count, self.reset_timeout)
                self._half_open_calls += 1
            
            return _CircuitBreakerGuard(self)
        finally:
            self._release_lock()
    
    async def record_success(self):
        """记录成功调用"""
        async with self._lock if self._lock else _nullcontext():
            if self._state == "HALF_OPEN":
                self._half_open_calls = 0
                self._state = "CLOSED"
                self._failure_count = 0
                logger.info(f"熔断器 [{self.source}] 恢复 CLOSED 状态")
            elif self._state == "CLOSED":
                self._failure_count = max(0, self._failure_count - 1)
    
    async def record_failure(self, error: Exception = None):
        """记录失败调用"""
        async with self._lock if self._lock else _nullcontext():
            self._failure_count += 1
            self._last_failure_time = time.time()
            
            if self._state == "HALF_OPEN":
                self._state = "OPEN"
                self._half_open_calls = 0
                logger.warning(f"熔断器 [{self.source}] HALF_OPEN -> OPEN (失败 {self._failure_count} 次)")
            elif self._state == "CLOSED" and self._failure_count >= self.failure_threshold:
                self._state = "OPEN"
                logger.warning(f"熔断器 [{self.source}] CLOSED -> OPEN (失败 {self._failure_count} 次，{self.reset_timeout}秒后恢复)")
    
    def reset(self):
        """手动重置熔断器"""
        self._state = "CLOSED"
        self._failure_count = 0
        self._last_failure_time = None
        self._half_open_calls = 0
        logger.info(f"熔断器 [{self.source}] 已手动重置")


class _CircuitBreakerGuard:
    """熔断器上下文管理器内部类"""
    
    def __init__(self, breaker: CircuitBreaker):
        self.breaker = breaker
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            await self.breaker.record_success()
        else:
            await self.breaker.record_failure(exc_val)
        return False


class _nullcontext:
    """空上下文管理器（用于同步场景）"""
    async def __aenter__(self):
        pass
    async def __aexit__(self, *args):
        pass


class FallbackManager:
    """
    降级策略管理器
    
    按优先级尝试多个数据源，当前源失败时自动切换到下一个。
    
    参数：
        sources: 数据源列表 [(source_name, fetch_func), ...]
        circuit_breakers: 可选的熔断器字典 {source_name: CircuitBreaker}
    """
    
    def __init__(
        self,
        sources: List[Tuple[str, Callable]],
        circuit_breakers: Optional[Dict[str, CircuitBreaker]] = None
    ):
        self.sources = sources
        self.circuit_breakers = circuit_breakers or {}
        self._fallback_history: Dict[str, int] = {s[0]: 0 for s in sources}
    
    async def fetch(
        self,
        *args,
        skip_sources: Optional[List[str]] = None,
        **kwargs
    ) -> Any:
        """
        尝试从多个数据源获取数据
        
        参数：
            skip_sources: 跳过的数据源列表
            *args, **kwargs: 传递给各 fetch 函数的参数
        
        返回：
            第一个成功的数据源返回结果
        
        异常：
            FallbackError: 所有数据源都失败时抛出
        """
        skip_sources = skip_sources or []
        errors: Dict[str, str] = {}
        
        for source_name, fetch_func in self.sources:
            # 跳过指定的源
            if source_name in skip_sources:
                continue
            
            # 检查熔断器
            if source_name in self.circuit_breakers:
                cb = self.circuit_breakers[source_name]
                if cb.state == "OPEN":
                    logger.debug(f"跳过熔断的数据源：{source_name}")
                    continue
            
            try:
                logger.debug(f"尝试数据源：{source_name}")
                
                # 检查是否是异步函数
                if asyncio.iscoroutinefunction(fetch_func):
                    result = await fetch_func(*args, **kwargs)
                else:
                    result = fetch_func(*args, **kwargs)
                
                # 记录成功
                if source_name in self.circuit_breakers:
                    await self.circuit_breakers[source_name].record_success()
                
                self._fallback_history[source_name] = 0
                logger.info(f"数据源 {source_name} 获取成功")
                return result
                
            except CircuitBreakerError:
                # 熔断器触发，跳过
                errors[source_name] = "Circuit breaker open"
                continue
                
            except SourceRateLimitedError as e:
                # 限流，记录并尝试下一个
                errors[source_name] = f"Rate limited (retry after: {e.details.get('retry_after_seconds', 'unknown')}s)"
                continue
                
            except SourceUnavailableError as e:
                # 源不可用，记录错误
                errors[source_name] = str(e)
                if source_name in self.circuit_breakers:
                    await self.circuit_breakers[source_name].record_failure(e)
                continue
                
            except Exception as e:
                # 其他异常
                errors[source_name] = f"{type(e).__name__}: {str(e)[:100]}"
                if source_name in self.circuit_breakers:
                    await self.circuit_breakers[source_name].record_failure(e)
                continue
        
        # 所有源都失败
        primary = self.sources[0][0] if self.sources else "unknown"
        fallbacks = [s[0] for s in self.sources[1:]]
        
        logger.error(f"所有数据源均失败：primary={primary}, fallbacks={fallbacks}")
        raise FallbackError(primary, fallbacks, errors)
    
    def get_fallback_order(self) -> List[str]:
        """获取数据源优先级顺序"""
        return [s[0] for s in self.sources]


def retry_with_backoff(
    max_retries: int = 3,
    backoff_factors: List[float] = None,
    retryable_exceptions: tuple = (Exception,)
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


# 默认熔断器配置
DEFAULT_CIRCUIT_BREAKERS = {
    "akshare": CircuitBreaker("akshare", failure_threshold=5, reset_timeout=60),
    "eastmoney": CircuitBreaker("eastmoney", failure_threshold=5, reset_timeout=60),
    "sina": CircuitBreaker("sina", failure_threshold=5, reset_timeout=60),
    "tushare": CircuitBreaker("tushare", failure_threshold=3, reset_timeout=120),
}

# 默认数据源优先级
DEFAULT_SOURCE_PRIORITY = [
    ("akshare", None),  # 第一个为 None 表示需要动态导入
    ("eastmoney", None),
    ("sina", None),
]
