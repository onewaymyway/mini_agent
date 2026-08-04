"""
proxy_pool.py - 代理池管理模块

支持 HTTP/SOCKS5 代理轮换、健康检查、自动故障转移。
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import aiohttp

logger = logging.getLogger(__name__)


class ProxyType(Enum):
    """代理类型"""
    HTTP = "http"
    HTTPS = "https"
    SOCKS4 = "socks4"
    SOCKS5 = "socks5"


@dataclass
class ProxyInfo:
    """代理信息"""
    host: str
    port: int
    proxy_type: ProxyType = ProxyType.HTTP
    username: Optional[str] = None
    password: Optional[str] = None
    country: Optional[str] = None
    latency: float = 0.0
    success_count: int = 0
    failure_count: int = 0
    is_active: bool = True
    last_used: float = 0.0
    
    @property
    def url(self) -> str:
        """生成代理 URL"""
        scheme = self.proxy_type.value
        if self.username and self.password:
            return f"{scheme}://{self.username}:{self.password}@{self.host}:{self.port}"
        return f"{scheme}://{self.host}:{self.port}"
    
    @property
    def health_score(self) -> float:
        """健康度评分（0-1）"""
        if self.failure_count > 0:
            total = self.success_count + self.failure_count
            return self.success_count / total
        return 1.0
    
    def mark_success(self):
        """标记成功"""
        self.success_count += 1
        self.is_active = True
        self.last_used = time.monotonic()
    
    def mark_failure(self):
        """标记失败"""
        self.failure_count += 1
        if self.failure_count >= 3:
            self.is_active = False
            logger.warning(f"代理 {self.url} 连续失败 3 次，标记为不可用")


@dataclass
class ProxyPoolConfig:
    """代理池配置"""
    # 健康检查
    health_check_enabled: bool = True
    health_check_interval: float = 60.0  # 检查间隔（秒）
    health_check_timeout: float = 10.0  # 检查超时（秒）
    health_check_url: str = "https://httpbin.org/ip"  # 检查 URL
    
    # 轮换策略
    rotation_strategy: str = "health_score"  # health_score / random / round_robin
    min_health_score: float = 0.3  # 最低健康度阈值
    
    # 故障处理
    max_consecutive_failures: int = 3  # 连续失败次数阈值
    recovery_timeout: float = 300.0  # 恢复超时（秒）
    
    # 并发控制
    max_concurrent: int = 5  # 最大并发连接数


class ProxyPool:
    """
    代理池管理器
    
    支持代理轮换、健康检查、自动故障转移
    """
    
    def __init__(self, config: ProxyPoolConfig = None):
        self.config = config or ProxyPoolConfig()
        self._proxies: List[ProxyInfo] = []
        self._current_index: int = 0
        self._lock = asyncio.Lock()
        self._health_check_task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
    
    def add_proxy(self, proxy: ProxyInfo):
        """添加代理"""
        self._proxies.append(proxy)
        logger.info(f"添加代理: {proxy.url}")
    
    def add_proxies(self, proxies: List[Dict[str, Any]]):
        """批量添加代理"""
        for p in proxies:
            proxy = ProxyInfo(
                host=p["host"],
                port=p["port"],
                proxy_type=ProxyType(p.get("type", "http")),
                username=p.get("username"),
                password=p.get("password"),
                country=p.get("country")
            )
            self.add_proxy(proxy)
        logger.info(f"添加 {len(proxies)} 个代理")
    
    def remove_proxy(self, proxy_url: str):
        """移除代理"""
        self._proxies = [p for p in self._proxies if p.url != proxy_url]
        logger.info(f"移除代理: {proxy_url}")
    
    def get_active_proxies(self) -> List[ProxyInfo]:
        """获取所有活跃代理"""
        return [p for p in self._proxies if p.is_active]
    
    def get_proxy_by_health_score(self) -> Optional[ProxyInfo]:
        """按健康度评分选择代理"""
        active = self.get_active_proxies()
        if not active:
            return None
        
        # 过滤最低健康度阈值
        qualified = [p for p in active if p.health_score >= self.config.min_health_score]
        if not qualified:
            qualified = active
        
        # 按健康度排序，选择最高的
        return max(qualified, key=lambda p: p.health_score)
    
    def get_proxy_by_round_robin(self) -> Optional[ProxyInfo]:
        """轮询选择代理"""
        active = self.get_active_proxies()
        if not active:
            return None
        
        proxy = active[self._current_index % len(active)]
        self._current_index += 1
        return proxy
    
    def get_proxy_by_random(self) -> Optional[ProxyInfo]:
        """随机选择代理"""
        active = self.get_active_proxies()
        if not active:
            return None
        return random.choice(active)
    
    async def get_next_proxy(self) -> Optional[ProxyInfo]:
        """
        获取下一个代理
        
        Returns:
            Optional[ProxyInfo]: 代理信息，无可用代理时返回 None
        """
        async with self._lock:
            if self.config.rotation_strategy == "health_score":
                proxy = self.get_proxy_by_health_score()
            elif self.config.rotation_strategy == "round_robin":
                proxy = self.get_proxy_by_round_robin()
            else:  # random
                proxy = self.get_proxy_by_random()
            
            if proxy:
                proxy.last_used = asyncio.get_event_loop().time()
            return proxy
    
    async def test_proxy(self, proxy: ProxyInfo) -> bool:
        """
        测试代理可用性
        
        Args:
            proxy: 要测试的代理
            
        Returns:
            bool: 是否可用
        """
        try:
            if self._session is None:
                self._session = aiohttp.ClientSession()
            
            start_time = asyncio.get_event_loop().time()
            
            async with self._session.get(
                self.config.health_check_url,
                proxy=proxy.url,
                timeout=aiohttp.ClientTimeout(total=self.config.health_check_timeout)
            ) as response:
                elapsed = asyncio.get_event_loop().time() - start_time
                proxy.latency = elapsed
                
                if response.status == 200:
                    proxy.mark_success()
                    logger.debug(f"代理 {proxy.url} 测试通过，延迟 {elapsed:.2f}s")
                    return True
                else:
                    proxy.mark_failure()
                    logger.warning(f"代理 {proxy.url} 测试失败，状态码: {response.status}")
                    return False
                    
        except Exception as e:
            proxy.mark_failure()
            logger.warning(f"代理 {proxy.url} 测试异常: {e}")
            return False
    
    async def health_check_all(self):
        """对所有代理进行健康检查"""
        logger.info("开始代理池健康检查...")
        
        tasks = [self.test_proxy(p) for p in self._proxies]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        active_count = sum(1 for r in results if r is True)
        logger.info(f"健康检查完成：{active_count}/{len(self._proxies)} 个代理可用")
    
    async def start_health_check(self):
        """启动定期健康检查"""
        if self._health_check_task and not self._health_check_task.done():
            logger.debug("健康检查任务已在运行")
            return
        
        async def _check_loop():
            while True:
                try:
                    await self.health_check_all()
                    await asyncio.sleep(self.config.health_check_interval)
                except Exception as e:
                    logger.error(f"健康检查循环异常: {e}")
                    await asyncio.sleep(self.config.health_check_interval)
        
        self._health_check_task = asyncio.create_task(_check_loop())
        logger.info(f"启动定期健康检查，间隔 {self.config.health_check_interval}s")
    
    async def stop_health_check(self):
        """停止定期健康检查"""
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
            self._health_check_task = None
            logger.info("停止定期健康检查")
    
    async def close(self):
        """关闭代理池"""
        await self.stop_health_check()
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("代理池已关闭")
    
    @property
    def proxy_count(self) -> int:
        return len(self._proxies)
    
    @property
    def active_count(self) -> int:
        return len(self.get_active_proxies())
    
    def get_stats(self) -> Dict[str, Any]:
        """获取代理池统计信息"""
        return {
            "total": self.proxy_count,
            "active": self.active_count,
            "inactive": self.proxy_count - self.active_count,
            "rotation_strategy": self.config.rotation_strategy,
            "proxies": [
                {
                    "url": p.url,
                    "latency": p.latency,
                    "health_score": p.health_score,
                    "is_active": p.is_active
                }
                for p in self._proxies
            ]
        }


# 全局单例
_proxy_pool: Optional[ProxyPool] = None


def get_proxy_pool() -> ProxyPool:
    """获取全局代理池单例"""
    global _proxy_pool
    if _proxy_pool is None:
        _proxy_pool = ProxyPool()
    return _proxy_pool


def set_proxy_pool(pool: ProxyPool):
    """设置全局代理池"""
    global _proxy_pool
    _proxy_pool = pool
    logger.debug("设置全局代理池")


def reset_proxy_pool():
    """重置全局代理池"""
    global _proxy_pool
    _proxy_pool = None
    logger.debug("重置全局代理池")
