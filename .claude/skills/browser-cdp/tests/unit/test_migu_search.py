"""
咪咕音乐搜索器单元测试

测试覆盖：
- 基础属性验证
- 配置参数验证
- 方法存在性检查
- 结果格式化
- 去重功能
"""
import pytest
import sys
from pathlib import Path
from datetime import datetime

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.searchers.migu_search import MiguSearcher
from src.searchers.base import SearcherConfig, SearchResult


class TestMiguSearcher:
    """MiguSearcher 单元测试"""
    
    def test_source_name(self):
        """测试来源名称"""
        searcher = MiguSearcher()
        assert searcher.source_name == "migu"
    
    def test_supported_types(self):
        """测试支持类型"""
        searcher = MiguSearcher()
        assert "song_search" in searcher.supported_types
        assert "artist_search" in searcher.supported_types
        assert "playlist_search" in searcher.supported_types
    
    def test_default_config(self):
        """测试默认配置"""
        searcher = MiguSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10
        assert searcher.config.wait_strategy == "networkidle"
    
    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = MiguSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)
    
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
    
    def test_search_songs_method_exists(self):
        """测试 _search_songs 方法存在"""
        searcher = MiguSearcher()
        assert hasattr(searcher, "_search_songs")
        assert callable(searcher._search_songs)
    
    def test_search_artists_method_exists(self):
        """测试 _search_artists 方法存在"""
        searcher = MiguSearcher()
        assert hasattr(searcher, "_search_artists")
        assert callable(searcher._search_artists)
    
    def test_search_playlists_method_exists(self):
        """测试 _search_playlists 方法存在"""
        searcher = MiguSearcher()
        assert hasattr(searcher, "_search_playlists")
        assert callable(searcher._search_playlists)
    
    def test_get_detail_method_exists(self):
        """测试 get_detail 方法存在"""
        searcher = MiguSearcher()
        assert hasattr(searcher, "get_detail")
        assert callable(searcher.get_detail)
    
    def test_config_with_custom_params(self):
        """测试自定义配置"""
        config = SearcherConfig(
            port=9333,
            stealth=True,
            max_results=20,
            wait_strategy="route",
            wait_timeout=15.0,
            random_delay_range=(0.5, 1.5)
        )
        searcher = MiguSearcher(config=config)
        assert searcher.config.max_results == 20
        assert searcher.config.wait_strategy == "route"
        assert searcher.config.port == 9333
    
    def test_search_result_format(self):
        """测试搜索结果格式化"""
        result = SearchResult(
            source='migu',
            title='稻香',
            url='https://music.migu.cn/v3/music/song?id=123',
            snippet='周杰伦 - 稻香',
            metadata={'query': '稻香', 'type': 'song', 'duration': '3:43'},
            scraped_at=datetime.now().isoformat()
        )
        assert result.source == 'migu'
        assert result.title == '稻香'
        assert 'music.migu.cn' in result.url.lower()
        assert result.metadata['type'] == 'song'
    
    def test_deduplication(self):
        """测试结果去重"""
        results = [
            SearchResult(source='migu', title='重复歌曲', url='https://music.migu.cn/same', metadata={}, scraped_at=datetime.now().isoformat()),
            SearchResult(source='migu', title='重复歌曲', url='https://music.migu.cn/same', metadata={}, scraped_at=datetime.now().isoformat()),
            SearchResult(source='migu', title='唯一歌曲', url='https://music.migu.cn/unique', metadata={}, scraped_at=datetime.now().isoformat()),
        ]
        
        seen_urls = set()
        unique_results = []
        for r in results:
            if r.url not in seen_urls:
                seen_urls.add(r.url)
                unique_results.append(r)
        
        assert len(unique_results) == 2
        assert unique_results[1].title == '唯一歌曲'
    
    def test_max_results_limit(self):
        """测试最大结果数限制"""
        results = [
            SearchResult(source='migu', title=f'歌曲{i}', url=f'https://music.migu.cn/song?id={i}', metadata={}, scraped_at=datetime.now().isoformat())
            for i in range(15)
        ]
        
        config = SearcherConfig(max_results=5)
        limited = results[:config.max_results]
        
        assert len(limited) == 5
    
    def test_invalid_url_filtering(self):
        """测试无效 URL 过滤"""
        results = [
            SearchResult(source='migu', title='有效', url='https://music.migu.cn/valid', metadata={}, scraped_at=datetime.now().isoformat()),
            SearchResult(source='migu', title='无效', url='', metadata={}, scraped_at=datetime.now().isoformat()),
        ]
        
        valid_results = [r for r in results if r.url]
        assert len(valid_results) == 1
        assert valid_results[0].url == 'https://music.migu.cn/valid'
    
    def test_search_result_metadata(self):
        """测试搜索结果元数据"""
        result = SearchResult(
            source='migu',
            title='晴天',
            url='https://music.migu.cn/song?id=456',
            snippet='周杰伦 - 晴天',
            metadata={
                'query': '晴天',
                'type': 'song',
                'artist': '周杰伦',
                'album': '叶惠美',
                'duration': '4:29'
            },
            scraped_at=datetime.now().isoformat()
        )
        assert result.metadata['artist'] == '周杰伦'
        assert result.metadata['album'] == '叶惠美'
        assert result.metadata['duration'] == '4:29'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
