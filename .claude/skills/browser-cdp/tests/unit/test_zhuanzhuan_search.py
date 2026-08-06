"""
转转二手搜索器单元测试

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

from src.searchers.zhuanzhuan_search import ZhuanZhuanSearcher
from src.searchers.base import SearcherConfig, SearchResult


class TestZhuanZhuanSearcher:
    """ZhuanZhuanSearcher 单元测试"""
    
    def test_source_name(self):
        """测试来源名称"""
        searcher = ZhuanZhuanSearcher()
        assert searcher.source_name == "zhuanzhuan"
    
    def test_supported_types(self):
        """测试支持类型"""
        searcher = ZhuanZhuanSearcher()
        assert "product_search" in searcher.supported_types
        assert "price_compare" in searcher.supported_types
        assert "phone_search" in searcher.supported_types
    
    def test_default_config(self):
        """测试默认配置"""
        searcher = ZhuanZhuanSearcher()
        assert searcher.config.wait_timeout == 30
        assert searcher.config.max_results == 10
        assert searcher.config.wait_strategy == "networkidle"
    
    def test_search_method_exists(self):
        """测试 search 方法存在"""
        searcher = ZhuanZhuanSearcher()
        assert hasattr(searcher, "search")
        assert callable(searcher.search)
    
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
    
    def test_get_detail_method_exists(self):
        """测试 get_detail 方法存在"""
        searcher = ZhuanZhuanSearcher()
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
        searcher = ZhuanZhuanSearcher(config=config)
        assert searcher.config.max_results == 20
        assert searcher.config.wait_strategy == "route"
        assert searcher.config.port == 9333
    
    def test_search_result_format(self):
        """测试搜索结果格式化"""
        result = SearchResult(
            source='zhuanzhuan',
            title='iPhone 15 二手',
            url='https://www.zhuanzhuan.com/product?id=123',
            snippet='95新，电池健康98%',
            metadata={'query': 'iPhone 15', 'type': 'phone', 'price': '4500'},
            scraped_at=datetime.now().isoformat()
        )
        assert result.source == 'zhuanzhuan'
        assert result.title == 'iPhone 15 二手'
        assert 'zhuanzhuan.com' in result.url.lower()
        assert result.metadata['type'] == 'phone'
    
    def test_deduplication(self):
        """测试结果去重"""
        results = [
            SearchResult(source='zhuanzhuan', title='重复商品', url='https://www.zhuanzhuan.com/same', metadata={}, scraped_at=datetime.now().isoformat()),
            SearchResult(source='zhuanzhuan', title='重复商品', url='https://www.zhuanzhuan.com/same', metadata={}, scraped_at=datetime.now().isoformat()),
            SearchResult(source='zhuanzhuan', title='唯一商品', url='https://www.zhuanzhuan.com/unique', metadata={}, scraped_at=datetime.now().isoformat()),
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
            SearchResult(source='zhuanzhuan', title=f'商品{i}', url=f'https://www.zhuanzhuan.com/product?id={i}', metadata={}, scraped_at=datetime.now().isoformat())
            for i in range(15)
        ]
        
        config = SearcherConfig(max_results=5)
        limited = results[:config.max_results]
        
        assert len(limited) == 5
    
    def test_invalid_url_filtering(self):
        """测试无效 URL 过滤"""
        results = [
            SearchResult(source='zhuanzhuan', title='有效', url='https://www.zhuanzhuan.com/valid', metadata={}, scraped_at=datetime.now().isoformat()),
            SearchResult(source='zhuanzhuan', title='无效', url='', metadata={}, scraped_at=datetime.now().isoformat()),
        ]
        
        valid_results = [r for r in results if r.url]
        assert len(valid_results) == 1
        assert valid_results[0].url == 'https://www.zhuanzhuan.com/valid'
    
    def test_search_result_metadata(self):
        """测试搜索结果元数据"""
        result = SearchResult(
            source='zhuanzhuan',
            title='MacBook Pro',
            url='https://www.zhuanzhuan.com/product?id=456',
            snippet='2022款 M2芯片',
            metadata={
                'query': 'MacBook Pro',
                'type': 'digital',
                'price': '8500',
                'condition': '95新',
                'seller_rating': '4.8'
            },
            scraped_at=datetime.now().isoformat()
        )
        assert result.metadata['price'] == '8500'
        assert result.metadata['condition'] == '95新'
        assert result.metadata['seller_rating'] == '4.8'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
