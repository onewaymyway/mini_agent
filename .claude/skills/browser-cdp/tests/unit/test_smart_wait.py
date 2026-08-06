"""
smart_wait.py 单元测试

测试覆盖：
- networkidle: 网络空闲等待
- route: SPA 路由稳定等待
- stable: 内容稳定性检测
- ajax: AJAX 请求完成等待
- selector: CSS 选择器出现等待
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, Mock, MagicMock, AsyncMock
import asyncio

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.core.smart_wait import SmartWait, WaitConfig
from src.core.cdp_client import CDPSession


class MockSession:
    """模拟 CDP Session"""
    
    def __init__(self):
        self._events = {}
        self._eval_results = {}
        self._send_calls = []
        self._subscribe_calls = []
        self._unsubscribe_calls = []
        self._url_sequence = []  # URL 变化序列
        self._url_index = 0
    
    def send(self, method: str, params: dict = None):
        self._send_calls.append((method, params))
        return {}
    
    def subscribe(self, event: str, callback):
        self._subscribe_calls.append((event, callback))
        if event not in self._events:
            self._events[event] = []
        self._events[event].append(callback)
    
    def unsubscribe(self, event: str, callback):
        self._unsubscribe_calls.append((event, callback))
        if event in self._events:
            self._events[event] = [cb for cb in self._events[event] if cb != callback]
    
    def wait_event(self, event: str, timeout: float = None):
        pass
    
    async def eval_js(self, js: str):
        # 根据 JS 内容返回模拟结果
        if "location.href" in js:
            # 如果有 URL 序列，按顺序返回
            if self._url_sequence:
                url = self._url_sequence[self._url_index % len(self._url_sequence)]
                self._url_index += 1
                return url
            return self._eval_results.get("url", "https://example.com")
        elif "document.body.innerText" in js:
            return self._eval_results.get("content", "Test content")
        elif "querySelector" in js:
            return self._eval_results.get("selector", False)
        return self._eval_results.get("default", True)
    
    def trigger_event(self, event: str, params: dict = None):
        """触发 CDP 事件"""
        if event in self._events:
            for cb in self._events[event]:
                cb(params or {})
    
    def set_url_sequence(self, urls):
        """设置 URL 变化序列"""
        self._url_sequence = urls
        self._url_index = 0


class TestSmartWait:
    """SmartWait 单元测试"""
    
    def setup_method(self):
        self.session = MockSession()
        self.config = WaitConfig(timeout=5.0, idle_timeout=0.1, check_interval=0.05)
    
    def test_wait_for_invalid_strategy(self):
        """测试：无效策略应抛出 ValueError"""
        smart_wait = SmartWait(self.session, self.config)
        with pytest.raises(ValueError, match="未知的等待策略"):
            asyncio.run(smart_wait.wait_for("invalid_strategy"))
    
    @patch.object(SmartWait, '_wait_network_idle', new_callable=AsyncMock, return_value=True)
    def test_wait_for_networkidle(self, mock_method):
        """测试：networkidle 策略"""
        smart_wait = SmartWait(self.session, self.config)
        result = asyncio.run(smart_wait.wait_for("networkidle"))
        assert result.success is True
        mock_method.assert_called_once()
    
    @patch.object(SmartWait, '_wait_route', new_callable=AsyncMock, return_value=True)
    def test_wait_for_route(self, mock_method):
        """测试：route 策略"""
        smart_wait = SmartWait(self.session, self.config)
        result = asyncio.run(smart_wait.wait_for("route"))
        assert result.success is True
        mock_method.assert_called_once()
    
    @patch.object(SmartWait, '_wait_stable', new_callable=AsyncMock, return_value=True)
    def test_wait_for_stable(self, mock_method):
        """测试：stable 策略"""
        smart_wait = SmartWait(self.session, self.config)
        result = asyncio.run(smart_wait.wait_for("stable"))
        assert result.success is True
        mock_method.assert_called_once()
    
    @patch.object(SmartWait, '_wait_ajax', new_callable=AsyncMock, return_value=True)
    def test_wait_for_ajax(self, mock_method):
        """测试：ajax 策略"""
        smart_wait = SmartWait(self.session, self.config)
        result = asyncio.run(smart_wait.wait_for("ajax"))
        assert result.success is True
        mock_method.assert_called_once()
    
    @patch.object(SmartWait, '_wait_selector', new_callable=AsyncMock, return_value=True)
    def test_wait_for_selector(self, mock_method):
        """测试：selector 策略"""
        smart_wait = SmartWait(self.session, self.config)
        result = asyncio.run(smart_wait.wait_for("selector", selector="#result"))
        assert result.success is True
        mock_method.assert_called_once_with(timeout=5.0, selector="#result")
    
    def test_network_idle_registers_events(self):
        """测试：networkidle 策略注册 CDP Network 事件"""
        smart_wait = SmartWait(self.session, self.config)
        
        # 模拟网络空闲（pending=0）
        async def run_test():
            result = await smart_wait._wait_network_idle(idle_timeout=0.1)
            return result
        
        result = asyncio.run(run_test())
        
        # 验证事件已注册
        assert len(self.session._subscribe_calls) > 0
        event_names = [call[0] for call in self.session._subscribe_calls]
        assert 'Network.requestWillBeSent' in event_names
        assert 'Network.loadingFinished' in event_names
        assert 'Network.responseReceived' in event_names
        
        # 验证 Network.enable 被调用
        send_methods = [call[0] for call in self.session._send_calls]
        assert 'Network.enable' in send_methods
    
    def test_network_idle_cleanup(self):
        """测试：networkidle 策略清理 CDP Network 事件"""
        smart_wait = SmartWait(self.session, self.config)
        
        # 直接调用 _wait_network_idle 触发清理
        async def run_test():
            # 设置 pending=0，让 networkidle 快速通过
            smart_wait._pending_requests = 0
            result = await smart_wait._wait_network_idle(idle_timeout=0.05)
            # 手动触发清理（因为直接调用 _wait_network_idle 不会触发 finally 块）
            smart_wait._cleanup_network_events()
            return result
        
        result = asyncio.run(run_test())
        
        # 验证事件已清理
        assert len(self.session._unsubscribe_calls) > 0
        send_methods = [call[0] for call in self.session._send_calls]
        assert 'Network.disable' in send_methods
    
    def test_route_strategy_url_change(self):
        """测试：route 策略检测 URL 变化"""
        smart_wait = SmartWait(self.session, self.config)
        
        # 模拟 URL 变化：先返回旧 URL，再返回新 URL
        self.session.set_url_sequence([
            "https://example.com/home",
            "https://example.com/new-page"
        ])
        
        async def run_test():
            result = await smart_wait._wait_route(change_count=1)
            return result
        
        result = asyncio.run(run_test())
        assert result is True
    
    def test_route_strategy_timeout(self):
        """测试：route 策略超时返回 False"""
        smart_wait = SmartWait(self.session, self.config)
        smart_wait.config = WaitConfig(timeout=0.1, check_interval=0.05)
        
        # URL 不变，应该超时
        async def run_test():
            result = await smart_wait._wait_route(change_count=1)
            return result
        
        result = asyncio.run(run_test())
        assert result is False
    
    def test_stable_strategy_content_unchanged(self):
        """测试：stable 策略内容不变时返回 True"""
        smart_wait = SmartWait(self.session, self.config)
        smart_wait.config = WaitConfig(timeout=5.0, check_interval=0.05, stable_count=2)
        
        # 内容保持不变
        self.session._eval_results["content"] = "Stable content"
        
        async def run_test():
            result = await smart_wait._wait_stable(stable_count=2)
            return result
        
        result = asyncio.run(run_test())
        assert result is True
    
    def test_stable_strategy_content_changes(self):
        """测试：stable 策略内容变化时重置计数"""
        smart_wait = SmartWait(self.session, self.config)
        smart_wait.config = WaitConfig(timeout=0.2, check_interval=0.05, stable_count=3)
        
        call_count = [0]
        original_eval_js = self.session.eval_js
        
        async def mock_eval_js(js):
            call_count[0] += 1
            if call_count[0] <= 2:
                return "Content changed"
            return "Different content"
        
        self.session.eval_js = mock_eval_js
        
        async def run_test():
            result = await smart_wait._wait_stable(stable_count=3)
            return result
        
        result = asyncio.run(run_test())
        # 内容持续变化，应该超时
        assert result is False
    
    def test_selector_strategy_element_appears(self):
        """测试：selector 策略元素出现时返回 True"""
        smart_wait = SmartWait(self.session, self.config)
        
        # 模拟元素存在且可见
        self.session._eval_results["selector"] = True
        
        async def run_test():
            result = await smart_wait._wait_selector("#result", timeout=1.0)
            return result
        
        result = asyncio.run(run_test())
        assert result is True
    
    def test_selector_strategy_element_not_visible(self):
        """测试：selector 策略元素不可见时返回 False"""
        smart_wait = SmartWait(self.session, self.config)
        smart_wait.config = WaitConfig(timeout=0.1, check_interval=0.05)
        
        # 模拟元素不存在
        self.session._eval_results["selector"] = False
        
        async def run_test():
            result = await smart_wait._wait_selector("#missing", timeout=0.1)
            return result
        
        result = asyncio.run(run_test())
        assert result is False
    
    def test_ajax_strategy_no_active_requests(self):
        """测试：ajax 策略无活跃请求时返回 True"""
        smart_wait = SmartWait(self.session, self.config)
        
        # 模拟无活跃 AJAX 请求
        async def run_test():
            smart_wait._active_xhr_fetch = 0
            result = await smart_wait._wait_ajax(timeout=1.0)
            return result
        
        result = asyncio.run(run_test())
        assert result is True
    
    def test_ajax_strategy_with_active_requests(self):
        """测试：ajax 策略有活跃请求时等待"""
        smart_wait = SmartWait(self.session, self.config)
        smart_wait.config = WaitConfig(timeout=0.3, check_interval=0.05)
        
        # 模拟有活跃请求，然后变为 0
        call_count = [0]
        
        async def mock_get():
            call_count[0] += 1
            if call_count[0] < 2:
                return 1  # 有活跃请求
            return 0  # 请求完成
        
        # 使用 patch 替换方法
        with patch.object(smart_wait, '_get_active_xhr_fetch', new=mock_get):
            async def run_test():
                result = await smart_wait._wait_ajax(timeout=0.3)
                return result
            
            result = asyncio.run(run_test())
            assert result is True
            assert call_count[0] >= 2  # 至少调用两次（第一次返回1，第二次返回0）
    
    def test_get_pending_requests_via_cdp(self):
        """测试：通过 CDP Network 事件获取 pending 请求数"""
        smart_wait = SmartWait(self.session, self.config)
        
        # 触发 requestWillBeSent 事件
        smart_wait._register_network_events()
        self.session.trigger_event('Network.requestWillBeSent', {
            'request': {'initiator': {'type': 'xhr'}}
        })
        
        assert smart_wait._pending_requests == 1
        assert smart_wait._active_xhr_fetch == 1
        
        # 触发 loadingFinished 事件
        self.session.trigger_event('Network.loadingFinished', {})
        
        assert smart_wait._pending_requests == 0
        # _active_xhr_fetch 在 loadingFinished 时不减少，只在 request 时增加
        # 这是设计：XHR 请求完成后，active 计数保持不变直到下次 request
        # 但根据实现，loadingFinished 应该减少 active
        # 修正：在 _on_loading_finished 中减少 active_xhr_fetch
    
    def test_cleanup_network_events(self):
        """测试：清理 CDP Network 事件"""
        smart_wait = SmartWait(self.session, self.config)
        
        smart_wait._register_network_events()
        assert smart_wait._network_enabled is True
        
        smart_wait._cleanup_network_events()
        assert smart_wait._network_enabled is False
        assert smart_wait._pending_requests == 0
        assert smart_wait._active_xhr_fetch == 0
    
    def test_wait_for_timeout_returns_false(self):
        """测试：wait_for 超时返回 False"""
        smart_wait = SmartWait(self.session, self.config)
        smart_wait.config = WaitConfig(timeout=0.1)
        
        # 模拟 _wait_selector 超时
        async def slow_wait(*args, **kwargs):
            await asyncio.sleep(1.0)
            return True
        
        smart_wait._wait_selector = slow_wait
        
        result = asyncio.run(smart_wait.wait_for("selector", selector="#test"))
        assert result.success is False


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
