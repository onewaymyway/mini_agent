"""
test_ecommerce_pattern.py - 电商 Pattern 单元测试

验证：
1. EcommercePattern 基类初始化
2. TaobaoSearchPattern 搜索器创建与选择器注册
3. JDSearchPattern 搜索器创建与选择器注册
4. ProductResultItem 序列化
5. EcommerceResults 数据模型
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.selector_manager import SelectorManager, Selector, SelectorType
from src.interaction_patterns.ecommerce_pattern import (
    EcommercePattern,
    EcommerceResults,
    ProductResultItem,
)
from src.interaction_patterns.taobao_search_pattern import TaobaoSearchPattern
from src.interaction_patterns.jd_search_pattern import JDSearchPattern


class TestEcommercePatternBase:
    """EcommercePattern 基类测试"""

    def setup_method(self):
        SelectorManager.reset_instance()

    def test_init_default_selectors(self):
        mock_session = MagicMock()
        pattern = EcommercePattern(mock_session, "example.com")
        assert pattern.domain == "example.com"
        # 默认选择器应已注册
        assert pattern._selectors.has_domain("example.com")
        sel = pattern._selectors.resolve("example.com", "search_input")
        assert sel is not None
        assert sel.type == SelectorType.CSS

    def test_scenes_types_defined(self):
        assert "search" in EcommercePattern.SCENE_TYPES
        assert "cart" in EcommercePattern.SCENE_TYPES
        assert "compare" in EcommercePattern.SCENE_TYPES

    def test_get_selector_value(self):
        mock_session = MagicMock()
        pattern = EcommercePattern(mock_session, "test.com")
        val = pattern._get_selector_value("search_input")
        assert val is not None
        assert "input" in val

    @pytest.mark.asyncio
    async def test_validate_result(self):
        mock_session = MagicMock()
        pattern = EcommercePattern(mock_session, "test.com")
        assert pattern.validate_result({"results": []}) is True
        assert pattern.validate_result(None) is False


class TestProductResultItem:
    """ProductResultItem 数据模型测试"""

    def test_create_empty(self):
        item = ProductResultItem()
        assert item.title == ""
        assert item.price_num == 0.0
        assert item.is_empty if hasattr(item, 'is_empty') else True

    def test_create_with_data(self):
        item = ProductResultItem(
            title="iPhone 15",
            url="https://taobao.com/item/123",
            price="¥5999",
            price_num=5999.0,
            sales="1万+",
            shop="Apple旗舰店",
        )
        d = item.to_dict()
        assert d["title"] == "iPhone 15"
        assert d["price_num"] == 5999.0
        assert d["shop"] == "Apple旗舰店"
        assert d["source_domain"] == ""

    def test_to_dict_required_fields(self):
        item = ProductResultItem(title="T", url="http://u", price="100")
        d = item.to_dict()
        assert "title" in d
        assert "url" in d
        assert "price" in d
        assert "metadata" in d
        assert isinstance(d["metadata"], dict)


class TestEcommerceResults:
    """EcommerceResults 数据模型测试"""

    def test_empty_results(self):
        results = EcommerceResults(success=True, query="test")
        assert results.is_empty
        assert len(results.results) == 0

    def test_results_with_items(self):
        items = [
            ProductResultItem(title="A", url="http://a"),
            ProductResultItem(title="B", url="http://b"),
        ]
        results = EcommerceResults(success=True, query="q", results=items)
        assert not results.is_empty
        assert len(results.results) == 2

    def test_to_dict(self):
        items = [ProductResultItem(title="T", url="http://u", price="50")]
        results = EcommerceResults(
            success=True, query="q", results=items, pattern_used="TestPattern"
        )
        d = results.to_dict()
        assert d["success"] is True
        assert d["query"] == "q"
        assert d["pattern"] == "TestPattern"
        assert len(d["results"]) == 1
        assert d["results"][0]["title"] == "T"

    def test_error_results(self):
        results = EcommerceResults(success=False, query="q", error_message="timeout")
        d = results.to_dict()
        assert d["success"] is False
        assert d["error"] == "timeout"


class TestTaobaoSearchPattern:
    """TaobaoSearchPattern 单元测试"""

    def setup_method(self):
        SelectorManager.reset_instance()

    def test_init_registers_selectors(self):
        mock_session = MagicMock()
        pattern = TaobaoSearchPattern(mock_session)
        assert pattern.domain == "taobao.com"
        assert pattern._site_name == "taobao"
        # 检查关键选择器已注册
        for name in ["search_input", "result_item", "result_price", "cart_button"]:
            sel = pattern._selectors.resolve("taobao.com", name)
            assert sel is not None, f"Selector '{name}' not registered"

    def test_search_url_config(self):
        mock_session = MagicMock()
        pattern = TaobaoSearchPattern(mock_session)
        assert "taobao.com/search" in pattern._config["search_url"]

    def test_custom_config_override(self):
        mock_session = MagicMock()
        custom = {"search_url": "https://custom.com/s?q={query}"}
        pattern = TaobaoSearchPattern(mock_session, config=custom)
        assert pattern._config["search_url"] == "https://custom.com/s?q={query}"

    @pytest.mark.asyncio
    async def test_execute_returns_error_on_exception(self):
        mock_session = MagicMock()
        mock_session.navigate = AsyncMock(side_effect=Exception("Network error"))
        pattern = TaobaoSearchPattern(mock_session)
        result = await pattern.execute("测试关键词")
        assert result.success is False
        assert "Network error" in result.error_message
        assert result.pattern_used == "TaobaoSearchPattern"


class TestJDSearchPattern:
    """JDSearchPattern 单元测试"""

    def setup_method(self):
        SelectorManager.reset_instance()

    def test_init_registers_selectors(self):
        mock_session = MagicMock()
        pattern = JDSearchPattern(mock_session)
        assert pattern.domain == "jd.com"
        assert pattern._site_name == "jd"
        for name in ["search_input", "result_item", "detail_price", "cart_button"]:
            sel = pattern._selectors.resolve("jd.com", name)
            assert sel is not None, f"Selector '{name}' not registered"

    def test_search_url_config(self):
        mock_session = MagicMock()
        pattern = JDSearchPattern(mock_session)
        assert "search.jd.com" in pattern._config["search_url"]

    def test_custom_config_override(self):
        mock_session = MagicMock()
        custom = {"max_pages": 5}
        pattern = JDSearchPattern(mock_session, config=custom)
        assert pattern._config["max_pages"] == 5

    @pytest.mark.asyncio
    async def test_execute_returns_error_on_exception(self):
        mock_session = MagicMock()
        mock_session.navigate = AsyncMock(side_effect=Exception("Timeout"))
        pattern = JDSearchPattern(mock_session)
        result = await pattern.execute("iPhone")
        assert result.success is False
        assert "Timeout" in result.error_message


class TestPatternIntegration:
    """Pattern 协作集成测试（mock）"""

    def setup_method(self):
        SelectorManager.reset_instance()

    @pytest.mark.asyncio
    async def test_taobao_and_jd_selectors_isolated(self):
        """淘宝和京东的选择器空间应相互隔离"""
        mock_session = MagicMock()
        tb = TaobaoSearchPattern(mock_session)
        jd = JDSearchPattern(mock_session)

        tb_sel = tb._selectors.resolve("taobao.com", "search_input")
        jd_sel = jd._selectors.resolve("jd.com", "search_input")
        assert tb_sel is not None
        assert jd_sel is not None
        # 两个域名的选择器值不同
        assert tb_sel.value != jd_sel.value

    @pytest.mark.asyncio
    async def test_ecommerce_pattern_subclass_relationship(self):
        """验证继承关系"""
        from src.interaction_patterns import EcommercePattern as EP
        assert issubclass(TaobaoSearchPattern, EP)
        assert issubclass(JDSearchPattern, EP)

    def test_product_result_item_price_extraction(self):
        """验证价格解析"""
        import re
        # 模拟 _extract_product_item 中的价格提取逻辑
        price_str = "¥ 5,999.00"
        nums = re.findall(r'[\d,]+\.?\d*', price_str.replace(',', ''))
        assert float(nums[0]) == 5999.0

        price_str2 = "到手价￥3299"
        nums2 = re.findall(r'[\d,]+\.?\d*', price_str2.replace(',', ''))
        assert float(nums2[0]) == 3299.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
