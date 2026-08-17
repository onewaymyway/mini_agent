# -*- coding: utf-8 -*-
"""多源适配器单元测试"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from finance_toolkit.adapters.multi_source_adapter import (
    MultiSourceAdapter,
    SourcePriority,
    create_standard_adapter,
)


class TestMultiSourceAdapter:
    """测试多源适配器"""

    def setup_method(self):
        """每个测试前创建新适配器"""
        self.adapter = MultiSourceAdapter()

    def test_initialization(self):
        """测试初始化"""
        assert self.adapter._priority == ['tencent', 'sina', 'eastmoney']
        assert self.adapter._sources == {}
        assert self.adapter._results_cache == {}

    def test_custom_priority(self):
        """测试自定义优先级"""
        adapter = MultiSourceAdapter(priority=['sina', 'tencent'])
        assert adapter._priority == ['sina', 'tencent']

    @pytest.mark.asyncio
    async def test_fetch_with_cache_hit(self):
        """测试缓存命中"""
        # 预填充缓存
        self.adapter._results_cache['quote:600000'] = {
            'data': [{'code': '600000', 'price': 10.5}],
            'ttl': asyncio.get_event_loop().time() + 1000,
        }

        result = await self.adapter.fetch_with_fallback('600000', 'quote')
        assert result['success'] is True
        assert result['cache_hit'] is True
        assert result['source'] == 'cache'
        assert len(result['data']) == 1

    @pytest.mark.asyncio
    async def test_fetch_with_fallback(self):
        """测试降级逻辑"""
        # 模拟腾讯失败，新浪成功
        async def tencent_fail(query, data_type, **kwargs):
            raise Exception("腾讯超时")

        async def sina_success(query, data_type, **kwargs):
            return [{'code': '600000', 'name': '浦发银行', 'price': 10.5}]

        self.adapter.register_source('tencent', tencent_fail)
        self.adapter.register_source('sina', sina_success)

        result = await self.adapter.fetch_with_fallback('600000', 'quote')
        assert result['success'] is True
        assert result['source'] == 'sina'
        assert len(result['data']) == 1
        assert result['data'][0]['code'] == '600000'

    @pytest.mark.asyncio
    async def test_all_sources_fail(self):
        """测试所有数据源都失败"""
        async def always_fail(query, data_type, **kwargs):
            raise Exception("全部失败")

        self.adapter.register_source('tencent', always_fail)
        self.adapter.register_source('sina', always_fail)
        self.adapter.register_source('eastmoney', always_fail)

        result = await self.adapter.fetch_with_fallback('600000', 'quote')
        assert result['success'] is False
        assert result['data'] == []
        assert result['source'] == 'none'

    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        """测试超时处理"""
        async def slow_source(query, data_type, **kwargs):
            await asyncio.sleep(10)  # 故意慢
            return [{'code': '600000'}]

        self.adapter.register_source('tencent', slow_source)

        result = await self.adapter.fetch_with_fallback('600000', 'quote', timeout=0.1)
        assert result['success'] is False
        assert result['source'] == 'none'

    def test_register_source(self):
        """测试注册数据源"""
        mock_fetcher = AsyncMock(return_value=[{'code': '600000'}])
        self.adapter.register_source('test_source', mock_fetcher)
        assert 'test_source' in self.adapter._sources
        assert self.adapter._sources['test_source']['fetcher'] == mock_fetcher

    def test_get_stats(self):
        """测试统计信息"""
        mock_fetcher = AsyncMock(return_value=[{'code': '600000'}])
        self.adapter.register_source('tencent', mock_fetcher)
        self.adapter.register_source('sina', mock_fetcher)

        stats = self.adapter.get_stats()
        assert 'registered_sources' in stats
        assert 'circuit_breaker_status' in stats
        assert stats['registered_sources'] == ['tencent', 'sina']

    def test_clear_cache(self):
        """测试清除缓存"""
        self.adapter._results_cache['test'] = {'data': [], 'ttl': 1000}
        self.adapter.clear_cache()
        assert self.adapter._results_cache == {}

    def test_reset_circuit_breaker(self):
        """测试重置熔断器"""
        self.adapter._circuit_breaker['tencent'] = 5
        self.adapter.reset_circuit_breaker('tencent')
        assert 'tencent' not in self.adapter._circuit_breaker

    def test_circuit_breaker_threshold(self):
        """测试熔断阈值"""
        async def failing_source(query, data_type, **kwargs):
            raise Exception("失败")

        self.adapter.register_source('tencent', failing_source)

        # 多次调用触发熔断
        for i in range(6):
            asyncio.run(self.adapter.fetch_with_fallback('600000', 'quote'))

        # 检查熔断器状态
        assert self.adapter._circuit_breaker.get('tencent', 0) >= 5

    def test_create_standard_adapter(self):
        """测试标准适配器工厂"""
        adapter = create_standard_adapter()
        assert isinstance(adapter, MultiSourceAdapter)

    def test_empty_query(self):
        """测试空查询"""
        result = asyncio.run(self.adapter.fetch_with_fallback('', 'quote'))
        assert result['success'] is False

    def test_priority_order_preserved(self):
        """测试优先级顺序保留"""
        adapter = MultiSourceAdapter(priority=['source3', 'source1', 'source2'])
        # 注册顺序应与优先级顺序一致
        adapter.register_source('source3', AsyncMock(return_value=[{'code': '3'}]))
        adapter.register_source('source1', AsyncMock(return_value=[{'code': '1'}]))
        adapter.register_source('source2', AsyncMock(return_value=[{'code': '2'}]))

        # 验证注册顺序
        assert list(adapter._sources.keys()) == ['source3', 'source1', 'source2']


class TestSourcePriority:
    """测试优先级枚举"""

    def test_enum_values(self):
        """测试枚举值"""
        assert SourcePriority.TENCENT.value == 1
        assert SourcePriority.SINA.value == 2
        assert SourcePriority.EASTMONEY.value == 3

    def test_comparison(self):
        """测试枚举比较"""
        assert SourcePriority.TENCENT < SourcePriority.SINA
        assert SourcePriority.EASTMONEY > SourcePriority.TENCENT
