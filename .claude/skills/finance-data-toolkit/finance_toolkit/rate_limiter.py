# -*- coding: utf-8 -*-
"""
Finance Data Toolkit - 限流器模块

提供多种限流算法，控制 API 调用频率，防止触发限流。

使用示例：
    from finance_toolkit.rate_limiter import RateLimiter, TokenBucket, LeakyBucket
    
    # 令牌桶限流器
    limiter = TokenBucket(max_tokens=10, refill_rate=1.0)
    with limiter.acquire():
        data = fetch_data()
    
    # 漏桶限流器
    leaky = LeakyBucket(capacity=10, rate=1.0)
    leaky.add(1)  # 添加一个请求
"""

import asyncio
import logging
import time
from typing import Dict, Optional, List, Any
from dataclasses import dataclass
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    基础限流器接口
    """
    
    def __init__(self, max_calls: int = 10, period: int = 60):
        self.max_calls = max_calls
        self.period = period
    
    async def acquire(self, tokens: int = 1) -> None:
        """获取令牌（异步）"""
        raise NotImplementedError
    
    def acquire_sync(self, tokens: int = 1) -> None:
        """获取令牌（同步）"""
        raise NotImplementedError
    
    @property
    def available_tokens(self) -> float:
        """获取可用令牌数"""
        raise NotImplementedError


class TokenBucket(RateLimiter):
    """
    令牌桶限流器
    
    令牌以固定速率生成，请求需要消耗令牌。令牌不足时等待。
    支持突发流量（burst）。
    
    参数：
        max_tokens: 桶容量（最大令牌数）
        refill_rate: 补充速率（令牌/秒）
    """
    
    def __init__(self, max_tokens: int = 10, refill_rate: float = 1.0):
        super().__init__(max_calls=max_tokens, period=int(max_tokens / refill_rate))
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self._tokens = float(max_tokens)
        self._last_refill = time.time()
        try:
            self._lock = asyncio.Lock() if asyncio.get_event_loop().is_running() else None
        except RuntimeError:
            self._lock = None
    
    def _refill(self):
        """补充令牌"""
        now = time.time()
        elapsed = now - self._last_refill
        self._tokens = min(self.max_tokens, self._tokens + elapsed * self.refill_rate)
        self._last_refill = now
    
    async def acquire(self, tokens: int = 1):
        """获取令牌（异步）"""
        while True:
            if self._lock:
                async with self._lock:
                    self._refill()
                    if self._tokens >= tokens:
                        self._tokens -= tokens
                        return
            else:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
            # 计算等待时间
            wait_time = (tokens - self._tokens) / self.refill_rate
            logger.debug(f"令牌桶等待 {wait_time:.2f} 秒")
            await asyncio.sleep(wait_time)
    
    def acquire_sync(self, tokens: int = 1):
        """获取令牌（同步）"""
        while True:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return
            wait_time = (tokens - self._tokens) / self.refill_rate
            logger.debug(f"令牌桶等待 {wait_time:.2f} 秒")
            time.sleep(wait_time)
    
    @property
    def available_tokens(self) -> float:
        """获取可用令牌数"""
        self._refill()
        return self._tokens
    
    def try_acquire(self, tokens: int = 1) -> bool:
        """尝试获取令牌（不等待）"""
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False


class LeakyBucket(RateLimiter):
    """
    漏桶限流器
    
    请求以固定速率流出，超出容量的请求被拒绝或排队。
    
    参数：
        capacity: 桶容量
        rate: 流出速率（请求/秒）
    """
    
    def __init__(self, capacity: int = 10, rate: float = 1.0):
        super().__init__(max_calls=capacity, period=int(capacity / rate))
        self.capacity = capacity
        self.rate = rate
        self._water = 0.0
        self._last_add = time.time()
        try:
            self._lock = asyncio.Lock() if asyncio.get_event_loop().is_running() else None
        except RuntimeError:
            self._lock = None
    
    def _drain(self):
        """排水"""
        now = time.time()
        elapsed = now - self._last_add
        self._water = max(0, self._water - elapsed * self.rate)
        self._last_add = now
    
    async def acquire(self, tokens: int = 1):
        """获取令牌（异步）"""
        while True:
            if self._lock:
                async with self._lock:
                    self._drain()
                    if self._water + tokens <= self.capacity:
                        self._water += tokens
                        return
            else:
                self._drain()
                if self._water + tokens <= self.capacity:
                    self._water += tokens
                    return
            # 计算等待时间
            wait_time = (self._water + tokens - self.capacity) / self.rate
            logger.debug(f"漏桶等待 {wait_time:.2f} 秒")
            await asyncio.sleep(wait_time)
    
    def acquire_sync(self, tokens: int = 1):
        """获取令牌（同步）"""
        while True:
            self._drain()
            if self._water + tokens <= self.capacity:
                self._water += tokens
                return
            wait_time = (self._water + tokens - self.capacity) / self.rate
            logger.debug(f"漏桶等待 {wait_time:.2f} 秒")
            time.sleep(wait_time)
    
    @property
    def available_tokens(self) -> float:
        """获取可用令牌数"""
        self._drain()
        return max(0, self.capacity - self._water)
    
    def try_acquire(self, tokens: int = 1) -> bool:
        """尝试获取令牌（不等待）"""
        self._drain()
        if self._water + tokens <= self.capacity:
            self._water += tokens
            return True
        return False


class SlidingWindowCounter(RateLimiter):
    """
    滑动窗口计数器限流器
    
    将时间窗口划分为多个小格子，统计每个格子的请求数。
    
    参数：
        max_calls: 窗口内最大调用次数
        window_size: 窗口大小（秒）
        buckets: 格子数量
    """
    
    def __init__(self, max_calls: int = 10, window_size: int = 60, buckets: int = 10):
        super().__init__(max_calls=max_calls, period=window_size)
        self.window_size = window_size
        self.buckets = buckets
        self.bucket_size = window_size / buckets
        self._buckets = [0] * buckets
        self._last_bucket = 0
        try:
            self._lock = asyncio.Lock() if asyncio.get_event_loop().is_running() else None
        except RuntimeError:
            self._lock = None
    
    def _get_bucket_index(self) -> int:
        """获取当前格子索引"""
        return int(time.time() / self.bucket_size) % self.buckets
    
    def _drain_old_buckets(self):
        """清除过期格子"""
        current_bucket = self._get_bucket_index()
        if current_bucket != self._last_bucket:
            # 清除旧格子
            for i in range(self.buckets):
                if i != current_bucket:
                    self._buckets[i] = 0
            self._last_bucket = current_bucket
    
    async def acquire(self, tokens: int = 1):
        """获取令牌（异步）"""
        while True:
            if self._lock:
                async with self._lock:
                    self._drain_old_buckets()
                    current_count = sum(self._buckets)
                    if current_count + tokens <= self.max_calls:
                        idx = self._get_bucket_index()
                        self._buckets[idx] += tokens
                        return
            else:
                self._drain_old_buckets()
                current_count = sum(self._buckets)
                if current_count + tokens <= self.max_calls:
                    idx = self._get_bucket_index()
                    self._buckets[idx] += tokens
                    return
            # 等待下一个窗口
            wait_time = self.bucket_size - (time.time() % self.bucket_size)
            logger.debug(f"滑动窗口等待 {wait_time:.2f} 秒")
            await asyncio.sleep(wait_time)
    
    def acquire_sync(self, tokens: int = 1):
        """获取令牌（同步）"""
        while True:
            self._drain_old_buckets()
            current_count = sum(self._buckets)
            if current_count + tokens <= self.max_calls:
                idx = self._get_bucket_index()
                self._buckets[idx] += tokens
                return
            wait_time = self.bucket_size - (time.time() % self.bucket_size)
            logger.debug(f"滑动窗口等待 {wait_time:.2f} 秒")
            time.sleep(wait_time)
    
    @property
    def available_tokens(self) -> float:
        """获取可用令牌数"""
        self._drain_old_buckets()
        return max(0, self.max_calls - sum(self._buckets))
    
    def try_acquire(self, tokens: int = 1) -> bool:
        """尝试获取令牌（不等待）"""
        self._drain_old_buckets()
        current_count = sum(self._buckets)
        if current_count + tokens <= self.max_calls:
            idx = self._get_bucket_index()
            self._buckets[idx] += tokens
            return True
        return False


class SourceRateLimiter:
    """
    数据源专用限流器
    
    为每个数据源配置独立的限流策略。
    """
    
    # 默认限流配置
    DEFAULT_CONFIGS = {
        "akshare": {"max_calls": 10, "period": 60, "algorithm": "token_bucket"},
        "eastmoney": {"max_calls": 5, "period": 60, "algorithm": "token_bucket"},
        "sina": {"max_calls": 20, "period": 60, "algorithm": "sliding_window"},
        "tushare": {"max_calls": 2, "period": 60, "algorithm": "token_bucket"},
    }
    
    def __init__(self, configs: Optional[Dict[str, Dict]] = None):
        self.configs = configs or self.DEFAULT_CONFIGS
        self._limiters: Dict[str, RateLimiter] = {}
        self._init_limiters()
    
    def _init_limiters(self):
        """初始化限流器"""
        for source, config in self.configs.items():
            algorithm = config.get("algorithm", "token_bucket")
            max_calls = config.get("max_calls", 10)
            period = config.get("period", 60)
            
            if algorithm == "token_bucket":
                self._limiters[source] = TokenBucket(
                    max_tokens=max_calls,
                    refill_rate=max_calls / period
                )
            elif algorithm == "sliding_window":
                self._limiters[source] = SlidingWindowCounter(
                    max_calls=max_calls,
                    window_size=period
                )
            else:
                self._limiters[source] = TokenBucket(
                    max_tokens=max_calls,
                    refill_rate=max_calls / period
                )
            
            logger.info(f"初始化数据源限流器：{source} ({algorithm})")
    
    async def acquire(self, source: str, tokens: int = 1):
        """获取指定数据源的令牌"""
        if source not in self._limiters:
            logger.warning(f"未配置数据源限流器：{source}，使用默认配置")
            self._limiters[source] = TokenBucket(max_tokens=10, refill_rate=1.0)
        
        await self._limiters[source].acquire(tokens)
    
    def acquire_sync(self, source: str, tokens: int = 1):
        """同步获取指定数据源的令牌"""
        if source not in self._limiters:
            self._limiters[source] = TokenBucket(max_tokens=10, refill_rate=1.0)
        
        self._limiters[source].acquire_sync(tokens)
    
    def get_status(self, source: str) -> Dict[str, Any]:
        """获取数据源限流状态"""
        limiter = self._limiters.get(source)
        if not limiter:
            return {"source": source, "configured": False}
        
        return {
            "source": source,
            "configured": True,
            "available_tokens": limiter.available_tokens,
            "max_calls": limiter.max_calls,
            "period": limiter.period
        }
    
    def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有数据源限流状态"""
        return {source: self.get_status(source) for source in self._limiters}


