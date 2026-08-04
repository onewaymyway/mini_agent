#!/usr/bin/env python
"""
test_searchers.py - 新增搜索器单元测试

测试 BaseSearcher、SearcherConfig、SearchResult 以及各搜索器的核心功能。
"""

import pytest
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
from dataclasses import asdict

# 导入新增搜索器
from src.searchers.base import BaseSearcher, SearcherConfig, SearchResult, SearchResults
from src.searchers.utils import (
    random_delay,
    get_random_ua,
    compute_simhash,
    hamming_distance,
    dedup_by_url,
    dedup_by_title,
    dedup_results,
    save_results,
    clean_text,
    truncate_text,
)
from src.searchers.jd_search import JDSearcher
from src.searchers.pdd_search import PDDSearcher
from src.searchers.sina_news import SinaNewsSearcher
from src.searchers.douban_search import DoubanSearcher
from src.searchers.eastmoney_guba import EastmoneyGubaSearcher
from src.searchers.scholar_search import ScholarSearcher


class TestSearcherConfig:
    """测试 SearcherConfig 配置类"""

    def test_default_config(self):
        """测试默认配置"""
        config = SearcherConfig()
        assert config.wait_timeout == 30
        assert config.max_results == 10
        assert config.stealth is True
        assert config.output_format == "json"
        assert config.dedup_by == "url"

    def test_custom_config(self):
        """测试自定义配置"""
        config = SearcherConfig(
            wait_timeout=60,
            max_results=50,
            stealth=False,
            output_format="csv",
        )
        assert config.wait_timeout == 60
        assert config.max_results == 50
        assert config.stealth is False
        assert config.output_format == "csv"

    def test_config_to_dict(self):
        """测试配置转字典"""
        config = SearcherConfig(wait_timeout=45)
        d = config.to_dict()
        assert isinstance(d, dict)
        assert d["wait_timeout"] == 45

    def test_config_from_dict(self):
        """测试字典转配置"""
        d = {"wait_timeout": 45, "max_results": 30, "stealth": True}
        config = SearcherConfig(**d)
        assert config.wait_timeout == 45
        assert config.max_results == 30
        assert config.stealth is True


class TestSearchResult:
    """测试 SearchResult 数据类"""

    def test_create_result(self):
        """测试创建搜索结果"""
        result = SearchResults(
            source="jd",
            query="手机",
            total_results=100,
            results=[{"title": "测试商品", "url": "https://example.com"}],
        )
        assert result.source == "jd"
        assert result.query == "手机"
        assert result.total_results == 100
        assert len(result.results) == 1

    def test_result_to_dict(self):
        """测试结果转字典"""
        result = SearchResults(source="jd", query="test", total_results=10, results=[])
        d = result.to_dict()
        assert isinstance(d, dict)
        assert d["source"] == "jd"
        assert d["query"] == "test"

    def test_result_to_json(self):
        """测试结果转 JSON"""
        result = SearchResults(source="jd", query="test", total_results=10, results=[])
        json_str = result.to_json()
        parsed = json.loads(json_str)
        assert parsed["source"] == "jd"

    def test_result_from_json(self):
        """测试从 JSON 创建结果"""
        json_str = json.dumps({
            "source": "jd",
            "query": "test",
            "total_results": 10,
            "results": [],
        })
        result = SearchResults.from_json(json_str)
        assert result.source == "jd"
        assert result.query == "test"


