"""
login_state_detector_v2.py - 增强版登录状态检测器

新增检测策略：
- JWT token 格式检测（三段式）
- Authorization header 检测
- OAuth 回调参数检测（code/state/error）
- 2FA 页面特征检测
- 综合评分系统（0-100分，>=50 分为已登录）
- 异常状态检测（有 token 但在登录页）
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple

logger = logging.getLogger(__name__)


@dataclass
class LoginState:
    """增强版登录状态"""
    is_logged_in: bool
    confidence: float          # 0.0 - 1.0
    score: int                 # 0-100 综合评分
    method: str                # 检测方法
    details: Dict[str, Any] = field(default_factory=dict)
    
    # 新增字段
    detected_features: List[str] = field(default_factory=list)
    needs_relogin: bool = False
    
    def to_dict(self) -> dict:
        return {
            "is_logged_in": self.is_logged_in,
            "confidence": self.confidence,
            "score": self.score,
            "method": self.method,
            "details": self.details,
            "detected_features": self.detected_features,
            "needs_relogin": self.needs_relogin,
        }


class LoginStateDetectorV2:
    """
    增强版登录状态检测器
    
    7 类检测策略，综合评分 0-100 分
    >= 50 分判定为已登录
    """
    
    # 登录成功 URL 模式
    LOGIN_SUCCESS_PATTERNS = [
        r'/dashboard', r'/home', r'/user', r'/profile', r'/inbox',
        r'/my-', r'/account', r'/settings', r'/messages',
        r'/orders', r'/cart', r'/favorites', r'/saved',
        r'/feed', r'/timeline', r'/wall',
    ]
    
    # 登录失败/登录页 URL 模式
    LOGIN_PAGE_PATTERNS = [
        r'/login', r'/signin', r'/sign-in', r'/auth',
        r'/account/login', r'/user/login', r'/session',
        r'/oauth/authorize', r'/sso',
    ]
    
    # 2FA/验证码页面特征
    TWOFA_SELECTORS = [
        "input[name*='code']", "input[name*='otp']",
        "input[name*='verify']", "input[name*='captcha']",
        "input[placeholder*='code']", "input[placeholder*='otp']",
        ".otp-input", ".two-factor", ".2fa", ".mfa",
        "form[action*='verify']",
    ]
    
    # OAuth 回调参数模式
    OAUTH_PARAM_PATTERNS = ['code=', 'state=', 'error=', 'access_token=']
    
    def __init__(self, session):
        self.session = session
    
    # =========================================================================
    # 主检测入口
    # =========================================================================
    
    def check_login_state(self) -> LoginState:
        """
        综合检测登录状态（7 类检测）
        
        Returns:
            LoginState: 综合评分结果
        """
        scores: Dict[str, int] = {}
        features: List[str] = []
        
        try:
            # 1. URL 模式检测
            scores['url_pattern'] = self._check_url_pattern(features)
            
            # 2. Token 检测（JWT + localStorage）
            scores['token'] = self._check_token(features)
            
            # 3. Cookie 检测
            scores['cookie'] = self._check_cookie(features)
            
            # 4. UI 元素检测
            scores['ui_element'] = self._check_ui_elements(features)
            
            # 5. OAuth 回调检测
            scores['oauth_callback'] = self._check_oauth_callback(features)
            
            # 6. 2FA 检测
            scores['two_factor'] = self._check_two_factor(features)
            
            # 7. 登录页异常检测（有 token 但在登录页）
            scores['anomalous'] = self._check_anomalous(features)
        except Exception as e:
            logger.error(f"登录状态检测异常: {e}")
            return LoginState(
                is_logged_in=False,
                confidence=0.0,
                score=0,
                method="error",
                details={"error": str(e)},
            )
        
        total_score = sum(scores.values())
        is_logged = total_score >= 50
        
        # 计算置信度（基于得分分布）
        confidence = min(1.0, total_score / 100.0)
        
        # 判断是否需要重新登录
        needs_relogin = not is_logged and (scores.get('url_pattern', 0) > 0 or scores.get('token', 0) > 0)
        
        return LoginState(
            is_logged_in=is_logged,
            confidence=confidence,
            score=total_score,
            method="comprehensive_v2",
            details={"scores": scores},
            detected_features=features,
            needs_relogin=needs_relogin,
        )
    
    # =========================================================================
    # 7 类检测策略
    # =========================================================================
    
    def _check_url_pattern(self, features: List[str]) -> int:
        """1. URL 模式检测 - 成功/失败路径识别"""
        js = """
        (function() {
            var url = window.location.href.toLowerCase();
            var path = window.location.pathname.toLowerCase();
            
            // 成功模式（正分）
            var successPatterns = [
                '/dashboard', '/home', '/user', '/profile', '/inbox',
                '/my-', '/account', '/settings', '/messages',
                '/orders', '/cart', '/favorites', '/saved',
                '/feed', '/timeline', '/wall', '/private'
            ];
            // 失败模式（负分）
            var failPatterns = [
                '/login', '/signin', '/sign-in', '/auth',
                '/account/login', '/user/login', '/session'
            ];
            // OAuth 回调
            var oauthPatterns = ['?code=', '?state=', '?error='];
            
            var successScore = 0;
            var failScore = 0;
            var detected = [];
            
            successPatterns.forEach(function(p) {
                if (path.indexOf(p) !== -1 || url.indexOf(p) !== -1) {
                    successScore += 15;
                    detected.push('url_success:' + p);
                }
            });
            
            failPatterns.forEach(function(p) {
                if (path.indexOf(p) !== -1 || url.indexOf(p) !== -1) {
                    failScore += 20;
                    detected.push('url_fail:' + p);
                }
            });
            
            // OAuth 回调参数（中等置信度）
            oauthPatterns.forEach(function(p) {
                if (url.indexOf(p) !== -1) {
                failScore += 5;
                detected.push('url_oauth_callback');
            }
        });
            
            var netScore = Math.max(0, successScore - failScore);
            return { score: netScore, detected: detected };
        })()
        """
        try:
            result = self.session.eval_js(js)
            score = result.get('score', 0)
            for d in result.get('detected', []):
                if 'url_success' in d:
                    features.append(d)
                elif 'url_fail' in d:
                    features.append('login_page_detected')
            return score
        except Exception as e:
            logger.debug(f"URL 模式检测失败: {e}")
            return 0
    
    def _check_token(self, features: List[str]) -> int:
        """2. Token 检测 - JWT + localStorage/sessionStorage"""
        js = """
        (function() {
            var score = 0;
            var detected = [];
            
            // 检查 JWT token（三段式 base64）
            var jwtRegex = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;
            var jwtFound = false;
            
            // localStorage
            try {
                var keys = Object.keys(localStorage);
                keys.forEach(function(k) {
                    var v = localStorage.getItem(k);
                    if (!v) return;
                    // JWT 匹配
                    if (jwtRegex.test(v)) {
                        jwtFound = true;
                        score += 40;
                        detected.push('jwt_localstorage:' + k);
                    }
                    // token 关键字
                    if ((k.toLowerCase().indexOf('token') !== -1 || 
                         k.toLowerCase().indexOf('auth') !== -1) && v.length > 10) {
                        score += 15;
                        detected.push('token_key:' + k);
                    }
                });
            } catch(e) {}
            
            // sessionStorage
            try {
                var skeys = Object.keys(sessionStorage);
                skeys.forEach(function(k) {
                    var v = sessionStorage.getItem(k);
                    if (!v) return;
                    if (jwtRegex.test(v)) {
                        score += 30;
                        detected.push('jwt_sessionstorage:' + k);
                    }
                    if (k.toLowerCase().indexOf('token') !== -1 && v.length > 10) {
                        score += 10;
                        detected.push('sess_token:' + k);
                    }
                });
            } catch(e) {}
            
            // document.cookie 中的 token
            try {
                var cookies = document.cookie.split(';');
                cookies.forEach(function(c) {
                    var parts = c.trim().split('=');
                    var name = parts[0].toLowerCase();
                    var value = parts.length > 1 ? parts[1] : '';
                    if ((name.indexOf('token') !== -1 || name.indexOf('auth') !== -1) && value.length > 10) {
                        score += 12;
                        detected.push('cookie_token:' + name);
                    }
                });
            } catch(e) {}
            
            // Authorization header（通过 JS 检查 window 对象）
            try {
                if (window.__lastAuthHeader && window.__lastAuthHeader.length > 10) {
                    score += 20;
                    detected.push('auth_header_present');
                }
            } catch(e) {}
            
            return { score: Math.min(score, 100), detected: detected };
        })()
        """
        try:
            result = self.session.eval_js(js)
            score = result.get('score', 0)
            for d in result.get('detected', []):
                if 'jwt' in d or 'token_key' in d or 'sess_token' in d:
                    features.append(d)
            return min(score, 40)  # 封顶 40 分
        except Exception as e:
            logger.debug(f"Token 检测失败: {e}")
            return 0
    
    def _check_cookie(self, features: List[str]) -> int:
        """3. Cookie 检测 - session/auth 相关 Cookie"""
        js = """
        (function() {
            var score = 0;
            var detected = [];
            
            try {
                var cookies = document.cookie.split(';');
                var sessionCount = 0;
                var authCount = 0;
                
                cookies.forEach(function(c) {
                    var parts = c.trim().split('=');
                    var name = parts[0].toLowerCase();
                    var value = parts.length > 1 ? parts[1] : '';
                    
                    if (value.length < 5) return;  // 跳过短值
                    
                    if (name.indexOf('session') !== -1) {
                        sessionCount++;
                        detected.push('session_cookie:' + name);
                    }
                    if (name.indexOf('auth') !== -1 || name.indexOf('token') !== -1) {
                        authCount++;
                        detected.push('auth_cookie:' + name);
                    }
                    // 常见登录 Cookie 名
                    var knownNames = ['sid', 'ssid', 'bsid', 'connect.sid', 'session_id', 'xsrf'];
                    knownNames.forEach(function(n) {
                        if (name.indexOf(n) !== -1) {
                            detected.push('known_cookie:' + n);
                        }
                    });
                });
                
                score = sessionCount * 8 + authCount * 10;
            } catch(e) {}
            
            return { score: Math.min(score, 25), detected: detected };
        })()
        """
        try:
            result = self.session.eval_js(js)
            score = result.get('score', 0)
            for d in result.get('detected', []):
                if 'auth_cookie' in d or 'session_cookie' in d:
                    features.append(d)
            return score
        except Exception as e:
            logger.debug(f"Cookie 检测失败: {e}")
            return 0
    
    def _check_ui_elements(self, features: List[str]) -> int:
        """4. UI 元素检测 - 用户菜单/头像/登出按钮"""
        js = """
        (function() {
            var score = 0;
            var detected = [];
            
            // 已登录特征选择器
            var loggedInSelectors = [
                '[class*="user-menu"]', '[class*="user-profile"]',
                '[class*="account"]', '[id*="user-menu"]',
                'button[class*="logout"]', 'a[class*="logout"]',
                '[class*="sign-out"]', '[data-testid="user-menu"]',
                '.user-avatar', '.profile-dropdown', '[class*="welcome"]',
                '[class*="username"]', '[class*="display-name"]',
                '[class*="my-account"]', '[class*="personal-center"]',
                '.nav-user', '.user-dropdown', '[aria-label*="account"]'
            ];
            
            // 未登录特征选择器
            var loggedOutSelectors = [
                'form[action*="login"]', 'form[action*="signin"]',
                'button[class*="login"]', 'a[class*="login"]',
                '[class*="sign-in"]', '[class*="signin"]',
                'input[type="password"][name*="password"]',
                '[class*="login-form"]', '[class*="auth-form"]',
                '.login-box', '.sign-in-box'
            ];
            
            var logCount = 0;
            loggedInSelectors.forEach(function(sel) {
                try {
                    if (document.querySelectorAll(sel).length > 0) {
                        logCount++;
                        detected.push(sel);
                    }
                } catch(e) {}
            });
            
            var logoutCount = 0;
            loggedOutSelectors.forEach(function(sel) {
                try {
                    if (document.querySelectorAll(sel).length > 0) {
                        logoutCount++;
                        detected.push('OUT:' + sel);
                    }
                } catch(e) {}
            });
            
            // 净得分：登录特征加分，未登录特征扣分
            score = Math.max(0, logCount * 8 - logoutCount * 10);
            return { score: Math.min(score, 30), detected: detected };
        })()
        """
        try:
            result = self.session.eval_js(js)
            score = result.get('score', 0)
            for d in result.get('detected', []):
                if not d.startswith('OUT:'):
                    features.append(d)
            return score
        except Exception as e:
            logger.debug(f"UI 元素检测失败: {e}")
            return 0
    
    def _check_oauth_callback(self, features: List[str]) -> int:
        """5. OAuth 回调检测 - code/state/error 参数"""
        js = """
        (function() {
            var url = window.location.search.toLowerCase();
            var score = 0;
            var detected = [];
            
            if (url.indexOf('code=') !== -1) {
                score += 10;
                detected.push('oauth_code_present');
            }
            if (url.indexOf('state=') !== -1) {
                score += 5;
                detected.push('oauth_state_present');
            }
            if (url.indexOf('error=') !== -1 || url.indexOf('error_description') !== -1) {
                score -= 10;  // OAuth 失败
                detected.push('oauth_error');
            }
            
            return { score: Math.max(0, score), detected: detected };
        })()
        """
        try:
            result = self.session.eval_js(js)
            score = result.get('score', 0)
            for d in result.get('detected', []):
                if d != 'oauth_error':
                    features.append(d)
            return score
        except Exception as e:
            logger.debug(f"OAuth 回调检测失败: {e}")
            return 0
    
    def _check_two_factor(self, features: List[str]) -> int:
        """6. 2FA 检测 - 验证码/OTP 页面特征"""
        js = """
        (function() {
            var score = 0;
            var detected = [];
            
            var selectors = [
                "input[name*='code']", "input[name*='otp']",
                "input[name*='verify']", "input[name*='captcha']",
                "input[placeholder*='code']", "input[placeholder*='otp']",
                ".otp-input", ".two-factor", ".2fa", ".mfa",
                "form[action*='verify']", "form[action*='2fa']",
                '.sms-verify', '.email-verify', '.phone-verify'
            ];
            
            selectors.forEach(function(sel) {
                try {
                    if (document.querySelectorAll(sel).length > 0) {
                        score += 15;  // 检测到 2FA 页面，负分
                        detected.push(sel);
                    }
                } catch(e) {}
            });
            
            // 也检测常见的 2FA 文本
            var bodyText = document.body.innerText.toLowerCase();
            var twoFAPatterns = ['enter code', 'verify your', 'two-factor', '2fa', 'otp', 'verification code'];
            twoFAPatterns.forEach(function(p) {
                if (bodyText.indexOf(p) !== -1) {
                    score += 5;
                    detected.push('text:' + p);
                }
            });
            
            // 2FA 是负分（需要处理）
            return { score: -Math.min(score, 20), detected: detected };
        })()
        """
        try:
            result = self.session.eval_js(js)
            score = result.get('score', 0)
            for d in result.get('detected', []):
                features.append('2fa_detected:' + d)
            return score  # 可能为负数
        except Exception as e:
            logger.debug(f"2FA 检测失败: {e}")
            return 0
    
    def _check_anomalous(self, features: List[str]) -> int:
        """7. 异常状态检测 - 有 token 但在登录页"""
        js = """
        (function() {
            var anomaly = false;
            var reason = '';
            
            // 检测是否在登录页
            var path = window.location.pathname.toLowerCase();
            var isLoginPage = [
                '/login', '/signin', '/sign-in', '/auth',
                '/account/login', '/user/login'
            ].some(function(p) { return path.indexOf(p) !== -1; });
            
            // 检测是否有 token
            var hasToken = false;
            try {
                var keys = Object.keys(localStorage);
                for (var i = 0; i < keys.length; i++) {
                    var k = keys[i].toLowerCase();
                    var v = localStorage.getItem(keys[i]);
                    if ((k.indexOf('token') !== -1 || k.indexOf('auth') !== -1) && v && v.length > 20) {
                        hasToken = true;
                        break;
                    }
                }
            } catch(e) {}
            
            if (isLoginPage && hasToken) {
                anomaly = true;
                reason = 'token_in_login_page';
            }
            
            return { anomaly: anomaly, reason: reason };
        })()
        """
        try:
            result = self.session.eval_js(js)
            if result.get('anomaly'):
                features.append('anomalous:' + result.get('reason', ''))
                return -10  # 异常状态，扣分
            return 0
        except Exception as e:
            logger.debug(f"异常状态检测失败: {e}")
            return 0
    
    # =========================================================================
    # 等待登录完成
    # =========================================================================
    
    async def wait_for_login(self, timeout: float = 60.0, check_interval: float = 1.0) -> LoginState:
        """
        等待登录完成（轮询检测）
        
        Args:
            timeout: 超时时间（秒）
            check_interval: 检查间隔（秒）
        
        Returns:
            LoginState: 最终状态
        """
        import asyncio
        start_time = time.time()
        last_state: Optional[LoginState] = None
        
        while time.time() - start_time < timeout:
            state = self.check_login_state()
            
            if last_state:
                if last_state.is_logged_in != state.is_logged_in:
                    logger.info(f"登录状态变化: {last_state.is_logged_in} -> {state.is_logged_in}")
            
            if state.is_logged_in and state.confidence >= 0.5:
                logger.info(f"检测到登录成功 (score={state.score}, confidence={state.confidence:.2f})")
                return state
            
            last_state = state
            await asyncio.sleep(check_interval)
        
        logger.warning(f"等待登录超时 ({timeout}s)，最终 score={last_state.score if last_state else 0}")
        return last_state or LoginState(is_logged_in=False, confidence=0.0, score=0, method="timeout")
    
    # =========================================================================
    # 便捷方法
    # =========================================================================
    
    def is_logged_in(self, threshold: float = 0.5) -> bool:
        """快速检查是否已登录"""
        state = self.check_login_state()
        return state.is_logged_in and state.confidence >= threshold
    
    def get_login_url(self) -> Optional[str]:
        """获取登录页面 URL"""
        js = """
        (function() {
            var links = document.querySelectorAll('a[href*="login"], a[href*="signin"]');
            if (links.length > 0) return links[0].href;
            var forms = document.querySelectorAll('form[action*="login"], form[action*="signin"]');
            if (forms.length > 0) return forms[0].action;
            return null;
        })()
        """
        try:
            return self.session.eval_js(js)
        except Exception:
            return None
    
    def get_user_info(self) -> Optional[Dict[str, Any]]:
        """获取当前用户信息"""
        js = """
        (function() {
            var info = {};
            var userElements = document.querySelectorAll('[class*="username"], [class*="user-name"], [class*="display-name"]');
            if (userElements.length > 0) info.username = userElements[0].textContent.trim();
            try {
                var user = localStorage.getItem('user');
                if (user) info.user = JSON.parse(user);
                var token = localStorage.getItem('token');
                if (token) info.hasToken = true;
            } catch(e) {}
            return Object.keys(info).length > 0 ? info : null;
        })()
        """
        try:
            return self.session.eval_js(js)
        except Exception:
            return None


# 模块级便捷函数
import time

def check_login_state_v2(session) -> LoginState:
    """检测当前页面登录状态（增强版）"""
    detector = LoginStateDetectorV2(session)
    return detector.check_login_state()


def is_logged_in_v2(session, threshold: float = 0.5) -> bool:
    """快速检查是否已登录（增强版）"""
    detector = LoginStateDetectorV2(session)
    return detector.is_logged_in(threshold)
