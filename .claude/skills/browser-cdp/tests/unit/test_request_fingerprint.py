"""
test_request_fingerprint.py - 请求指纹隐藏模块测试

测试自定义User-Agent、Referer和请求头的功能。
"""
import pytest
import sys
from pathlib import Path

# 添加 skill 目录到路径
skill_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(skill_root))

from src.core.request_fingerprint import (
    RequestHeaders,
    FingerprintManager,
    USER_AGENTS,
    REFERERS,
    ACCEPT_LANGUAGES,
)
from src.core.request_interceptor import InterceptedRequest, RequestInterceptor
from src.core.fingerprint_integration import (
    FingerprintConfig,
    FingerprintBrowserIntegration,
    MultiDomainFingerprintManager,
    create_fingerprint_integration,
    create_multi_domain_manager,
)


class TestRequestHeaders:
    """测试RequestHeaders类"""
    
    def test_default_headers(self):
        """测试默认请求头"""
        headers = RequestHeaders()
        result = headers.get_headers()
        
        assert "User-Agent" in result
        assert "Accept" in result
        assert "Accept-Language" in result
        assert result["User-Agent"] in USER_AGENTS
        assert result["Accept-Language"] in ACCEPT_LANGUAGES
    
    def test_custom_user_agent(self):
        """测试自定义User-Agent"""
        custom_ua = "Mozilla/5.0 (Custom Browser)"
        headers = RequestHeaders(user_agent=custom_ua)
        result = headers.get_headers()
        
        assert result["User-Agent"] == custom_ua
    
    def test_custom_referer(self):
        """测试自定义Referer"""
        custom_ref = "https://example.com"
        headers = RequestHeaders(referer=custom_ref)
        result = headers.get_headers()
        
        assert result["Referer"] == custom_ref
    
    def test_custom_accept_language(self):
        """测试自定义Accept-Language"""
        custom_lang = "en-US,en;q=0.9"
        headers = RequestHeaders(accept_language=custom_lang)
        result = headers.get_headers()
        
        assert result["Accept-Language"] == custom_lang
    
    def test_custom_headers(self):
        """测试自定义请求头"""
        headers = RequestHeaders(custom_headers={
            "X-Custom-Header": "custom-value",
            "Authorization": "Bearer token123"
        })
        result = headers.get_headers()
        
        assert result["X-Custom-Header"] == "custom-value"
        assert result["Authorization"] == "Bearer token123"
    
    def test_set_referer_method(self):
        """测试set_referer方法"""
        headers = RequestHeaders()
        headers.set_referer("https://test.com")
        
        result = headers.get_headers()
        assert result["Referer"] == "https://test.com"
    
    def test_set_custom_header_method(self):
        """测试set_custom_header方法"""
        headers = RequestHeaders()
        headers.set_custom_header("X-Test", "value")
        
        result = headers.get_headers()
        assert result["X-Test"] == "value"
    
    def test_header_combination(self):
        """测试组合配置"""
        headers = RequestHeaders(
            user_agent="Custom UA",
            referer="https://example.com",
            accept_language="zh-CN,zh",
            custom_headers={"X-Test": "value"}
        )
        result = headers.get_headers()
        
        assert result["User-Agent"] == "Custom UA"
        assert result["Referer"] == "https://example.com"
        assert result["Accept-Language"] == "zh-CN,zh"
        assert result["X-Test"] == "value"


