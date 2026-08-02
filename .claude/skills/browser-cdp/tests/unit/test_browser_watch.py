"""
browser_watch.py 单元测试
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

from src.core import browser_watch
from src.core.cdp_client import CDPSession


class TestBrowserWatch:
    """协作监控模块单元测试"""
    
    def test_cmd_list_state(self, capsys):
        """测试：列出状态"""
        mock_tabs = [
            {'id': 'tab-1', 'url': 'https://example.com', 'title': 'Test'},
            {'id': 'tab-2', 'url': 'https://test.com', 'title': 'Test2'}
        ]
        with patch('src.core.browser_watch.list_tabs', return_value=mock_tabs):
            browser_watch.cmd_list_state('127.0.0.1', 9222)
            
            captured = capsys.readouterr()
            assert 'tab-1' in captured.out
            assert 'tab-2' in captured.out
    
    def test_poll_until_success(self):
        """测试：轮询成功"""
        mock_session = Mock()
        call_count = [0]
        
        def check_fn():
            call_count[0] += 1
            return call_count[0] >= 3
        
        result = browser_watch.poll_until(
            mock_session,
            check_fn,
            timeout=5.0,
            interval=0.1,
            desc="test condition"
        )
        
        assert result is True
        assert call_count[0] >= 3
    
    def test_poll_until_timeout(self):
        """测试：轮询超时"""
        mock_session = Mock()
        
        def check_fn():
            return False
        
        result = browser_watch.poll_until(
            mock_session,
            check_fn,
            timeout=0.1,
            interval=0.05,
            desc="test condition"
        )
        
        assert result is False
    
    def test_poll_until_exception_handled(self):
        """测试：轮询异常处理"""
        mock_session = Mock()
        
        def check_fn():
            raise Exception('Test error')
        
        result = browser_watch.poll_until(
            mock_session,
            check_fn,
            timeout=0.1,
            interval=0.05,
            desc="test condition"
        )
        
        assert result is False
    
    def test_main_with_list_state(self, monkeypatch):
        """测试：main 函数 --list-state 参数"""
        mock_tabs = [{'id': 'tab-1', 'url': 'https://example.com', 'title': 'Test'}]
        
        with patch('src.core.browser_watch.list_tabs', return_value=mock_tabs), \
             patch('sys.argv', ['browser_watch.py', '--list-state']):
            browser_watch.main()
    
    def test_main_with_wait_url(self, monkeypatch):
        """测试：main 函数 --wait-url-contains 参数"""
        mock_session = Mock()
        mock_session.eval_js.return_value = 'https://example.com/dashboard'
        
        with patch('src.core.browser_watch.get_session', return_value=mock_session), \
             patch('src.core.browser_watch.poll_until', return_value=True), \
             patch('sys.argv', ['browser_watch.py', '--tab', 'tab-1', '--wait-url-contains', 'dashboard']):
            browser_watch.main()
    
    def test_main_missing_required_args(self, monkeypatch):
        """测试：main 函数缺少必要参数"""
        with patch('src.core.utils.die') as mock_die:
            original_argv = sys.argv
            sys.argv = ['browser_watch.py']

            try:
                browser_watch.main()
                mock_die.assert_called_once()
            except SystemExit:
                # die() 内部调用 sys.exit(1)，会抛出 SystemExit
                pass
            finally:
                sys.argv = original_argv


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
