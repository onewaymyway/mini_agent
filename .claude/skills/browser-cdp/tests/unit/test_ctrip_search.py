"""
携程搜索器单元测试

测试覆盖：
- source_name: 来源名称
- supported_types: 支持的搜索类型
- config: 默认配置
- 方法存在性
"""
import pytest
import sys
from pathlib import Path

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.searchers.ctrip_search import CtripSearcher
from src.searchers.base import SearcherConfig, SearchResult


class TestCtripSearcher:
    """CtripSearcher 单元测试"""
    
    def test_source_name(self):
        """测试：来源名称"""
        searcher = CtripSearcher()
        assert searcher.source_name == 'ctrip'
    
    def test_supported_types(self):
        """测试：支持的搜索类型"""
        searcher = CtripSearcher()
        assert searcher.supported_types == ['hotel', 'flight', 'attraction', 'travel_guide', 'vacation']
    
    def test_default_config(self):
        """测试：默认配置"""
        searcher = CtripSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10
        assert searcher.config.stealth is True
    
    def test_search_method_exists(self):
        """测试：search 方法存在"""
        searcher = CtripSearcher()
        assert hasattr(searcher, 'search')
        assert callable(searcher.search)
    
    def test_get_detail_method_exists(self):
        """测试：get_detail 方法存在"""
        searcher = CtripSearcher()
        assert hasattr(searcher, 'get_detail')
        assert callable(searcher.get_detail)
    
    def test_health_check_exists(self):
        """测试：health_check 方法存在"""
        searcher = CtripSearcher()
        assert hasattr(searcher, 'health_check')
        assert callable(searcher.health_check)
    
    def test_close_method_exists(self):
        """测试：close 方法存在"""
        searcher = CtripSearcher()
        assert hasattr(searcher, 'close')
        assert callable(searcher.close)
    
    def test_smart_wait_exists(self):
        """测试：_smart_wait 方法存在"""
        searcher = CtripSearcher()
        assert hasattr(searcher, '_smart_wait')
        assert callable(searcher._smart_wait)
    
    def test_extract_results_exists(self):
        """测试：_extract_results 方法存在"""
        searcher = CtripSearcher()
        assert hasattr(searcher, '_extract_results')
        assert callable(searcher._extract_results)
    
    def test_search_hotel_exists(self):
        """测试：search_hotel 方法存在"""
        searcher = CtripSearcher()
        assert hasattr(searcher, 'search_hotel')
        assert callable(searcher.search_hotel)
    
    def test_search_flight_exists(self):
        """测试：search_flight 方法存在"""
        searcher = CtripSearcher()
        assert hasattr(searcher, 'search_flight')
        assert callable(searcher.search_flight)
    
    def test_handle_captcha_exists(self):
        """测试：handle_captcha 方法存在"""
        searcher = CtripSearcher()
        assert hasattr(searcher, 'handle_captcha')
        assert callable(searcher.handle_captcha)
    
    def test_simulate_human_behavior_exists(self):
        """测试：_simulate_human_behavior 方法存在"""
        searcher = CtripSearcher()
        assert hasattr(searcher, '_simulate_human_behavior')
        assert callable(searcher._simulate_human_behavior)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