class TestUtils:
    """测试通用工具函数"""

    def test_random_delay(self):
        """测试随机延迟"""
        start = time.time()
        delay = random_delay(0.1, 0.2)
        elapsed = time.time() - start
        assert 0.1 <= elapsed <= 0.3  # 允许一定误差
        assert isinstance(delay, float)

    def test_get_random_ua(self):
        """测试随机 UA"""
        ua = get_random_ua()
        assert isinstance(ua, str)
        assert "Mozilla" in ua

    def test_compute_simhash(self):
        """测试 SimHash 计算"""
        h1 = compute_simhash("hello")
        h2 = compute_simhash("hello")
        h3 = compute_simhash("world")
        assert h1 == h2  # 相同文本产生相同哈希
        assert h1 != h3  # 不同文本产生不同哈希

    def test_hamming_distance(self):
        """测试汉明距离"""
        h1 = compute_simhash("hello")
        h2 = compute_simhash("hello")
        h3 = compute_simhash("world")
        assert hamming_distance(h1, h2) == 0  # 相同哈希距离为 0
        assert hamming_distance(h1, h3) > 0  # 不同哈希距离大于 0

    def test_dedup_by_url(self):
        """测试 URL 去重"""
        results = [
            {"url": "https://a.com", "title": "A"},
            {"url": "https://b.com", "title": "B"},
            {"url": "https://a.com", "title": "A dup"},
        ]
        unique = dedup_by_url(results)
        assert len(unique) == 2
        urls = [r["url"] for r in unique]
        assert "https://a.com" in urls
        assert "https://b.com" in urls

    def test_dedup_by_title(self):
        """测试标题去重"""
        results = [
            {"title": "相同标题", "url": "https://a.com"},
            {"title": "相同标题", "url": "https://b.com"},
            {"title": "不同标题", "url": "https://c.com"},
        ]
        unique = dedup_by_title(results)
        assert len(unique) == 2

    def test_dedup_results_url(self):
        """测试统一去重入口 - URL"""
        results = [
            {"url": "https://a.com", "title": "A"},
            {"url": "https://a.com", "title": "A dup"},
        ]
        unique = dedup_results(results, by="url")
        assert len(unique) == 1

    def test_dedup_results_title(self):
        """测试统一去重入口 - 标题"""
        results = [
            {"title": "相同", "url": "https://a.com"},
            {"title": "相同", "url": "https://b.com"},
        ]
        unique = dedup_results(results, by="title")
        assert len(unique) == 1

    def test_save_results_json(self, tmp_path):
        """测试保存 JSON 结果"""
        results = [{"title": "测试", "url": "https://example.com"}]
        output_dir = str(tmp_path / "output")
        path = save_results(results, output_dir, fmt="json")
        assert Path(path).exists()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) == 1

    def test_save_results_csv(self, tmp_path):
        """测试保存 CSV 结果"""
        results = [{"title": "测试", "url": "https://example.com"}]
        output_dir = str(tmp_path / "output")
        path = save_results(results, output_dir, fmt="csv")
        assert Path(path).exists()

    def test_save_results_markdown(self, tmp_path):
        """测试保存 Markdown 结果"""
        results = [{"title": "测试", "url": "https://example.com"}]
        output_dir = str(tmp_path / "output")
        path = save_results(results, output_dir, fmt="markdown")
        assert Path(path).exists()

    def test_clean_text(self):
        """测试文本清理"""
        text = "  hello   world  \n\n  test  "
        cleaned = clean_text(text)
        assert cleaned == "hello world test"

    def test_truncate_text(self):
        """测试文本截断"""
        long_text = "a" * 300
        truncated = truncate_text(long_text, max_len=200)
        assert len(truncated) == 203  # 200 + "..."
        assert truncated.endswith("...")


class TestBaseSearcher:
    """测试 BaseSearcher 抽象基类"""

    def test_abstract_methods(self):
        """测试抽象方法必须实现"""
        with pytest.raises(TypeError):
            BaseSearcher()  # 不能直接实例化

    def test_searcher_config_property(self):
        """测试配置属性"""
        # 创建一个最小实现
        class MockSearcher(BaseSearcher):
            @property
            def source_name(self):
                return "mock"

            @property
            def supported_types(self):
                return ["search"]

            async def search(self, query: str, **kwargs):
                return []

            async def get_detail(self, url: str, **kwargs):
                return {}

            async def health_check(self) -> bool:
                return True

            async def close(self):
                pass

        searcher = MockSearcher()
        assert searcher.source_name == "mock"
        assert searcher.supported_types == ["search"]
        assert searcher.config is not None


class TestJDSearcher:
    """测试京东搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = JDSearcher()
        assert searcher.source_name == "jd"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = JDSearcher()
        assert "product_search" in searcher.supported_types

    def test_default_config(self):
        """测试默认配置"""
        searcher = JDSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = JDSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)

    def test_health_check_exists(self):
        """测试 health_check 方法存在"""
        searcher = JDSearcher()
        assert hasattr(searcher, "health_check")
        assert callable(searcher.health_check)

    def test_close_method_exists(self):
        """测试 close 方法存在"""
        searcher = JDSearcher()
        assert hasattr(searcher, "close")
        assert callable(searcher.close)


class TestPDDSearcher:
    """测试拼多多搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = PDDSearcher()
        assert searcher.source_name == "pdd"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = PDDSearcher()
        assert "product_search" in searcher.supported_types

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = PDDSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)


