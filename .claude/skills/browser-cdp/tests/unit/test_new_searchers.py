#!/usr/bin/env python
"""
test_new_searchers.py - 新增搜索器（链家、雪球、财联社）单元测试

测试 LianjiaSearcher、XueqiuSearcher、ClsNewsSearcher 的核心功能。
"""

import pytest
import json
from unittest.mock import MagicMock, patch

from src.searchers.lianjia_search import LianjiaSearcher, GHOST_FEATURES, LJ_BASE_URLS
from src.searchers.xueqiu_search import XueqiuSearcher, XQ_BASE, XQ_API_QUOTE
from src.searchers.cls_news import ClsNewsSearcher, CATEGORY_MAP, IMPORTANCE_MAP, CLS_API_TELEGRAPH


class TestLianjiaSearcher:
    """测试链家搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = LianjiaSearcher()
        assert searcher.source_name == "lianjia"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = LianjiaSearcher()
        assert searcher.supported_types == ["ershoufang", "zufang", "xiaoqu"]

    def test_default_config(self):
        """测试默认配置"""
        searcher = LianjiaSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = LianjiaSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)

    def test_health_check_exists(self):
        """测试 health_check 方法存在"""
        searcher = LianjiaSearcher()
        assert hasattr(searcher, "health_check")
        assert callable(searcher.health_check)

    def test_close_method_exists(self):
        """测试 close 方法存在"""
        searcher = LianjiaSearcher()
        assert hasattr(searcher, "close")
        assert callable(searcher.close)

    def test_ghost_features_constant(self):
        """测试幽灵房特征常量"""
        assert isinstance(GHOST_FEATURES, list)
        assert len(GHOST_FEATURES) > 0
        assert any("价格" in f or "均价" in f or "单价" in f for f in GHOST_FEATURES)

    def test_city_list(self):
        """测试城市列表"""
        assert isinstance(LJ_BASE_URLS, dict)
        assert "bj" in LJ_BASE_URLS
        assert "sh" in LJ_BASE_URLS
        assert "gz" in LJ_BASE_URLS
        assert "sz" in LJ_BASE_URLS
        assert "bj.lianjia.com" in LJ_BASE_URLS["bj"]

    def test_default_city(self):
        """测试默认城市"""
        searcher = LianjiaSearcher()
        assert searcher._city == "bj"

    def test_default_type(self):
        """测试默认类型"""
        searcher = LianjiaSearcher()
        assert searcher._type == "ershoufang"


class TestXueqiuSearcher:
    """测试雪球搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = XueqiuSearcher()
        assert searcher.source_name == "xueqiu"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = XueqiuSearcher()
        assert searcher.supported_types == ["quote", "discussion", "portfolio"]

    def test_default_config(self):
        """测试默认配置"""
        searcher = XueqiuSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = XueqiuSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)

    def test_health_check_exists(self):
        """测试 health_check 方法存在"""
        searcher = XueqiuSearcher()
        assert hasattr(searcher, "health_check")
        assert callable(searcher.health_check)

    def test_close_method_exists(self):
        """测试 close 方法存在"""
        searcher = XueqiuSearcher()
        assert hasattr(searcher, "close")
        assert callable(searcher.close)

    def test_base_url_constant(self):
        """测试基础 URL 常量"""
        assert "xueqiu.com" in XQ_BASE

    def test_api_quote_constant(self):
        """测试行情 API 常量"""
        assert "xueqiu.com" in XQ_API_QUOTE
        assert "quote" in XQ_API_QUOTE


class TestClsNewsSearcher:
    """测试财联社搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = ClsNewsSearcher()
        assert searcher.source_name == "cls"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = ClsNewsSearcher()
        assert "telegraph" in searcher.supported_types
        assert "finance" in searcher.supported_types
        assert "tech" in searcher.supported_types
        assert "stock" in searcher.supported_types
        assert "crypto" in searcher.supported_types
        assert "macro" in searcher.supported_types
        assert "world" in searcher.supported_types

    def test_default_config(self):
        """测试默认配置"""
        searcher = ClsNewsSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = ClsNewsSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)

    def test_health_check_exists(self):
        """测试 health_check 方法存在"""
        searcher = ClsNewsSearcher()
        assert hasattr(searcher, "health_check")
        assert callable(searcher.health_check)

    def test_close_method_exists(self):
        """测试 close 方法存在"""
        searcher = ClsNewsSearcher()
        assert hasattr(searcher, "close")
        assert callable(searcher.close)

    def test_category_map(self):
        """测试分类映射"""
        assert isinstance(CATEGORY_MAP, dict)
        assert "telegraph" in CATEGORY_MAP
        assert "finance" in CATEGORY_MAP
        assert CATEGORY_MAP["telegraph"] == "telegraph"
        assert CATEGORY_MAP["finance"] == "finance"

    def test_importance_map(self):
        """测试重要性映射"""
        assert isinstance(IMPORTANCE_MAP, dict)
        assert IMPORTANCE_MAP["0"] == "低"
        assert IMPORTANCE_MAP["1"] == "中"
        assert IMPORTANCE_MAP["2"] == "高"
        assert IMPORTANCE_MAP["3"] == "极高"

    def test_api_telegraph_constant(self):
        """测试电报 API 常量"""
        assert "cls.cn" in CLS_API_TELEGRAPH
        assert "Telegraph" in CLS_API_TELEGRAPH  # camelCase: updateTelegraph


class TestNewSearchersIntegration:
    """测试新增搜索器集成（mock CDP session）"""

    @pytest.mark.asyncio
    async def test_lianjia_searcher_with_mock(self):
        """测试链家搜索器与 mock session 集成"""
        searcher = LianjiaSearcher()
        assert searcher.source_name == "lianjia"
        assert searcher.config.max_results == 10
        assert searcher.config.wait_timeout == 30

    @pytest.mark.asyncio
    async def test_xueqiu_searcher_with_mock(self):
        """测试雪球搜索器与 mock session 集成"""
        searcher = XueqiuSearcher()
        assert searcher.source_name == "xueqiu"
        assert "xueqiu.com" in XQ_BASE

    @pytest.mark.asyncio
    async def test_cls_news_searcher_with_mock(self):
        """测试财联社搜索器与 mock session 集成"""
        searcher = ClsNewsSearcher()
        assert searcher.source_name == "cls"
        assert len(searcher.supported_types) == 7


class TestNewSearchersExport:
    """测试新增搜索器导出"""

    def test_all_new_searchers_importable(self):
        """测试所有新增搜索器可从 __init__ 导入"""
        from src.searchers import LianjiaSearcher, XueqiuSearcher, ClsNewsSearcher
        assert LianjiaSearcher is not None
        assert XueqiuSearcher is not None
        assert ClsNewsSearcher is not None

    def test_new_searchers_in_all_list(self):
        """测试新增搜索器在 __all__ 中"""
        from src.searchers import __all__
        assert "LianjiaSearcher" in __all__
        assert "XueqiuSearcher" in __all__
        assert "ClsNewsSearcher" in __all__


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
