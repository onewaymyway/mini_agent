"""
unit tests for browser_browse.py

验证错误处理、重试机制、结果格式化等功能。
"""
from __future__ import annotations

import pytest
import time
from unittest.mock import MagicMock, patch, call
from dataclasses import asdict

from src.core.browser_browse import (
    BrowseResult,
    format_result,
    BrowserError,
    BrowserErrorType,
    classify_error,
    retry_operation,
    cmd_screenshot,
    cmd_click,
    cmd_type,
    cmd_scroll,
    cmd_wait,
    cmd_hover,
    cmd_drag,
    cmd_keys,
)
from src.reliability.error import (
    ElementNotFoundError,
    NavigationTimeoutError,
    ReliabilityError,
    ErrorCategory,
)


# ============================================================================
# 测试数据
# ============================================================================

@pytest.fixture
def mock_session():
    """创建模拟 CDP session"""
    session = MagicMock()
    session.eval_js.return_value = {"url": "https://example.com", "title": "Test"}
    return session


@pytest.fixture
def mock_elements():
    """创建模拟元素列表"""
    return [
        {"index": 0, "tag": "a", "text": "Link 1", "rect": {"x": 10, "y": 20, "width": 100, "height": 30}},
        {"index": 1, "tag": "button", "text": "Button 1", "rect": {"x": 50, "y": 60, "width": 80, "height": 25}},
        {"index": 2, "tag": "input", "text": "", "rect": {"x": 100, "y": 100, "width": 200, "height": 30}},
    ]


# ============================================================================
# BrowserError 测试
# ============================================================================

class TestBrowserError:
    """测试 BrowserError 数据类"""
    
    def test_create_error(self):
        """测试创建错误对象"""
        error = BrowserError(
            error_type=BrowserErrorType.TIMEOUT,
            message="等待超时",
            operation="wait",
            attempt=3,
            max_attempts=3
        )
        assert error.error_type == BrowserErrorType.TIMEOUT
        assert error.message == "等待超时"
        assert error.operation == "wait"
        assert error.attempt == 3
        assert error.max_attempts == 3
    
    def test_error_str(self):
        """测试错误字符串表示"""
        error = BrowserError(
            error_type=BrowserErrorType.ELEMENT_NOT_FOUND,
            message="元素未找到",
            operation="click",
            attempt=2,
            max_attempts=3
        )
        error_str = str(error)
        assert "element_not_found" in error_str
        assert "click" in error_str
        assert "2/3" in error_str
    
    def test_error_to_dict(self):
        """测试错误转字典"""
        error = BrowserError(
            error_type=BrowserErrorType.CONNECTION_LOST,
            message="连接丢失",
            operation="navigate",
            details={"url": "https://example.com"}
        )
        error_dict = error.to_dict()
        assert error_dict["error_type"] == "connection_lost"
        assert error_dict["message"] == "连接丢失"
        assert error_dict["operation"] == "navigate"
        assert error_dict["details"]["url"] == "https://example.com"


# ============================================================================
# BrowserErrorType 测试
# ============================================================================

class TestBrowserErrorType:
    """测试错误类型枚举"""
    
    def test_all_error_types(self):
        """测试所有错误类型存在"""
        assert hasattr(BrowserErrorType, "TIMEOUT")
        assert hasattr(BrowserErrorType, "CONNECTION_LOST")
        assert hasattr(BrowserErrorType, "ELEMENT_NOT_FOUND")
        assert hasattr(BrowserErrorType, "NAVIGATION_FAILED")
        assert hasattr(BrowserErrorType, "SCREENSHOT_FAILED")
        assert hasattr(BrowserErrorType, "PAGE_CRASHED")
        assert hasattr(BrowserErrorType, "UNKNOWN")
    
    def test_error_type_values(self):
        """测试错误类型值"""
        assert BrowserErrorType.TIMEOUT.value == "timeout"
        assert BrowserErrorType.CONNECTION_LOST.value == "connection_lost"
        assert BrowserErrorType.ELEMENT_NOT_FOUND.value == "element_not_found"


