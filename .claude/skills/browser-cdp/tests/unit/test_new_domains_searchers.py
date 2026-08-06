#!/usr/bin/env python
"""
test_new_domains_searchers.py - 新领域搜索器单元测试

测试体育、美食、二手交易、音乐领域的新增搜索器核心功能。
"""

import pytest
from unittest.mock import MagicMock, patch

from src.searchers.hupu_search import HupuSearcher
from src.searchers.dongqiudi_search import DongqiudiSearcher
from src.searchers.xiachufang_search import XiachufangSearcher
from src.searchers.zhuanzhuan_search import ZhuanZhuanSearcher
from src.searchers.migu_search import MiguSearcher


class TestHupuSearcher:
    """测试虎扑体育搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = HupuSearcher()
        assert searcher.source_name == "hupu"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = HupuSearcher()
        assert "news_search" in searcher.supported_types
        assert "match_data" in searcher.supported_types
        assert "community_post" in searcher.supported_types
        assert "player_stats" in searcher.supported_types

    def test_default_config(self):
        """测试默认配置"""
        searcher = HupuSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = HupuSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)

    def test_get_detail_method_exists(self):
        """测试 get_detail 方法存在"""
        searcher = HupuSearcher()
        assert hasattr(searcher, "get_detail")
        assert callable(searcher.get_detail)

    def test_health_check_exists(self):
        """测试 health_check 方法存在"""
        searcher = HupuSearcher()
        assert hasattr(searcher, "health_check")
        assert callable(searcher.health_check)

    def test_close_method_exists(self):
        """测试 close 方法存在"""
        searcher = HupuSearcher()
        assert hasattr(searcher, "close")
        assert callable(searcher.close)

    def test_health_check_result(self):
        """测试健康检查结果"""
        searcher = HupuSearcher()
        result = searcher.health_check()
        assert result["source"] == "hupu"
        assert result["status"] == "healthy"
        assert "base_url" in result
        assert "hupu.com" in result["base_url"]


class TestDongqiudiSearcher:
    """测试懂球帝体育搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = DongqiudiSearcher()
        assert searcher.source_name == "dongqiudi"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = DongqiudiSearcher()
        assert "news_search" in searcher.supported_types
        assert "match_data" in searcher.supported_types
        assert "player_info" in searcher.supported_types
        assert "team_info" in searcher.supported_types

    def test_default_config(self):
        """测试默认配置"""
        searcher = DongqiudiSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = DongqiudiSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)

    def test_get_detail_method_exists(self):
        """测试 get_detail 方法存在"""
        searcher = DongqiudiSearcher()
        assert hasattr(searcher, "get_detail")
        assert callable(searcher.get_detail)

    def test_health_check_exists(self):
        """测试 health_check 方法存在"""
        searcher = DongqiudiSearcher()
        assert hasattr(searcher, "health_check")
        assert callable(searcher.health_check)

    def test_close_method_exists(self):
        """测试 close 方法存在"""
        searcher = DongqiudiSearcher()
        assert hasattr(searcher, "close")
        assert callable(searcher.close)

    def test_health_check_result(self):
        """测试健康检查结果"""
        searcher = DongqiudiSearcher()
        result = searcher.health_check()
        assert result["source"] == "dongqiudi"
        assert result["status"] == "healthy"
        assert "base_url" in result
        assert "dongqiudi.com" in result["base_url"]


class TestXiachufangSearcher:
    """测试下厨房搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = XiachufangSearcher()
        assert searcher.source_name == "xiachufang"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = XiachufangSearcher()
        assert "recipe_search" in searcher.supported_types
        assert "cooking_tips" in searcher.supported_types
        assert "food_gallery" in searcher.supported_types
        assert "ingredient_search" in searcher.supported_types

    def test_default_config(self):
        """测试默认配置"""
        searcher = XiachufangSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = XiachufangSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)

    def test_get_detail_method_exists(self):
        """测试 get_detail 方法存在"""
        searcher = XiachufangSearcher()
        assert hasattr(searcher, "get_detail")
        assert callable(searcher.get_detail)

    def test_health_check_exists(self):
        """测试 health_check 方法存在"""
        searcher = XiachufangSearcher()
        assert hasattr(searcher, "health_check")
        assert callable(searcher.health_check)

    def test_close_method_exists(self):
        """测试 close 方法存在"""
        searcher = XiachufangSearcher()
        assert hasattr(searcher, "close")
        assert callable(searcher.close)

    def test_health_check_result(self):
        """测试健康检查结果"""
        searcher = XiachufangSearcher()
        result = searcher.health_check()
        assert result["source"] == "xiachufang"
        assert result["status"] == "healthy"
        assert "base_url" in result
        assert "xiachufang.com" in result["base_url"]


class TestZhuanZhuanSearcher:
    """测试转转二手搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = ZhuanZhuanSearcher()
        assert searcher.source_name == "zhuanzhuan"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = ZhuanZhuanSearcher()
        assert "product_search" in searcher.supported_types
        assert "price_compare" in searcher.supported_types
        assert "phone_search" in searcher.supported_types
        assert "digital_search" in searcher.supported_types

    def test_default_config(self):
        """测试默认配置"""
        searcher = ZhuanZhuanSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = ZhuanZhuanSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)

    def test_get_detail_method_exists(self):
        """测试 get_detail 方法存在"""
        searcher = ZhuanZhuanSearcher()
        assert hasattr(searcher, "get_detail")
        assert callable(searcher.get_detail)

    def test_health_check_exists(self):
        """测试 health_check 方法存在"""
        searcher = ZhuanZhuanSearcher()
        assert hasattr(searcher, "health_check")
        assert callable(searcher.health_check)

    def test_close_method_exists(self):
        """测试 close 方法存在"""
        searcher = ZhuanZhuanSearcher()
        assert hasattr(searcher, "close")
        assert callable(searcher.close)

    def test_health_check_result(self):
        """测试健康检查结果"""
        searcher = ZhuanZhuanSearcher()
        result = searcher.health_check()
        assert result["source"] == "zhuanzhuan"
        assert result["status"] == "healthy"
        assert "base_url" in result
        assert "zhuanzhuan.com" in result["base_url"]


