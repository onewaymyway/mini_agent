"""
test_login_v2.py - Standalone test for enhanced login state detector
Run with: python tests/unit/test_login_v2.py
"""
import sys
import os
import unittest
from unittest.mock import MagicMock

skill_root = r'E:\codes\mini_claude_code\.claude\skills\browser-cdp'
sys.path.insert(0, skill_root)
sys.path.insert(0, os.path.join(skill_root, 'src'))

import importlib.util

def load_module(name, rel_path):
    path = os.path.join(skill_root, rel_path)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

login_mod = load_module('login_state_detector_v2', 'src/core/login/login_state_detector_v2.py')
LoginStateDetectorV2 = login_mod.LoginStateDetectorV2
LoginState = login_mod.LoginState
check_login_state_v2 = login_mod.check_login_state_v2
is_logged_in_v2 = login_mod.is_logged_in_v2

try:
    # CaptchaLoginIntegrator requires the full package for relative imports to work
    import importlib
    captcha_mod = importlib.import_module('src.core.login.captcha_login_integrator')
    CaptchaLoginIntegrator = captcha_mod.CaptchaLoginIntegrator
    HAS_CAPTCHA = True
except Exception as e:
    print(f'Captcha module skipped: {e}')
    HAS_CAPTCHA = False


class MockSession:
    def __init__(self, cookies=None, storage=None, current_url=None):
        self.cookies = cookies or []
        self.storage = storage or {}
        self.current_url = current_url or 'https://example.com/login'
        self.page = MagicMock()
    
    def get_url(self):
        return self.current_url
    
    def get_cookies(self):
        return self.cookies
    
    def get_storage(self):
        return self.storage
    
    def wait_for_network_idle(self, timeout=5):
        return True
    
    def wait_for_page_ready(self, timeout=5):
        return True
    
    def eval_js(self, js_code):
        url = self.current_url.lower()
        # URL pattern detection
        if 'window.location' in js_code:
            success = ['/dashboard', '/home', '/user', '/profile', '/account', '/settings']
            fail = ['/login', '/signin', '/sign-in', '/auth']
            s_score = sum(15 for p in success if p in url)
            f_score = sum(20 for p in fail if p in url)
            return {'score': max(0, s_score - f_score), 'detected': []}
        # Token detection
        if 'localStorage' in js_code or 'sessionStorage' in js_code:
            import re
            jwt_re = r'^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$'
            score = 0
            for k, v in self.storage.items():
                if re.match(jwt_re, v):
                    score += 40
                if ('token' in k.lower() or 'auth' in k.lower()) and len(v) > 10:
                    score += 15
            return {'score': min(score, 100), 'detected': []}
        # Cookie detection - simulate document.cookie parsing
        if 'document.cookie' in js_code:
            score = 0
            detected = []
            cookie_str = '; '.join(f"{c['name']}={c['value']}" for c in self.cookies)
            cookies = cookie_str.split(';')
            for c in cookies:
                parts = c.trim().split('=') if hasattr(c, 'trim') else c.strip().split('=')
                name = parts[0].lower().strip()
                value = parts[1].strip() if len(parts) > 1 else ''
                if len(value) < 5:
                    continue
                if 'session' in name:
                    score += 8
                    detected.append(f'session_cookie:{name}')
                if 'auth' in name or 'token' in name:
                    score += 10
                    detected.append(f'auth_cookie:{name}')
            return {'score': min(score, 25), 'detected': detected}
        # UI element detection
        if 'querySelectorAll' in js_code:
            if any(p in url for p in ['/dashboard', '/user', '/profile', '/account']):
                return {'score': 30, 'detected': ['ui_logged_in']}
            return {'score': 0, 'detected': []}
        # OAuth callback detection
        if 'window.location.search' in js_code:
            score = 0
            if '?code=' in url:
                score += 10
            if '?state=' in url:
                score += 5
            if '?error=' in url:
                score -= 10
            return {'score': max(0, score), 'detected': []}
        # 2FA detection
        if 'input[name' in js_code and ('otp' in js_code or 'code' in js_code or 'verify' in js_code):
            if any(p in url for p in ['/2fa', '/verify', '/mfa', '/otp']):
                return {'score': 20, 'detected': ['2fa_detected']}
            return {'score': 0, 'detected': []}
        return {'score': 0, 'detected': []}


class TestLoginStateDetectorV2(unittest.TestCase):
    def setUp(self):
        self.detector = LoginStateDetectorV2(MockSession())
    
    def test_unauthenticated(self):
        """未登录状态检测"""
        session = MockSession(current_url='https://example.com/login')
        detector = LoginStateDetectorV2(session)
        state = detector.check_login_state()
        self.assertFalse(state.is_logged_in)
        self.assertLessEqual(state.score, 49)
    
    def test_authenticated_by_url(self):
        """URL判定已登录"""
        session = MockSession(current_url='https://example.com/dashboard')
        detector = LoginStateDetectorV2(session)
        state = detector.check_login_state()
        self.assertTrue(state.is_logged_in)
        self.assertGreaterEqual(state.score, 50)
    
    def test_authenticated_by_token(self):
        """Token判定已登录"""
        session = MockSession(
            storage={'auth_token': 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U'},
            current_url='https://example.com/dashboard'
        )
        detector = LoginStateDetectorV2(session)
        state = detector.check_login_state()
        self.assertTrue(state.is_logged_in)
        self.assertGreaterEqual(state.score, 50)
    
    def test_mixed_signals(self):
        """混合信号场景"""
        session = MockSession(
            cookies=[{'name': 'session_id', 'value': 'abc123def456'}],
            storage={},
            current_url='https://example.com/login'
        )
        detector = LoginStateDetectorV2(session)
        state = detector.check_login_state()
        # 有session cookie但URL是login，应处于边缘状态
        self.assertFalse(state.is_logged_in)
    
    def test_login_state_dict(self):
        """登录状态字典"""
        session = MockSession(current_url='https://example.com/dashboard')
        detector = LoginStateDetectorV2(session)
        state = detector.check_login_state()
        d = state.to_dict()
        self.assertIsInstance(d, dict)
        self.assertIn('is_logged_in', d)
        self.assertIn('score', d)
    
    def test_score_components(self):
        """评分组件测试"""
        session = MockSession(
            cookies=[{'name': 'auth_token', 'value': 'valid_token_value'}],
            storage={'user_token': 'valid_user_token_value'},
            current_url='https://example.com/user/profile'
        )
        detector = LoginStateDetectorV2(session)
        state = detector.check_login_state()
        self.assertGreater(state.score, 0)
        self.assertTrue(state.is_logged_in)


class TestCaptchaLoginIntegrator(unittest.TestCase):
    @unittest.skipIf(not HAS_CAPTCHA, 'CaptchaLoginIntegrator not available')
    def test_basic_instantiation(self):
        """基础实例化测试"""
        integrator = CaptchaLoginIntegrator(MockSession())
        self.assertIsNotNone(integrator.captcha_handler)
        self.assertIsNotNone(integrator.login_detector)


def main():
    print('=' * 60)
    print('browser-cdp 登录状态检测器 v2 测试')
    print('=' * 60)
    
    if HAS_CAPTCHA:
        print('✅ CaptchaLoginIntegrator 可用')
    else:
        print('⚠️  CaptchaLoginIntegrator 不可用（需要完整包导入）')
    
    unittest.main(verbosity=2)


if __name__ == '__main__':
    main()