class TestFingerprintManager:
    """测试FingerprintManager类"""
    
    def setup_method(self):
        """每个测试前重置管理器"""
        from src.core.request_fingerprint import reset_fingerprint_manager
        reset_fingerprint_manager()
    
    def test_singleton_pattern(self):
        """测试单例模式"""
        from src.core.request_fingerprint import get_fingerprint_manager
        
        manager1 = get_fingerprint_manager()
        manager2 = get_fingerprint_manager()
        
        assert manager1 is manager2
    
    def test_enable_disable(self):
        """测试启用/禁用"""
        manager = FingerprintManager()
        
        assert manager.is_enabled() == True
        manager.disable()
        assert manager.is_enabled() == False
        manager.enable()
        assert manager.is_enabled() == True
    
    def test_set_default_headers(self):
        """测试设置默认请求头"""
        manager = FingerprintManager()
        headers = RequestHeaders(user_agent="Default UA")
        manager.set_default_headers(headers)
        
        result = manager.get_headers_for_domain("example.com")
        assert result.user_agent == "Default UA"
    
    def test_domain_specific_headers(self):
        """测试域名特定请求头"""
        manager = FingerprintManager()
        headers1 = RequestHeaders(user_agent="UA1")
        headers2 = RequestHeaders(user_agent="UA2")
        
        manager.add_domain_config("site1.com", headers1)
        manager.add_domain_config("site2.com", headers2)
        
        result1 = manager.get_headers_for_domain("site1.com")
        result2 = manager.get_headers_for_domain("site2.com")
        result3 = manager.get_headers_for_domain("other.com")
        
        assert result1.user_agent == "UA1"
        assert result2.user_agent == "UA2"
        assert result3 != "UA1" and result3 != "UA2"  # 其他域名使用随机配置
    
    def test_remove_domain_config(self):
        """测试移除域名配置"""
        manager = FingerprintManager()
        headers = RequestHeaders(user_agent="Test UA")
        
        manager.add_domain_config("test.com", headers)
        manager.remove_domain_config("test.com")
        
        result = manager.get_headers_for_domain("test.com")
        assert result.user_agent != "Test UA"  # 应该返回随机UA
    
    def test_clear_all(self):
        """测试清除所有配置"""
        manager = FingerprintManager()
        headers1 = RequestHeaders(user_agent="UA1")
        headers2 = RequestHeaders(user_agent="UA2")
        
        manager.add_domain_config("site1.com", headers1)
        manager.add_domain_config("site2.com", headers2)
        manager.set_default_headers(RequestHeaders(user_agent="Default UA"))
        
        manager.clear_all()
        
        result = manager.get_headers_for_domain("site1.com")
        assert result.user_agent != "UA1"
        assert result.user_agent != "UA2"
        assert result.user_agent != "Default UA"
    
    def test_generate_random_headers(self):
        """测试生成随机请求头"""
        manager = FingerprintManager()
        
        for _ in range(10):
            headers = manager.generate_random_headers()
            result_headers = headers.get_headers()
            assert result_headers["User-Agent"] in USER_AGENTS
            assert result_headers["Accept-Language"] in ACCEPT_LANGUAGES
    
    def test_get_predefined_config(self):
        """测试获取预定义配置"""
        manager = FingerprintManager()
        
        news_config = manager.get_predefined_config("news")
        ecommerce_config = manager.get_predefined_config("ecommerce")
        social_config = manager.get_predefined_config("social")
        
        assert news_config.referer is not None
        assert ecommerce_config.referer is not None
        assert social_config.referer is not None
    
    def test_invalid_site_type(self):
        """测试无效站点类型"""
        manager = FingerprintManager()
        
        config = manager.get_predefined_config("invalid")
        assert isinstance(config, RequestHeaders)


class TestInterceptedRequest:
    """测试InterceptedRequest类"""
    
    def test_basic_creation(self):
        """测试基本创建"""
        request = InterceptedRequest(
            request_id="1",
            url="https://example.com/page",
            method="GET",
            headers={"Host": "example.com"}
        )
        
        assert request.request_id == "1"
        assert request.url == "https://example.com/page"
        assert request.method == "GET"
    
    def test_domain_extraction(self):
        """测试域名提取"""
        request = InterceptedRequest(
            request_id="1",
            url="https://www.example.com/path",
            method="GET",
            headers={}
        )
        
        assert request.domain() == "www.example.com"
    
    def test_set_header(self):
        """测试设置请求头"""
        request = InterceptedRequest(
            request_id="1",
            url="https://example.com",
            method="GET",
            headers={}
        )
        
        request.set_header("X-Custom", "value")
        assert request.headers["X-Custom"] == "value"
    
    def test_remove_header(self):
        """测试移除请求头"""
        request = InterceptedRequest(
            request_id="1",
            url="https://example.com",
            method="GET",
            headers={"X-Test": "value"}
        )
        
        request.remove_header("X-Test")
        assert "X-Test" not in request.headers
    
    def test_invalid_url_domain(self):
        """测试无效URL的域名提取"""
        request = InterceptedRequest(
            request_id="1",
            url="not-a-url",
            method="GET",
            headers={}
        )
        
        assert request.domain() == ""


