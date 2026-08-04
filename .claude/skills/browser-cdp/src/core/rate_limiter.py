"""
rate_limiter.py - 请求速率控制模块

支持令牌桶/漏桶算法、指数退避重试、熔断器模式。
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Optional, Callable, Any
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class RateLimitAlgorithm(Enum):
    """速率限制算法"""
    TOKEN_BUCKET = "token_bucket"
    LEAKY_BUCKET = "leaky_bucket"
    FIXED_WINDOW = "fixed_window"


@dataclass
class RateLimitConfig:
    """速率限制配置"""
    # 算法选择
    algorithm: RateLimitAlgorithm = RateLimitAlgorithm.TOKEN_BUCKET
    
    # 令牌桶参数
    token_rate: float = 1.0  # 每秒生成的令牌数
    max_tokens: float = 10.0  # 最大令牌数（突发容量）
    
    # 漏桶参数
    bucket_capacity: float = 10.0  # 桶容量
    leak_rate: float = 1.0  # 每秒泄漏速率
    
    # 固定窗口参数
    window_size: float = 1.0  # 窗口大小（秒）
    max_requests: int = 10  # 窗口内最大请求数
    
    # 指数退避参数
    base_delay: float = 1.0  # 基础延迟
    max_delay: float = 60.0  # 最大延迟
    max_retries: int = 3  # 最大重试次数
    backoff_factor: float = 2.0  # 退避因子
    
    # 熔断器参数
    failure_threshold: int = 5  # 触发熔断的失败次数
    recovery_timeout: float = 30.0  # 熔断恢复超时（秒）


class TokenBucket:
    """令牌桶算法实现"""
    
    def __init__(self, rate: float, capacity: float):
        self.rate = rate  # 令牌生成速率
        self.capacity = capacity  # 桶容量
        self.tokens = capacity  # 当前令牌数
        self.last_time = time.monotonic()
    
    def _refill(self):
        """补充令牌"""
        now = time.monotonic()
        elapsed = now - self.last_time
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_time = now
    
    async def acquire(self, tokens: float = 1.0) -> float:
        """
        获取令牌
        
        Args:
            tokens: 需要获取的令牌数
            
        Returns:
            float: 等待时间（秒）
        """
        self._refill()
        
        if self.tokens >= tokens:
            self.tokens -= tokens
            return 0.0
        
        # 计算等待时间
        wait_time = (tokens - self.tokens) / self.rate
        await asyncio.sleep(wait_time)
        self.tokens = 0.0
        return wait_time


class LeakyBucket:
    """漏桶算法实现"""
    
    def __init__(self, capacity: float, leak_rate: float):
        self.capacity = capacity  # 桶容量
        self.leak_rate = leak_rate  # 泄漏速率
        self.water = 0.0  # 当前水量
        self.last_time = time.monotonic()
    
    def _drain(self):
        """排水"""
        now = time.monotonic()
        elapsed = now - self.last_time
        self.water = max(0, self.water - elapsed * self.leak_rate)
        self.last_time = now
    
    async def acquire(self) -> float:
        """
        获取请求许可
        
        Returns:
            float: 等待时间（秒）
        """
        self._drain()
        
        if self.water + 1 <= self.capacity:
            self.water += 1
            return 0.0
        
        # 计算等待时间
        wait_time = (self.water + 1 - self.capacity) / self.leak_rate
        await asyncio.sleep(wait_time)
        self.water = self.capacity - 1
        return wait_time


class FixedWindow:
    """固定窗口算法实现"""
    
    def __init__(self, window_size: float, max_requests: int):
        self.window_size = window_size
        self.max_requests = max_requests
        self.request_count = 0
        self.window_start = time.monotonic()
    
    def _check_window(self):
        """检查窗口是否过期"""
        now = time.monotonic()
        if now - self.window_start >= self.window_size:
            self.request_count = 0
            self.window_start = now
    
    async def acquire(self) -> float:
        """
        获取请求许可
        
        Returns:
            float: 等待时间（秒）
        """
        self._check_window()
        
        if self.request_count < self.max_requests:
            self.request_count += 1
            return 0.0
        
        # 等待窗口重置
        wait_time = self.window_size - (time.monotonic() - self.window_start)
        await asyncio.sleep(wait_time)
        self.request_count = 1
        self.window_start = time.monotonic()
        return wait_time


class CircuitBreaker:
    """熔断器模式实现"""
    
    class State(Enum):
        CLOSED = "closed"  # 正常
        OPEN = "open"  # 熔断
        HALF_OPEN = "half_open"  # 半开
    
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        
        self.state = self.State.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0
        self._lock = asyncio.Lock()
    
    async def check(self) -> bool:
        """
        检查是否允许请求
        
        Returns:
            bool: 是否允许
        """
        async with self._lock:
            if self.state == self.State.CLOSED:
                return True
            
            if self.state == self.State.OPEN:
                # 检查是否可以恢复
                if time.monotonic() - self.last_failure_time >= self.recovery_timeout:
                    self.state = self.State.HALF_OPEN
                    logger.info("熔断器进入半开状态")
                    return True
                return False
            
            # HALF_OPEN 状态允许一个请求通过测试
            return True
    
    async def record_success(self):
        """记录成功"""
        async with self._lock:
            if self.state == self.State.HALF_OPEN:
                self.state = self.State.CLOSED
                self.failure_count = 0
                logger.info("熔断器恢复正常")
    
    async def record_failure(self):
        """记录失败"""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.monotonic()
            
            if self.failure_count >= self.failure_threshold:
                self.state = self.State.OPEN
                logger.warning(f"熔断器触发（失败次数：{self.failure_count}）")
    
    @property
    def is_open(self) -> bool:
        return self.state == self.State.OPEN


class RateLimiter:
    """
    速率限制器
    
    支持令牌桶、漏桶、固定窗口三种算法
    """
    
    def __init__(self, config: RateLimitConfig = None):
        self.config = config or RateLimitConfig()
        self._limiter: Optional[Any] = None
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=self.config.failure_threshold,
            recovery_timeout=self.config.recovery_timeout
        )
        self._setup_limiter()
    
    def _setup_limiter(self):
        """根据配置创建对应的限制器"""
        if self.config.algorithm == RateLimitAlgorithm.TOKEN_BUCKET:
            self._limiter = TokenBucket(
                rate=self.config.token_rate,
                capacity=self.config.max_tokens
            )
        elif self.config.algorithm == RateLimitAlgorithm.LEAKY_BUCKET:
            self._limiter = LeakyBucket(
                capacity=self.config.bucket_capacity,
                leak_rate=self.config.leak_rate
            )
        elif self.config.algorithm == RateLimitAlgorithm.FIXED_WINDOW:
            self._limiter = FixedWindow(
                window_size=self.config.window_size,
                max_requests=self.config.max_requests
            )
    
    async def acquire(self) -> float:
        """
        获取请求许可
        
        Returns:
            float: 等待时间（秒）
        """
        if not await self._circuit_breaker.check():
            raise Exception("熔断器已触发，请稍后重试")
        
        wait_time = await self._limiter.acquire()
        return wait_time
    
    async def execute(self, func: Callable, *args, **kwargs) -> Any:
        """
        执行带速率限制的函数
        
        Args:
            func: 要执行的异步函数
            *args: 位置参数
            **kwargs: 关键字参数
            
        Returns:
            Any: 函数返回值
        """
        # 获取许可
        wait_time = await self.acquire()
        if wait_time > 0:
            logger.debug(f"等待 {wait_time:.2f}s 以获取请求许可")
        
        # 执行函数（带重试）
        for attempt in range(self.config.max_retries + 1):
            try:
                result = await func(*args, **kwargs)
                await self._circuit_breaker.record_success()
                return result
            except Exception as e:
                await self._circuit_breaker.record_failure()
                
                if attempt == self.config.max_retries:
                    logger.error(f"请求失败，已重试 {self.config.max_retries} 次: {e}")
                    raise
                
                # 指数退避
                delay = self.config.base_delay * (self.config.backoff_factor ** attempt)
                delay = min(delay, self.config.max_delay)
                delay += random.random() * 0.5  # jitter
                logger.warning(f"请求失败，{delay:.2f}s 后重试 ({attempt + 1}/{self.config.max_retries})")
                await asyncio.sleep(delay)
    
    @property
    def is_circuit_open(self) -> bool:
        return self._circuit_breaker.is_open
    
    async def reset_circuit(self):
        """手动重置熔断器"""
        self._circuit_breaker.state = self._circuit_breaker.State.CLOSED
        self._circuit_breaker.failure_count = 0
        logger.info("熔断器已手动重置")


# 全局单例
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """获取全局速率限制器单例"""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def set_rate_limiter(limiter: RateLimiter):
    """设置全局速率限制器"""
    global _rate_limiter
    _rate_limiter = limiter
    logger.debug("设置全局速率限制器")


def reset_rate_limiter():
    """重置全局速率限制器"""
    global _rate_limiter
    _rate_limiter = None
    logger.debug("重置全局速率限制器")