# 全局限流器实例
_global_limiter: Optional[SourceRateLimiter] = None


def get_global_limiter() -> SourceRateLimiter:
    """获取全局限流器实例"""
    global _global_limiter
    if _global_limiter is None:
        _global_limiter = SourceRateLimiter()
    return _global_limiter


async def acquire_rate_limit(source: str, tokens: int = 1):
    """便捷函数：获取限流令牌"""
    limiter = get_global_limiter()
    await limiter.acquire(source, tokens)


def acquire_rate_limit_sync(source: str, tokens: int = 1):
    """便捷函数：同步获取限流令牌"""
    limiter = get_global_limiter()
    limiter.acquire_sync(source, tokens)


@contextmanager
def rate_limit(source: str):
    """上下文管理器：自动获取和释放限流令牌"""
    acquire_rate_limit_sync(source)
    try:
        yield
    finally:
        pass


# 预定义的限流器实例
AKSHARE_LIMITER = TokenBucket(max_tokens=10, refill_rate=1.0)
EASTMONEY_LIMITER = TokenBucket(max_tokens=5, refill_rate=5/60)
SINA_LIMITER = SlidingWindowCounter(max_calls=20, window_size=60)
TUSHARE_LIMITER = TokenBucket(max_tokens=2, refill_rate=2/60)