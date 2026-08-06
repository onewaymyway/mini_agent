"""
搜索场景优化单元测试

测试：
- search_pagination.py
- search_query_builder.py
- search_result_parser.py
"""
import pytest
import json
import time
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, '.')

from src.searchers.search_pagination import (
    PaginationInfo,
    PaginationDetector,
    PaginationNavigator,
    AutoPagination,
    detect_pagination,
)
from src.searchers.search_query_builder import (
    QueryExpansion,
    QueryBuilder,
    build_query,
    optimize_query,
    suggest_queries,
)
from src.searchers.search_result_parser import (
    ParsedResult,
    ResultParser,
    parse_search_results,
    deduplicate_results,
)


class TestPaginationInfo:
    """PaginationInfo 测试"""
    
    def test_to_dict(self):
        """测试序列化"""
        info = PaginationInfo(
            current_page=2,
            total_pages=10,
            total_results=200,
            page_size=20,
            has_next=True,
            has_prev=True,
            next_page_url="https://example.com?page=3",
            prev_page_url="https://example.com?page=1",
        )
        
        d = info.to_dict()
        assert d["current_page"] == 2
        assert d["total_pages"] == 10
        assert d["has_next"] is True
        assert d["next_page_url"] == "https://example.com?page=3"


class TestPaginationDetector:
    """PaginationDetector 测试"""
    
    def setup_method(self):
        self.session = MagicMock()
        self.detector = PaginationDetector(self.session)
    
    def test_detect_pagination(self):
        """测试分页检测"""
        # 模拟不同调用的返回值
        self.session.eval_js.side_effect = [
            5,   # _detect_total_pages
            100, # _detect_total_results
            1,   # _detect_current_page
            None,# _detect_next_page_url
            None,# _detect_prev_page_url
        ]
        
        info = self.detector.detect()
        
        assert info.total_pages == 5
        assert info.total_results == 100
        assert info.current_page == 1
    
    def test_detect_total_results(self):
        """测试总结果数检测"""
        self.session.eval_js.return_value = 150
        
        total = self.detector._detect_total_results()
        assert total == 150
    
    def test_detect_current_page(self):
        """测试当前页码检测"""
        # 模拟 URL 参数
        self.session.eval_js.return_value = 3
        
        page = self.detector._detect_current_page()
        assert page == 3
    
    def test_detect_next_page_url(self):
        """测试下一页 URL 检测"""
        self.session.eval_js.return_value = "https://example.com/search?page=2"
        
        url = self.detector._detect_next_page_url()
        assert url == "https://example.com/search?page=2"
    
    def test_detect_no_next_page(self):
        """测试无下一页时返回 None"""
        self.session.eval_js.return_value = None
        
        url = self.detector._detect_next_page_url()
        assert url is None


class TestPaginationNavigator:
    """PaginationNavigator 测试"""
    
    def setup_method(self):
        self.session = MagicMock()
        self.navigator = PaginationNavigator(self.session)
    
    def test_go_to_page(self):
        """测试导航到指定页"""
        self.session.send.return_value = {"result": {"value": "https://example.com?page=3"}}
        
        result = self.navigator.go_to_page(3, "https://example.com/search")
        
        assert result is True
        self.session.send.assert_called()
    
    def test_go_next(self):
        """测试下一页导航"""
        self.session.send.return_value = {"result": {"value": "https://example.com?page=2"}}
        
        result = self.navigator.go_next()
        
        assert result is True
    
    def test_go_prev(self):
        """测试上一页导航"""
        self.session.send.return_value = {"result": {"value": "https://example.com?page=1"}}
        
        result = self.navigator.go_prev()
        
        assert result is True
    
    def test_wait_for_page_load(self):
        """测试等待页面加载"""
        self.session.send.return_value = {}
        
        result = self.navigator.wait_for_page_load(timeout=1)
        
        assert result is True
    
    def test_get_page_history(self):
        """测试分页历史"""
        self.navigator._page_history = [
            {"page": 1, "url": "https://example.com?page=1"},
            {"page": 2, "url": "https://example.com?page=2"},
        ]
        
        history = self.navigator.get_page_history()
        assert len(history) == 2
    
    def test_clear_history(self):
        """测试清空历史"""
        self.navigator._page_history = [{"page": 1}]
        self.navigator.clear_history()
        assert len(self.navigator._page_history) == 0


class TestAutoPagination:
    """AutoPagination 测试"""
    
    def setup_method(self):
        self.session = MagicMock()
        self.pagination = AutoPagination(self.session, max_pages=3)
    
    def test_paginate(self):
        """测试自动分页"""
        # 模拟分页检测
        with patch.object(self.pagination.detector, 'detect') as mock_detect:
            mock_detect.return_value = PaginationInfo(
                current_page=1,
                total_pages=2,
                has_next=True,
            )
            
            # 模拟导航
            with patch.object(self.pagination.navigator, 'go_next') as mock_go_next:
                with patch.object(self.pagination.navigator, 'wait_for_page_load') as mock_wait:
                    mock_go_next.return_value = True
                    mock_wait.return_value = True
                    
                    # 模拟回调
                    def callback(page_info, session):
                        return [{"title": f"结果 {page_info.current_page}"}]
                    
                    results = self.pagination.paginate(callback=callback)
                    
                    assert len(results) > 0
    
    def test_get_results(self):
        """测试获取结果"""
        self.pagination._collected_results = [{"title": "test"}]
        
        results = self.pagination.get_results()
        assert len(results) == 1
    
    def test_reset(self):
        """测试重置"""
        self.pagination._collected_results = [{"title": "test"}]
        self.pagination.reset()
        assert len(self.pagination._collected_results) == 0


