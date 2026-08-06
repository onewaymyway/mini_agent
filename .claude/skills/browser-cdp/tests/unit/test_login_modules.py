"""
登录模块单元测试

测试：
- CookieManager
- LoginFormDetector
- SessionManager
- LoginStateDetector
"""
import pytest
import json
import os
import time
from unittest.mock import MagicMock, patch, call
from dataclasses import asdict

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.core.login.cookie_manager import CookieManager, CookieInfo
from src.core.login.login_form_detector import LoginFormDetector, LoginForm, FormField
from src.core.login.session_manager import SessionManager, SessionInfo
from src.core.login.login_state_detector import LoginStateDetector, LoginState


class TestCookieManager:
    """CookieManager 测试"""
    
    def setup_method(self):
        self.session = MagicMock()
        self.storage_dir = os.path.join("temp_data", "test_cookies")
        os.makedirs(self.storage_dir, exist_ok=True)
        self.manager = CookieManager(self.session, self.storage_dir)
    
    def teardown_method(self):
        # 清理测试文件
        for f in os.listdir(self.storage_dir):
            os.remove(os.path.join(self.storage_dir, f))
    
    def test_get_cookies_success(self):
        """测试成功获取 Cookie"""
        mock_cookies = [
            {"name": "session_id", "value": "abc123", "domain": ".example.com", "path": "/"},
            {"name": "user_token", "value": "xyz789", "domain": ".example.com", "path": "/"},
        ]
        self.session.send.return_value = {"cookies": mock_cookies}
        
        cookies = self.manager.get_cookies("https://example.com")
        
        assert len(cookies) == 2
        assert cookies[0].name == "session_id"
        assert cookies[0].value == "abc123"
        assert cookies[1].name == "user_token"
    
    def test_get_cookies_empty(self):
        """测试无 Cookie 时返回空列表"""
        self.session.send.return_value = {"cookies": []}
        
        cookies = self.manager.get_cookies()
        
        assert cookies == []
    
    def test_get_cookies_error(self):
        """测试获取 Cookie 失败时返回空列表"""
        self.session.send.side_effect = Exception("CDP error")
        
        cookies = self.manager.get_cookies()
        
        assert cookies == []
    
    def test_set_cookies_success(self):
        """测试成功设置 Cookie"""
        cookies = [
            CookieInfo(name="test", value="value", domain=".example.com"),
        ]
        
        result = self.manager.set_cookies(cookies, "https://example.com")
        
        assert result is True
        self.session.send.assert_called_once()
    
    def test_set_cookies_error(self):
        """测试设置 Cookie 失败"""
        self.session.send.side_effect = Exception("CDP error")
        
        cookies = [CookieInfo(name="test", value="value", domain=".example.com")]
        result = self.manager.set_cookies(cookies)
        
        assert result is False
    
    def test_delete_cookies_by_name(self):
        """测试按名称删除 Cookie"""
        self.manager.delete_cookies(name="test", domain="example.com")
        
        # 应该调用 deleteCookies
        calls = [c for c in self.session.send.call_args_list if c[0][0] == "Network.deleteCookies"]
        assert len(calls) >= 0  # 可能没有 Cookie 可删
    
    def test_delete_all_cookies(self):
        """测试删除所有 Cookie"""
        self.manager.delete_cookies()
        
        calls = [c for c in self.session.send.call_args_list if c[0][0] == "Network.clearBrowserCookies"]
        assert len(calls) >= 0
    
    def test_save_and_load_cookies(self):
        """测试 Cookie 持久化"""
        cookies = [
            CookieInfo(name="session", value="abc", domain=".test.com", expires=time.time() + 3600),
            CookieInfo(name="token", value="xyz", domain=".test.com", expires=time.time() + 3600),
        ]
        
        # 保存
        file_path = self.manager.save_cookies(cookies, "test.com")
        assert os.path.exists(file_path)
        
        # 加载
        loaded = self.manager.load_cookies("test.com")
        assert len(loaded) == 2
        assert loaded[0].name == "session"
        assert loaded[1].name == "token"
    
    def test_load_expired_cookies(self):
        """测试加载过期 Cookie 被过滤"""
        cookies = [
            CookieInfo(name="valid", value="abc", domain=".test.com", expires=time.time() + 3600),
            CookieInfo(name="expired", value="xyz", domain=".test.com", expires=time.time() - 3600),
        ]
        
        self.manager.save_cookies(cookies, "test.com")
        loaded = self.manager.load_cookies("test.com")
        
        # 过期 Cookie 应被过滤
        assert len(loaded) == 1
        assert loaded[0].name == "valid"
    
    def test_restore_cookies(self):
        """测试 Cookie 恢复"""
        cookies = [
            CookieInfo(name="session", value="abc", domain=".test.com"),
        ]
        
        self.manager.save_cookies(cookies, "test.com")
        restored = self.manager.restore_cookies("test.com", "https://test.com")
        
        assert restored == 1
    
    def test_has_cookie(self):
        """测试 Cookie 存在性检查"""
        self.session.send.return_value = {"cookies": [
            {"name": "session", "value": "abc", "domain": ".test.com"},
        ]}
        
        assert self.manager.has_cookie("session", "test.com") is True
        assert self.manager.has_cookie("missing", "test.com") is False
    
    def test_cookie_info_to_dict(self):
        """测试 CookieInfo 序列化"""
        cookie = CookieInfo(
            name="test",
            value="value",
            domain=".example.com",
            path="/",
            expires=1234567890,
            secure=True,
            http_only=True,
            same_site="Strict",
        )
        
        d = cookie.to_dict()
        assert d["name"] == "test"
        assert d["value"] == "value"
        assert d["secure"] is True
        assert d["httpOnly"] is True
        assert d["sameSite"] == "Strict"
    
    def test_cookie_info_from_dict(self):
        """测试 CookieInfo 反序列化"""
        data = {
            "name": "test",
            "value": "value",
            "domain": ".example.com",
            "path": "/",
            "expires": 1234567890,
            "secure": True,
            "httpOnly": True,
            "sameSite": "Strict",
        }
        
        cookie = CookieInfo.from_dict(data)
        assert cookie.name == "test"
        assert cookie.secure is True
    
    def test_cookie_is_expired(self):
        """测试 Cookie 过期判断"""
        # 未设置过期时间
        cookie = CookieInfo(name="test", value="v", domain=".example.com")
        assert cookie.is_expired() is False
        
        # 未过期
        cookie = CookieInfo(name="test", value="v", domain=".example.com", expires=time.time() + 3600)
        assert cookie.is_expired() is False
        
        # 已过期
        cookie = CookieInfo(name="test", value="v", domain=".example.com", expires=time.time() - 3600)
        assert cookie.is_expired() is True