# ============================================================================
# classify_error 测试
# ============================================================================

class TestClassifyError:
    """测试错误分类函数"""
    
    def test_timeout_error(self):
        """测试超时错误分类"""
        exc = Exception("Timeout waiting for element")
        assert classify_error(exc) == BrowserErrorType.TIMEOUT
    
    def test_connection_error(self):
        """测试连接错误分类"""
        exc = Exception("Connection lost")
        assert classify_error(exc) == BrowserErrorType.CONNECTION_LOST
        
        exc = Exception("Disconnected from browser")
        assert classify_error(exc) == BrowserErrorType.CONNECTION_LOST
    
    def test_element_not_found_error(self):
        """测试元素未找到错误分类"""
        exc = Exception("Selector not found")
        assert classify_error(exc) == BrowserErrorType.ELEMENT_NOT_FOUND
    
    def test_navigation_error(self):
        """测试导航错误分类"""
        exc = Exception("Navigation failed")
        assert classify_error(exc) == BrowserErrorType.NAVIGATION_FAILED
    
    def test_screenshot_error(self):
        """测试截图错误分类"""
        exc = Exception("Screenshot capture failed")
        assert classify_error(exc) == BrowserErrorType.SCREENSHOT_FAILED
    
    def test_page_crashed_error(self):
        """测试页面崩溃错误分类"""
        exc = Exception("Page crashed")
        assert classify_error(exc) == BrowserErrorType.PAGE_CRASHED
    
    def test_unknown_error(self):
        """测试未知错误分类"""
        exc = Exception("Some random error")
        assert classify_error(exc) == BrowserErrorType.UNKNOWN


# ============================================================================
# RetryConfig 测试
# ============================================================================

class TestRetryConfig:
    """测试重试配置"""
    
    def test_default_config(self):
        """测试默认配置"""
        from src.reliability.retry import RetryConfig
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.base_delay == 1.0
        assert config.max_delay == 30.0
    
    def test_custom_config(self):
        """测试自定义配置"""
        from src.reliability.retry import RetryConfig
        config = RetryConfig(max_retries=5, base_delay=2.0, max_delay=60.0)
        assert config.max_retries == 5
        assert config.base_delay == 2.0
        assert config.max_delay == 60.0


# ============================================================================
# BrowseResult 测试
# ============================================================================

class TestBrowseResult:
    """测试浏览结果"""
    
    def test_success_result(self):
        """测试成功结果"""
        result = format_result(
            operation="screenshot",
            success=True,
            data={"screenshot": "shot.png"},
            elapsed=1.5
        )
        assert result.success is True
        assert result.operation == "screenshot"
        assert result.data["screenshot"] == "shot.png"
        assert result.elapsed == 1.5
        assert result.error is None
    
    def test_error_result(self):
        """测试错误结果"""
        error = BrowserError(
            error_type=BrowserErrorType.TIMEOUT,
            message="等待超时",
            operation="wait"
        )
        result = format_result(
            operation="wait",
            success=False,
            error=error,
            elapsed=10.0
        )
        assert result.success is False
        assert result.operation == "wait"
        assert result.error is not None
        assert result.elapsed == 10.0
    
    def test_result_to_dict(self):
        """测试结果转字典"""
        result = format_result(
            operation="click",
            success=True,
            data={"index": 3},
            elapsed=0.5
        )
        result_dict = result.to_dict()
        assert result_dict["success"] is True
        assert result_dict["operation"] == "click"
        assert result_dict["elapsed"] == 0.5
        assert result_dict["data"]["index"] == 3
    
    def test_result_str_success(self):
        """测试成功结果字符串"""
        result = format_result(operation="test", success=True, elapsed=1.0)
        assert "[ok]" in str(result)
        assert "test" in str(result)
    
    def test_result_str_error(self):
        """测试错误结果字符串"""
        error = BrowserError(error_type=BrowserErrorType.UNKNOWN, message="错误", operation="test")
        result = format_result(operation="test", success=False, error=error)
        assert "[error]" in str(result)


