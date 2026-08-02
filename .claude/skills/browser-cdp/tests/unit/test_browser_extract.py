"""
browser_extract.py 单元测试
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

from src.core import browser_extract
from src.core.cdp_client import CDPSession


class TestBrowserExtract:
    """内容抽取模块单元测试"""
    
    def test_mode_html(self):
        """测试：提取 HTML"""
        mock_session = Mock(spec=CDPSession)
        mock_session.send = Mock(side_effect=[
            {'root': {'nodeId': 1}},  # DOM.getDocument
            {'outerHTML': '<html><body>Test</body></html>'}  # DOM.getOuterHTML
        ])
        
        result = browser_extract.mode_html(mock_session)
        assert '<html>' in result
        assert 'Test' in result
        assert mock_session.send.call_count == 2
    
    def test_scan_interactive_elements(self, capsys):
        """测试：扫描可交互元素"""
        mock_session = Mock(spec=CDPSession)
        mock_session.eval_js = Mock(return_value=[
            {'index': 1, 'tag': 'button', 'text': 'Submit', 'x': 100, 'y': 100, 'w': 80, 'h': 30},
            {'index': 2, 'tag': 'input', 'text': '', 'x': 100, 'y': 150, 'w': 200, 'h': 30}
        ])
        
        result = browser_extract.scan_interactive_elements(mock_session)
        assert len(result) == 2
        assert result[0]['tag'] == 'button'
        assert result[1]['tag'] == 'input'
        mock_session.eval_js.assert_called_once()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
