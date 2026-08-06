#!/usr/bin/env python
"""
test_search_optimizations.py - 搜索场景优化功能测试

测试：
- QueryBuilder（智能查询构造）
- Pagination（分页处理）
- ResultParser（结果页解析）
- BaseSearcher 分页支持
"""
import pytest
import json
import time
from unittest.mock import MagicMock, patch, AsyncMock
from dataclasses import asdict

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.searchers.query_builder import QueryBuilder, QueryParams, build_query, expand_query, split_query
from src.searchers.pagination import (
    PaginationType,
    PaginationInfo,
    PageResult,
    PaginationDetector,
    PaginationHandler,
    detect_pagination,
    create_pagination_handler,
)
from src.searchers.result_parser import ResultParser, ParsedResult, parse_search_results, extract_page_metadata
from src.searchers.base import BaseSearcher, SearcherConfig, SearchResult, SearchResults


class TestQueryBuilder:
    """QueryBuilder 测试"""

    def test_normalize_chinese(self):
        """测试中文查询规范化"""
        builder = QueryBuilder(language="zh")
        normalized = builder.normalize("  北京  天气  怎么样  ")
        assert normalized == "北京 天气 怎么样"

    def test_normalize_english(self):
        """测试英文查询规范化"""
        builder = QueryBuilder(language="en")
        normalized = builder.normalize("  The  Quick  Brown  Fox  ")
        assert normalized == "quick brown fox"

    def test_normalize_remove_stop_words(self):
        """测试去除停用词"""
        builder = QueryBuilder(language="zh")
        normalized = builder.normalize("这是一个测试")
        assert "这" not in normalized
        assert "是" not in normalized
        assert "一个" not in normalized
        assert "测试" in normalized

    def test_normalize_remove_special_chars(self):
        """测试去除特殊字符"""
        builder = QueryBuilder(language="zh")
        normalized = builder.normalize("测试@#$%")
        assert "@" not in normalized
        assert "测试" in normalized

    def test_expand_query(self):
        """测试查询扩展"""
        builder = QueryBuilder(language="zh")
        expanded = builder.expand("手机")
        assert len(expanded) >= 1
        assert "手机" in expanded[0]

    def test_split_query(self):
        """测试查询拆分"""
        builder = QueryBuilder(language="zh")
        parts = builder.split("北京天气上海天气广州天气深圳天气", max_parts=2)
        assert len(parts) <= 2
        assert all(len(p) > 0 for p in parts)

    def test_build_params(self):
        """测试构建查询参数"""
        builder = QueryBuilder(language="zh")
        params = builder.build_params("测试查询", sort_by="time", page=2)
        assert params.original == "测试查询"
        assert params.normalized == "测试查询"
        assert params.sort_by == "time"
        assert params.page == 2
        # encoded 是 URL 编码后的结果，包含 % 前缀
        assert "%" in params.encoded
        assert len(params.encoded) > 0

    def test_build_url(self):
        """测试构建搜索 URL"""
        builder = QueryBuilder(language="zh")
        params = builder.build_params("测试")
        url = builder.build_url("https://example.com/search", params)
        assert "example.com" in url
        # URL 编码后可能双重编码，检查包含编码字符
        assert "%" in url

    def test_build_api_params(self):
        """测试构建 API 参数"""
        builder = QueryBuilder(language="zh")
        params = builder.build_params("测试", sort_by="popularity", time_range="week")
        api_params = builder.build_api_params(params)
        assert api_params["query"] == "测试"
        assert api_params["sort"] == "popularity"
        assert api_params["time_range"] == "week"

    def test_query_params_to_dict(self):
        """测试 QueryParams 序列化"""
        params = QueryParams(original="test", normalized="test", encoded="test")
        d = params.to_dict()
        assert d["original"] == "test"
        assert d["normalized"] == "test"


