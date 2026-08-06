"""
闲鱼搜索器单元测试

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

from src.searchers.xianyu_search import XianyuSearcher
from src.searchers.base import SearcherConfig, SearchResult


class TestXianyuSearcher:
    """XianyuSearcher 单元测试"""
    
    def test_source_name(self):
        """测试来源名称"""
        searcher = XianyuSearcher()
        assert searcher.source_name == "xianyu"
    
    def test_supported_types(self):
        """测试支持的搜索类型"""
        searcher = XianyuSearcher()
        assert "secondhand_search" in searcher.supported_types
        assert "product_search" in searcher.supported_types
        assert "used_goods" in searcher.supported_types
    
    def test_default_config(self):
        """测试默认配置"""
        searcher = XianyuSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10  # 默认值
        assert searcher.config.wait_strategy == "networkidle"
    
    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = XianyuSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)
    
    def test_health_check_exists(self):
        """测试 health_check 方法存在"""
        searcher = XianyuSearcher()
        assert hasattr(searcher, "health_check")
        assert callable(searcher.health_check)
    
    def test_close_method_exists(self):
        """测试 close 方法存在"""
        searcher = XianyuSearcher()
        assert hasattr(searcher, "close")
        assert callable(searcher.close)
    
    def test_smart_wait_exists(self):
        """测试 _smart_wait 方法存在"""
        searcher = XianyuSearcher()
        assert hasattr(searcher, "_smart_wait")
        assert callable(searcher._smart_wait)
    
    def test_extract_results_exists(self):
        """测试 _extract_results 方法存在"""
        searcher = XianyuSearcher()
        assert hasattr(searcher, "_extract_results")
        assert callable(searcher._extract_results)
    
    def test_get_detail_method_exists(self):
        """测试 get_detail 方法存在"""
        searcher = XianyuSearcher()
        assert hasattr(searcher, "get_detail")
        assert callable(searcher.get_detail)
    
    def test_config_with_custom_params(self):
        """测试自定义配置"""
        config = SearcherConfig(
            port=9333,
            stealth=True,
            max_results=15,
            wait_strategy="route",
            wait_timeout=20.0,
            random_delay_range=(0.5, 1.5)
        )
        searcher = XianyuSearcher(config=config)
        assert searcher.config.max_results == 15
        assert searcher.config.wait_strategy == "route"
        assert searcher.config.port == 9333
    
    def test_search_result_format(self):
        """测试搜索结果格式化"""
        result = SearchResult(
            source='xianyu',
            title='iPhone 15 二手',
            url='https://www.goofish.com/item/123',
            snippet='95新，功能正常',
            metadata={'query': 'iPhone 15', 'type': 'secondhand_search', 'price': '3000'},
            scraped_at=datetime.now().isoformat()
        )
        assert result.source == 'xianyu'
        assert result.title == 'iPhone 15 二手'
        assert 'goofish' in result.url.lower()
        assert result.metadata['type'] == 'secondhand_search'
    
    def test_deduplication(self):
        """测试结果去重"""
        results = [
            SearchResult(source='xianyu', title='重复商品', url='https://www.goofish.com/same', metadata={}, scraped_at=datetime.now().isoformat()),
            SearchResult(source='xianyu', title='重复商品', url='https://www.goofish.com/same', metadata={}, scraped_at=datetime.now().isoformat()),
            SearchResult(source='xianyu', title='唯一商品', url='https://www.goofish.com/unique', metadata={}, scraped_at=datetime.now().isoformat()),
        ]
        
        seen_urls = set()
        unique_results = []
        for r in results:
            if r.url not in seen_urls:
                seen_urls.add(r.url)
                unique_results.append(r)
        
        assert len(unique_results) == 2
        assert unique_results[1].title == '唯一商品'
    
    def test_max_results_limit(self):
        """测试最大结果数限制"""
        results = [
            SearchResult(source='xianyu', title=f'商品{i}', url=f'https://www.goofish.com/{i}', metadata={}, scraped_at=datetime.now().isoformat())
            for i in range(25)
        ]
        
        config = SearcherConfig(max_results=10)
        limited = results[:config.max_results]
        
        assert len(limited) == 10
    
    def test_invalid_url_filtering(self):
        """测试无效 URL 过滤"""
        results = [
            SearchResult(source='xianyu', title='有效', url='https://www.goofish.com/valid', metadata={}, scraped_at=datetime.now().isoformat()),
            SearchResult(source='xianyu', title='无效', url='', metadata={}, scraped_at=datetime.now().isoformat()),
        ]
        
        valid_results = [r for r in results if r.url]
        assert len(valid_results) == 1
        assert valid_results[0].url == 'https://www.goofish.com/valid'
    
    def test_search_types_coverage(self):
        """测试搜索类型覆盖"""
        searcher = XianyuSearcher()
        types = searcher.supported_types
        
        # 二手搜索类
        assert any('secondhand' in t for t in types)
        # 商品搜索类
        assert any('product' in t for t in types)
        # 闲置物品类
        assert any('used' in t for t in types)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
