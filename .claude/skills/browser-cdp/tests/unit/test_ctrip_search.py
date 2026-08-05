#!/usr/bin/env python
"""
test_ctrip_search.py - 携程搜索器单元测试

测试 CtripSearcher 的核心功能。
"""

import pytest
from src.searchers.ctrip_search import CtripSearcher
from src.searchers.base import SearcherConfig


class TestCtripSearcher:
    """测试携程搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = CtripSearcher()
        assert searcher.source_name == "ctrip"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = CtripSearcher()
        assert "hotel" in searcher.supported_types
        assert "flight" in searcher.supported_types
        assert "attraction" in searcher.supported_types
        assert "travel_guide" in searcher.supported_types

    def test_default_config(self):
        """测试默认配置"""
        searcher = CtripSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10
        assert searcher.config.stealth == True

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = CtripSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)

    def test_get_detail_method_exists(self):
        """测试 get_detail 方法存在"""
        searcher = CtripSearcher()
        assert hasattr(searcher, "get_detail")
        assert callable(searcher.get_detail)

    def test_health_check_exists(self):
        """测试 health_check 方法存在"""
        searcher = CtripSearcher()
        assert hasattr(searcher, "health_check")
        assert callable(searcher.health_check)

    def test_close_method_exists(self):
        """测试 close 方法存在"""
        searcher = CtripSearcher()
        assert hasattr(searcher, "close")
        assert callable(searcher.close)

    def test_validate_config_valid(self):
        """测试配置验证 - 有效配置"""
        searcher = CtripSearcher()
        config = SearcherConfig(max_results=10, wait_timeout=30)
        assert searcher.validate_config(config) == True

    def test_validate_config_invalid_max_results(self):
        """测试配置验证 - 无效 max_results"""
        searcher = CtripSearcher()
        with pytest.raises(ValueError):
            searcher.validate_config(SearcherConfig(max_results=0))

    def test_validate_config_invalid_wait_timeout(self):
        """测试配置验证 - 无效 wait_timeout"""
        searcher = CtripSearcher()
        with pytest.raises(ValueError):
            searcher.validate_config(SearcherConfig(wait_timeout=3))

    def test_format_results_json(self):
        """测试 JSON 格式输出"""
        searcher = CtripSearcher()
        from src.searchers.base import SearchResult
        results = [
            SearchResult(
                source="ctrip",
                title="测试酒店",
                url="https://hotels.ctrip.com/hotel/123.html",
                snippet="北京市中心",
                metadata={"price": "¥588"}
            )
        ]
        output = searcher.format_results(results, "json")
        assert "ctrip" in output
        assert "测试酒店" in output

    def test_format_results_markdown(self):
        """测试 Markdown 格式输出"""
        searcher = CtripSearcher()
        from src.searchers.base import SearchResult
        results = [
            SearchResult(
                source="ctrip",
                title="测试酒店",
                url="https://hotels.ctrip.com/hotel/123.html",
                snippet="北京市中心"
            )
        ]
        output = searcher.format_results(results, "markdown")
        assert "# ctrip 搜索结果" in output
        assert "## 1. 测试酒店" in output

    def test_deduplicate(self):
        """测试去重功能"""
        searcher = CtripSearcher()
        from src.searchers.base import SearchResults, SearchResult
        results = SearchResults(
            source="ctrip",
            query="北京酒店",
            results=[
                SearchResult(source="ctrip", title="酒店A", url="https://ctrip.com/hotel/1"),
                SearchResult(source="ctrip", title="酒店B", url="https://ctrip.com/hotel/2"),
                SearchResult(source="ctrip", title="酒店A", url="https://ctrip.com/hotel/1"),  # 重复
            ]
        )
        removed = results.deduplicate(by="url")
        assert removed == 1
        assert len(results.results) == 2


class TestCtripSearcherIntegration:
    """测试携程搜索器集成"""

    @pytest.mark.asyncio
    async def test_searcher_initialization(self):
        """测试搜索器初始化"""
        searcher = CtripSearcher()
        assert searcher.source_name == "ctrip"
        assert searcher.config.max_results == 10

    @pytest.mark.asyncio
    async def test_health_check(self):
        """测试健康检查"""
        searcher = CtripSearcher()
        result = await searcher.health_check()
        assert result == True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