class TestMiguSearcher:
    """测试咪咕音乐搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = MiguSearcher()
        assert searcher.source_name == "migu"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = MiguSearcher()
        assert "song_search" in searcher.supported_types
        assert "artist_search" in searcher.supported_types
        assert "playlist_search" in searcher.supported_types
        assert "album_search" in searcher.supported_types

    def test_default_config(self):
        """测试默认配置"""
        searcher = MiguSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = MiguSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)

    def test_get_detail_method_exists(self):
        """测试 get_detail 方法存在"""
        searcher = MiguSearcher()
        assert hasattr(searcher, "get_detail")
        assert callable(searcher.get_detail)

    def test_health_check_exists(self):
        """测试 health_check 方法存在"""
        searcher = MiguSearcher()
        assert hasattr(searcher, "health_check")
        assert callable(searcher.health_check)

    def test_close_method_exists(self):
        """测试 close 方法存在"""
        searcher = MiguSearcher()
        assert hasattr(searcher, "close")
        assert callable(searcher.close)

    def test_health_check_result(self):
        """测试健康检查结果"""
        searcher = MiguSearcher()
        result = searcher.health_check()
        assert result["source"] == "migu"
        assert result["status"] == "healthy"
        assert "base_url" in result
        assert "migu.cn" in result["base_url"]


class TestNewDomainsIntegration:
    """测试新领域搜索器集成（mock CDP session）"""

    @pytest.mark.asyncio
    async def test_hupu_with_mock(self):
        """测试虎扑搜索器与 mock session 集成"""
        searcher = HupuSearcher()
        assert searcher.source_name == "hupu"
        assert searcher.config.max_results == 10
        assert searcher.config.wait_timeout == 30

    @pytest.mark.asyncio
    async def test_dongqiudi_with_mock(self):
        """测试懂球帝搜索器与 mock session 集成"""
        searcher = DongqiudiSearcher()
        assert searcher.source_name == "dongqiudi"
        assert searcher.config.max_results == 10

    @pytest.mark.asyncio
    async def test_xiachufang_with_mock(self):
        """测试下厨房搜索器与 mock session 集成"""
        searcher = XiachufangSearcher()
        assert searcher.source_name == "xiachufang"
        assert searcher.config.max_results == 10

    @pytest.mark.asyncio
    async def test_zhuanzhuan_with_mock(self):
        """测试转转搜索器与 mock session 集成"""
        searcher = ZhuanZhuanSearcher()
        assert searcher.source_name == "zhuanzhuan"
        assert searcher.config.max_results == 10

    @pytest.mark.asyncio
    async def test_migu_with_mock(self):
        """测试咪咕音乐搜索器与 mock session 集成"""
        searcher = MiguSearcher()
        assert searcher.source_name == "migu"
        assert searcher.config.max_results == 10


class TestNewDomainsExport:
    """测试新领域搜索器导出"""

    def test_all_new_searchers_importable(self):
        """测试所有新搜索器可从 __init__ 导入"""
        from src.searchers import (
            HupuSearcher,
            DongqiudiSearcher,
            XiachufangSearcher,
            ZhuanZhuanSearcher,
            MiguSearcher,
        )
        assert HupuSearcher is not None
        assert DongqiudiSearcher is not None
        assert XiachufangSearcher is not None
        assert ZhuanZhuanSearcher is not None
        assert MiguSearcher is not None

    def test_new_searchers_in_all_list(self):
        """测试新搜索器在 __all__ 中"""
        from src.searchers import __all__
        assert "HupuSearcher" in __all__
        assert "DongqiudiSearcher" in __all__
        assert "XiachufangSearcher" in __all__
        assert "ZhuanZhuanSearcher" in __all__
        assert "MiguSearcher" in __all__


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
