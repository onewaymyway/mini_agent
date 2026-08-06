"""
test_browser_interactions.py - BrowserInteractions 模块单元测试

测试 browser_interactions.py 中的所有功能：
1. 动态页面支持（等待、滚动、SPA路由）
2. 表单操作（填写、提交）
3. 弹窗处理
4. AJAX 监控
5. 页面状态管理
6. 组合操作
7. 错误恢复
8. 统计信息
"""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch, call
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.core.browser_interactions import (
    BrowserInteractions,
    InteractionStats,
    InteractionResult,
    PopupType,
    ErrorRecoveryStrategy,
)


# ============================================================================
# Mock 类定义
# ============================================================================

@dataclass
class MockScrollResult:
    pages_loaded: int = 0
    items_found: int = 0
    success: bool = True


@dataclass
class MockSPAInfo:
    framework: MagicMock = None
    version: str = "1.0"
    is_spa: bool = True


class MockSession:
    """模拟 CDP Session"""
    
    def __init__(self):
        self.eval_js_calls = []
        self.send_calls = []
        self._page_state = {
            "url": "https://example.com",
            "title": "Test Page",
            "scrollY": 0,
            "scrollHeight": 1000,
            "elementCount": 50,
        }
    
    async def eval_js(self, js: str) -> Any:
        """模拟 JS 执行"""
        self.eval_js_calls.append(js)
        
        # 根据 JS 内容返回模拟结果
        if "location.href" in js:
            return self._page_state["url"]
        elif "document.title" in js:
            return self._page_state["title"]
        elif "scrollY" in js:
            return self._page_state["scrollY"]
        elif "scrollHeight" in js:
            return self._page_state["scrollHeight"]
        elif "querySelectorAll" in js:
            return self._page_state["elementCount"]
        elif "__ajax_requests" in js:
            return []
        else:
            return True
    
    def send(self, method: str, params: dict = None):
        """模拟 CDP 命令发送"""
        self.send_calls.append((method, params))
    
    def subscribe(self, event: str, handler):
        """模拟事件订阅"""
        pass
    
    def unsubscribe(self, event: str, handler=None):
        """模拟事件取消订阅"""
        pass


# ============================================================================
# InteractionStats 测试
# ============================================================================

class TestInteractionStats:
    """测试交互统计"""
    
    def test_default_stats(self):
        """测试默认统计"""
        stats = InteractionStats()
        assert stats.total_operations == 0
        assert stats.successful_operations == 0
        assert stats.failed_operations == 0
        assert stats.total_elapsed == 0.0
    
    def test_record_success(self):
        """测试记录成功操作"""
        stats = InteractionStats()
        stats.record_success(1.5)
        assert stats.total_operations == 1
        assert stats.successful_operations == 1
        assert stats.failed_operations == 0
        assert stats.total_elapsed == 1.5
    
    def test_record_failure(self):
        """测试记录失败操作"""
        stats = InteractionStats()
        stats.record_failure(0.5)
        assert stats.total_operations == 1
        assert stats.successful_operations == 0
        assert stats.failed_operations == 1
        assert stats.total_elapsed == 0.5
    
    def test_to_dict(self):
        """测试统计转字典"""
        stats = InteractionStats()
        stats.record_success(1.0)
        stats.record_success(2.0)
        stats.record_failure(0.5)
        
        result = stats.to_dict()
        assert result["total_operations"] == 3
        assert result["successful_operations"] == 2
        assert result["failed_operations"] == 1
        assert result["success_rate"] == pytest.approx(66.67, abs=0.01)
        assert result["avg_elapsed"] == pytest.approx(1.75, abs=0.01)
    
    def test_to_dict_empty(self):
        """测试空统计转字典"""
        stats = InteractionStats()
        result = stats.to_dict()
        assert result["total_operations"] == 0
        assert result["success_rate"] == 0.0
        assert result["avg_elapsed"] == 0.0


# ============================================================================
# BrowserInteractions 测试
# ============================================================================