class TestFingerprintConfig:
    """测试FingerprintConfig类"""
    
    def test_default_config(self):
        """测试默认配置"""
        config = FingerprintConfig()
        
        assert config.user_agent is None
        assert config.referer is None
        assert config.auto_randomize is True
    
    def test_full_config(self):
        """测试完整配置"""
        config = FingerprintConfig(
            user_agent="Custom UA",
            referer="https://example.com",
            accept_language="en-US",
            custom_headers={"X-Test": "value"},
            auto_randomize=False
        )
        
        assert config.user_agent == "Custom UA"
        assert config.referer == "https://example.com"
        assert config.accept_language == "en-US"
        assert config.custom_headers["X-Test"] == "value"
        assert config.auto_randomize is False
    
    def test_to_request_headers(self):
        """测试转换为RequestHeaders"""
        config = FingerprintConfig(
            user_agent="Test UA",
            referer="https://test.com",
            custom_headers={"X-Custom": "value"}
        )
        
        headers = config.to_request_headers()
        
        assert headers.user_agent == "Test UA"
        assert headers.referer == "https://test.com"
        assert headers.custom_headers["X-Custom"] == "value"


class TestFingerprintBrowserIntegration:
    """测试FingerprintBrowserIntegration类（不依赖真实CDP）"""
    
    def test_create_integration(self):
        """测试创建集成器"""
        # 创建一个mock cdp_session
        class MockCDPSession:
            def send(self, method, params=None):
                pass
            def subscribe(self, event, handler):
                pass
            def unsubscribe(self, event, handler):
                pass
        
        mock_cdp = MockCDPSession()
        config = FingerprintConfig(user_agent="Test UA")
        
        integration = FingerprintBrowserIntegration(mock_cdp, config)
        
        assert not integration.is_initialized()
        integration.initialize()
        assert integration.is_initialized()
    
    def test_set_config(self):
        """测试设置配置"""
        class MockCDPSession:
            def send(self, method, params=None):
                pass
            def subscribe(self, event, handler):
                pass
            def unsubscribe(self, event, handler):
                pass
        
        mock_cdp = MockCDPSession()
        integration = FingerprintBrowserIntegration(mock_cdp)
        
        new_config = FingerprintConfig(user_agent="New UA")
        integration.set_config(new_config)
        
        assert integration.get_config().user_agent == "New UA"
    
    def test_randomize(self):
        """测试随机化"""
        class MockCDPSession:
            def send(self, method, params=None):
                pass
            def subscribe(self, event, handler):
                pass
            def unsubscribe(self, event, handler):
                pass
        
        mock_cdp = MockCDPSession()
        integration = FingerprintBrowserIntegration(mock_cdp)
        integration.initialize()
        
        # 记录原始UA
        original_ua = integration.get_config().user_agent
        
        # 随机化多次
        for _ in range(5):
            integration.randomize()
            # 每次都应该有不同的UA（概率上）
    
    def test_clear(self):
        """测试清除"""
        class MockCDPSession:
            def send(self, method, params=None):
                pass
            def subscribe(self, event, handler):
                pass
            def unsubscribe(self, event, handler):
                pass
        
        mock_cdp = MockCDPSession()
        integration = FingerprintBrowserIntegration(mock_cdp)
        integration.initialize()
        
        integration.clear()
        assert not integration.is_initialized()
    
    def test_get_status(self):
        """测试获取状态"""
        class MockCDPSession:
            def send(self, method, params=None):
                pass
            def subscribe(self, event, handler):
                pass
            def unsubscribe(self, event, handler):
                pass
        
        mock_cdp = MockCDPSession()
        integration = FingerprintBrowserIntegration(mock_cdp)
        integration.initialize()
        
        status = integration.get_status()
        
        assert status["initialized"] is True
        assert "user_agent" in status
        assert "referer" in status


