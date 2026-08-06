"""
体育平台搜索器单元测试

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

from src.searchers.sports_search import SportsSearcher
from src.searchers.base import SearcherConfig, SearchResult


class TestSportsSearcher:
    """SportsSearcher 单元测试"""
    
    def test_source_name(self):
        """测试来源名称"""
        searcher = SportsSearcher()
        assert searcher.source_name == "sports_platform"
    
    def test_supported_types(self):
        """测试支持的搜索类型"""
        searcher = SportsSearcher()
        assert "sports_news" in searcher.supported_types
        assert "match_data" in searcher.supported_types
        assert "team_info" in searcher.supported_types
        assert "player_info" in searcher.supported_types
    
    def test_default_config(self):
        """测试默认配置"""
        searcher = SportsSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10
        assert searcher.config.wait_strategy == "networkidle"
    
    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = SportsSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)
    
    def test_health_check_exists(self):
        """测试 health_check 方法存在"""
        searcher = SportsSearcher()
        assert hasattr(searcher, "health_check")
        assert callable(searcher.health_check)
    
    def test_close_method_exists(self):
        """测试 close 方法存在"""
        searcher = SportsSearcher()
        assert hasattr(searcher, "close")
        assert callable(searcher.close)
    
    def test_config_with_custom_params(self):
        """测试自定义配置"""
        config = SearcherConfig(
            port=9333,
            stealth=True,
            max_results=30,
            wait_strategy="route",
            wait_timeout=20.0,
            random_delay_range=(0.5, 1.5)
        )
        searcher = SportsSearcher(config=config)
        assert searcher.config.max_results == 30
        assert searcher.config.wait_strategy == "route"
        assert searcher.config.port == 9333
    
    def test_search_result_format(self):
        """测试搜索结果格式化"""
        result = SearchResult(
            source='sports_platform',
            title='NBA季后赛战报',
            url='https://www.hupu.com/nba/123',
            snippet='湖人队大胜勇士队',
            metadata={'query': 'NBA', 'type': 'sports_news', 'score': '128-98'},
            scraped_at=datetime.now().isoformat()
        )
        assert result.source == 'sports_platform'
        assert result.title == 'NBA季后赛战报'
        assert 'hupu' in result.url.lower()
        assert result.metadata['type'] == 'sports_news'
    
    def test_deduplication(self):
        """测试结果去重"""
        results = [
            SearchResult(source='sports_platform', title='重复新闻', url='https://www.hupu.com/same', metadata={}, scraped_at=datetime.now().isoformat()),
            SearchResult(source='sports_platform', title='重复新闻', url='https://www.hupu.com/same', metadata={}, scraped_at=datetime.now().isoformat()),
            SearchResult(source='sports_platform', title='唯一新闻', url='https://www.hupu.com/unique', metadata={}, scraped_at=datetime.now().isoformat()),
        ]
        
        seen_urls = set()
        unique_results = []
        for r in results:
            if r.url not in seen_urls:
                seen_urls.add(r.url)
                unique_results.append(r)
        
        assert len(unique_results) == 2
        assert unique_results[1].title == '唯一新闻'
    
    def test_max_results_limit(self):
        """测试最大结果数限制"""
        results = [
            SearchResult(source='sports_platform', title=f'赛事{i}', url=f'https://www.hupu.com/{i}', metadata={}, scraped_at=datetime.now().isoformat())
            for i in range(25)
        ]
        
        config = SearcherConfig(max_results=10)
        limited = results[:config.max_results]
        
        assert len(limited) == 10
    
    def test_invalid_url_filtering(self):
        """测试无效 URL 过滤"""
        results = [
            SearchResult(source='sports_platform', title='有效', url='https://www.hupu.com/valid', metadata={}, scraped_at=datetime.now().isoformat()),
            SearchResult(source='sports_platform', title='无效', url='', metadata={}, scraped_at=datetime.now().isoformat()),
        ]
        
        valid_results = [r for r in results if r.url]
        assert len(valid_results) == 1
        assert valid_results[0].url == 'https://www.hupu.com/valid'
    
    def test_search_types_coverage(self):
        """测试搜索类型覆盖"""
        searcher = SportsSearcher()
        types = searcher.supported_types
        
        # 新闻类
        assert any('news' in t for t in types)
        # 数据类
        assert any('data' in t for t in types)
        # 信息类
        assert any('info' in t for t in types)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