class TestBrowserInteractions:
    """测试 BrowserInteractions 类"""
    
    @pytest.fixture
    def mock_session(self):
        """创建模拟 session"""
        return MockSession()
    
    @pytest.fixture
    def interactions(self, mock_session):
        """创建 BrowserInteractions 实例"""
        return BrowserInteractions(mock_session)
    
    # =========================================================================
    # 动态页面支持测试
    # =========================================================================
    
    @pytest.mark.asyncio
    async def test_wait_for_page_ready_success(self, interactions, mock_session):
        """测试等待页面就绪成功"""
        with patch.object(interactions._dynamic_support, 'wait_for_page_ready', new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = True
            result = await interactions.wait_for_page_ready(timeout=30.0)
            assert result == True
            mock_wait.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_wait_for_page_ready_failure(self, interactions, mock_session):
        """测试等待页面就绪失败"""
        with patch.object(interactions._dynamic_support, 'wait_for_page_ready', new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = False
            result = await interactions.wait_for_page_ready(timeout=30.0)
            assert result == False
    
    @pytest.mark.asyncio
    async def test_wait_for_element_success(self, interactions, mock_session):
        """测试等待元素成功"""
        with patch.object(interactions._dynamic_support, 'wait_for_element', new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = True
            result = await interactions.wait_for_element("#result", timeout=10.0)
            assert result == True
            mock_wait.assert_called_once_with(selector="#result", timeout=10.0, visible=True)
    
    @pytest.mark.asyncio
    async def test_wait_for_element_not_found(self, interactions, mock_session):
        """测试等待元素未找到"""
        with patch.object(interactions._dynamic_support, 'wait_for_element', new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = False
            result = await interactions.wait_for_element("#missing", timeout=5.0)
            assert result == False
    
    @pytest.mark.asyncio
    async def test_scroll_to_load_success(self, interactions, mock_session):
        """测试滚动加载成功"""
        with patch.object(interactions._dynamic_support, 'scroll_to_load', new_callable=AsyncMock) as mock_scroll:
            mock_scroll.return_value = MockScrollResult(pages_loaded=3, items_found=30)
            result = await interactions.scroll_to_load(".item", max_pages=5, max_items=50)
            assert result["pages_loaded"] == 3
            assert result["items_found"] == 30
            assert result["success"] == True
    
    @pytest.mark.asyncio
    async def test_scroll_and_collect_success(self, interactions, mock_session):
        """测试滚动收集成功"""
        mock_items = [{"text": "item1"}, {"text": "item2"}]
        with patch.object(interactions._dynamic_support, 'scroll_and_collect', new_callable=AsyncMock) as mock_collect:
            mock_collect.return_value = mock_items
            result = await interactions.scroll_and_collect(".item", max_items=10)
            assert len(result) == 2
            assert result[0]["text"] == "item1"
    
    @pytest.mark.asyncio
    async def test_wait_for_lazy_images(self, interactions, mock_session):
        """测试懒加载图片等待"""
        with patch.object(interactions._dynamic_support, 'wait_for_lazy_images', new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = 5
            result = await interactions.wait_for_lazy_images(timeout=10.0)
            assert result == 5
    
    @pytest.mark.asyncio
    async def test_wait_for_dom_stable(self, interactions, mock_session):
        """测试 DOM 稳定等待"""
        with patch.object(interactions._dynamic_support, 'wait_for_dom_stable', new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = True
            result = await interactions.wait_for_dom_stable(timeout=30.0)
            assert result == True
    
    @pytest.mark.asyncio
    async def test_detect_spa(self, interactions, mock_session):
        """测试 SPA 检测"""
        mock_framework = MagicMock()
        mock_framework.value = "react"
        mock_info = MockSPAInfo()
        mock_info.framework = mock_framework
        mock_info.version = "1.0"
        mock_info.is_spa = True
        with patch.object(interactions._dynamic_support, 'detect_spa', new_callable=AsyncMock) as mock_detect:
            mock_detect.return_value = mock_info
            result = await interactions.detect_spa()
            assert result["framework"] == "react"
            assert result["version"] == "1.0"
            assert result["is_spa"] == True
    
    # =========================================================================
    # 表单操作测试
    # =========================================================================
    
    @pytest.mark.asyncio
    async def test_fill_form_success(self, interactions, mock_session):
        """测试填写表单成功"""
        with patch.object(interactions._interaction, 'submit_form', new_callable=AsyncMock) as mock_submit:
            mock_submit.return_value = InteractionResult(success=True, operation="fill_form")
            result = await interactions.fill_form("#form", {"name": "test"})
            assert result.success == True
    
    @pytest.mark.asyncio
    async def test_submit_form_success(self, interactions, mock_session):
        """测试提交表单成功"""
        with patch.object(interactions._interaction, 'submit_form', new_callable=AsyncMock) as mock_submit:
            mock_submit.return_value = InteractionResult(success=True, operation="submit_form")
            result = await interactions.submit_form("#form", {"name": "test"})
            assert result.success == True
    
    @pytest.mark.asyncio
    async def test_submit_form_failure(self, interactions, mock_session):
        """测试提交表单失败"""
        with patch.object(interactions._interaction, 'submit_form', new_callable=AsyncMock) as mock_submit:
            mock_submit.side_effect = Exception("Submit failed")
            result = await interactions.submit_form("#form", {"name": "test"})
            assert result.success == False
            assert "Submit failed" in result.error
    
    # =========================================================================
    # 弹窗处理测试
    # =========================================================================
    
    @pytest.mark.asyncio
    async def test_handle_popup_detected(self, interactions, mock_session):
        """测试处理检测到的弹窗"""
        with patch.object(interactions._interaction, 'handle_popup', new_callable=AsyncMock) as mock_handle:
            mock_handle.return_value = InteractionResult(
                success=True,
                operation="handle_popup",
                data={"popup_detected": True, "popup_type": "modal"}
            )
            result = await interactions.handle_popup(timeout=10.0)
            assert result.success == True
            assert result.data.get("popup_detected") == True
    
    @pytest.mark.asyncio
    async def test_handle_popup_not_detected(self, interactions, mock_session):
        """测试未检测到弹窗"""
        with patch.object(interactions._interaction, 'handle_popup', new_callable=AsyncMock) as mock_handle:
            mock_handle.return_value = InteractionResult(
                success=True,
                operation="handle_popup",
                data={"popup_detected": False}
            )
            result = await interactions.handle_popup(timeout=10.0)
            assert result.success == True
            assert result.data.get("popup_detected") == False
    
    @pytest.mark.asyncio
    async def test_auto_handle_popups(self, interactions, mock_session):
        """测试自动处理所有弹窗"""
        with patch.object(interactions._interaction, 'handle_popup', new_callable=AsyncMock) as mock_handle:
            # 第一次检测到弹窗，第二次未检测到
            mock_handle.side_effect = [
                InteractionResult(success=True, operation="handle_popup", data={"popup_detected": True}),
                InteractionResult(success=True, operation="handle_popup", data={"popup_detected": False}),
            ]
            results = await interactions.auto_handle_popups(timeout=5.0, max_attempts=3)
            assert len(results) == 1
            assert mock_handle.call_count == 2
    
    # =========================================================================
    # AJAX 监控测试
    # =========================================================================
    
    @pytest.mark.asyncio
    async def test_wait_for_ajax_success(self, interactions, mock_session):
        """测试等待 AJAX 成功"""
        from src.core.browser_interaction import AjaxRequest
        mock_requests = [
            AjaxRequest(url="https://api.example.com/data", method="GET", status=200, duration=0.5),
        ]
        with patch.object(interactions._interaction, 'wait_for_ajax', new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = mock_requests
            result = await interactions.wait_for_ajax(timeout=15.0)
            assert len(result) == 1
            assert result[0]["url"] == "https://api.example.com/data"
    
    @pytest.mark.asyncio
    async def test_monitor_ajax_requests(self, interactions, mock_session):
        """测试监控 AJAX 请求"""
        from src.core.browser_interaction import AjaxRequest
        mock_requests = [
            AjaxRequest(url="https://api.example.com/search", method="POST", status=200, duration=0.3),
        ]
        with patch.object(interactions._interaction, 'monitor_ajax_requests', new_callable=AsyncMock) as mock_monitor:
            mock_monitor.return_value = mock_requests
            result = await interactions.monitor_ajax_requests(timeout=30.0)
            assert len(result) == 1
            assert result[0]["method"] == "POST"
    
    # =========================================================================
    # 页面状态管理测试
    # =========================================================================
    
    @pytest.mark.asyncio
    async def test_capture_page_state(self, interactions, mock_session):
        """测试捕获页面状态"""
        from src.core.browser_interaction import PageState
        mock_state = PageState(
            url="https://example.com",
            title="Test Page",
            scroll_position=100,
            page_height=2000,
            element_count=50,
            timestamp=1234567890.0,
        )
        with patch.object(interactions._interaction, 'capture_page_state', new_callable=AsyncMock) as mock_capture:
            mock_capture.return_value = mock_state
            result = await interactions.capture_page_state()
            assert result["url"] == "https://example.com"
            assert result["title"] == "Test Page"
            assert result["scroll_position"] == 100
    
    def test_get_page_state_history(self, interactions, mock_session):
        """测试获取页面状态历史"""
        from src.core.browser_interaction import PageState
        states = [
            PageState(url="https://example.com/1", title="Page 1", timestamp=1000.0),
            PageState(url="https://example.com/2", title="Page 2", timestamp=2000.0),
        ]
        interactions._interaction._page_states = states
        result = interactions.get_page_state_history(limit=10)
        assert len(result) == 2
        assert result[0]["title"] == "Page 1"
    
    # =========================================================================
    # 组合操作测试
    # =========================================================================
    
    @pytest.mark.asyncio
    async def test_search_and_collect_success(self, interactions, mock_session):
        """测试搜索并收集成功"""
        with patch.object(interactions._interaction, 'search_and_collect', new_callable=AsyncMock) as mock_search:
            mock_search.return_value = InteractionResult(
                success=True,
                operation="search_and_collect",
                data={"query": "test", "items": [{"title": "Item 1"}]}
            )
            result = await interactions.search_and_collect(
                search_url="https://example.com/search",
                query="test",
                item_selector=".item",
                max_items=10,
            )
            assert result.success == True
            assert len(result.data["items"]) == 1
    
    @pytest.mark.asyncio
    async def test_navigate_and_collect(self, interactions, mock_session):
        """测试导航并收集"""
        mock_items = [{"text": "item1"}, {"text": "item2"}]
        with patch.object(interactions, 'wait_for_page_ready', new_callable=AsyncMock) as mock_ready:
            with patch.object(interactions, 'scroll_and_collect', new_callable=AsyncMock) as mock_scroll:
                mock_ready.return_value = True
                mock_scroll.return_value = mock_items
                result = await interactions.navigate_and_collect(
                    url="https://example.com",
                    item_selector=".item",
                    max_items=10,
                )
                assert len(result) == 2
                mock_ready.assert_called_once()
    
    # =========================================================================
    # 错误恢复测试
    # =========================================================================
    
    @pytest.mark.asyncio
    async def test_recover_from_timeout(self, interactions, mock_session):
        """测试超时错误恢复"""
        error = TimeoutError("Timeout")
        # Patch the handler in the dictionary
        mock_handler = AsyncMock(return_value=(True, "Timeout handled"))
        interactions._error_handlers["TimeoutError"] = mock_handler
        result = await interactions.recover_from_error(
            error,
            strategy=ErrorRecoveryStrategy.RETRY,
            max_retries=3,
        )
        assert result[0] == True
        mock_handler.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_recover_from_element_not_found(self, interactions, mock_session):
        """测试元素未找到错误恢复"""
        from src.reliability.error import ElementNotFoundError
        error = ElementNotFoundError(selector="#test")
        # Patch the handler in the dictionary
        mock_handler = AsyncMock(return_value=(True, "Element not found, skipped"))
        interactions._error_handlers["ElementNotFoundError"] = mock_handler
        result = await interactions.recover_from_error(
            error,
            strategy=ErrorRecoveryStrategy.SKIP,
            max_retries=1,
        )
        assert result[0] == True
        mock_handler.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_recover_from_connection_lost(self, interactions, mock_session):
        """测试连接丢失错误恢复"""
        from src.reliability.error import CDPConnectionLostError
        error = CDPConnectionLostError({"reason": "Connection lost"})
        # Patch the handler in the dictionary
        mock_handler = AsyncMock(return_value=(False, "Connection lost, cannot recover"))
        interactions._error_handlers["CDPConnectionLostError"] = mock_handler
        result = await interactions.recover_from_error(
            error,
            strategy=ErrorRecoveryStrategy.RETRY,
            max_retries=3,
        )
        assert result[0] == False
        mock_handler.assert_called_once()
    
    # =========================================================================
    # 统计信息测试
    # =========================================================================
    
    def test_get_stats(self, interactions, mock_session):
        """测试获取统计信息"""
        # 模拟一些操作
        interactions._stats.record_success(1.0)
        interactions._stats.record_success(2.0)
        interactions._stats.record_failure(0.5)
        
        stats = interactions.get_stats()
        assert stats["total_operations"] == 3
        assert stats["successful_operations"] == 2
        assert stats["failed_operations"] == 1
    
    def test_reset_stats(self, interactions, mock_session):
        """测试重置统计信息"""
        interactions._stats.record_success(1.0)
        interactions.reset_stats()
        
        stats = interactions.get_stats()
        assert stats["total_operations"] == 0
        assert stats["successful_operations"] == 0
        assert stats["failed_operations"] == 0
    
    # =========================================================================
    # 错误处理测试
    # =========================================================================
    
    @pytest.mark.asyncio
    async def test_wait_for_page_ready_exception(self, interactions, mock_session):
        """测试等待页面就绪异常处理"""
        with patch.object(interactions._dynamic_support, 'wait_for_page_ready', new_callable=AsyncMock) as mock_wait:
            mock_wait.side_effect = Exception("Unexpected error")
            result = await interactions.wait_for_page_ready(timeout=30.0)
            assert result == False
    
    @pytest.mark.asyncio
    async def test_scroll_to_load_exception(self, interactions, mock_session):
        """测试滚动加载异常处理"""
        with patch.object(interactions._dynamic_support, 'scroll_to_load', new_callable=AsyncMock) as mock_scroll:
            mock_scroll.side_effect = Exception("Scroll failed")
            result = await interactions.scroll_to_load(".item", max_pages=5)
            assert result["success"] == False
            assert result["pages_loaded"] == 0
    
    @pytest.mark.asyncio
    async def test_handle_popup_exception(self, interactions, mock_session):
        """测试弹窗处理异常"""
        with patch.object(interactions._interaction, 'handle_popup', new_callable=AsyncMock) as mock_handle:
            mock_handle.side_effect = Exception("Popup error")
            result = await interactions.handle_popup(timeout=10.0)
            assert result.success == False
            assert "Popup error" in result.error


# ============================================================================
# 便捷函数测试
# ============================================================================

class TestConvenienceFunctions:
    """测试便捷函数"""
    
    @pytest.fixture
    def mock_session(self):
        return MockSession()
    
    @pytest.mark.asyncio
    async def test_wait_for_page_ready_function(self, mock_session):
        """测试 wait_for_page_ready 便捷函数"""
        from src.core.browser_interactions import wait_for_page_ready
        with patch('src.core.browser_interactions.BrowserInteractions') as mock_class:
            mock_instance = MagicMock()
            mock_instance.wait_for_page_ready = AsyncMock(return_value=True)
            mock_class.return_value = mock_instance
            
            result = await wait_for_page_ready(mock_session, timeout=30.0)
            assert result == True
            mock_instance.wait_for_page_ready.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_scroll_and_collect_function(self, mock_session):
        """测试 scroll_and_collect 便捷函数"""
        from src.core.browser_interactions import scroll_and_collect
        with patch('src.core.browser_interactions.BrowserInteractions') as mock_class:
            mock_instance = MagicMock()
            mock_instance.scroll_and_collect = AsyncMock(return_value=[{"text": "item1"}])
            mock_class.return_value = mock_instance
            
            result = await scroll_and_collect(mock_session, ".item", max_items=10)
            assert len(result) == 1
    
    @pytest.mark.asyncio
    async def test_handle_popup_function(self, mock_session):
        """测试 handle_popup 便捷函数"""
        from src.core.browser_interactions import handle_popup
        with patch('src.core.browser_interactions.BrowserInteractions') as mock_class:
            mock_instance = MagicMock()
            mock_instance.handle_popup = AsyncMock(return_value=InteractionResult(success=True, operation="handle_popup"))
            mock_class.return_value = mock_instance
            
            result = await handle_popup(mock_session, timeout=10.0)
            assert result.success == True
    
    @pytest.mark.asyncio
    async def test_wait_for_ajax_function(self, mock_session):
        """测试 wait_for_ajax 便捷函数"""
        from src.core.browser_interactions import wait_for_ajax
        with patch('src.core.browser_interactions.BrowserInteractions') as mock_class:
            mock_instance = MagicMock()
            mock_instance.wait_for_ajax = AsyncMock(return_value=[])
            mock_class.return_value = mock_instance
            
            result = await wait_for_ajax(mock_session, timeout=15.0)
            assert result == []
    
    @pytest.mark.asyncio
    async def test_search_and_collect_function(self, mock_session):
        """测试 search_and_collect 便捷函数"""
        from src.core.browser_interactions import search_and_collect
        with patch('src.core.browser_interactions.BrowserInteractions') as mock_class:
            mock_instance = MagicMock()
            mock_instance.search_and_collect = AsyncMock(return_value=InteractionResult(success=True, operation="search_and_collect"))
            mock_class.return_value = mock_instance
            
            result = await search_and_collect(
                mock_session,
                search_url="https://example.com/search",
                query="test",
                item_selector=".item",
            )
            assert result.success == True


# ============================================================================
# 集成测试
# ============================================================================

class TestBrowserInteractionsIntegration:
    """集成测试"""
    
    @pytest.fixture
    def mock_session(self):
        return MockSession()
    
    @pytest.fixture
    def interactions(self, mock_session):
        return BrowserInteractions(mock_session)
    
    @pytest.mark.asyncio
    async def test_full_workflow(self, interactions, mock_session):
        """测试完整工作流程"""
        # 1. 等待页面就绪
        with patch.object(interactions._dynamic_support, 'wait_for_page_ready', new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = True
            result = await interactions.wait_for_page_ready(timeout=30.0)
            assert result == True
        
        # 2. 滚动加载内容
        with patch.object(interactions._dynamic_support, 'scroll_to_load', new_callable=AsyncMock) as mock_scroll:
            mock_scroll.return_value = MockScrollResult(pages_loaded=2, items_found=20)
            result = await interactions.scroll_to_load(".item", max_pages=5)
            assert result["pages_loaded"] == 2
        
        # 3. 检查统计
        stats = interactions.get_stats()
        assert stats["total_operations"] >= 2
        assert stats["successful_operations"] >= 2
    
    @pytest.mark.asyncio
    async def test_error_recovery_workflow(self, interactions, mock_session):
        """测试错误恢复工作流程"""
        # 模拟一系列错误并恢复
        errors = [
            TimeoutError("Timeout 1"),
            Exception("Element not found"),
            ConnectionError("Connection lost"),
        ]
        
        for error in errors:
            result = await interactions.recover_from_error(error, strategy=ErrorRecoveryStrategy.RETRY)
            # 记录恢复结果
            interactions._stats.record_success(0.1) if result[0] else interactions._stats.record_failure(0.1)
        
        stats = interactions.get_stats()
        assert stats["total_operations"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
