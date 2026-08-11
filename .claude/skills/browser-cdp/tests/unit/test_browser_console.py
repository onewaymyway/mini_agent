"""
browser_console.py 单元测试

测试覆盖场景：
- JS 表达式执行 (cmd_eval)
- Console 日志监听 (cmd_watch_console)
- 网络请求监听 (cmd_watch_network)
- Cookie 管理 (get/set/delete/clear)

依赖模块：browser_console, cdp_client
"""
import pytest
import sys
from pathlib import Path
import tempfile
import time
from unittest.mock import patch, Mock, MagicMock
from io import StringIO

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

import src.core.browser_console as browser_console
from src.core.cdp_client import CDPSession, CDPError


class TestBrowserConsole:
    """浏览器控制台模块单元测试"""
    
    def test_cmd_eval_success(self, capsys):
        """测试：JS 表达式执行成功"""
        mock_session = Mock(spec=CDPSession)
        mock_session.eval_js.return_value = "Test Page Title"
        
        browser_console.cmd_eval(mock_session, "document.title")
        
        captured = capsys.readouterr()
        assert '"result"' in captured.out
        assert "Test Page Title" in captured.out
        mock_session.eval_js.assert_called_once_with("document.title", await_promise=True)
    
    def test_cmd_eval_exception(self):
        """测试：JS 表达式执行抛出异常"""
        mock_session = Mock(spec=CDPSession)
        mock_session.eval_js.side_effect = CDPError("JS execution error")
        
        with pytest.raises(CDPError):
            browser_console.cmd_eval(mock_session, "invalid.code")
    
    def test_cmd_watch_console_basic(self, capsys):
        """测试：Console 日志监听基础功能"""
        mock_session = Mock()
        mock_session.send = Mock()
        mock_session.drain_events.return_value = [
            {
                "method": "Runtime.consoleAPICalled",
                "params": {
                    "args": [{"value": "Hello from console"}],
                    "type": "log"
                }
            }
        ]
        
        browser_console.cmd_watch_console(mock_session, duration=5.0)
        
        captured = capsys.readouterr()
        assert "Hello from console" in captured.out
        mock_session.send.assert_any_call("Runtime.enable")
        mock_session.send.assert_any_call("Log.enable")
    
    def test_cmd_watch_console_with_exception(self, capsys):
        """测试：Console 日志监听捕获异常"""
        mock_session = Mock()
        mock_session.drain_events.return_value = [
            {
                "method": "Runtime.exceptionThrown",
                "params": {
                    "exceptionDetails": {
                        "text": "ReferenceError: x is not defined",
                        "url": "test.js:1:1"
                    }
                }
            }
        ]
        
        browser_console.cmd_watch_console(mock_session, duration=5.0)
        
        captured = capsys.readouterr()
        assert "ReferenceError" in captured.out
    
    def test_cmd_watch_network_basic(self, capsys):
        """测试：网络请求监听基础功能"""
        mock_session = Mock()
        mock_session.drain_events.return_value = [
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": "req-1",
                    "request": {
                        "url": "https://example.com/api/data",
                        "method": "GET"
                    }
                }
            },
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "req-1",
                    "response": {
                        "status": 200,
                        "mimeType": "application/json"
                    }
                }
            }
        ]
        
        browser_console.cmd_watch_network(mock_session, duration=5.0)
        
        captured = capsys.readouterr()
        assert "https://example.com/api/data" in captured.out
        assert "200" in captured.out
    
    def test_cmd_watch_network_with_error(self, capsys):
        """测试：网络请求监听捕获加载失败"""
        mock_session = Mock()
        mock_session.drain_events.return_value = [
            {
                "method": "Network.loadingFailed",
                "params": {
                    "requestId": "req-1",
                    "errorText": "Net::ERR_CONNECTION_REFUSED"
                }
            }
        ]
        
        browser_console.cmd_watch_network(mock_session, duration=5.0)
        
        captured = capsys.readouterr()
        assert "ERR_CONNECTION_REFUSED" in captured.out
    
    def test_cmd_get_cookies(self, capsys):
        """测试：获取所有 Cookies"""
        mock_session = Mock()
        mock_session.get_all_cookies.return_value = [
            {"name": "session_id", "value": "abc123", "domain": ".example.com"},
            {"name": "user", "value": "testuser", "domain": ".example.com"}
        ]
        
        browser_console.cmd_get_cookies(mock_session)
        
        captured = capsys.readouterr()
        assert "session_id" in captured.out
        assert "testuser" in captured.out
        mock_session.get_all_cookies.assert_called_once()
    
    def test_cmd_set_cookie(self, capsys):
        """测试：设置 Cookie"""
        mock_session = Mock()
        mock_session.set_cookie.return_value = {"success": True}
        
        browser_console.cmd_set_cookie(
            mock_session, 
            name="theme", 
            value="dark", 
            domain="example.com", 
            path="/"
        )
        
        captured = capsys.readouterr()
        assert "true" in captured.out.lower()
        mock_session.set_cookie.assert_called_once()
    
    def test_cmd_delete_cookie(self, capsys):
        """测试：删除 Cookie"""
        mock_session = Mock()
        mock_session.delete_cookie.return_value = {"deleted": True}
        
        browser_console.cmd_delete_cookie(mock_session, "session_id", domain="example.com")
        
        captured = capsys.readouterr()
        assert "true" in captured.out.lower()
        mock_session.delete_cookie.assert_called_once()
    
    def test_cmd_clear_cookies(self, capsys):
        """测试：清除所有 Cookies"""
        mock_session = Mock()
        mock_session.clear_all_cookies.return_value = {"cleared": True}
        
        browser_console.cmd_clear_cookies(mock_session)
        
        captured = capsys.readouterr()
        assert "true" in captured.out.lower()
        mock_session.clear_all_cookies.assert_called_once()
    
    def test_main_with_eval(self, monkeypatch):
        """测试：main 函数 --eval 参数"""
        mock_session = Mock()
        mock_session.eval_js.return_value = "Test Result"
        
        with patch('src.core.browser_console.get_session', return_value=mock_session), \
             patch('sys.argv', ['browser_console.py', '--eval', 'document.title']):
            browser_console.main()
            mock_session.eval_js.assert_called_once_with('document.title', await_promise=True)
    
    def test_main_with_watch_console(self, monkeypatch):
        """测试：main 函数 --watch-console 参数"""
        mock_session = Mock()
        mock_session.drain_events.return_value = []
        
        with patch('src.core.browser_console.get_session', return_value=mock_session), \
             patch('sys.argv', ['browser_console.py', '--watch-console', '--duration', '2.0']):
            browser_console.main()
            mock_session.send.assert_any_call('Runtime.enable')
            mock_session.send.assert_any_call('Log.enable')
            mock_session.drain_events.assert_called_once_with(duration=2.0)
    
    def test_main_with_invalid_arguments(self, monkeypatch):
        """测试：main 函数无效参数组合（无操作参数时打印帮助）"""
        with patch('src.core.browser_console.get_session') as mock_get_session, \
             patch('src.core.utils.die') as mock_die:
            mock_session = Mock()
            mock_get_session.return_value = mock_session
            original_argv = sys.argv
            sys.argv = ['browser_console.py']

            try:
                browser_console.main()
                # 无操作参数时，main() 会调用 get_session 然后打印帮助，不会调用 die
                mock_get_session.assert_called_once()
            finally:
                sys.argv = original_argv

    def test_eval_alias(self, capsys):
        """测试：eval 别名函数"""
        mock_session = Mock()
        mock_session.eval_js.return_value = "Alias Test"

        browser_console.eval(mock_session, "document.title")

        captured = capsys.readouterr()
        assert "Alias Test" in captured.out
        mock_session.eval_js.assert_called_once_with("document.title", await_promise=True)

    def test_watch_console_alias(self, capsys):
        """测试：watch_console 别名函数"""
        mock_session = Mock()
        mock_session.drain_events.return_value = []

        browser_console.watch_console(mock_session, duration=1.0)

        mock_session.drain_events.assert_called_once_with(duration=1.0)

    def test_cmd_watch_console_log_entry_added(self, capsys):
        """测试：Console 日志监听 Log.entryAdded 事件"""
        mock_session = Mock()
        mock_session.drain_events.return_value = [
            {
                "method": "Log.entryAdded",
                "params": {
                    "entry": {
                        "level": "warning",
                        "text": "Page warning message",
                        "source": "console-api"
                    }
                }
            }
        ]

        browser_console.cmd_watch_console(mock_session, duration=5.0)

        captured = capsys.readouterr()
        assert "warning" in captured.out
        assert "Page warning message" in captured.out

    def test_cmd_watch_network_missing_request_id(self, capsys):
        """测试：网络请求监听跳过无 requestId 的事件"""
        mock_session = Mock()
        mock_session.drain_events.return_value = [
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    # 缺少 requestId
                    "request": {
                        "url": "https://example.com",
                        "method": "GET"
                    }
                }
            }
        ]

        browser_console.cmd_watch_network(mock_session, duration=5.0)

        captured = capsys.readouterr()
        # 应该输出空列表
        assert "[]" in captured.out

    def test_cmd_set_cookie_full_params(self, capsys):
        """测试：设置 Cookie 完整参数"""
        mock_session = Mock()
        mock_session.set_cookie.return_value = {"success": True}

        browser_console.cmd_set_cookie(
            mock_session,
            name="tracking_id",
            value="xyz789",
            domain=".example.com",
            path="/api",
            secure=False,
            http_only=True,
            same_site="Strict",
            expires=1700000000.0
        )

        mock_session.set_cookie.assert_called_once_with(
            "tracking_id", "xyz789", ".example.com", "/api",
            False, True, "Strict", 1700000000.0
        )

    def test_cmd_delete_cookie_with_domain_path(self, capsys):
        """测试：删除 Cookie 带域名和路径"""
        mock_session = Mock()
        mock_session.delete_cookie.return_value = {"deleted": True}

        browser_console.cmd_delete_cookie(
            mock_session,
            "session_id",
            domain=".example.com",
            path="/api"
        )

        mock_session.delete_cookie.assert_called_once_with(
            "session_id", ".example.com", "/api"
        )

    def test_main_with_set_cookie(self, monkeypatch):
        """测试：main 函数 --set-cookie 参数"""
        mock_session = Mock()
        mock_session.set_cookie.return_value = {"success": True}

        with patch('src.core.browser_console.get_session', return_value=mock_session), \
             patch('sys.argv', ['browser_console.py', '--set-cookie', 'theme', 'dark',
                               '--cookie-domain', 'example.com', '--cookie-path', '/']):
            browser_console.main()
            mock_session.set_cookie.assert_called_once()

    def test_main_with_delete_cookie(self, monkeypatch):
        """测试：main 函数 --delete-cookie 参数"""
        mock_session = Mock()
        mock_session.delete_cookie.return_value = {"deleted": True}

        with patch('src.core.browser_console.get_session', return_value=mock_session), \
             patch('sys.argv', ['browser_console.py', '--delete-cookie', 'session_id',
                               '--cookie-domain', 'example.com']):
            browser_console.main()
            mock_session.delete_cookie.assert_called_once()

    def test_main_with_clear_cookies(self, monkeypatch):
        """测试：main 函数 --clear-cookies 参数"""
        mock_session = Mock()
        mock_session.clear_all_cookies.return_value = {"cleared": True}

        with patch('src.core.browser_console.get_session', return_value=mock_session), \
             patch('sys.argv', ['browser_console.py', '--clear-cookies']):
            browser_console.main()
            mock_session.clear_all_cookies.assert_called_once()

    def test_cmd_watch_network_multiple_requests(self, capsys):
        """测试：网络请求监听多个请求"""
        mock_session = Mock()
        mock_session.drain_events.return_value = [
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": "req-1",
                    "request": {"url": "https://api.example.com/data", "method": "GET"}
                }
            },
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": "req-2",
                    "request": {"url": "https://api.example.com/user", "method": "POST"}
                }
            },
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "req-1",
                    "response": {"status": 200, "mimeType": "application/json"}
                }
            },
            {
                "method": "Network.loadingFailed",
                "params": {
                    "requestId": "req-2",
                    "errorText": "Net::ERR_NAME_NOT_RESOLVED"
                }
            }
        ]

        browser_console.cmd_watch_network(mock_session, duration=5.0)

        captured = capsys.readouterr()
        assert "https://api.example.com/data" in captured.out
        assert "https://api.example.com/user" in captured.out
        assert "200" in captured.out
        assert "ERR_NAME_NOT_RESOLVED" in captured.out


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
