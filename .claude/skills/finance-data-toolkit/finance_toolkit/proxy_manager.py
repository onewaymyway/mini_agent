# -*- coding: utf-8 -*-
"""
代理管理模块

提供代理池管理、健康检查、轮换策略等功能：
- 代理池管理（添加、删除、查询）
- 代理健康检查（HTTP 请求验证）
- 代理轮换策略（轮询、随机、加权）
- 代理状态追踪（成功/失败计数）

使用示例：
    from finance_toolkit.proxy_manager import ProxyManager
    
    # 创建代理管理器
    manager = ProxyManager()
    
    # 添加代理
    manager.add_proxy('http://127.0.0.1:7890')
    manager.add_proxy('http://127.0.0.1:7891')
    
    # 获取代理
    proxy = manager.get_proxy()
    
    # 标记代理状态
    manager.mark_success(proxy)
    manager.mark_failure(proxy)
"""

import asyncio
import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ProxyStatus(Enum):
    """代理状态枚举"""
    HEALTHY = "healthy"           # 健康
    UNHEALTHY = "unhealthy"       # 不健康
    UNKNOWN = "unknown"           # 未知
    CHECKING = "checking"         # 检测中


class RotationStrategy(Enum):
    """轮换策略枚举"""
    ROUND_ROBIN = "round_robin"   # 轮询
    RANDOM = "random"             # 随机
    WEIGHTED = "weighted"         # 加权


@dataclass
class ProxyInfo:
    """代理信息"""
    url: str
    status: ProxyStatus = ProxyStatus.UNKNOWN
    success_count: int = 0
    failure_count: int = 0
    last_check_time: float = 0.0
    last_success_time: float = 0.0
    last_failure_time: float = 0.0
    response_time: float = 0.0
    weight: int = 1               # 加权策略权重
    
    @property
    def health_score(self) -> float:
        """健康度评分（0-1）"""
        if self.failure_count == 0:
            return 1.0
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.5
    
    @property
    def is_healthy(self) -> bool:
        """是否健康"""
        return self.status == ProxyStatus.HEALTHY and self.health_score >= 0.5