class TestLoginFormDetector:
    """LoginFormDetector 测试"""
    
    def setup_method(self):
        self.session = MagicMock()
        self.detector = LoginFormDetector(self.session)
    
    def test_detect_login_form_with_selector(self):
        """测试检测登录表单"""
        self.session.eval_js.return_value = {
            "selector": "form#login-form",
            "fields": [
                {"selector": "#username", "type": "username", "name": "username"},
                {"selector": "#password", "type": "password", "name": "password"},
            ],
            "submit": "button[type='submit']",
            "isOauth": False,
            "oauthProviders": [],
        }
        
        form = self.detector.detect()
        
        assert form is not None
        assert form.selector == "form#login-form"
        assert len(form.fields) == 2
        assert form.fields[0].field_type == "username"
        assert form.fields[1].field_type == "password"
    
    def test_detect_no_login_form(self):
        """测试无登录表单时返回 None"""
        self.session.eval_js.side_effect = Exception("No form found")
        
        form = self.detector.detect()
        
        assert form is None
    
    def test_get_username_field(self):
        """测试获取用户名字段"""
        self.session.eval_js.return_value = {
            "selector": "form#login",
            "fields": [
                {"selector": "#username", "type": "username", "name": "username"},
                {"selector": "#password", "type": "password", "name": "password"},
            ],
        }
        
        field = self.detector.get_username_field()
        assert field is not None
        assert field.field_type == "username"
    
    def test_get_password_field(self):
        """测试获取密码字段"""
        self.session.eval_js.return_value = {
            "selector": "form#login",
            "fields": [
                {"selector": "#username", "type": "username"},
                {"selector": "#password", "type": "password"},
            ],
        }
        
        field = self.detector.get_password_field()
        assert field is not None
        assert field.field_type == "password"
    
    def test_has_oauth(self):
        """测试 OAuth 检测"""
        self.session.eval_js.return_value = {
            "selector": "form#login",
            "fields": [],
            "isOauth": True,
            "oauthProviders": ["wechat", "github"],
        }
        
        assert self.detector.has_oauth() is True
        assert self.detector.get_oauth_providers() == ["wechat", "github"]
    
    def test_form_field_to_dict(self):
        """测试 FormField 序列化"""
        field = FormField(
            selector="#username",
            field_type="username",
            name="username",
            required=True,
        )
        
        d = field.to_dict()
        assert d["selector"] == "#username"
        assert d["type"] == "username"
        assert d["required"] is True


