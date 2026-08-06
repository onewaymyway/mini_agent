"""
test_dynamic_page_support.py - 动态页面支持模块单元测试

测试覆盖：
- DynamicPageSupport 类
- 元素等待
- 滚动加载
- SPA 路由检测
- 懒加载图片等待
- DOM 变化监听
- 组合操作
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, Mock, MagicMock, AsyncMock
import asyncio

SKILL_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.core.dynamic_page_support import (
    DynamicPageSupport,
    DynamicPageResult,
    wait_for_element,
    scroll_to_load,
    wait_for_spa_route,
    wait_for_lazy_images,
    wait_for_page_ready,
)
from src.core.spa_detector import SPADetector, SPAFramework, SPAInfo


class MockSession:
    """模拟 CDP Session"""
    
    def __init__(self):
        self._eval_results = {}
        self._selector_results = {}
        self._url = "https://example.com"
    
    async def eval_js(self, js: str):
        if "location.href" in js or "window.location" in js:
            return self._url
        if "__REACT_ROOT__" in js:
            return "object"
        if "__VUE__" in js:
            return "object"
        return "undefined"
    
    async def query_selector_all(self, selector: str):
        return self._selector_results.get(selector, [])
    
    async def query_selector(self, selector: str):
        elements = await self.query_selector_all(selector)
        return elements[0] if elements else None
    
    async def click(self, selector: str, timeout: float = None):
        return True
    
    def get_url(self):
        return self._url


class TestDynamicPageResult:
    """测试 DynamicPageResult 数据类"""
    
    def test_success_result(self):
        result = DynamicPageResult(success=True, operation="scroll")
        assert result.success is True
        assert result.operation == "scroll"
        assert result.error is None
    
    def test_error_result(self):
        result = DynamicPageResult(success=False, operation="wait", error="timeout")
        assert result.success is False
        assert result.error == "timeout"
    
    def test_to_dict(self):
        result = DynamicPageResult(
            success=True,
            operation="scroll",
            data={"items": 10},
            elapsed=1.5
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["operation"] == "scroll"
        assert d["elapsed"] == 1.5
        assert d["data"]["items"] == 10
    
    def test_to_dict_with_error(self):
        result = DynamicPageResult(success=False, operation="wait", error="timeout")
        d = result.to_dict()
        assert "error" in d
        assert d["error"] == "timeout"


class TestDynamicPageSupport:
    """测试 DynamicPageSupport 类"""
    
    @pytest.fixture
    def mock_session(self):
        return MockSession()
    
    @pytest.fixture
    def support(self, mock_session):
        with patch('src.core.dynamic_page_support.SmartWait'), \
             patch('src.core.dynamic_page_support.EnhancedDynamicLoader'), \
             patch('src.core.dynamic_page_support.SPADetector'), \
             patch('src.core.dynamic_page_support.DOMObserver'):
            return DynamicPageSupport(mock_session)
    
    async def test_init(self, mock_session):
        support = DynamicPageSupport(mock_session)
        assert support.session == mock_session
    
    async def test_wait_for_element(self, support, mock_session):
        with patch.object(support._smart_wait, 'wait_for', new_callable=AsyncMock) as mock_wait:
            with patch.object(support._smart_wait, 'wait_for_selector', new_callable=AsyncMock) as mock_selector:
                mock_result = Mock()
                mock_result.success = True
                mock_wait.return_value = mock_result
                mock_selector.return_value = mock_result

                result = await support.wait_for_element("#test", timeout=5.0)
                assert result is True
    
    async def test_wait_for_elements(self, support):
        with patch.object(support, 'wait_for_element', new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = True
            
            results = await support.wait_for_elements(["#a", "#b"])
            assert results["#a"] is True
            assert results["#b"] is True
    
    async def test_scroll_to_load(self, support):
        with patch('src.core.dynamic_page_support.EnhancedDynamicLoader') as mock_loader_class:
            mock_loader = AsyncMock()
            mock_result = Mock()
            mock_result.pages_loaded = 3
            mock_result.items_found = 50
            mock_loader.smart_scroll.return_value = mock_result
            mock_loader_class.return_value = mock_loader
            
            result = await support.scroll_to_load(".item", max_pages=5)
            assert result.pages_loaded == 3
    
    async def test_load_virtual_list(self, support):
        with patch('src.core.dynamic_page_support.EnhancedDynamicLoader') as mock_loader_class:
            mock_loader = AsyncMock()
            mock_loader.load_virtual_list.return_value = [{"id": 1}, {"id": 2}]
            mock_loader_class.return_value = mock_loader
            
            items = await support.load_virtual_list(".item", max_items=10)
            assert len(items) == 2
    
    async def test_wait_for_spa_route(self, support):
        with patch.object(support._spa_detector, 'detect', new_callable=AsyncMock) as mock_detect, \
             patch.object(support._smart_wait, 'wait_for', new_callable=AsyncMock) as mock_wait:
            mock_detect.return_value = SPAInfo(framework=SPAFramework.REACT)
            mock_result = Mock()
            mock_result.success = True
            mock_wait.return_value = mock_result
            
            result = await support.wait_for_spa_route(timeout=10.0)
            assert result is True
    
    async def test_detect_spa(self, support):
        with patch.object(support._spa_detector, 'detect', new_callable=AsyncMock) as mock_detect:
            mock_detect.return_value = SPAInfo(framework=SPAFramework.REACT, version="18.2.0")
            
            info = await support.detect_spa()
            assert info.framework == SPAFramework.REACT
            assert info.version == "18.2.0"
    
    async def test_wait_for_lazy_images(self, support):
        with patch('src.core.dynamic_page_support.EnhancedDynamicLoader') as mock_loader_class:
            mock_loader = AsyncMock()
            mock_loader.wait_for_lazy_images.return_value = 5
            mock_loader_class.return_value = mock_loader
            
            count = await support.wait_for_lazy_images(timeout=5.0)
            assert count == 5
    
    async def test_wait_for_dom_stable(self, support):
        with patch.object(support._dom_observer, 'observe', new_callable=AsyncMock) as mock_observe, \
             patch.object(support._dom_observer, 'wait_for_stable', new_callable=AsyncMock) as mock_wait, \
             patch.object(support._dom_observer, 'stop', new_callable=AsyncMock) as mock_stop:
            mock_wait.return_value = True
            
            result = await support.wait_for_dom_stable(timeout=10.0)
            assert result is True
            mock_stop.assert_called_once()
    
    async def test_wait_for_content_change(self, support):
        with patch.object(support._dom_observer, 'observe', new_callable=AsyncMock) as mock_observe, \
             patch.object(support._dom_observer, 'wait_for_content_change', new_callable=AsyncMock) as mock_wait, \
             patch.object(support._dom_observer, 'stop', new_callable=AsyncMock) as mock_stop:
            mock_wait.return_value = True
            
            result = await support.wait_for_content_change(selector="body")
            assert result is True
            mock_stop.assert_called_once()
    
    async def test_wait_for_page_ready(self, support):
        with patch.object(support._smart_wait, 'wait_for', new_callable=AsyncMock) as mock_wait:
            with patch.object(support._smart_wait, 'wait_for_selector', new_callable=AsyncMock) as mock_selector:
                mock_result = Mock()
                mock_result.success = True
                mock_wait.return_value = mock_result
                mock_selector.return_value = mock_result

                result = await support.wait_for_page_ready(selector="#content", timeout=10.0)
                assert result is True
    
    async def test_scroll_and_collect(self, support):
        with patch('src.core.dynamic_page_support.EnhancedDynamicLoader') as mock_loader_class:
            mock_loader = MagicMock()
            mock_loader.smart_scroll = AsyncMock(return_value=Mock(pages_loaded=2, items_found=20))
            mock_loader._collect_visible_items = AsyncMock(return_value=[{"id": 1}])
            mock_loader._deduplicate_items.return_value = [i for i in [{"id": 1}] if i.get('id') not in set()]
            mock_loader_class.return_value = mock_loader
            
            with patch.object(support, 'wait_for_lazy_images', new_callable=AsyncMock):
                items = await support.scroll_and_collect(".item", max_items=10)
                assert len(items) == 1
    
    async def test_wait_for_element_async(self, support, mock_session):
        """测试异步等待元素"""
        with patch.object(support._smart_wait, 'wait_for', new_callable=AsyncMock) as mock_wait:
            with patch.object(support._smart_wait, 'wait_for_selector', new_callable=AsyncMock) as mock_selector:
                mock_result = Mock()
                mock_result.success = True
                mock_wait.return_value = mock_result
                mock_selector.return_value = mock_result

                result = await support.wait_for_element("#test", timeout=5.0)
                assert result is True


class TestSPADetector:
    """测试 SPADetector 类"""
    
    @pytest.fixture
    def mock_session(self):
        return MockSession()
    
    async def test_detect_react(self, mock_session):
        with patch('src.core.spa_detector.SPADetector._detect_framework', new_callable=AsyncMock) as mock_detect:
            mock_detect.return_value = SPAFramework.REACT
            
            detector = SPADetector(mock_session)
            info = await detector.detect()
            assert info.framework == SPAFramework.REACT
    
    async def test_detect_vue(self, mock_session):
        with patch('src.core.spa_detector.SPADetector._detect_framework', new_callable=AsyncMock) as mock_detect:
            mock_detect.return_value = SPAFramework.VUE
            
            detector = SPADetector(mock_session)
            info = await detector.detect()
            assert info.framework == SPAFramework.VUE
    
    async def test_detect_angular(self, mock_session):
        with patch('src.core.spa_detector.SPADetector._detect_framework', new_callable=AsyncMock) as mock_detect:
            mock_detect.return_value = SPAFramework.ANGULAR
            
            detector = SPADetector(mock_session)
            info = await detector.detect()
            assert info.framework == SPAFramework.ANGULAR
    
    async def test_detect_nextjs(self, mock_session):
        with patch('src.core.spa_detector.SPADetector._detect_framework', new_callable=AsyncMock) as mock_detect:
            mock_detect.return_value = SPAFramework.NEXTJS
            
            detector = SPADetector(mock_session)
            info = await detector.detect()
            assert info.framework == SPAFramework.NEXTJS
    
    async def test_detect_nuxt(self, mock_session):
        with patch('src.core.spa_detector.SPADetector._detect_framework', new_callable=AsyncMock) as mock_detect:
            mock_detect.return_value = SPAFramework.NUXT
            
            detector = SPADetector(mock_session)
            info = await detector.detect()
            assert info.framework == SPAFramework.NUXT
    
    async def test_detect_remix(self, mock_session):
        with patch('src.core.spa_detector.SPADetector._detect_framework', new_callable=AsyncMock) as mock_detect:
            mock_detect.return_value = SPAFramework.REMIX
            
            detector = SPADetector(mock_session)
            info = await detector.detect()
            assert info.framework == SPAFramework.REMIX
    
    async def test_detect_sveltekit(self, mock_session):
        with patch('src.core.spa_detector.SPADetector._detect_framework', new_callable=AsyncMock) as mock_detect:
            mock_detect.return_value = SPAFramework.SVELTEKIT
            
            detector = SPADetector(mock_session)
            info = await detector.detect()
            assert info.framework == SPAFramework.SVELTEKIT
    
    async def test_wait_for_route_change_not_spa(self, mock_session):
        detector = SPADetector(mock_session)
        detector._spa_info = SPAInfo(framework=SPAFramework.UNKNOWN)
        
        result = await detector.wait_for_route_change()
        assert result is True
    
    async def test_wait_for_element(self, mock_session):
        detector = SPADetector(mock_session)
        
        with patch.object(mock_session, 'query_selector_all', new_callable=AsyncMock) as mock_query:
            mock_query.return_value = [Mock()]
            
            result = await detector.wait_for_element("#test", timeout=1.0)
            assert result is True


class TestSPAFramework:
    """测试 SPAFramework 枚举"""
    
    def test_all_frameworks(self):
        assert SPAFramework.REACT.value == "react"
        assert SPAFramework.VUE.value == "vue"
        assert SPAFramework.ANGULAR.value == "angular"
        assert SPAFramework.SVELTE.value == "svelte"
        assert SPAFramework.NEXTJS.value == "nextjs"
        assert SPAFramework.NUXT.value == "nuxt"
        assert SPAFramework.REMIX.value == "remix"
        assert SPAFramework.SVELTEKIT.value == "sveltekit"
        assert SPAFramework.UNKNOWN.value == "unknown"
    
    def test_framework_count(self):
        assert len(SPAFramework) >= 9


class TestSPAInfo:
    """测试 SPAInfo 数据类"""
    
    def test_default_values(self):
        info = SPAInfo(framework=SPAFramework.REACT)
        assert info.version is None
        assert info.router_version is None
        assert info.is_spa is True
    
    def test_full_values(self):
        info = SPAInfo(
            framework=SPAFramework.VUE,
            version="3.4.0",
            router_version="4.3.0"
        )
        assert info.version == "3.4.0"
        assert info.router_version == "4.3.0"
        assert info.is_spa is True
    
    def test_unknown_is_not_spa(self):
        info = SPAInfo(framework=SPAFramework.UNKNOWN, is_spa=False)
        assert info.is_spa is False


class TestConvenienceFunctions:
    """测试便捷函数"""
    
    async def test_wait_for_element_function(self):
        mock_session = MockSession()
        with patch('src.core.dynamic_page_support.DynamicPageSupport') as mock_support_class:
            mock_support = AsyncMock()
            mock_support.wait_for_element.return_value = True
            mock_support_class.return_value = mock_support
            
            result = await wait_for_element(mock_session, "#test")
            assert result is True
    
    async def test_scroll_to_load_function(self):
        mock_session = MockSession()
        with patch('src.core.dynamic_page_support.DynamicPageSupport') as mock_support_class:
            mock_support = AsyncMock()
            mock_support.scroll_to_load.return_value = Mock(pages_loaded=2, items_found=20)
            mock_support_class.return_value = mock_support
            
            result = await scroll_to_load(mock_session, ".item")
            assert result.pages_loaded == 2
    
    async def test_wait_for_spa_route_function(self):
        mock_session = MockSession()
        with patch('src.core.dynamic_page_support.DynamicPageSupport') as mock_support_class:
            mock_support = AsyncMock()
            mock_support.wait_for_spa_route.return_value = True
            mock_support_class.return_value = mock_support
            
            result = await wait_for_spa_route(mock_session)
            assert result is True
    
    async def test_wait_for_lazy_images_function(self):
        mock_session = MockSession()
        with patch('src.core.dynamic_page_support.DynamicPageSupport') as mock_support_class:
            mock_support = AsyncMock()
            mock_support.wait_for_lazy_images.return_value = 5
            mock_support_class.return_value = mock_support
            
            result = await wait_for_lazy_images(mock_session)
            assert result == 5
    
    async def test_wait_for_page_ready_function(self):
        mock_session = MockSession()
        with patch('src.core.dynamic_page_support.DynamicPageSupport') as mock_support_class:
            mock_support = AsyncMock()
            mock_support.wait_for_page_ready.return_value = True
            mock_support_class.return_value = mock_support
            
            result = await wait_for_page_ready(mock_session)
            assert result is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