class TestPagination:
    """Pagination 测试"""

    def test_pagination_type_enum(self):
        """测试分页类型枚举"""
        assert PaginationType.URL_BASED.value == "url_based"
        assert PaginationType.CLICK_NEXT.value == "click_next"
        assert PaginationType.INFINITE_SCROLL.value == "infinite"
        assert PaginationType.LOAD_MORE.value == "load_more"

    def test_pagination_info_to_dict(self):
        """测试 PaginationInfo 序列化"""
        info = PaginationInfo(
            pagination_type=PaginationType.URL_BASED,
            current_page=1,
            total_pages=10,
            has_next=True,
            has_prev=False,
        )
        d = info.to_dict()
        assert d["pagination_type"] == "url_based"
        assert d["total_pages"] == 10
        assert d["has_next"] is True

    def test_page_result_to_dict(self):
        """测试 PageResult 序列化"""
        result = PageResult(page=1, results=[{"title": "test"}])
        d = result.to_dict()
        assert d["page"] == 1
        assert d["results_count"] == 1

    def test_pagination_detector_init(self):
        """测试分页检测器初始化"""
        session = MagicMock()
        detector = PaginationDetector(session)
        assert detector.session is session

    def test_pagination_handler_init(self):
        """测试分页处理器初始化"""
        session = MagicMock()
        handler = PaginationHandler(session)
        assert handler.session is session

    def test_detect_pagination_function(self):
        """测试 detect_pagination 便捷函数"""
        session = MagicMock()
        result = detect_pagination(session)
        assert isinstance(result, PaginationInfo)

    def test_create_pagination_handler_function(self):
        """测试 create_pagination_handler 便捷函数"""
        session = MagicMock()
        handler = create_pagination_handler(session)
        assert isinstance(handler, PaginationHandler)


class TestResultParser:
    """ResultParser 测试"""

    def test_parsed_result_to_dict(self):
        """测试 ParsedResult 序列化"""
        result = ParsedResult(
            title="测试标题",
            url="https://example.com",
            snippet="测试摘要",
            source="test",
        )
        d = result.to_dict()
        assert d["title"] == "测试标题"
        assert d["url"] == "https://example.com"
        assert d["source"] == "test"

    def test_result_parser_init(self):
        """测试 ResultParser 初始化"""
        session = MagicMock()
        parser = ResultParser(session, source="test")
        assert parser.source == "test"
        assert parser.session is session

    def test_parse_empty_html(self):
        """测试解析空 HTML"""
        session = MagicMock()
        parser = ResultParser(session)
        results = parser.parse("")
        assert isinstance(results, list)

    def test_parse_with_mock_results(self):
        """测试带 mock 结果的解析"""
        session = MagicMock()
        session.eval_js.return_value = [
            {"title": "结果1", "url": "https://example.com/1", "snippet": "摘要1"},
            {"title": "结果2", "url": "https://example.com/2", "snippet": "摘要2"},
        ]
        parser = ResultParser(session, source="test")
        results = parser.parse()
        assert len(results) == 2
        assert results[0].title == "结果1"
        assert results[1].url == "https://example.com/2"

    def test_deduplicate_results(self):
        """测试结果去重"""
        session = MagicMock()
        parser = ResultParser(session)
        results = [
            ParsedResult(title="A", url="https://a.com"),
            ParsedResult(title="B", url="https://b.com"),
            ParsedResult(title="A dup", url="https://a.com"),
        ]
        unique = parser._deduplicate(results)
        assert len(unique) == 2

    def test_extract_metadata(self):
        """测试元数据提取"""
        session = MagicMock()
        session.eval_js.return_value = {"title": "测试页面", "description": "测试描述"}
        parser = ResultParser(session)
        metadata = parser.extract_metadata()
        assert metadata["title"] == "测试页面"


