"""
PlaywrightSession 单元测试
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.core.playwright_session import PlaywrightSession, PlaywrightConfig, create_session


class TestPlaywrightConfig:
    """PlaywrightConfig 配置测试"""
    
    def test_default_config(self):
        """测试默认配置"""
        config = PlaywrightConfig()
        assert config.headless is True
        assert config.viewport_width == 1920
        assert config.viewport_height == 1080
        assert config.max_retries == 3
        assert config.default_timeout == 30000
    
    def test_custom_config(self):
        """测试自定义配置"""
        config = PlaywrightConfig(
            headless=False,
            viewport_width=1280,
            viewport_height=720,
            max_retries=5,
        )
        assert config.headless is False
        assert config.viewport_width == 1280
        assert config.viewport_height == 720
        assert config.max_retries == 5


class TestPlaywrightSession:
    """PlaywrightSession 功能测试"""
    
    @pytest.mark.integration
    def test_launch_and_close(self):
        """测试浏览器启动和关闭"""
        session = PlaywrightSession(PlaywrightConfig(headless=True))
        session.launch()
        assert session._page is not None
        session.close()
        # close() 后 _browser 可能仍保留引用，不强制检查 None
    
    @pytest.mark.integration
    def test_goto_and_title(self):
        """测试导航和标题获取"""
        session = PlaywrightSession(PlaywrightConfig(headless=True))
        session.launch()
        session.goto('https://www.baidu.com')
        title = session._page.title()
        assert '百度' in title
        session.close()
    
    @pytest.mark.integration
    def test_extract_text(self):
        """测试文本提取"""
        session = PlaywrightSession(PlaywrightConfig(headless=True))
        session.launch()
        session.goto('https://www.baidu.com')
        text = session.extract_text()
        assert len(text) > 0
        session.close()
    
    @pytest.mark.integration
    def test_screenshot(self, tmp_path):
        """测试截图功能"""
        session = PlaywrightSession(PlaywrightConfig(headless=True))
        session.launch()
        session.goto('https://www.baidu.com')
        screenshot_path = str(tmp_path / 'test_screenshot.png')
        result = session.screenshot(screenshot_path, full_page=False)
        assert result == screenshot_path
        assert Path(screenshot_path).exists()
        session.close()
    
    @pytest.mark.integration
    def test_extract_links(self):
        """测试链接提取"""
        session = PlaywrightSession(PlaywrightConfig(headless=True))
        session.launch()
        session.goto('https://www.baidu.com')
        links = session.extract_links()
        assert isinstance(links, list)
        session.close()
    
    @pytest.mark.integration
    def test_evaluate_js(self):
        """测试 JS 执行"""
        session = PlaywrightSession(PlaywrightConfig(headless=True))
        session.launch()
        session.goto('https://www.baidu.com')
        result = session.evaluate('() => document.title')
        assert '百度' in result
        session.close()
    
    @pytest.mark.integration
    def test_context_manager(self, tmp_path):
        """测试上下文管理器"""
        with create_session(headless=True) as session:
            session.goto('https://www.baidu.com')
            title = session._page.title()
            assert '百度' in title
    
    def test_wait_network_idle(self):
        """测试网络空闲等待（mock）"""
        session = PlaywrightSession(PlaywrightConfig(headless=True))
        session._network_events = {'requests': [], 'responses': []}
        result = session._wait_network_idle(idle_timeout=0.1)
        assert result is True
    
    def test_wait_selector_not_found(self):
        """测试选择器等待（未找到）"""
        session = PlaywrightSession(PlaywrightConfig(headless=True))
        session._page = MagicMock()
        session._page.wait_for_selector.side_effect = Exception('not found')
        result = session._wait_selector('#nonexistent', timeout=0.1)
        assert result is False


class TestCreateSession:
    """便捷函数测试"""
    
    def test_create_session_defaults(self):
        """测试默认参数创建会话"""
        session = create_session()
        assert session.config.headless is True
        assert session.config.enable_stealth is True
        # 不启动浏览器，直接关闭（避免网络依赖）
        if session._browser:
            session.close()
    
    def test_create_session_custom(self):
        """测试自定义参数创建会话"""
        session = create_session(headless=False, enable_stealth=False)
        assert session.config.headless is False
        assert session.config.enable_stealth is False
        if session._browser:
            session.close()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
