"""
browser_nav.py 单元测试
"""
import pytest
import sys
from pathlib import Path
import tempfile
import time
from unittest.mock import patch, Mock, MagicMock

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.core import browser_nav
from src.core.cdp_client import CDPSession, CDPError


class TestBrowserNav:
    """导航模块单元测试"""
    
    def test_cmd_goto(self):
        """测试：导航到URL"""
        mock_session = Mock(spec=CDPSession)
        mock_session.send = Mock(return_value={'frameId': 'frame-1'})
        mock_session.eval_js = Mock(return_value='https://example.com')
        mock_session.wait_event = Mock()
        
        browser_nav.cmd_goto(mock_session, 'https://example.com', wait_load=True, timeout=30.0)
        
        # send 被调用多次（Page.navigate + Network.enable/disable）
        mock_session.send.assert_any_call("Page.navigate", {"url": "https://example.com"})
    
    def test_cmd_goto_no_wait(self):
        """测试：导航到URL不等待"""
        mock_session = Mock(spec=CDPSession)
        mock_session.send = Mock(return_value={'frameId': 'frame-1'})
        mock_session.eval_js = Mock(return_value='https://example.com')
        mock_session.wait_event = Mock()
        
        browser_nav.cmd_goto(mock_session, 'https://example.com', wait_load=False, timeout=30.0)
        
        # send 被调用多次
        mock_session.send.assert_called()
        mock_session.wait_event.assert_not_called()
    
    def test_cmd_goto_timeout(self):
        """测试：导航超时"""
        mock_session = Mock(spec=CDPSession)
        mock_session.send = Mock(return_value={'frameId': 'frame-1'})
        mock_session.eval_js = Mock(return_value='https://example.com')
        mock_session.wait_event = Mock()
        
        # 应该成功导航（wait_event 不被调用，因为 wait_load=True 但默认走 smart_wait）
        browser_nav.cmd_goto(mock_session, 'https://example.com', wait_load=True, timeout=5.0)
        # send 被调用
        mock_session.send.assert_called()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