class TestMultiDomainFingerprintManager:
    """测试MultiDomainFingerprintManager类"""
    
    def test_create_manager(self):
        """测试创建管理器"""
        class MockCDPSession:
            def send(self, method, params=None):
                pass
            def subscribe(self, event, handler):
                pass
            def unsubscribe(self, event, handler):
                pass
        
        mock_cdp = MockCDPSession()
        manager = MultiDomainFingerprintManager(mock_cdp)
        
        assert len(manager._integrations) == 0
    
    def test_get_or_create_integration(self):
        """测试获取或创建集成器"""
        class MockCDPSession:
            def send(self, method, params=None):
                pass
            def subscribe(self, event, handler):
                pass
            def unsubscribe(self, event, handler):
                pass
        
        mock_cdp = MockCDPSession()
        manager = MultiDomainFingerprintManager(mock_cdp)
        
        # 首次获取应创建新集成器
        integration1 = manager.get_or_create_integration("site1.com")
        assert integration1.is_initialized()
        
        # 再次获取应返回同一个集成器
        integration2 = manager.get_or_create_integration("site1.com")
        assert integration1 is integration2
    
    def test_remove_integration(self):
        """测试移除集成器"""
        class MockCDPSession:
            def send(self, method, params=None):
                pass
            def subscribe(self, event, handler):
                pass
            def unsubscribe(self, event, handler):
                pass
        
        mock_cdp = MockCDPSession()
        manager = MultiDomainFingerprintManager(mock_cdp)
        
        manager.get_or_create_integration("site1.com")
        manager.remove_integration("site1.com")
        
        assert "site1.com" not in manager._integrations
    
    def test_clear_all(self):
        """测试清除所有"""
        class MockCDPSession:
            def send(self, method, params=None):
                pass
            def subscribe(self, event, handler):
                pass
            def unsubscribe(self, event, handler):
                pass
        
        mock_cdp = MockCDPSession()
        manager = MultiDomainFingerprintManager(mock_cdp)
        
        manager.get_or_create_integration("site1.com")
        manager.get_or_create_integration("site2.com")
        manager.clear_all()
        
        assert len(manager._integrations) == 0
        assert manager._default_config is None
    
    def test_get_all_status(self):
        """测试获取所有状态"""
        class MockCDPSession:
            def send(self, method, params=None):
                pass
            def subscribe(self, event, handler):
                pass
            def unsubscribe(self, event, handler):
                pass
        
        mock_cdp = MockCDPSession()
        manager = MultiDomainFingerprintManager(mock_cdp)
        
        manager.get_or_create_integration("site1.com")
        manager.get_or_create_integration("site2.com")
        
        status = manager.get_all_status()
        
        assert "site1.com" in status
        assert "site2.com" in status
        assert status["site1.com"]["initialized"] is True


class TestConvenienceFunctions:
    """测试便捷函数"""
    
    def test_create_fingerprint_integration(self):
        """测试create_fingerprint_integration函数"""
        class MockCDPSession:
            def send(self, method, params=None):
                pass
            def subscribe(self, event, handler):
                pass
            def unsubscribe(self, event, handler):
                pass
        
        mock_cdp = MockCDPSession()
        integration = create_fingerprint_integration(mock_cdp)
        
        assert isinstance(integration, FingerprintBrowserIntegration)
    
    def test_create_multi_domain_manager(self):
        """测试create_multi_domain_manager函数"""
        class MockCDPSession:
            def send(self, method, params=None):
                pass
            def subscribe(self, event, handler):
                pass
            def unsubscribe(self, event, handler):
                pass
        
        mock_cdp = MockCDPSession()
        manager = create_multi_domain_manager(mock_cdp)
        
        assert isinstance(manager, MultiDomainFingerprintManager)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])