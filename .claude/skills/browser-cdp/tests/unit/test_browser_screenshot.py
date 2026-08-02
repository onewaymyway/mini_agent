"""
browser_screenshot.py 单元测试
"""
import pytest
import sys
from pathlib import Path
import tempfile
import time
from unittest.mock import patch, Mock, MagicMock
from io import BytesIO

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.core import browser_screenshot
from src.core.cdp_client import CDPSession


class TestBrowserScreenshot:
    """截图模块单元测试"""
    
    def test_capture_viewport(self):
        """测试：视口截图"""
        import base64
        mock_session = Mock(spec=CDPSession)
        # 返回合法的 base64 数据（1字节PNG）
        mock_session.send = Mock(return_value={'data': base64.b64encode(b'\x89PNG\r\n\x1a\n').decode()})

        result = browser_screenshot.capture(mock_session, full_page=False)

        assert isinstance(result, bytes)
        mock_session.send.assert_called_once()

    def test_capture_full_page(self):
        """测试：整页截图"""
        import base64
        mock_session = Mock(spec=CDPSession)
        # full_page=True 时先调用 Page.getLayoutMetrics，再调用 Page.captureScreenshot
        mock_session.send = Mock(side_effect=[
            {'cssContentSize': {'width': 1000, 'height': 2000}},
            {'data': base64.b64encode(b'\x89PNG\r\n\x1a\n').decode()}
        ])

        result = browser_screenshot.capture(mock_session, full_page=True)

        assert isinstance(result, bytes)
        assert mock_session.send.call_count == 2

    def test_capture_with_clip(self):
        """测试：裁剪截图"""
        import base64
        mock_session = Mock(spec=CDPSession)
        mock_session.send = Mock(return_value={'data': base64.b64encode(b'\x89PNG\r\n\x1a\n').decode()})
        clip = {'x': 0, 'y': 0, 'width': 100, 'height': 100}

        result = browser_screenshot.capture(mock_session, full_page=False, clip=clip)

        assert isinstance(result, bytes)
        mock_session.send.assert_called_once()

    def test_annotate_png(self):
        """测试：PNG标注"""
        import base64
        # 生成一个合法的1x1 PNG
        from PIL import Image
        import io
        img = Image.new('RGB', (10, 10), color='red')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        png_bytes = buf.getvalue()

        elements = [
            {'index': 0, 'tag': 'button', 'text': 'Submit',
             'rect': {'x': 1, 'y': 1, 'width': 8, 'height': 8}, 'inViewport': True}
        ]

        result = browser_screenshot.annotate_png(png_bytes, elements)
        assert isinstance(result, bytes)
        assert len(result) > 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