class ProxyManager:
    """
    代理管理器
    
    管理代理池，提供健康检查、轮换、状态追踪等功能。
    """
    
    def __init__(
        self,
        check_interval: int = 300,
        check_timeout: float = 10.0,
        unhealthy_threshold: int = 3,
        rotation_strategy: RotationStrategy = RotationStrategy.ROUND_ROBIN,
    ):
        self.check_interval = check_interval
        self.check_timeout = check_timeout
        self.unhealthy_threshold = unhealthy_threshold
        self.rotation_strategy = rotation_strategy
        
        # 代理池
        self._proxies: Dict[str, ProxyInfo] = {}
        self._round_robin_index = 0
        
        # 后台任务
        self._check_task: Optional[asyncio.Task] = None
        self._running = False
    
    # ============== 代理池管理 ==============
    
    def add_proxy(self, url: str, weight: int = 1) -> bool:
        """
        添加代理到池中
        
        Args:
            url: 代理 URL
            weight: 加权权重（默认 1）
        
        Returns:
            是否添加成功
        """
        if url in self._proxies:
            logger.debug(f"代理已存在，更新权重: {url}")
            self._proxies[url].weight = weight
            return True
        
        self._proxies[url] = ProxyInfo(url=url, weight=weight)
        logger.info(f"代理已添加: {url} (weight={weight})")
        return True
    
    def remove_proxy(self, url: str) -> bool:
        """
        从池中移除代理
        
        Args:
            url: 代理 URL
        
        Returns:
            是否移除成功
        """
        if url in self._proxies:
            del self._proxies[url]
            logger.info(f"代理已移除: {url}")
            return True
        return False
    
    def get_proxy(self) -> Optional[str]:
        """
        根据轮换策略获取代理
        
        Returns:
            代理 URL 或 None
        """
        healthy_proxies = self.get_healthy_proxies()
        if not healthy_proxies:
            logger.warning("无健康代理，返回任意代理")
            return self._get_any_proxy()
        
        if self.rotation_strategy == RotationStrategy.ROUND_ROBIN:
            return self._round_robin_select(healthy_proxies)
        elif self.rotation_strategy == RotationStrategy.RANDOM:
            return random.choice(healthy_proxies).url
        elif self.rotation_strategy == RotationStrategy.WEIGHTED:
            return self._weighted_select(healthy_proxies)
        
        return self._round_robin_select(healthy_proxies)
    
    def _round_robin_select(self, proxies: List[ProxyInfo]) -> str:
        """轮询选择"""
        if not proxies:
            return None
        
        # 找到当前索引对应的代理
        urls = [p.url for p in proxies]
        index = self._round_robin_index % len(urls)
        self._round_robin_index = (index + 1) % len(urls)
        
        return urls[index]
    
    def _weighted_select(self, proxies: List[ProxyInfo]) -> str:
        """加权选择"""
        total_weight = sum(p.weight for p in proxies)
        if total_weight == 0:
            return random.choice(proxies).url
        
        r = random.uniform(0, total_weight)
        cumulative = 0
        for proxy in proxies:
            cumulative += proxy.weight
            if r <= cumulative:
                return proxy.url
        
        return proxies[-1].url
    
    def _get_any_proxy(self) -> Optional[str]:
        """获取任意代理（包括不健康的）"""
        if not self._proxies:
            return None
        return random.choice(list(self._proxies.values())).url
    
    # ============== 状态管理 ==============
    
    def mark_success(self, url: str):
        """标记代理成功"""
        if url in self._proxies:
            proxy = self._proxies[url]
            proxy.success_count += 1
            proxy.last_success_time = time.time()
            proxy.status = ProxyStatus.HEALTHY
            proxy.response_time = 0  # 实际响应时间由调用方设置
            logger.debug(f"代理成功: {url} (success={proxy.success_count}, failure={proxy.failure_count})")
    
    def mark_failure(self, url: str, error: Optional[str] = None):
        """标记代理失败"""
        if url in self._proxies:
            proxy = self._proxies[url]
            proxy.failure_count += 1
            proxy.last_failure_time = time.time()
            
            # 连续失败超过阈值标记为不健康
            if proxy.failure_count >= self.unhealthy_threshold:
                proxy.status = ProxyStatus.UNHEALTHY
                logger.warning(f"代理标记为不健康: {url} (连续失败 {proxy.failure_count} 次)")
            else:
                proxy.status = ProxyStatus.HEALTHY  # 仍保持健康但计数增加
            
            logger.debug(f"代理失败: {url} - {error} (success={proxy.success_count}, failure={proxy.failure_count})")
    
    def reset_proxy(self, url: str):
        """重置代理状态"""
        if url in self._proxies:
            proxy = self._proxies[url]
            proxy.success_count = 0
            proxy.failure_count = 0
            proxy.status = ProxyStatus.UNKNOWN
            logger.info(f"代理状态已重置: {url}")
    
    # ============== 健康检查 ==============
    
    def get_healthy_proxies(self) -> List[ProxyInfo]:
        """获取所有健康代理"""
        return [p for p in self._proxies.values() if p.is_healthy]
    
    def get_proxy_count(self) -> int:
        """获取代理总数"""
        return len(self._proxies)
    
    def get_healthy_count(self) -> int:
        """获取健康代理数"""
        return len(self.get_healthy_proxies())
    
    def get_proxy_stats(self) -> Dict[str, Any]:
        """获取代理统计信息"""
        return {
            'total': self.get_proxy_count(),
            'healthy': self.get_healthy_count(),
            'unhealthy': self.get_proxy_count() - self.get_healthy_count(),
            'proxies': {
                url: {
                    'status': proxy.status.value,
                    'success': proxy.success_count,
                    'failure': proxy.failure_count,
                    'health_score': round(proxy.health_score, 2),
                    'response_time': round(proxy.response_time, 2),
                }
                for url, proxy in self._proxies.items()
            }
        }
    
    async def check_proxy_health(self, url: str) -> bool:
        """
        检查单个代理的健康状态
        
        Args:
            url: 代理 URL
        
        Returns:
            是否健康
        """
        if url not in self._proxies:
            return False
        
        proxy = self._proxies[url]
        proxy.status = ProxyStatus.CHECKING
        
        try:
            import httpx
            start_time = time.time()
            
            async with httpx.AsyncClient(timeout=self.check_timeout) as client:
                response = await client.get(
                    'http://httpbin.org/get',
                    proxies={'http://': url, 'https://': url}
                )
            
            proxy.response_time = time.time() - start_time
            
            if response.status_code == 200:
                proxy.status = ProxyStatus.HEALTHY
                logger.debug(f"代理健康: {url} (响应时间: {proxy.response_time:.2f}s)")
                return True
            else:
                proxy.status = ProxyStatus.UNHEALTHY
                logger.warning(f"代理返回错误状态: {url} (status={response.status_code})")
                return False
                
        except Exception as e:
            proxy.status = ProxyStatus.UNHEALTHY
            logger.warning(f"代理检查失败: {url} - {type(e).__name__}: {str(e)[:50]}")
            return False
    
    async def _check_with_url(self, url: str) -> tuple:
        """包装检查方法，返回 (url, is_healthy) 元组"""
        return url, await self.check_proxy_health(url)

    async def check_all_proxies(self) -> Dict[str, bool]:
        """
        并发检查所有代理的健康状态

        Returns:
            {url: is_healthy} 字典
        """
        results = {}
        tasks = [self._check_with_url(url) for url in self._proxies.keys()]

        for task in asyncio.as_completed(tasks):
            try:
                url, is_healthy = await task
                results[url] = is_healthy
            except Exception as e:
                logger.error(f"代理检查任务异常: {e}")

        logger.info(f"代理健康检查完成: 健康 {sum(results.values())}/{len(results)}")
        return results
    
    async def start_health_check_loop(self):
        """启动后台健康检查循环"""
        if self._running:
            logger.debug("健康检查循环已在运行")
            return
        
        self._running = True
        logger.info(f"启动代理健康检查循环 (间隔: {self.check_interval}s)")
        
        while self._running:
            try:
                await self.check_all_proxies()
                await asyncio.sleep(self.check_interval)
            except Exception as e:
                logger.error(f"健康检查循环异常: {e}")
                await asyncio.sleep(self.check_interval)
    
    def stop_health_check_loop(self):
        """停止后台健康检查循环"""
        self._running = False
        logger.info("代理健康检查循环已停止")
    
    # ============== 批量操作 ==============
    
    def add_proxies(self, urls: List[str], weight: int = 1):
        """批量添加代理"""
        for url in urls:
            self.add_proxy(url, weight)
        logger.info(f"批量添加 {len(urls)} 个代理")
    
    def remove_proxies(self, urls: List[str]):
        """批量移除代理"""
        for url in urls:
            self.remove_proxy(url)
        logger.info(f"批量移除 {len(urls)} 个代理")
    
    def clear_all(self):
        """清空代理池"""
        self._proxies.clear()
        self._round_robin_index = 0
        logger.info("代理池已清空")
    
    def __len__(self) -> int:
        return len(self._proxies)
    
    def __contains__(self, url: str) -> bool:
        return url in self._proxies
    
    def __iter__(self):
        return iter(self._proxies.values())


