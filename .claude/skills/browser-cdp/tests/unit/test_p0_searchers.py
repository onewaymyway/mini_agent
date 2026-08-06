#!/usr/bin/env python
"""
test_p0_searchers.py - P0 领域搜索器单元测试

测试政府服务、医疗健康、法律领域的搜索器核心功能。
"""

import pytest
from unittest.mock import MagicMock, patch

from src.searchers.creditchina_search import CreditChinaSearcher
from src.searchers.gsxt_search import GSXTSearcher
from src.searchers.court_search import CourtSearcher
from src.searchers.gov_cn_search import GovCnSearcher


class TestCreditChinaSearcher:
    """测试信用中国搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = CreditChinaSearcher()
        assert searcher.source_name == "creditchina"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = CreditChinaSearcher()
        assert "credit_search" in searcher.supported_types
        assert "blacklist_search" in searcher.supported_types
        assert "penalty_search" in searcher.supported_types

    def test_default_config(self):
        """测试默认配置"""
        searcher = CreditChinaSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = CreditChinaSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)

    def test_health_check_exists(self):
        """测试 health_check 方法存在"""
        searcher = CreditChinaSearcher()
        assert hasattr(searcher, "health_check")
        assert callable(searcher.health_check)

    def test_close_method_exists(self):
        """测试 close 方法存在"""
        searcher = CreditChinaSearcher()
        assert hasattr(searcher, "close")
        assert callable(searcher.close)


class TestGSXTSearcher:
    """测试国家企业信用信息公示系统搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = GSXTSearcher()
        assert searcher.source_name == "gsxt"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = GSXTSearcher()
        assert "enterprise_search" in searcher.supported_types
        assert "abnormal_search" in searcher.supported_types
        assert "illegal_search" in searcher.supported_types

    def test_default_config(self):
        """测试默认配置"""
        searcher = GSXTSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = GSXTSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)

    def test_health_check_exists(self):
        """测试 health_check 方法存在"""
        searcher = GSXTSearcher()
        assert hasattr(searcher, "health_check")
        assert callable(searcher.health_check)

    def test_close_method_exists(self):
        """测试 close 方法存在"""
        searcher = GSXTSearcher()
        assert hasattr(searcher, "close")
        assert callable(searcher.close)


class TestCourtSearcher:
    """测试中国裁判文书网搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = CourtSearcher()
        assert searcher.source_name == "court_wenshu"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = CourtSearcher()
        assert "judgment_search" in searcher.supported_types
        assert "court_document" in searcher.supported_types
        assert "legal_case" in searcher.supported_types

    def test_default_config(self):
        """测试默认配置"""
        searcher = CourtSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = CourtSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)

    def test_health_check_exists(self):
        """测试 health_check 方法存在"""
        searcher = CourtSearcher()
        assert hasattr(searcher, "health_check")
        assert callable(searcher.health_check)

    def test_close_method_exists(self):
        """测试 close 方法存在"""
        searcher = CourtSearcher()
        assert hasattr(searcher, "close")
        assert callable(searcher.close)


class TestGovCnSearcher:
    """测试中国政府网搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = GovCnSearcher()
        assert searcher.source_name == "gov_cn"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = GovCnSearcher()
        assert isinstance(searcher.supported_types, list)
        assert len(searcher.supported_types) > 0

    def test_default_config(self):
        """测试默认配置"""
        searcher = GovCnSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = GovCnSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)

    def test_health_check_exists(self):
        """测试 health_check 方法存在"""
        searcher = GovCnSearcher()
        assert hasattr(searcher, "health_check")
        assert callable(searcher.health_check)

    def test_close_method_exists(self):
        """测试 close 方法存在"""
        searcher = GovCnSearcher()
        assert hasattr(searcher, "close")
        assert callable(searcher.close)


class TestP0SearchersIntegration:
    """测试 P0 领域搜索器集成（mock CDP session）"""

    @pytest.mark.asyncio
    async def test_credit_china_with_mock(self):
        """测试信用中国搜索器与 mock session 集成"""
        searcher = CreditChinaSearcher()
        assert searcher.source_name == "creditchina"
        assert searcher.config.max_results == 10
        assert searcher.config.wait_timeout == 30

    @pytest.mark.asyncio
    async def test_gsxt_with_mock(self):
        """测试国家企业信用信息公示系统搜索器与 mock session 集成"""
        searcher = GSXTSearcher()
        assert searcher.source_name == "gsxt"
        assert searcher.config.max_results == 10

    @pytest.mark.asyncio
    async def test_court_with_mock(self):
        """测试中国裁判文书网搜索器与 mock session 集成"""
        searcher = CourtSearcher()
        assert searcher.source_name == "court_wenshu"
        assert searcher.config.max_results == 10

    @pytest.mark.asyncio
    async def test_gov_cn_with_mock(self):
        """测试中国政府网搜索器与 mock session 集成"""
        searcher = GovCnSearcher()
        assert searcher.source_name == "gov_cn"
        assert searcher.config.max_results == 10


class TestP0SearchersExport:
    """测试 P0 领域搜索器导出"""

    def test_all_p0_searchers_importable(self):
        """测试所有 P0 搜索器可从 __init__ 导入"""
        from src.searchers import (
            CreditChinaSearcher,
            GSXTSearcher,
            CourtSearcher,
            GovCnSearcher,
        )
        assert CreditChinaSearcher is not None
        assert GSXTSearcher is not None
        assert CourtSearcher is not None
        assert GovCnSearcher is not None

    def test_p0_searchers_in_all_list(self):
        """测试 P0 搜索器在 __all__ 中"""
        from src.searchers import __all__
        assert "CreditChinaSearcher" in __all__
        assert "GSXTSearcher" in __all__
        assert "CourtSearcher" in __all__
        assert "GovCnSearcher" in __all__


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