# ============================================================================
# retry_operation 装饰器测试
# ============================================================================

class TestRetryOperation:
    """测试重试装饰器"""
    
    def test_success_on_first_attempt(self):
        """测试第一次尝试成功"""
        call_count = 0
        
        @retry_operation
        def test_func():
            nonlocal call_count
            call_count += 1
            return "success"
        
        result = test_func()
        assert result == "success"
        assert call_count == 1
    
    def test_success_after_retries(self):
        """测试重试后成功"""
        call_count = 0
        
        @retry_operation
        def test_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Temporary error")
            return "success"
        
        result = test_func()
        assert result == "success"
        assert call_count == 3
    
    def test_exhausted_retries(self):
        """测试重试耗尽"""
        call_count = 0

        @retry_operation
        def test_func():
            nonlocal call_count
            call_count += 1
            raise Exception("Permanent error")

        with pytest.raises(BrowserError):
            test_func()
        # 默认 max_retries=3，总尝试次数 = 1 初始 + 3 重试 = 4
        assert call_count == 4
    
    def test_custom_retry_config(self):
        """测试自定义重试配置"""
        call_count = 0

        @retry_operation
        def test_func():
            nonlocal call_count
            call_count += 1
            raise Exception("Error")

        from src.reliability.retry import RetryConfig
        config = RetryConfig(max_retries=2, base_delay=0.01)
        with pytest.raises(BrowserError):
            test_func(retry_config=config)
        assert call_count == 3
    
    def test_error_classification(self):
        """测试错误分类"""
        call_count = 0
        
        @retry_operation
        def test_func():
            nonlocal call_count
            call_count += 1
            raise Exception("Timeout waiting")
        
        with pytest.raises(BrowserError) as exc_info:
            test_func()
        assert exc_info.value.error_type == BrowserErrorType.TIMEOUT


# ============================================================================
# cmd_screenshot 测试
# ============================================================================

class TestCmdScreenshot:
    """测试截图命令"""
    
    @patch('src.core.browser_browse.scan_interactive_elements')
    @patch('src.core.browser_browse.capture')
    @patch('src.core.browser_browse.save_screenshot')
    def test_basic_screenshot(self, mock_save, mock_capture, mock_scan):
        """测试基本截图"""
        mock_session = MagicMock()
        mock_capture.return_value = b'fake_png_data'
        
        result = cmd_screenshot(
            mock_session,
            out="test.png"
        )
        
        assert result["success"] is True
        assert result["screenshot"] == "test.png"
        mock_capture.assert_called_once()
    
    @patch('src.core.browser_browse.scan_interactive_elements')
    @patch('src.core.browser_browse.capture')
    @patch('src.core.browser_browse.save_screenshot')
    @patch('src.core.browser_browse.annotate_png')
    def test_annotate_screenshot(self, mock_annotate, mock_save, mock_capture, mock_scan):
        """测试标注截图"""
        mock_session = MagicMock()
        mock_capture.return_value = b'fake_png_data'
        mock_scan.return_value = [
            {"index": 0, "tag": "a", "text": "Link", "rect": {"x": 10, "y": 20, "width": 100, "height": 30}, "inViewport": True}
        ]
        mock_annotate.return_value = b'annotated_png_data'
        
        result = cmd_screenshot(
            mock_session,
            out="test.png",
            annotate=True
        )
        
        assert result["success"] is True
        assert "elements_file" in result
        assert result["element_count"] == 1
        mock_annotate.assert_called_once()
    
    @patch('src.core.browser_browse.scan_interactive_elements')
    @patch('src.core.browser_browse.capture')
    def test_element_not_found(self, mock_capture, mock_scan):
        """测试元素未找到"""
        mock_session = MagicMock()
        mock_capture.return_value = b'fake_png_data'
        mock_scan.return_value = []
        
        with pytest.raises(ElementNotFoundError):
            cmd_screenshot(
                mock_session,
                out="test.png",
                element_index=5
            )