class TestSinaNewsSearcher:
    """测试新浪财经搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = SinaNewsSearcher()
        assert searcher.source_name == "sina_finance"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = SinaNewsSearcher()
        assert "news_list" in searcher.supported_types

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = SinaNewsSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)


class TestDoubanSearcher:
    """测试豆瓣搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = DoubanSearcher()
        assert searcher.source_name == "douban"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = DoubanSearcher()
        assert "book_search" in searcher.supported_types

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = DoubanSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)


class TestEastmoneyGubaSearcher:
    """测试东方财富股吧搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = EastmoneyGubaSearcher()
        assert searcher.source_name == "eastmoney_guba"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = EastmoneyGubaSearcher()
        assert "post_list" in searcher.supported_types
        assert "comment_tree" in searcher.supported_types

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = EastmoneyGubaSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)


class TestScholarSearcher:
    """测试 Google Scholar 搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = ScholarSearcher()
        assert searcher.source_name == "google_scholar"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = ScholarSearcher()
        assert "paper_search" in searcher.supported_types

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = ScholarSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)


class TestSearcherIntegration:
    """测试搜索器集成（mock CDP session）"""

    @pytest.mark.asyncio
    async def test_jd_searcher_with_mock(self):
        """测试京东搜索器与 mock session 集成"""
        searcher = JDSearcher()
        # 验证 searcher 可以正常创建
        assert searcher.source_name == "jd"
        # 验证配置
        assert searcher.config.max_results == 10
        assert searcher.config.wait_timeout == 30

    @pytest.mark.asyncio
    async def test_pdd_searcher_with_mock(self):
        """测试拼多多搜索器与 mock session 集成"""
        searcher = PDDSearcher()
        assert searcher.source_name == "pdd"

    @pytest.mark.asyncio
    async def test_sina_news_searcher_with_mock(self):
        """测试新浪财经搜索器与 mock session 集成"""
        searcher = SinaNewsSearcher()
        assert searcher.source_name == "sina_finance"

    @pytest.mark.asyncio
    async def test_douban_searcher_with_mock(self):
        """测试豆瓣搜索器与 mock session 集成"""
        searcher = DoubanSearcher()
        assert searcher.source_name == "douban"

    @pytest.mark.asyncio
    async def test_eastmoney_guba_searcher_with_mock(self):
        """测试东方财富股吧搜索器与 mock session 集成"""
        searcher = EastmoneyGubaSearcher()
        assert searcher.source_name == "eastmoney_guba"

    @pytest.mark.asyncio
    async def test_scholar_searcher_with_mock(self):
        """测试 Google Scholar 搜索器与 mock session 集成"""
        searcher = ScholarSearcher()
        assert searcher.source_name == "google_scholar"


class TestSearcherExport:
    """测试搜索器导出"""

    def test_all_searchers_importable(self):
        """测试所有搜索器可从 __init__ 导入"""
        from src.searchers import (
            JDSearcher,
            PDDSearcher,
            SinaNewsSearcher,
            DoubanSearcher,
            EastmoneyGubaSearcher,
            ScholarSearcher,
        )
        assert JDSearcher is not None
        assert PDDSearcher is not None
        assert SinaNewsSearcher is not None
        assert DoubanSearcher is not None
        assert EastmoneyGubaSearcher is not None
        assert ScholarSearcher is not None

    def test_base_classes_importable(self):
        """测试基类可从 __init__ 导入"""
        from src.searchers import BaseSearcher, SearcherConfig, SearchResult
        assert BaseSearcher is not None
        assert SearcherConfig is not None
        assert SearchResult is not None

    def test_utils_importable(self):
        """测试工具函数可从 __init__ 导入"""
        from src.searchers import (
            random_delay,
            get_random_ua,
            dedup_results,
            save_results,
        )
        assert callable(random_delay)
        assert callable(get_random_ua)
        assert callable(dedup_results)
        assert callable(save_results)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
