"""
Mock 浏览器工厂

提供可复用的 Mock 浏览器实例，支持单元测试和 CI/CD。
"""
from unittest.mock import Mock, MagicMock, patch
from pathlib import Path
import sys
from typing import Dict, List, Optional, Any

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


class MockBrowserFactory:
    """Mock 浏览器工厂，用于单元测试"""
    
    def __init__(self):
        self._mocks = []
    
    def create_mock_session(self, url: str = "https://example.com", title: str = "Test Page") -> Mock:
        """创建 Mock CDP Session"""
        mock_session = Mock()
        mock_session.url = url
        mock_session.title = title
        mock_session.send = Mock(return_value={'frameId': 'frame-1'})
        mock_session.eval_js = Mock(return_value=url)
        mock_session.wait_event = Mock()
        return mock_session
    
    def create_mock_tab(self, tab_id: str = "test-tab-1", url: str = "about:blank", title: str = "Test Page") -> Dict:
        """创建 Mock Tab"""
        return {
            "id": tab_id,
            "url": url,
            "title": title,
            "is_active": True
        }
    
    def create_mock_browser(self, tabs: Optional[List[Dict]] = None) -> Dict:
        """创建 Mock 浏览器实例"""
        if tabs is None:
            tabs = [self.create_mock_tab()]
        return {
            "tabs": tabs,
            "active_tab_id": tabs[0]["id"] if tabs else None
        }
    
    def patch_browser_launch(self, port: int = 9333, tab_id: str = "test-tab-1") -> patch:
        """Patch browser_launch 模块用于测试"""
        mock_result = {"port": port, "tab_id": tab_id}
        return patch('src.core.browser_launch.cmd_dedicated', return_value=mock_result)
    
    def patch_cdp_client(self) -> patch:
        """Patch CDP Client 用于测试"""
        mock_client = Mock()
        mock_client.connect = Mock(return_value=True)
        mock_client.close = Mock(return_value=None)
        return patch('src.core.cdp_client.CDPClient', return_value=mock_client)
    
    def patch_browser_nav(self) -> Dict[str, patch]:
        """Patch browser_nav 模块用于测试"""
        mocks = {
            'goto': patch('src.core.browser_nav.cmd_goto', return_value=True),
            'back': patch('src.core.browser_nav.cmd_back', return_value=True),
            'forward': patch('src.core.browser_nav.cmd_forward', return_value=True),
            'refresh': patch('src.core.browser_nav.cmd_refresh', return_value=True),
            'wait_element': patch('src.core.browser_nav.cmd_wait_element', return_value=True),
        }
        for m in mocks.values():
            m.start()
        return mocks
    
    def patch_browser_extract(self) -> Dict[str, patch]:
        """Patch browser_extract 模块用于测试"""
        mocks = {
            'text': patch('src.core.browser_extract.mode_text', return_value="Test content"),
            'html': patch('src.core.browser_extract.mode_html', return_value="<html><body>Test</body></html>"),
            'links': patch('src.core.browser_extract.mode_links', return_value=["https://example.com"]),
            'elements': patch('src.core.browser_extract.mode_elements', return_value=[]),
            'meta': patch('src.core.browser_extract.mode_meta', return_value={"title": "Test", "description": "Test desc"}),
        }
        for m in mocks.values():
            m.start()
        return mocks
    
    def patch_browser_input(self) -> Dict[str, patch]:
        """Patch browser_input 模块用于测试"""
        mocks = {
            'type': patch('src.core.browser_input.cmd_type', return_value=True),
            'click': patch('src.core.browser_input.cmd_click', return_value=True),
            'scroll': patch('src.core.browser_input.cmd_scroll', return_value=True),
            'get_value': patch('src.core.browser_input.cmd_get_value', return_value="test value"),
            'select': patch('src.core.browser_input.cmd_select', return_value=True),
        }
        for m in mocks.values():
            m.start()
        return mocks
    
    def patch_browser_screenshot(self) -> Dict[str, patch]:
        """Patch browser_screenshot 模块用于测试"""
        mocks = {
            'full': patch('src.core.browser_screenshot.mode_full', return_value="/tmp/test_full.png"),
            'element': patch('src.core.browser_screenshot.mode_element', return_value="/tmp/test_element.png"),
            'annotate': patch('src.core.browser_screenshot.mode_annotate', return_value="/tmp/test_annotate.png"),
        }
        for m in mocks.values():
            m.start()
        return mocks
    
    def stop_all(self):
        """停止所有 patches"""
        # 注意：实际使用时需要跟踪 patch 对象
        pass
    
    def create_mock_cdp_session(self) -> Mock:
        """创建 Mock CDP Session（用于底层测试）"""
        mock_session = Mock()
        mock_session.send = Mock()
        mock_session.eval_js = Mock()
        mock_session.wait_event = Mock()
        return mock_session


# 全局工厂实例
_factory = MockBrowserFactory()


def get_factory() -> MockBrowserFactory:
    """获取全局 Mock 浏览器工厂实例"""
    return _factory