# ============================================================================
# cmd_click 测试
# ============================================================================

class TestCmdClick:
    """测试点击命令"""
    
    def test_click_by_index(self, mock_session, mock_elements):
        """测试按编号点击"""
        with patch('src.core.browser_browse.scan_interactive_elements', return_value=mock_elements):
            with patch('src.core.browser_browse.find_element_by_index', return_value=mock_elements[1]):
                with patch('src.core.browser_browse.element_center', return_value=(50, 60)):
                    with patch('src.core.browser_browse.mouse_click'):
                        result = cmd_click(
                            mock_session,
                            index=1
                        )
                        assert result["index"] == 1
                        assert result["tag"] == "button"
    
    def test_click_by_selector(self, mock_session):
        """测试按选择器点击"""
        mock_session.eval_js.return_value = {"x": 100, "y": 200}
        
        with patch('src.core.browser_browse.mouse_click'):
            result = cmd_click(
                mock_session,
                selector="#submit"
            )
            assert result["selector"] == "#submit"
    
    def test_click_selector_not_found(self, mock_session):
        """测试选择器未找到"""
        mock_session.eval_js.return_value = None
        
        with pytest.raises(ElementNotFoundError):
            cmd_click(
                mock_session,
                selector="#missing"
            )
    
    def test_click_by_text(self, mock_session, mock_elements):
        """测试按文本点击"""
        with patch('src.core.browser_browse.find_element_by_text', return_value={"x": 50, "y": 60, "text": "Button 1"}):
            with patch('src.core.browser_browse.mouse_click'):
                result = cmd_click(
                    mock_session,
                    text="Button 1"
                )
                assert result["found"] is True


# ============================================================================
# cmd_type 测试
# ============================================================================

class TestCmdType:
    """测试输入命令"""
    
    def test_type_by_index(self, mock_session, mock_elements):
        """测试按编号输入"""
        with patch('src.core.browser_browse.find_element_by_index', return_value=mock_elements[2]):
            with patch('src.core.browser_browse.element_center', return_value=(200, 115)):
                with patch('src.core.browser_browse.mouse_click'):
                    with patch('src.core.browser_browse.type_text'):
                        result = cmd_type(
                            mock_session,
                            index=2,
                            text="hello"
                        )
                        assert result["text"] == "hello"
    
    def test_type_by_selector(self, mock_session):
        """测试按选择器输入"""
        mock_session.eval_js.return_value = True
        
        with patch('src.core.browser_browse.type_text'):
            result = cmd_type(
                mock_session,
                selector="input[name='q']",
                text="test"
            )
            assert result["text"] == "test"
    
    def test_type_missing_text(self, mock_session):
        """测试缺少文本"""
        with pytest.raises(ElementNotFoundError):
            cmd_type(
                mock_session,
                index=0
            )


# ============================================================================
# cmd_scroll 测试
# ============================================================================

class TestCmdScroll:
    """测试滚动命令"""
    
    def test_scroll_to_index(self, mock_session, mock_elements):
        """测试滚动到元素"""
        with patch('src.core.browser_browse.scroll_index_into_view', return_value={"x": 10, "y": 20}):
            result = cmd_scroll(
                mock_session,
                index=1
            )
            assert result["index"] == 1
    
    def test_scroll_by(self, mock_session):
        """测试偏移滚动"""
        result = cmd_scroll(
            mock_session,
            by=(0, 500)
        )
        assert result["dx"] == 0
        assert result["dy"] == 500
    
    def test_scroll_to_top(self, mock_session):
        """测试滚动到顶部"""
        result = cmd_scroll(
            mock_session,
            to_top=True
        )
        assert result["direction"] == "top"
    
    def test_scroll_to_bottom(self, mock_session):
        """测试滚动到底部"""
        result = cmd_scroll(
            mock_session,
            to_bottom=True
        )
        assert result["direction"] == "bottom"