# ============== 全局单例 ==============

_default_manager: Optional[ProxyManager] = None


def get_proxy_manager() -> ProxyManager:
    """
    获取全局代理管理器实例（单例模式）
    
    Returns:
        ProxyManager 实例
    """
    global _default_manager
    if _default_manager is None:
        _default_manager = ProxyManager()
    return _default_manager


def reset_proxy_manager():
    """重置全局代理管理器（用于测试）"""
    global _default_manager
    if _default_manager:
        _default_manager.stop_health_check_loop()
    _default_manager = None


if __name__ == '__main__':
    # 测试
    logging.basicConfig(level=logging.INFO)
    
    manager = ProxyManager()
    
    # 添加代理
    manager.add_proxy('http://127.0.0.1:7890')
    manager.add_proxy('http://127.0.0.1:7891')
    manager.add_proxy('http://127.0.0.1:7892', weight=2)
    
    print(f"代理池大小: {len(manager)}")
    print(f"健康代理数: {manager.get_healthy_count()}")
    
    # 测试轮换
    for i in range(5):
        proxy = manager.get_proxy()
        print(f"第 {i+1} 次选择: {proxy}")
    
    # 测试状态标记
    manager.mark_success('http://127.0.0.1:7890')
    manager.mark_failure('http://127.0.0.1:7891')
    
    print(f"\n代理统计: {manager.get_proxy_stats()}")