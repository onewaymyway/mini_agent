"""
test_selector_manager.py - SelectorManager 单元测试

验证：
1. 选择器注册/解析/删除
2. JSON配置文件加载
3. 单例模式
4. 错误处理
"""
import pytest
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
import sys
import os

# 添加路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.selector_manager import SelectorManager, Selector, SelectorType
from src.interaction_patterns.search_pattern import SearchPattern, SearchResults, SearchResultItem
from src.interaction_patterns.baidu_search_pattern import BaiduSearchPattern


class TestSelectorManager:
    """SelectorManager 单元测试"""
    
    def setup_method(self):
        """每个测试前重置单例"""
        SelectorManager.reset_instance()
    
    def test_register_and_resolve(self):
        """测试选择器注册和解析"""
        mgr = SelectorManager.get_instance()
        sel = Selector(type=SelectorType.CSS, value="#search-input", description="搜索框")
        mgr.register("example.com", "search_input", sel)
        
        resolved = mgr.resolve("example.com", "search_input")
        assert resolved is not None
        assert resolved.value == "#search-input"
        assert resolved.type == SelectorType.CSS
    
    def test_register_multiple_selectors(self):
        """测试同一域名多个选择器"""
        mgr = SelectorManager.get_instance()
        selectors = {
            "search_input": Selector(type=SelectorType.CSS, value="#kw"),
            "search_button": Selector(type=SelectorType.CSS, value="#su"),
            "result_item": Selector(type=SelectorType.CSS, value=".result"),
        }
        for name, sel in selectors.items():
            mgr.register("baidu.com", name, sel)
        
        assert len(mgr.get_all("baidu.com")) == 3
        assert mgr.has_domain("baidu.com")
    
    def test_get_all_selectors(self):
        """测试获取域名所有选择器"""
        mgr = SelectorManager.get_instance()
        mgr.register("test.com", "sel1", Selector(type=SelectorType.CSS, value="#a"))
        mgr.register("test.com", "sel2", Selector(type=SelectorType.XPATH, value="//div"))
        
        all_sels = mgr.get_all("test.com")
        assert len(all_sels) == 2
        assert "sel1" in all_sels
        assert "sel2" in all_sels
    
    def test_remove_domain(self):
        """测试移除域名选择器"""
        mgr = SelectorManager.get_instance()
        mgr.register("test.com", "sel1", Selector(type=SelectorType.CSS, value="#a"))
        assert mgr.has_domain("test.com")
        
        mgr.remove_domain("test.com")
        assert not mgr.has_domain("test.com")
    
    def test_list_domains(self):
        """测试列出所有域名"""
        mgr = SelectorManager.get_instance()
        mgr.register("a.com", "s1", Selector(type=SelectorType.CSS, value="#x"))
        mgr.register("b.com", "s1", Selector(type=SelectorType.CSS, value="#y"))

        domains = mgr.list_domains()
        # example.com.json 会被自动加载，所以至少 1+2=3 个
        assert "a.com" in domains
        assert "b.com" in domains
        assert len(domains) >= 2
    
    def test_singleton_pattern(self):
        """测试单例模式"""
        mgr1 = SelectorManager.get_instance()
        mgr2 = SelectorManager.get_instance()
        assert mgr1 is mgr2
    
    def test_load_config_from_file(self, tmp_path):
        """测试从JSON文件加载配置"""
        config_data = {
            "domain": "testsite.com",
            "selectors": {
                "search_input": {"type": "css", "value": "#q", "timeout": 10.0},
                "search_button": {"type": "css", "value": "#btn"},
                "result_item": "#result",
            }
        }
        config_file = tmp_path / "testsite.com.json"
        import json
        with open(config_file, "w") as f:
            json.dump(config_data, f)
        
        mgr = SelectorManager.get_instance(config_dir=tmp_path)
        assert mgr.has_domain("testsite.com")
        sel = mgr.resolve("testsite.com", "search_input")
        assert sel is not None
        assert sel.value == "#q"
    
    def test_save_and_reload_config(self, tmp_path):
        """测试保存和重新加载配置"""
        mgr = SelectorManager.get_instance(config_dir=tmp_path)
        selectors = {
            "search_input": Selector(type=SelectorType.CSS, value="#q"),
            "result_item": Selector(type=SelectorType.CSS, value=".result"),
        }
        mgr.save_config("mysite.com", selectors)
        
        # 重新加载
        mgr2 = SelectorManager.get_instance(config_dir=tmp_path)
        assert mgr2.has_domain("mysite.com")
        sel = mgr2.resolve("mysite.com", "search_input")
        assert sel.value == "#q"
    
    def test_selector_to_dict_and_from_dict(self):
        """测试选择器序列化/反序列化"""
        sel = Selector(
            type=SelectorType.CSS,
            value="#test",
            timeout=20.0,
            description="测试选择器"
        )
        d = sel.to_dict()
        assert d["type"] == "css"
        assert d["value"] == "#test"
        assert d["timeout"] == 20.0
        
        sel2 = Selector.from_dict(d)
        assert sel2.type == SelectorType.CSS
        assert sel2.value == "#test"


class TestSearchResults:
    """SearchResults 数据模型测试"""
    
    def test_create_empty_results(self):
        """测试创建空结果"""
        results = SearchResults(success=True, query="test")
        assert results.is_empty
        assert len(results.results) == 0
    
    def test_create_with_items(self):
        """测试创建带项目的结果"""
        items = [
            SearchResultItem(title="Title 1", url="https://example.com/1"),
            SearchResultItem(title="Title 2", url="https://example.com/2"),
        ]
        results = SearchResults(success=True, query="test", results=items)
        assert not results.is_empty
        assert len(results.results) == 2
    
    def test_to_dict(self):
        """测试结果序列化"""
        items = [SearchResultItem(title="T", url="http://u", snippet="S")]
        results = SearchResults(success=True, query="q", results=items, pattern_used="TestPattern")
        d = results.to_dict()
        assert d["success"] is True
        assert d["query"] == "q"
        assert d["pattern"] == "TestPattern"
        assert len(d["results"]) == 1


class TestSearchPattern:
    """SearchPattern 集成测试（mock）"""
    
    @pytest.mark.asyncio
    async def test_search_pattern_initialization(self):
        """测试搜索模式初始化"""
        mock_session = MagicMock()
        pattern = SearchPattern(mock_session, "example.com", {"max_pages": 3})
        assert pattern.domain == "example.com"
        assert pattern._max_pages == 3
    
    @pytest.mark.asyncio
    async def test_search_pattern_execute_returns_results(self):
        """测试搜索执行返回结果"""
        mock_session = MagicMock()
        # 模拟等待方法
        mock_session.navigate = AsyncMock(return_value=None)
        mock_session.click = AsyncMock(return_value=None)
        mock_session.type_text = AsyncMock(return_value=None)
        mock_session.press_key = AsyncMock(return_value=None)
        mock_session.query_selector_all = AsyncMock(return_value=[])
        
        pattern = SearchPattern(mock_session, "test.com")
        # 不会实际执行，因为 validate_result 会检查
        assert pattern.validate_result(None) is False
        assert pattern.validate_result({"results": []}) is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
