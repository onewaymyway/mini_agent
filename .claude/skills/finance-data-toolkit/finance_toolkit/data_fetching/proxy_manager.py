# -*- coding: utf-8 -*-
"""
代理池管理器

提供代理节点的健康检查、自动轮换、故障转移功能。
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class ProxyNode:
    """代理节点信息"""
    address: str
    protocol: str = 'http'
    healthy: bool = True
    last_check: float = 0.0
    fail_count: int = 0
    success_count: int = 0

    @property
    def failure_rate(self) -> float:
        total = self.fail_count + self.success_count
        if total == 0:
            return 0.0
        return self.fail_count / total

    @property
    def proxy_url(self) -> str:
        return f"{self.protocol}://{self.address}"


class ProxyPool:
    """代理池管理器"""

    def __init__(self, max_size: int = 10, check_interval: float = 60.0):
        self._nodes: Dict[str, ProxyNode] = {}
        self._max_size = max_size
        self._check_interval = check_interval
        self._last_check_time = 0.0
        self._lock = asyncio.Lock()

    def add_node(self, node: ProxyNode) -> None:
        """添加代理节点"""
        if len(self._nodes) >= self._max_size:
            logger.warning(f"代理池已满，拒绝添加: {node.proxy_url}")
            return
        self._nodes[node.address] = node
        logger.info(f"添加代理节点: {node.proxy_url}")

    def remove_node(self, address: str) -> None:
        """移除代理节点"""
        self._nodes.pop(address, None)
        logger.info(f"移除代理节点: {address}")

    def get_healthy_proxies(self) -> List[str]:
        """获取所有健康代理URL列表"""
        return [
            n.proxy_url for n in self._nodes.values()
            if n.healthy and n.failure_rate < 0.5
        ]

    def get_random_proxy(self) -> Optional[str]:
        """随机获取一个健康代理"""
        healthy = self.get_healthy_proxies()
        if not healthy:
            # 降级：返回所有未标记为不健康的节点
            all_nodes = [n.proxy_url for n in self._nodes.values() if n.healthy]
            return random.choice(all_nodes) if all_nodes else None
        return random.choice(healthy)

    def mark_healthy(self, address: str) -> None:
        """标记节点健康"""
        if address in self._nodes:
            self._nodes[address].healthy = True
            self._nodes[address].success_count += 1
            self._nodes[address].fail_count = max(0, self._nodes[address].fail_count - 1)

    def mark_unhealthy(self, address: str, reason: str = '') -> None:
        """标记节点不健康"""
        if address in self._nodes:
            node = self._nodes[address]
            node.healthy = False
            node.fail_count += 1
            logger.warning(f"代理不可用: {address} ({reason})")

    def update_last_check(self, address: str) -> None:
        """更新最后检查时间"""
        if address in self._nodes:
            self._nodes[address].last_check = time.time()

    def need_check(self) -> bool:
        """判断是否需要执行健康检查"""
        return time.time() - self._last_check_time > self._check_interval

    async def health_check(self, test_url: str = 'http://httpbin.org/get', timeout: float = 5.0) -> Dict[str, bool]:
        """对所有节点执行健康检查"""
        import httpx
        results = {}
        async with httpx.AsyncClient(timeout=timeout) as client:
            for addr, node in self._nodes.items():
                try:
                    resp = await client.get(test_url, proxy=node.proxy_url, timeout=timeout)
                    if resp.status_code == 200:
                        node.healthy = True
                        node.success_count += 1
                        results[addr] = True
                    else:
                        node.healthy = False
                        node.fail_count += 1
                        results[addr] = False
                except Exception as e:
                    node.healthy = False
                    node.fail_count += 1
                    results[addr] = False
                    logger.debug(f"健康检查失败 {addr}: {e}")
        self._last_check_time = time.time()
        return results

    def get_stats(self) -> Dict:
        """获取代理池统计信息"""
        total = len(self._nodes)
        healthy = sum(1 for n in self._nodes.values() if n.healthy)
        return {
            'total': total,
            'healthy': healthy,
            'availability_rate': (healthy / total * 100) if total > 0 else 0.0,
            'nodes': [
                {
                    'address': addr,
                    'healthy': n.healthy,
                    'failure_rate': round(n.failure_rate, 3),
                    'last_check': n.last_check,
                }
                for addr, n in self._nodes.items()
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


def reset_proxy_pool() -> None:
    """重置代理池（用于测试）"""
    global _proxy_pool
    _proxy_pool = None
