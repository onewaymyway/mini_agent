"""
browser_input.py 单元测试
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

from src.core import browser_input
from src.core.cdp_client import CDPSession


class TestBrowserInput:
    """用户输入模拟模块单元测试"""
    
    def test_mouse_click(self):
        """测试：鼠标点击"""
        mock_session = Mock(spec=CDPSession)
        mock_session.send = Mock(return_value={})
        
        browser_input.mouse_click(mock_session, 100, 100)
        
        # 验证发送了正确的鼠标事件序列 (mousePressed, mouseReleased)
        assert mock_session.send.call_count == 2
        calls = mock_session.send.call_args_list
        methods = [call[0][0] for call in calls]
        assert methods == ["Input.dispatchMouseEvent", "Input.dispatchMouseEvent"]
        # 验证参数
        assert calls[0][0][1]["type"] == "mousePressed"
        assert calls[1][0][1]["type"] == "mouseReleased"
        assert calls[0][0][1]["x"] == 100
        assert calls[0][0][1]["y"] == 100
    
    def test_dispatch_key(self):
        """测试：按键分发"""
        mock_session = Mock(spec=CDPSession)
        mock_session.send = Mock(return_value={})
        
        browser_input.dispatch_key(mock_session, "Enter")
        
        calls = mock_session.send.call_args_list
        methods = [call[0][0] for call in calls]
        # Enter 有 text 字段，所以会发送 3 次: rawKeyDown, char, keyUp
        assert methods.count("Input.dispatchKeyEvent") == 3
        assert calls[0][0][1]["type"] == "rawKeyDown"
        assert calls[1][0][1]["type"] == "char"
        assert calls[2][0][1]["type"] == "keyUp"
    
    def test_dispatch_key_unknown(self):
        """测试：未知按键"""
        mock_session = Mock(spec=CDPSession)
        mock_session.send = Mock(return_value={})
        
        with pytest.raises(SystemExit):
            browser_input.dispatch_key(mock_session, "UnknownKey")
    
    def test_type_text(self):
        """测试：输入文本"""
        mock_session = Mock(spec=CDPSession)
        mock_session.send = Mock(return_value={})
        
        browser_input.type_text(mock_session, "Hi", delay=0)
        
        # 每个字符发送 3 次: keyDown, char, keyUp
        assert mock_session.send.call_count == 6
        calls = mock_session.send.call_args_list
        methods = [call[0][0] for call in calls]
        assert all(m == "Input.dispatchKeyEvent" for m in methods)
    
    def test_find_element_by_index(self):
        """测试：通过索引查找元素"""
        mock_session = Mock(spec=CDPSession)
        mock_elements = [
            {"index": 1, "tag": "button", "text": "Submit"},
            {"index": 2, "tag": "input", "text": ""}
        ]
        
        with patch.object(browser_input, 'scan_interactive_elements', return_value=mock_elements):
            result = browser_input.find_element_by_index(mock_session, 1)
            assert result == mock_elements[0]
            
            result = browser_input.find_element_by_index(mock_session, 2)
            assert result == mock_elements[1]
            
            # 不存在的索引
            with pytest.raises(SystemExit):
                browser_input.find_element_by_index(mock_session, 999)
    
    def test_focus_and_click(self):
        """测试：聚焦并点击"""
        mock_session = Mock(spec=CDPSession)
        mock_session.send = Mock(return_value={})
        
        with patch.object(browser_input, 'mouse_click') as mock_click:
            browser_input.focus_and_click(mock_session, 150, 125)
            mock_click.assert_called_with(mock_session, 150, 125)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