# ============================================================================
# cmd_wait 测试
# ============================================================================

class TestCmdWait:
    """测试等待命令"""
    
    def test_wait_selector_found(self, mock_session):
        """测试等待元素出现"""
        mock_session.eval_js.side_effect = [False, False, True]
        
        result = cmd_wait(
            mock_session,
            selector="#result",
            timeout=1.0
        )
        assert result["success"] is True
        assert result["selector"] == "#result"
    
    def test_wait_selector_timeout(self, mock_session):
        """测试等待超时"""
        mock_session.eval_js.return_value = False
        
        with pytest.raises(BrowserError) as exc_info:
            cmd_wait(
                mock_session,
                selector="#missing",
                timeout=0.1
            )
        assert exc_info.value.error_type == BrowserErrorType.TIMEOUT


# ============================================================================
# cmd_hover 测试
# ============================================================================

class TestCmdHover:
    """测试悬停命令"""
    
    def test_hover(self, mock_session, mock_elements):
        """测试悬停操作"""
        with patch('src.core.browser_browse.find_element_by_index', return_value=mock_elements[0]):
            with patch('src.core.browser_browse.element_center', return_value=(60, 35)):
                with patch('src.core.browser_browse.mouse_click'):
                    result = cmd_hover(
                        mock_session,
                        index=0
                    )
                    assert result["index"] == 0


# ============================================================================
# cmd_drag 测试
# ============================================================================

class TestCmdDrag:
    """测试拖拽命令"""
    
    def test_drag(self, mock_session, mock_elements):
        """测试拖拽操作"""
        with patch('src.core.browser_browse.find_element_by_index') as mock_find:
            mock_find.side_effect = [mock_elements[0], mock_elements[1]]
            with patch('src.core.browser_browse.drag_elements'):
                result = cmd_drag(
                    mock_session,
                    from_index=0,
                    to_index=1
                )
                assert result["from"] == 0
                assert result["to"] == 1


# ============================================================================
# cmd_keys 测试
# ============================================================================

class TestCmdKeys:
    """测试按键命令"""
    
    def test_key_enter(self, mock_session):
        """测试 Enter 键"""
        with patch('src.core.browser_browse.dispatch_key'):
            result = cmd_keys(
                mock_session,
                key="Enter"
            )
            assert result["key"] == "Enter"
    
    def test_key_tab(self, mock_session):
        """测试 Tab 键"""
        with patch('src.core.browser_browse.dispatch_key'):
            result = cmd_keys(
                mock_session,
                key="Tab"
            )
            assert result["key"] == "Tab"


# ============================================================================
# 集成测试
# ============================================================================

class TestBrowserBrowseIntegration:
    """集成测试：验证完整操作流程"""
    
    def test_full_workflow(self, mock_session, mock_elements):
        """测试完整工作流程"""
        # 模拟导航
        mock_session.eval_js.return_value = "https://example.com"
        
        # 模拟截图
        with patch('src.core.browser_browse.scan_interactive_elements', return_value=mock_elements):
            with patch('src.core.browser_browse.capture', return_value=b'fake_png'):
                with patch('src.core.browser_browse.save_screenshot'):
                    with patch('src.core.browser_browse.annotate_png', return_value=b'annotated'):
                        result = cmd_screenshot(
                            mock_session,
                            out="test.png",
                            annotate=True
                        )
                        assert result["success"] is True
    
    def test_error_recovery(self, mock_session):
        """测试错误恢复"""
        call_count = 0
        
        @retry_operation
        def failing_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ElementNotFoundError(selector="test")
            return "success"
        
        # 测试重试逻辑
        result = failing_func()
        assert result == "success"
        assert call_count == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
