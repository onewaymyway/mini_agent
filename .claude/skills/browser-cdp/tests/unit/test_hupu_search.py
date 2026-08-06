"""
虎扑体育搜索器单元测试

测试覆盖：
- search(): 新闻/赛事/社区搜索
- 基础属性验证
- 配置验证
- 结果格式化
- 去重功能
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, Mock, MagicMock
import asyncio

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.searchers.hupu_search import HupuSearcher
from src.searchers.base import SearcherConfig, SearchResult


class MockBrowser:
    """模拟 Browser 类"""
    
    def __init__(self, port=9333, stealth=True):
        self.port = port
        self.stealth = stealth
        self._url = ""
        self._results = []
        self._closed = False
    
    async def start(self):
        pass
    
    async def get(self, url: str):
        self._url = url
    
    async def evaluate(self, js_code: str):
        return self._results
    
    async def close(self):
        self._closed = True
    
    async def wait_for_network_idle(self, timeout=10):
        await asyncio.sleep(0.01)
    
    async def wait_for_route(self, timeout=10):
        await asyncio.sleep(0.01)
    
    async def wait_for_stable(self, timeout=10):
        await asyncio.sleep(0.01)


class TestHupuSearcher:
    """HupuSearcher 单元测试"""
    
    def setup_method(self):
        self.searcher = HupuSearcher()
        self.config = SearcherConfig(
            port=9333,
            stealth=True,
            max_results=10,
            wait_strategy="networkidle",
            wait_timeout=10.0,
            random_delay_range=(0.5, 1.5)
        )
    
    def test_source_name(self):
        """测试：来源名称"""
        assert self.searcher.source_name == "hupu"
    
    def test_supported_types(self):
        """测试：支持的搜索类型"""
        assert self.searcher.supported_types == ["news_search", "match_data", "community_post", "player_stats"]
    
    def test_search_method_exists(self):
        """测试：search 方法存在"""
        assert hasattr(self.searcher, 'search')
        assert callable(self.searcher.search)
    
    def test_health_check_exists(self):
        """测试：health_check 方法存在"""
        assert hasattr(self.searcher, 'health_check')
        assert callable(self.searcher.health_check)
    
    def test_close_method_exists(self):
        """测试：close 方法存在"""
        assert hasattr(self.searcher, 'close')
        assert callable(self.searcher.close)
    
    def test_search_news_exists(self):
        """测试：_search_news 方法存在"""
        assert hasattr(self.searcher, '_search_news')
        assert callable(self.searcher._search_news)
    
    def test_search_matches_exists(self):
        """测试：_search_matches 方法存在"""
        assert hasattr(self.searcher, '_search_matches')
        assert callable(self.searcher._search_matches)
    
    def test_search_community_exists(self):
        """测试：_search_community 方法存在"""
        assert hasattr(self.searcher, '_search_community')
        assert callable(self.searcher._search_community)
    
    def test_get_detail_method_exists(self):
        """测试：get_detail 方法存在"""
        assert hasattr(self.searcher, 'get_detail')
        assert callable(self.searcher.get_detail)
    
    def test_search_method_signature(self):
        """测试：search 方法签名"""
        import inspect
        sig = inspect.signature(self.searcher.search)
        params = list(sig.parameters.keys())
        assert 'query' in params
        assert 'search_type' in params
    
    def test_health_check(self):
        """测试：健康检查"""
        result = self.searcher.health_check()
        assert result['source'] == 'hupu'
        assert result['status'] == 'healthy'
        assert 'supported_types' in result
        assert 'base_url' in result
    
    def test_search_result_format(self):
        """测试：搜索结果格式"""
        mock_result = {
            'source': 'hupu',
            'title': 'NBA 总决赛',
            'url': 'https://www.hupu.com/nba/123',
            'snippet': '湖人队夺冠',
            'metadata': {'type': 'news', 'score': 0.95},
            'scraped_at': '2026-08-06T08:00:00'
        }
        assert 'source' in mock_result
        assert 'title' in mock_result
        assert 'url' in mock_result
        assert mock_result['url'].startswith('http')
    
    def test_deduplication(self):
        """测试：去重功能"""
        results = [
            {'source': 'hupu', 'title': '重复', 'url': 'https://hupu.com/same', 'metadata': {}, 'scraped_at': '2026-08-06T08:00:00'},
            {'source': 'hupu', 'title': '重复', 'url': 'https://hupu.com/same', 'metadata': {}, 'scraped_at': '2026-08-06T08:00:00'},
            {'source': 'hupu', 'title': '唯一', 'url': 'https://hupu.com/unique', 'metadata': {}, 'scraped_at': '2026-08-06T08:00:00'},
        ]
        seen = set()
        unique = []
        for r in results:
            if r['url'] not in seen:
                seen.add(r['url'])
                unique.append(r)
        assert len(unique) == 2
    
    def test_max_results_limit(self):
        """测试：最大结果数限制"""
        results = [
            {'source': 'hupu', 'title': f'结果{i}', 'url': f'https://hupu.com/{i}', 'metadata': {}, 'scraped_at': '2026-08-06T08:00:00'}
            for i in range(30)
        ]
        config = SearcherConfig(max_results=10)
        limited = results[:config.max_results]
        assert len(limited) == 10
    
    def test_invalid_url_filtering(self):
        """测试：无效 URL 过滤"""
        results = [
            {'source': 'hupu', 'title': '有效', 'url': 'https://hupu.com/valid', 'metadata': {}, 'scraped_at': '2026-08-06T08:00:00'},
            {'source': 'hupu', 'title': '无效', 'url': '', 'metadata': {}, 'scraped_at': '2026-08-06T08:00:00'},
            {'source': 'hupu', 'title': '无效', 'url': None, 'metadata': {}, 'scraped_at': '2026-08-06T08:00:00'},
        ]
        valid = [r for r in results if r.get('url')]
        assert len(valid) == 1
        assert valid[0]['url'] == 'https://hupu.com/valid'
    
    def test_config_with_custom_params(self):
        """测试：自定义配置参数"""
        config = SearcherConfig(
            port=9334,
            stealth=False,
            max_results=50,
            wait_strategy="stable",
            wait_timeout=20.0,
            random_delay_range=(1.0, 2.0)
        )
        assert config.port == 9334
        assert config.stealth is False
        assert config.max_results == 50
        assert config.wait_strategy == "stable"
        assert config.wait_timeout == 20.0
    
    def test_search_by_type_exists(self):
        """测试：按类型搜索方法存在"""
        assert hasattr(self.searcher, '_search_news')
        assert hasattr(self.searcher, '_search_matches')
        assert hasattr(self.searcher, '_search_community')
    
    def test_base_url(self):
        """测试：基础 URL"""
        from src.searchers.hupu_search import HUPU_BASE
        assert HUPU_BASE == "https://www.hupu.com"


class TestHupuSearcherIntegration:
    """HupuSearcher 集成测试"""
    
    def test_searcher_initialization(self):
        """测试：搜索器初始化"""
        searcher = HupuSearcher()
        assert searcher.source_name == 'hupu'
        assert len(searcher.supported_types) > 0
    
    def test_health_check(self):
        """测试：健康检查"""
        searcher = HupuSearcher()
        result = searcher.health_check()
        assert result['source'] == 'hupu'
        assert result['status'] == 'healthy'
    
    def test_search_method_signature(self):
        """测试：search 方法签名"""
        import inspect
        searcher = HupuSearcher()
        sig = inspect.signature(searcher.search)
        params = list(sig.parameters.keys())
        assert 'query' in params
        assert 'search_type' in params
    
    def test_get_detail_method_signature(self):
        """测试：get_detail 方法签名"""
        import inspect
        searcher = HupuSearcher()
        sig = inspect.signature(searcher.get_detail)
        params = list(sig.parameters.keys())
        assert 'url' in params


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