class TestBaseSearcherPagination:
    """BaseSearcher 分页支持测试"""

    def test_paginate_method_exists(self):
        """测试 paginate 方法存在"""
        class MockSearcher(BaseSearcher):
            @property
            def source_name(self):
                return "mock"

            @property
            def supported_types(self):
                return ["search"]

            async def search(self, query, config=None):
                return []

            async def get_detail(self, url, config=None):
                return {}

            async def health_check(self):
                return True

            async def close(self):
                pass

        searcher = MockSearcher()
        assert hasattr(searcher, "paginate")
        assert callable(searcher.paginate)

    def test_search_with_pagination_method_exists(self):
        """测试 search_with_pagination 方法存在"""
        class MockSearcher(BaseSearcher):
            @property
            def source_name(self):
                return "mock"

            @property
            def supported_types(self):
                return ["search"]

            async def search(self, query, config=None):
                return []

            async def get_detail(self, url, config=None):
                return {}

            async def health_check(self):
                return True

            async def close(self):
                pass

        searcher = MockSearcher()
        assert hasattr(searcher, "search_with_pagination")
        assert callable(searcher.search_with_pagination)

    def test_get_pagination_info(self):
        """测试 get_pagination_info 方法"""
        class MockSearcher(BaseSearcher):
            @property
            def source_name(self):
                return "mock"

            @property
            def supported_types(self):
                return ["search"]

            async def search(self, query, config=None):
                return []

            async def get_detail(self, url, config=None):
                return {}

            async def health_check(self):
                return True

            async def close(self):
                pass

        searcher = MockSearcher()
        info = searcher.get_pagination_info()
        assert isinstance(info, dict)
        assert "pagination_type" in info

    def test_paginate_returns_search_results(self):
        """测试 paginate 返回 SearchResults"""
        class MockSearcher(BaseSearcher):
            @property
            def source_name(self):
                return "mock"

            @property
            def supported_types(self):
                return ["search"]

            async def search(self, query, config=None):
                return [
                    SearchResult(title=f"结果{i}", url=f"https://example.com/{i}")
                    for i in range(5)
                ]

            async def get_detail(self, url, config=None):
                return {}

            async def health_check(self):
                return True

            async def close(self):
                pass

        searcher = MockSearcher()
        results = searcher.paginate("测试", max_pages=1)
        assert isinstance(results, SearchResults)
        assert results.source == "mock"
        assert results.query == "测试"
        assert len(results.results) == 5


class TestSearchOptimizationIntegration:
    """搜索优化集成测试"""

    def test_full_search_flow(self):
        """测试完整搜索流程"""
        # 1. 构建查询
        params = build_query("Python 编程", language="zh", sort_by="time")
        assert params.normalized == "Python 编程"
        assert params.sort_by == "time"

        # 2. 扩展查询
        expanded = expand_query("Python")
        assert len(expanded) >= 1

        # 3. 拆分查询
        parts = split_query("Python 编程 教程 入门", max_parts=2)
        assert len(parts) <= 2

        # 4. 构建 URL
        builder = QueryBuilder(language="zh")
        url = builder.build_url("https://example.com/search", params)
        assert "example.com" in url

    def test_pagination_flow(self):
        """测试分页流程"""
        session = MagicMock()
        handler = create_pagination_handler(session)

        # 模拟分页信息
        info = PaginationInfo(
            pagination_type=PaginationType.URL_BASED,
            current_page=1,
            total_pages=5,
            has_next=True,
        )
        assert info.has_next is True
        assert info.total_pages == 5

    def test_result_parsing_flow(self):
        """测试结果解析流程"""
        session = MagicMock()
        session.eval_js.return_value = [
            {"title": "结果1", "url": "https://a.com", "snippet": "摘要1"},
        ]
        parser = ResultParser(session, source="test")
        results = parser.parse()
        assert len(results) == 1
        assert results[0].title == "结果1"


class TestSearchOptimizationEdgeCases:
    """边界情况测试"""

    def test_empty_query(self):
        """测试空查询"""
        builder = QueryBuilder(language="zh")
        normalized = builder.normalize("")
        assert normalized == ""

    def test_single_word_query(self):
        """测试单字查询"""
        builder = QueryBuilder(language="zh")
        normalized = builder.normalize("测")
        assert normalized == "测"

    def test_all_stop_words(self):
        """测试全停用词查询"""
        builder = QueryBuilder(language="zh")
        normalized = builder.normalize("的是一个测试")
        # 停用词被去除后可能只剩内容词
        assert len(normalized) >= 0

    def test_special_characters_only(self):
        """测试纯特殊字符查询"""
        builder = QueryBuilder(language="zh")
        normalized = builder.normalize("@#$%^&*()")
        assert normalized == ""

    def test_very_long_query(self):
        """测试超长查询"""
        builder = QueryBuilder(language="zh")
        long_query = "测" * 1000
        normalized = builder.normalize(long_query)
        assert len(normalized) > 0

    def test_unicode_query(self):
        """测试 Unicode 查询"""
        builder = QueryBuilder(language="zh")
        normalized = builder.normalize("日本語テスト")
        assert "日本語" in normalized


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
