"""
Simple standalone test for login state detector v2
Run with: python test_login_v2_simple.py
"""
import sys
import os
import re
from unittest.mock import MagicMock

skill_root = r'E:\codes\mini_claude_code\.claude\skills\browser-cdp'
sys.path.insert(0, os.path.join(skill_root, 'src'))

import importlib.util


def load_module(name, rel_path):
    path = os.path.join(skill_root, rel_path)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

login_mod = load_module('core.login.login_state_detector_v2', 'src/core/login/login_state_detector_v2.py')
LoginStateDetectorV2 = login_mod.LoginStateDetectorV2
LoginState = login_mod.LoginState
check_login_state_v2 = login_mod.check_login_state_v2
is_logged_in_v2 = login_mod.is_logged_in_v2


class MockSession:
    def __init__(self, cookies=None, storage=None, current_url=None):
        self.cookies = cookies or []
        self.storage = storage or {}
        self.current_url = current_url or 'https://example.com/login'
        self.page = MagicMock()
    
    def get_url(self): return self.current_url
    def get_cookies(self): return self.cookies
    def get_storage(self): return self.storage
    def wait_for_network_idle(self, timeout=5): return True
    def wait_for_page_ready(self, timeout=5): return True
    
    def eval_js(self, js_code):
        url = self.current_url.lower()
        # URL pattern detection
        if 'window.location' in js_code:
            success = ['/dashboard', '/home', '/user', '/profile', '/account']
            fail = ['/login', '/signin', '/sign-in', '/auth']
            s_score = sum(15 for p in success if p in url)
            f_score = sum(20 for p in fail if p in url)
            return {'score': max(0, s_score - f_score), 'detected': []}
        # Token detection
        if 'localStorage' in js_code or 'sessionStorage' in js_code:
            jwt_re = r'^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$'
            score = 0
            for k, v in self.storage.items():
                if re.match(jwt_re, v): score += 40
                if ('token' in k.lower() or 'auth' in k.lower()) and len(v) > 10: score += 15
            return {'score': min(score, 100), 'detected': []}
        # Cookie detection
        if 'document.cookie' in js_code:
            score = 0
            for c in self.cookies:
                name = c.get('name', '').lower()
                value = c.get('value', '')
                if len(value) < 5: continue
                if 'session' in name: score += 8
                if 'auth' in name or 'token' in name: score += 10
            return {'score': min(score, 25), 'detected': []}
        # UI element detection
        if 'querySelectorAll' in js_code:
            if any(p in url for p in ['/dashboard', '/user', '/profile', '/account']):
                return {'score': 30, 'detected': ['ui_logged_in']}
            return {'score': 0, 'detected': []}
        # OAuth callback
        if 'window.location.search' in js_code:
            score = 0
            if '?code=' in url: score += 10
            if '?state=' in url: score += 5
            if '?error=' in url: score -= 10
            return {'score': max(0, score), 'detected': []}
        # 2FA detection
        if 'input[name' in js_code and any(x in js_code for x in ['otp', 'verify']):
            if any(p in url for p in ['/2fa', '/verify', '/mfa']):
                return {'score': 20, 'detected': ['2fa_detected']}
            return {'score': 0, 'detected': []}
        return {'score': 0, 'detected': []}


def run_tests():
    passed = 0
    failed = 0
    
    # Test 1: URL logged in (URL + UI both score)
    try:
        session = MockSession(current_url='https://example.com/dashboard')
        result = LoginStateDetectorV2(session).check_login_state()
        assert result.is_logged_in is True, f"score={result.score}"
        print(f'PASS: test_url_logged_in (score={result.score})')
        passed += 1
    except Exception as e:
        print(f'FAIL: test_url_logged_in - {e}')
        failed += 1
    
    # Test 2: URL not logged in
    try:
        session = MockSession(current_url='https://example.com/login')
        result = LoginStateDetectorV2(session).check_login_state()
        assert result.is_logged_in is False
        print(f'PASS: test_url_not_logged_in (score={result.score})')
        passed += 1
    except Exception as e:
        print(f'FAIL: test_url_not_logged_in - {e}')
        failed += 1
    
    # Test 3: JWT token - provide multiple signals to reach threshold
    try:
        session = MockSession(
            storage={'token': 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123def'},
            current_url='https://example.com/dashboard'
        )
        result = LoginStateDetectorV2(session).check_login_state()
        assert result.is_logged_in is True, f"score={result.score}"
        print(f'PASS: test_jwt_token (score={result.score})')
        passed += 1
    except Exception as e:
        print(f'FAIL: test_jwt_token - {e}')
        failed += 1
    
    # Test 4: Multiple cookies + URL
    try:
        session = MockSession(
            cookies=[
                {'name': 'session_id', 'value': 'abc123xyz_long_value'},
                {'name': 'auth_token', 'value': 'another_valid_token_value'}
            ],
            current_url='https://example.com/user/profile'
        )
        result = LoginStateDetectorV2(session).check_login_state()
        assert result.is_logged_in is True, f"score={result.score}"
        print(f'PASS: test_cookies (score={result.score})')
        passed += 1
    except Exception as e:
        print(f'FAIL: test_cookies - {e}')
        failed += 1
    
    # Test 5: Storage tokens + URL
    try:
        session = MockSession(
            storage={
                'auth_token': 'some_long_token_value_here_with_enough_length',
                'jwt_token': 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.xyz789abc'
            },
            current_url='https://example.com/dashboard'
        )
        result = LoginStateDetectorV2(session).check_login_state()
        assert result.is_logged_in is True, f"score={result.score}"
        print(f'PASS: test_storage (score={result.score})')
        passed += 1
    except Exception as e:
        print(f'FAIL: test_storage - {e}')
        failed += 1
    
    # Test 6: Anonymous
    try:
        session = MockSession(current_url='https://example.com/login?error=anon')
        result = LoginStateDetectorV2(session).check_login_state()
        assert result.is_logged_in is False
        print(f'PASS: test_anonymous (score={result.score})')
        passed += 1
    except Exception as e:
        print(f'FAIL: test_anonymous - {e}')
        failed += 1
    
    # Test 7: Low confidence
    try:
        session = MockSession(cookies=[], storage={}, current_url='https://example.com/unknown')
        result = LoginStateDetectorV2(session).check_login_state()
        assert result.is_logged_in is False
        print(f'PASS: test_low_confidence (score={result.score})')
        passed += 1
    except Exception as e:
        print(f'FAIL: test_low_confidence - {e}')
        failed += 1
    
    # Test 8: Module-level function
    try:
        session = MockSession(current_url='https://example.com/dashboard')
        session.cookies = [{'name': 'session_id', 'value': 'test_session_value_long_enough'}]
        result = check_login_state_v2(session)
        assert result is not None and isinstance(result, LoginState)
        print(f'PASS: test_check_login_state_v2 (score={result.score})')
        passed += 1
    except Exception as e:
        print(f'FAIL: test_check_login_state_v2 - {e}')
        failed += 1
    
    # Test 9: Module-level is_logged_in_v2
    try:
        session = MockSession(current_url='https://example.com/dashboard')
        session.cookies = [{'name': 'token', 'value': 'valid_token_value_here_long_enough'}]
        assert is_logged_in_v2(session) is True
        print(f'PASS: test_is_logged_in_v2')
        passed += 1
    except Exception as e:
        print(f'FAIL: test_is_logged_in_v2 - {e}')
        failed += 1
    
    print(f'\nResults: {passed} passed, {failed} failed, {passed+failed} total')
    return failed == 0


if __name__ == '__main__':
    success = run_tests()
    sys.exit(0 if success else 1)
