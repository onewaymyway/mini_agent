#!/usr/bin/env python
"""
test_xiaohongshu_search.py - 小红书搜索器单元测试

测试 XiaohongshuSearcher 的核心功能。
"""

import pytest
from src.searchers.xiaohongshu_search import XiaohongshuSearcher
from src.searchers.base import SearcherConfig


class TestXiaohongshuSearcher:
    """测试小红书搜索器"""

    def test_source_name(self):
        """测试来源名称"""
        searcher = XiaohongshuSearcher()
        assert searcher.source_name == "xiaohongshu"

    def test_supported_types(self):
        """测试支持的数据类型"""
        searcher = XiaohongshuSearcher()
        assert "note" in searcher.supported_types
        assert "user" in searcher.supported_types
        assert "product" in searcher.supported_types
        assert "topic" in searcher.supported_types

    def test_default_config(self):
        """测试默认配置"""
        searcher = XiaohongshuSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10
        assert searcher.config.stealth == True

    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = XiaohongshuSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)

    def test_get_detail_method_exists(self):
        """测试 get_detail 方法存在"""
        searcher = XiaohongshuSearcher()
        assert hasattr(searcher, "get_detail")
        assert callable(searcher.get_detail)

    def test_search_by_topic_method_exists(self):
        """测试 search_by_topic 方法存在"""
        searcher = XiaohongshuSearcher()
        assert hasattr(searcher, "search_by_topic")
        assert callable(searcher.search_by_topic)

    def test_handle_captcha_method_exists(self):
        """测试 handle_captcha 方法存在"""
        searcher = XiaohongshuSearcher()
        assert hasattr(searcher, "handle_captcha")
        assert callable(searcher.handle_captcha)

    def test_simulate_human_behavior_method_exists(self):
        """测试 _simulate_human_behavior 方法存在"""
        searcher = XiaohongshuSearcher()
        assert hasattr(searcher, "_simulate_human_behavior")
        assert callable(searcher._simulate_human_behavior)

    def test_extract_results_method_exists(self):
        """测试 _extract_results 方法存在"""
        searcher = XiaohongshuSearcher()
        assert hasattr(searcher, "_extract_results")
        assert callable(searcher._extract_results)

    def test_config_validation(self):
        """测试配置验证"""
        config = SearcherConfig(
            max_results=50,
            wait_timeout=15,
            random_delay_range=(0.3, 1.0),
            stealth=True
        )
        
        assert config.max_results == 50
        assert config.stealth is True
        assert config.random_delay_range == (0.3, 1.0)

    def test_init_with_custom_config(self):
        """测试自定义配置初始化"""
        config = SearcherConfig(
            max_results=20,
            port=9333,
            stealth=True
        )
        searcher = XiaohongshuSearcher(config=config)
        assert searcher.config.max_results == 20
        assert searcher.config.port == 9333


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
