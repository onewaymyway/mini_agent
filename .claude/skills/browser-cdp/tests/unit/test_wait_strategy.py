"""
test_wait_strategy.py - 动态页面渲染等待策略测试
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from src.core.wait_strategy import (
    RenderStatus,
    ScrollState,
    PopupType,
    WaitForResult,
    ScrollResult,
    JSRenderDetector,
    ScrollLoadMonitor,
    PopupInterceptor,
    SPAWaitStrategy,
)


class MockSession:
    """模拟CDP session"""
    def __init__(self):
        self._eval_results = {}
        self._goto_url = None
    
    async def eval_js(self, script: str):
        if script in self._eval_results:
            return self._eval_results[script]
        if "document.readyState" in script:
            return "complete"
        if "window.location.href" in script:
            return "https://example.com"
        if "scrollHeight" in script:
            return 2000
        if "querySelectorAll" in script and "length" in script:
            return 10
        return None
    
    async def goto(self, url: str):
        self._goto_url = url
        return True
    
    def set_eval_result(self, script: str, result):
        self._eval_results[script] = result


class TestRenderStatus:
    def test_enum_values(self):
        assert RenderStatus.LOADING.value == "loading"
        assert RenderStatus.COMPLETE.value == "complete"
        assert RenderStatus.TIMEOUT.value == "timeout"


class TestScrollState:
    def test_enum_values(self):
        assert ScrollState.STABLE.value == "stable"
        assert ScrollState.END_REACHED.value == "end_reached"


class TestPopupType:
    def test_enum_values(self):
        assert PopupType.COOKIE_BANNER.value == "cookie_banner"
        assert PopupType.CAPTCHA.value == "captcha"


class TestWaitForResult:
    def test_to_dict(self):
        result = WaitForResult(
            status=RenderStatus.COMPLETE,
            wait_time=1.5,
            items_count=10,
            url="https://example.com",
            details={"key": "value"}
        )
        d = result.to_dict()
        assert d["status"] == "complete"
        assert d["wait_time"] == 1.5
        assert d["items_count"] == 10
        assert d["url"] == "https://example.com"
        assert d["details"]["key"] == "value"
    
    def test_timeout_result(self):
        result = WaitForResult(
            status=RenderStatus.TIMEOUT,
            wait_time=30.0,
            error="timeout"
        )
        assert result.status == RenderStatus.TIMEOUT
        assert result.error == "timeout"


class TestScrollResult:
    def test_end_reached(self):
        result = ScrollResult(
            state=ScrollState.END_REACHED,
            pages_scrolled=5,
            total_items=100
        )
        assert result.state == ScrollState.END_REACHED
        assert result.total_items == 100


class TestJSRenderDetector:
    def test_init(self):
        mock_session = MagicMock()
        detector = JSRenderDetector(mock_session)
        assert detector._session is mock_session
        assert detector._mutation_observer_installed is False
    
    def test_install_mutation_observer_success(self):
        mock_session = MagicMock()
        mock_session.eval_js.return_value = True
        detector = JSRenderDetector(mock_session)
        result = detector.install_mutation_observer()
        assert result is True
        assert detector._mutation_observer_installed is True
    
    def test_install_mutation_observer_fail(self):
        mock_session = MagicMock()
        mock_session.eval_js.side_effect = Exception("error")
        detector = JSRenderDetector(mock_session)
        result = detector.install_mutation_observer()
        assert result is False
    
    def test_get_mutation_count(self):
        mock_session = MagicMock()
        mock_session.eval_js.return_value = 42
        detector = JSRenderDetector(mock_session)
        detector._mutation_observer_installed = True
        count = detector.get_mutation_count()
        assert count == 42
    
    def test_get_mutation_count_error(self):
        mock_session = MagicMock()
        mock_session.eval_js.side_effect = Exception("error")
        detector = JSRenderDetector(mock_session)
        count = detector.get_mutation_count()
        assert count == 0


class TestScrollLoadMonitor:
    def test_init(self):
        mock_session = MagicMock()
        monitor = ScrollLoadMonitor(mock_session)
        assert monitor._session is mock_session
    
    def test_on_content_loaded(self):
        mock_session = MagicMock()
        monitor = ScrollLoadMonitor(mock_session)
        callback = MagicMock()
        monitor.on_content_loaded(callback)
        assert len(monitor._scroll_callbacks) == 1
    
    def test_scroll_by(self):
        mock_session = MagicMock()
        mock_session.eval_js = AsyncMock()
        monitor = ScrollLoadMonitor(mock_session)
        # This would need async testing
    
    def test_get_page_height(self):
        mock_session = MagicMock()
        mock_session.eval_js = AsyncMock(return_value=2000)
        monitor = ScrollLoadMonitor(mock_session)
        height = monitor._get_page_height()
        # Note: this is async, would need await


class TestPopupInterceptor:
    def test_init(self):
        mock_session = MagicMock()
        interceptor = PopupInterceptor(mock_session)
        assert interceptor._session is mock_session
        assert len(interceptor._intercepted_popups) == 0
    
    def test_clear_history(self):
        mock_session = MagicMock()
        interceptor = PopupInterceptor(mock_session)
        interceptor._intercepted_popups.append({"type": "test"})
        interceptor.clear_history()
        assert len(interceptor._intercepted_popups) == 0
    
    def test_popup_selectors_defined(self):
        assert len(PopupInterceptor.POPUP_SELECTORS) > 0
        assert PopupType.COOKIE_BANNER in PopupInterceptor.POPUP_SELECTORS
    
    def test_close_selectors_defined(self):
        assert len(PopupInterceptor.CLOSE_SELECTORS) > 0


class TestSPAWaitStrategy:
    def test_init(self):
        mock_session = MagicMock()
        strategy = SPAWaitStrategy(mock_session)
        assert strategy._session is mock_session
        assert isinstance(strategy._js_detector, JSRenderDetector)
        assert isinstance(strategy._scroll_monitor, ScrollLoadMonitor)
        assert isinstance(strategy._popup_interceptor, PopupInterceptor)
    
    def test_properties(self):
        mock_session = MagicMock()
        strategy = SPAWaitStrategy(mock_session)
        assert strategy.js_detector is strategy._js_detector
        assert strategy.scroll_monitor is strategy._scroll_monitor
        assert strategy.popup_interceptor is strategy._popup_interceptor


class TestIntegration:
    def test_wait_strategy_creation(self):
        mock_session = MagicMock()
        strategy = SPAWaitStrategy(mock_session)
        assert strategy is not None
        assert strategy._js_detector is not None
    
    def test_all_components_initialized(self):
        mock_session = MagicMock()
        strategy = SPAWaitStrategy(mock_session)
        assert isinstance(strategy.js_detector, JSRenderDetector)
        assert isinstance(strategy.scroll_monitor, ScrollLoadMonitor)
        assert isinstance(strategy.popup_interceptor, PopupInterceptor)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
