# -*- coding: utf-8 -*-
"""proxy_manager 单元测试"""

import pytest
import asyncio
from finance_toolkit.data_fetching.proxy_manager import ProxyNode, ProxyPool, get_proxy_pool, reset_proxy_pool


class TestProxyNode:
    def test_creation(self):
        node = ProxyNode(address='127.0.0.1:7890', protocol='http')
        assert node.proxy_url == 'http://127.0.0.1:7890'
        assert node.healthy is True
        assert node.fail_count == 0

    def test_failure_rate_zero_when_no_history(self):
        node = ProxyNode(address='1.2.3.4:8080')
        assert node.failure_rate == 0.0

    def test_failure_rate_calculation(self):
        node = ProxyNode(address='1.2.3.4:8080')
        node.fail_count = 3
        node.success_count = 7
        assert node.failure_rate == 0.3


class TestProxyPool:
    def setup_method(self):
        reset_proxy_pool()
        self.pool = ProxyPool(max_size=5, check_interval=60.0)

    def teardown_method(self):
        reset_proxy_pool()

    def test_add_and_get_proxies(self):
        self.pool.add_node(ProxyNode('1.1.1.1:8080'))
        self.pool.add_node(ProxyNode('2.2.2.2:8080'))
        proxies = self.pool.get_healthy_proxies()
        assert len(proxies) == 2

    def test_max_size_limit(self):
        for i in range(6):
            self.pool.add_node(ProxyNode(f'{i}.{i}.{i}.{i}:8080'))
        assert len(self.pool._nodes) == 5

    def test_remove_node(self):
        self.pool.add_node(ProxyNode('1.1.1.1:8080'))
        self.pool.remove_node('1.1.1.1:8080')
        assert len(self.pool._nodes) == 0

    def test_mark_unhealthy(self):
        self.pool.add_node(ProxyNode('1.1.1.1:8080'))
        self.pool.mark_unhealthy('1.1.1.1:8080', 'timeout')
        proxies = self.pool.get_healthy_proxies()
        assert len(proxies) == 0

    def test_mark_healthy_restores(self):
        self.pool.add_node(ProxyNode('1.1.1.1:8080'))
        self.pool.mark_unhealthy('1.1.1.1:8080')
        self.pool.mark_healthy('1.1.1.1:8080')
        proxies = self.pool.get_healthy_proxies()
        assert len(proxies) == 1

    def test_get_random_proxy(self):
        self.pool.add_node(ProxyNode('1.1.1.1:8080'))
        self.pool.add_node(ProxyNode('2.2.2.2:8080'))
        result = self.pool.get_random_proxy()
        assert result is not None
        assert '1.1.1.1:8080' in result or '2.2.2.2:8080' in result

    def test_all_unhealthy_fallback(self):
        self.pool.add_node(ProxyNode('1.1.1.1:8080'))
        self.pool.mark_unhealthy('1.1.1.1:8080')
        # fallback should return nodes that are still technically in pool
        result = self.pool.get_random_proxy()
        # get_random returns healthy ones first; if none healthy, falls back to all_nodes
        # all_nodes only has the unhealthy one which is excluded by healthy filter
        assert result is None

    def test_stats(self):
        self.pool.add_node(ProxyNode('1.1.1.1:8080'))
        self.pool.add_node(ProxyNode('2.2.2.2:8080'))
        stats = self.pool.get_stats()
        assert stats['total'] == 2
        assert stats['healthy'] == 2
        assert stats['availability_rate'] == 100.0

    def test_singleton(self):
        p1 = get_proxy_pool()
        p2 = get_proxy_pool()
        assert p1 is p2
        reset_proxy_pool()

    @pytest.mark.asyncio
    async def test_health_check_uses_httpx(self):
        """验证健康检查调用时不抛异常（不需要真实代理）"""
        self.pool.add_node(ProxyNode('127.0.0.1:1', protocol='http'))
        # 预期会超时/连接失败，但不崩溃
        result = await self.pool.health_check(timeout=1.0)
        assert '127.0.0.1:1' in result
        assert result['127.0.0.1:1'] is False  # 连接应失败