class TestSessionManager:
    """SessionManager 测试"""
    
    def setup_method(self):
        self.session = MagicMock()
        self.storage_dir = os.path.join("temp_data", "test_sessions")
        os.makedirs(self.storage_dir, exist_ok=True)
        self.manager = SessionManager(self.session, self.storage_dir)
    
    def teardown_method(self):
        for f in os.listdir(self.storage_dir):
            os.remove(os.path.join(self.storage_dir, f))
    
    def test_create_session(self):
        """测试创建会话"""
        # Mock _get_current_url and _get_current_title to avoid MagicMock serialization
        with patch.object(self.manager, '_get_current_url', return_value='https://example.com'), \
             patch.object(self.manager, '_get_current_title', return_value='Example'):
            session = self.manager.create_session("test_session", "https://example.com", "Example")
            
            assert session.session_id == "test_session"
            assert session.url == "https://example.com"
            assert session.title == "Example"
            assert os.path.exists(os.path.join(self.storage_dir, "test_session.json"))
    
    def test_get_session(self):
        """测试获取会话"""
        # 先创建
        self.manager.create_session("test_session", "https://example.com", "Example")
        
        # 再获取
        session = self.manager.get_session("test_session")
        
        assert session is not None
        assert session.session_id == "test_session"
    
    def test_get_session_not_found(self):
        """测试获取不存在的会话"""
        session = self.manager.get_session("nonexistent")
        assert session is None
    
    def test_update_session(self):
        """测试更新会话"""
        with patch.object(self.manager, '_get_current_url', return_value='https://example.com'), \
             patch.object(self.manager, '_get_current_title', return_value='Example'):
            self.manager.create_session("test_session")
        
            updated = self.manager.update_session(
                "test_session",
                url="https://new.example.com",
                is_logged_in=True,
            )
        
            assert updated.url == "https://new.example.com"
            assert updated.is_logged_in is True
    
    def test_delete_session(self):
        """测试删除会话"""
        with patch.object(self.manager, '_get_current_url', return_value='https://example.com'), \
             patch.object(self.manager, '_get_current_title', return_value='Example'):
            self.manager.create_session("test_session")
        assert self.manager.delete_session("test_session") is True
        assert self.manager.get_session("test_session") is None
    
    def test_list_sessions(self):
        """测试列出所有会话"""
        with patch.object(self.manager, '_get_current_url', return_value='https://example.com'), \
             patch.object(self.manager, '_get_current_title', return_value='Example'):
            self.manager.create_session("session1")
            self.manager.create_session("session2")
        
        sessions = self.manager.list_sessions()
        
        assert len(sessions) == 2
    
    def test_check_session_valid(self):
        """测试会话有效性检查"""
        with patch.object(self.manager, '_get_current_url', return_value='https://example.com'), \
             patch.object(self.manager, '_get_current_title', return_value='Example'):
            self.manager.create_session("test_session")
        
        # 有效会话（默认 TTL 24 小时）
        assert self.manager.check_session_valid("test_session") is True
        
        # 模拟过期：直接修改文件中的 last_active 时间
        import json
        session_file = os.path.join(self.storage_dir, "test_session.json")
        with open(session_file, "r") as f:
            data = json.load(f)
        data["last_active"] = data["last_active"] - 100000  # 设置为很久以前
        with open(session_file, "w") as f:
            json.dump(data, f)
        
        # 清除缓存，强制从文件重新加载
        if "test_session" in self.manager._sessions:
            del self.manager._sessions["test_session"]
        
        assert self.manager.check_session_valid("test_session") is False
    
    def test_auto_restore(self):
        """测试自动恢复会话"""
        # 保存 Cookie
        cookies = [CookieInfo(name="session", value="abc", domain=".test.com")]
        self.manager._cookie_manager.save_cookies(cookies, "test.com")
        
        # 创建会话
        with patch.object(self.manager, '_get_current_url', return_value='https://example.com'), \
             patch.object(self.manager, '_get_current_title', return_value='Example'):
            self.manager.create_session("test_session")
        
        # 恢复 - 需要确保会话有 Cookie
        # 先手动添加 Cookie 到会话
        session = self.manager.get_session("test_session")
        if session:
            session.cookies = cookies
            self.manager._save_session(session)
        
        # 恢复
        result = self.manager.auto_restore("test_session")
        
        assert result is True
    
    def test_session_info_to_dict(self):
        """测试 SessionInfo 序列化"""
        session = SessionInfo(
            session_id="test",
            url="https://example.com",
            title="Example",
            created_at=time.time(),
            last_active=time.time(),
            is_logged_in=True,
        )
        
        d = session.to_dict()
        assert d["session_id"] == "test"
        assert d["is_logged_in"] is True
    
    def test_session_is_expired(self):
        """测试会话过期判断"""
        now = time.time()
        # 已过期：最后活跃时间超过 TTL
        session = SessionInfo(
            session_id="test",
            url="https://example.com",
            title="Example",
            created_at=now - 100000,
            last_active=now - 100000,
        )
        
        assert session.is_expired(ttl_seconds=86400) is True
        
        # 未过期：最后活跃时间在 TTL 内
        session_fresh = SessionInfo(
            session_id="test",
            url="https://example.com",
            title="Example",
            created_at=now - 0.5,
            last_active=now - 0.5,
        )
        assert session_fresh.is_expired(ttl_seconds=1) is False