class TestQueryBuilder:
    """QueryBuilder 测试"""
    
    def setup_method(self):
        self.builder = QueryBuilder()
    
    def test_expand_query(self):
        """测试查询扩展"""
        expansion = self.builder.expand("手机")
        
        assert expansion.original == "手机"
        assert len(expansion.expanded) > 0
        # 检查是否包含原始查询或扩展词
        assert expansion.expanded[0] == "手机"
    
    def test_optimize_query(self):
        """测试查询优化"""
        optimized = self.builder.optimize("  手机  评测  ")
        
        assert optimized == "手机 评测"
    
    def test_suggest(self):
        """测试查询建议"""
        suggestions = self.builder.suggest("电脑", limit=3)
        
        assert len(suggestions) <= 3
        assert isinstance(suggestions, list)
    
    def test_deduplicate(self):
        """测试查询去重"""
        queries = ["手机", "手机", "电脑", "手机"]
        unique = self.builder.deduplicate(queries)
        
        assert len(unique) == 2
        assert "手机" in unique
        assert "电脑" in unique
    
    def test_add_to_history(self):
        """测试添加历史"""
        self.builder.add_to_history("测试查询")
        history = self.builder.get_history()
        
        assert "测试查询" in history
    
    def test_clear_history(self):
        """测试清空历史"""
        self.builder.add_to_history("测试")
        self.builder.clear_history()
        
        assert len(self.builder.get_history()) == 0
    
    def test_query_expansion_to_dict(self):
        """测试 QueryExpansion 序列化"""
        expansion = QueryExpansion(
            original="手机",
            expanded=["手机", "移动电话"],
            synonyms=["移动电话"],
            suggestions=["手机评测"],
        )
        
        d = expansion.to_dict()
        assert d["original"] == "手机"
        assert len(d["expanded"]) == 2


class TestResultParser:
    """ResultParser 测试"""
    
    def setup_method(self):
        self.session = MagicMock()
        self.parser = ResultParser(self.session)
    
    def test_parse_result_quality_score(self):
        """测试结果质量评分"""
        result = ParsedResult(
            title="这是一个很长的标题，包含很多有用信息",
            url="https://example.com/article",
            snippet="这是一段很长的摘要，包含了文章的主要内容...",
            published_time="2024-01-01",
        )
        
        score = result.quality_score()
        # 质量分数应该大于0
        assert score > 0
    
    def test_parse_result_to_dict(self):
        """测试结果序列化"""
        result = ParsedResult(
            title="测试标题",
            url="https://example.com",
            snippet="测试摘要",
        )
        
        d = result.to_dict()
        assert d["title"] == "测试标题"
        assert d["url"] == "https://example.com"
    
    def test_deduplicate_by_url(self):
        """测试 URL 去重"""
        results = [
            ParsedResult(title="A", url="https://example.com/1"),
            ParsedResult(title="B", url="https://example.com/2"),
            ParsedResult(title="A", url="https://example.com/1"),  # 重复
        ]
        
        unique = self.parser.deduplicate(results, by="url")
        assert len(unique) == 2
    
    def test_sort_by_quality(self):
        """测试按质量排序"""
        results = [
            ParsedResult(title="短", url="https://a.com"),
            ParsedResult(
                title="这是一个很长的标题",
                url="https://b.com",
                snippet="这是一段很长的摘要内容",
            ),
        ]
        
        sorted_results = self.parser.sort_results(results, by="quality")
        
        # 质量高的应该排在前面
        assert sorted_results[0].quality_score() >= sorted_results[1].quality_score()
    
    def test_filter_by_quality(self):
        """测试质量过滤"""
        results = [
            ParsedResult(title="短", url="https://a.com"),
            ParsedResult(
                title="长标题",
                url="https://b.com",
                snippet="长摘要内容",
            ),
        ]
        
        filtered = self.parser.filter_results(results, min_quality=0.5)
        
        # 只保留高质量结果
        for r in filtered:
            assert r.quality_score() >= 0.5


class TestSearchIntegration:
    """搜索优化集成测试"""
    
    def test_full_search_flow(self):
        """测试完整搜索流程"""
        session = MagicMock()
        
        # 1. 构建查询
        builder = QueryBuilder()
        expansion = builder.expand("手机")
        assert expansion.original == "手机"
        
        # 2. 解析结果
        parser = ResultParser(session)
        result = ParsedResult(
            title="测试手机",
            url="https://example.com/phone",
            snippet="手机摘要",
        )
        # 短标题和摘要质量分数可能为0，只检查不为负数
        assert result.quality_score() >= 0
        
        # 3. 分页检测
        detector = PaginationDetector(session)
        session.eval_js.return_value = 5
        info = detector.detect()
        assert info.total_pages == 5
        
        # 4. 去重
        results = [
            ParsedResult(title="A", url="https://a.com"),
            ParsedResult(title="B", url="https://b.com"),
        ]
        unique = parser.deduplicate(results)
        assert len(unique) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
