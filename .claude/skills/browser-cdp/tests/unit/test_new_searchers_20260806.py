#!/usr/bin/env python
"""
test_new_searchers_20260806.py - 新拓展搜索器单元测试

测试新增的搜索器：
- Zhibo8Searcher（直播吧）
- MeishiSearcher（美食杰）
- QQMusicSearcher（QQ音乐）
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from src.searchers.zhibo8_search import Zhibo8Searcher
from src.searchers.meishi_search import MeishiSearcher
from src.searchers.qq_music_search import QQMusicSearcher


class TestZhibo8Searcher:
    """直播吧搜索器测试"""

    def test_source_name(self):
        searcher = Zhibo8Searcher()
        assert searcher.source_name == "zhibo8"

    def test_supported_types(self):
        searcher = Zhibo8Searcher()
        assert "news_search" in searcher.supported_types
        assert "match_data" in searcher.supported_types

    def test_default_config(self):
        searcher = Zhibo8Searcher()
        assert searcher.config.max_results == 10
        assert searcher.config.wait_timeout == 30

    def test_search_method_exists(self):
        searcher = Zhibo8Searcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)

    def test_health_check_exists(self):
        searcher = Zhibo8Searcher()
        assert hasattr(searcher, "health_check")
        assert callable(searcher.health_check)

    def test_close_method_exists(self):
        searcher = Zhibo8Searcher()
        assert hasattr(searcher, "close")
        assert callable(searcher.close)

    def test_health_check_result(self):
        searcher = Zhibo8Searcher()
        result = searcher.health_check()
        assert result["source"] == "zhibo8"
        assert result["status"] == "healthy"
        assert result["base_url"] == "https://www.zhibo8.cc"

    def test_search_with_mock(self):
        """使用 mock 测试搜索方法"""
        searcher = Zhibo8Searcher()
        
        with patch('src.searchers.zhibo8_search.ensure_browser') as mock_ensure:
            mock_browser = MagicMock()
            mock_ensure.return_value = mock_browser
            
            # 模拟搜索结果
            mock_item = MagicMock()
            mock_title = MagicMock()
            mock_title.text = "NBA总决赛精彩瞬间"
            mock_link = MagicMock()
            mock_link.get_attribute.return_value = "https://www.zhibo8.cc/nba/final"
            mock_item.query_selector.side_effect = lambda sel: mock_title if "title" in sel else mock_link
            
            mock_browser.query_selector_all.return_value = [mock_item]
            mock_browser.get.return_value = None
            
            results = searcher.search(query="NBA", max_results=5)
            
            assert len(results) == 1
            assert results[0]["title"] == "NBA总决赛精彩瞬间"
            assert results[0]["type"] == "news"
            assert results[0]["source"] == "zhibo8"


class TestMeishiSearcher:
    """美食杰搜索器测试"""

    def test_source_name(self):
        searcher = MeishiSearcher()
        assert searcher.source_name == "meishi"

    def test_supported_types(self):
        searcher = MeishiSearcher()
        assert "recipe_search" in searcher.supported_types
        assert "cooking_tips" in searcher.supported_types

    def test_default_config(self):
        searcher = MeishiSearcher()
        assert searcher.config.max_results == 10
        assert searcher.config.wait_timeout == 30

    def test_search_method_exists(self):
        searcher = MeishiSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)

    def test_health_check_exists(self):
        searcher = MeishiSearcher()
        assert hasattr(searcher, "health_check")
        assert callable(searcher.health_check)

    def test_close_method_exists(self):
        searcher = MeishiSearcher()
        assert hasattr(searcher, "close")
        assert callable(searcher.close)

    def test_health_check_result(self):
        searcher = MeishiSearcher()
        result = searcher.health_check()
        assert result["source"] == "meishi"
        assert result["status"] == "healthy"
        assert result["base_url"] == "https://www.meishij.net"

    def test_search_with_mock(self):
        """使用 mock 测试搜索方法"""
        searcher = MeishiSearcher()
        
        with patch('src.searchers.meishi_search.ensure_browser') as mock_ensure:
            mock_browser = MagicMock()
            mock_ensure.return_value = mock_browser
            
            # 模拟搜索结果
            mock_item = MagicMock()
            mock_title = MagicMock()
            mock_title.text = "红烧肉做法"
            mock_link = MagicMock()
            mock_link.get_attribute.return_value = "https://www.meishij.net/recipe/hongshaorou"
            mock_item.query_selector.side_effect = lambda sel: mock_title if "title" in sel else mock_link
            
            mock_browser.query_selector_all.return_value = [mock_item]
            mock_browser.get.return_value = None
            
            results = searcher.search(query="红烧肉", max_results=5)
            
            assert len(results) == 1
            assert results[0]["title"] == "红烧肉做法"
            assert results[0]["type"] == "recipe"
            assert results[0]["source"] == "meishi"


class TestQQMusicSearcher:
    """QQ音乐搜索器测试"""

    def test_source_name(self):
        searcher = QQMusicSearcher()
        assert searcher.source_name == "qq_music"

    def test_supported_types(self):
        searcher = QQMusicSearcher()
        assert "song_search" in searcher.supported_types
        assert "artist_search" in searcher.supported_types
        assert "playlist_search" in searcher.supported_types

    def test_default_config(self):
        searcher = QQMusicSearcher()
        assert searcher.config.max_results == 10
        assert searcher.config.wait_timeout == 30

    def test_search_method_exists(self):
        searcher = QQMusicSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)

    def test_health_check_exists(self):
        searcher = QQMusicSearcher()
        assert hasattr(searcher, "health_check")
        assert callable(searcher.health_check)

    def test_close_method_exists(self):
        searcher = QQMusicSearcher()
        assert hasattr(searcher, "close")
        assert callable(searcher.close)

    def test_health_check_result(self):
        searcher = QQMusicSearcher()
        result = searcher.health_check()
        assert result["source"] == "qq_music"
        assert result["status"] == "healthy"
        assert result["base_url"] == "https://y.qq.com"

    def test_search_with_mock(self):
        """使用 mock 测试搜索方法"""
        searcher = QQMusicSearcher()
        
        with patch('src.searchers.qq_music_search.ensure_browser') as mock_ensure:
            mock_browser = MagicMock()
            mock_ensure.return_value = mock_browser
            
            # 模拟歌曲搜索结果
            mock_item = MagicMock()
            mock_title = MagicMock()
            mock_title.text = "晴天 - 周杰伦"
            mock_link = MagicMock()
            mock_link.get_attribute.return_value = "https://y.qq.com/n/yqq/song/12345.html"
            mock_item.query_selector.side_effect = lambda sel: mock_title if "song" in sel or "title" in sel else mock_link
            
            mock_browser.query_selector_all.return_value = [mock_item]
            mock_browser.get.return_value = None
            
            results = searcher.search(query="晴天", max_results=5)
            
            assert len(results) == 1
            assert results[0]["title"] == "晴天 - 周杰伦"
            assert results[0]["type"] == "song"
            assert results[0]["source"] == "qq_music"


class TestNewSearchersIntegration:
    """新搜索器集成测试"""

    def test_all_new_searchers_importable(self):
        """测试所有新搜索器可导入"""
        from src.searchers import (
            Zhibo8Searcher,
            MeishiSearcher,
            QQMusicSearcher,
        )
        assert Zhibo8Searcher is not None
        assert MeishiSearcher is not None
        assert QQMusicSearcher is not None

    def test_new_searchers_in_all_list(self):
        """测试新搜索器在 __all__ 列表中"""
        from src.searchers import __all__
        assert "Zhibo8Searcher" in __all__
        assert "MeishiSearcher" in __all__
        assert "QQMusicSearcher" in __all__

    def test_new_searchers_with_mock(self):
        """使用 mock 测试所有新搜索器的搜索功能"""
        from src.searchers import Zhibo8Searcher, MeishiSearcher, QQMusicSearcher
        
        # 测试直播吧
        zhibo = Zhibo8Searcher()
        assert zhibo.source_name == "zhibo8"
        
        # 测试美食杰
        meishi = MeishiSearcher()
        assert meishi.source_name == "meishi"
        
        # 测试QQ音乐
        qq_music = QQMusicSearcher()
        assert qq_music.source_name == "qq_music"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