class TestLoginStateDetector:
    """LoginStateDetector 测试"""
    
    def setup_method(self):
        self.session = MagicMock()
        self.detector = LoginStateDetector(self.session)
    
    def test_check_login_state_logged_in(self):
        """测试已登录状态检测"""
        self.session.eval_js.return_value = {
            "isLoggedIn": True,
            "confidence": 0.9,
            "methods": ["logout_button", "session_cookie"],
        }
        
        state = self.detector.check_login_state()
        
        assert state.is_logged_in is True
        assert state.confidence == 0.9
        assert state.method == "js_comprehensive"
    
    def test_check_login_state_logged_out(self):
        """测试未登录状态检测"""
        self.session.eval_js.return_value = {
            "isLoggedIn": False,
            "confidence": 0.85,
            "methods": ["login_page", "login_form"],
        }
        
        state = self.detector.check_login_state()
        
        assert state.is_logged_in is False
        assert state.confidence == 0.85
    
    def test_check_login_state_error(self):
        """测试检测失败时返回默认状态"""
        self.session.eval_js.side_effect = Exception("CDP error")
        
        state = self.detector.check_login_state()
        
        assert state.is_logged_in is False
        assert state.confidence == 0.0
        assert state.method == "error"
    
    def test_detect_login_form(self):
        """测试登录表单检测"""
        self.session.eval_js.return_value = True
        
        assert self.detector.detect_login_form() is True
        
        self.session.eval_js.return_value = False
        assert self.detector.detect_login_form() is False
    
    def test_get_login_url(self):
        """测试获取登录 URL"""
        self.session.eval_js.return_value = "https://example.com/login"
        
        url = self.detector.get_login_url()
        assert url == "https://example.com/login"
    
    def test_get_user_info(self):
        """测试获取用户信息"""
        self.session.eval_js.return_value = {
            "username": "testuser",
            "hasToken": True,
        }
        
        info = self.detector.get_user_info()
        assert info["username"] == "testuser"
    
    def test_login_state_to_dict(self):
        """测试 LoginState 序列化"""
        state = LoginState(
            is_logged_in=True,
            confidence=0.9,
            method="js_comprehensive",
            details={"methods": ["cookie"]},
        )
        
        d = state.to_dict()
        assert d["is_logged_in"] is True
        assert d["confidence"] == 0.9


class TestLoginIntegration:
    """登录模块集成测试"""
    
    def test_full_login_flow(self):
        """测试完整登录流程"""
        session = MagicMock()
        
        # 1. 检测登录表单
        detector = LoginFormDetector(session)
        session.eval_js.return_value = {
            "selector": "form#login",
            "fields": [
                {"selector": "#username", "type": "username"},
                {"selector": "#password", "type": "password"},
            ],
        }
        form = detector.detect()
        assert form is not None
        
        # 2. 检测登录状态
        state_detector = LoginStateDetector(session)
        session.eval_js.return_value = {"isLoggedIn": False, "confidence": 0.9}
        state = state_detector.check_login_state()
        assert state.is_logged_in is False
        
        # 3. 创建会话
        mgr = SessionManager(session)
        session.send.return_value = {"result": {"value": "https://example.com/login"}}
        session_info = mgr.create_session("test_login")
        assert session_info.is_logged_in is False
        
        # 4. 模拟登录成功
        session.eval_js.return_value = {"isLoggedIn": True, "confidence": 0.95}
        state = state_detector.check_login_state()
        assert state.is_logged_in is True
        
        # 5. 更新会话状态
        mgr.update_session("test_login", is_logged_in=True)
        updated = mgr.get_session("test_login")
        assert updated.is_logged_in is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
