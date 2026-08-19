"""
login_flow_manager.py - 完整登录流程管理器

支持:
- 传统表单登录
- 登录状态检测增强
- 验证码处理集成
- 登录成功验证
- OAuth/SSO 登录流程（预留接口）
- 2FA 等待处理
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable

from .login_state_detector import LoginStateDetector, LoginState

logger = logging.getLogger(__name__)


@dataclass
class LoginResult:
    """登录结果"""
    success: Optional[bool]
    state: Optional[LoginState] = None
    already_logged_in: bool = False
    error: Optional[str] = None
    captcha_detected: bool = False
    captcha_type: Optional[str] = None
    elapsed: float = 0.0
    steps: List[Dict[str, Any]] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "already_logged_in": self.already_logged_in,
            "error": self.error,
            "captcha_detected": self.captcha_detected,
            "captcha_type": self.captcha_type,
            "elapsed": round(self.elapsed, 2),
            "steps": self.steps,
            "state": self.state.to_dict() if self.state else None,
        }


@dataclass
class LoginFlowConfig:
    """登录流程配置"""
    # 登录成功后的典型 URL 模式
    success_url_patterns: List[str] = field(default_factory=lambda: [
        "/dashboard", "/home", "/user", "/profile", "/inbox", "/my-", 
        "/account", "/settings", "/members",
    ])
    # 登录成功后的典型 UI 特征
    success_element_selectors: List[str] = field(default_factory=lambda: [
        ".user-avatar", "[class*='welcome']", "[data-role='user-menu']",
        "[class*='user-name']", "[class*='profile']",
    ])
    # 登录页面的典型 URL 模式
    login_url_patterns: List[str] = field(default_factory=lambda: [
        "/login", "/signin", "/auth", "/account/login", "/user/login", "/sso", "/oidc",
    ])
    # 验证码检测选择器
    captcha_selectors: List[str] = field(default_factory=lambda: [
        ".geetest_widget", "#slideBlock", ".nc_wrapper",
        "[class*='captcha']", "[class*='verify']",
        ".captcha-container", ".verification-code",
    ])
    # 最大等待时间（秒）
    max_wait_timeout: float = 60.0
    # 检查间隔（秒）
    check_interval: float = 1.0
    # 是否自动处理验证码
    auto_handle_captcha: bool = True


class LoginFlowManager:
    """
    完整登录流程管理器
    
    整合登录状态检测、表单填写、验证码处理、登录成功验证
    """
    
    def __init__(self, session, config: LoginFlowConfig = None):
        self.session = session
        self.config = config or LoginFlowConfig()
        self.state_detector = LoginStateDetector(session)
        self._login_check_js_injected = False
    
    # =========================================================================
    # 主登录流程
    # =========================================================================
    
    async def login(
        self,
        username: str,
        password: str,
        login_url: str = None,
        form_selectors: Dict[str, str] = None,
        wait_for_login: bool = True,
        handle_captcha: bool = True,
        timeout: float = None,
    ) -> LoginResult:
        """
        执行完整登录流程
        
        Args:
            username: 用户名
            password: 密码
            login_url: 登录页面 URL
            form_selectors: 表单字段选择器 {"username": "input[name='user']", ...}
            wait_for_login: 是否等待登录完成
            handle_captcha: 是否自动处理验证码
            timeout: 总超时（秒）
        
        Returns:
            LoginResult
        """
        start_time = time.time()
        steps = []
        
        try:
            # 步骤1: 导航到登录页
            if login_url:
                steps.append({"step": "navigate", "url": login_url})
                logger.info(f"导航到登录页: {login_url}")
            
            # 步骤2: 检测当前状态
            initial_state = await self._detect_state()
            steps.append({"step": "initial_check", "logged_in": initial_state.is_logged_in, "confidence": initial_state.confidence})
            
            if initial_state.is_logged_in and initial_state.confidence >= 0.7:
                logger.info("已登录，跳过登录流程")
                return LoginResult(
                    success=True,
                    already_logged_in=True,
                    state=initial_state,
                    elapsed=time.time() - start_time,
                    steps=steps,
                )
            
            # 步骤3: 填写登录表单
            selectors = form_selectors or await self._detect_form_selectors()
            if not selectors:
                return LoginResult(
                    success=False,
                    error="无法检测登录表单选择器",
                    elapsed=time.time() - start_time,
                    steps=steps,
                )
            
            steps.append({"step": "form_detected", "selectors": selectors})
            await self._fill_login_form(username, password, selectors)
            
            # 步骤4: 检测并处理验证码
            if handle_captcha:
                captcha_state = await self._check_captcha()
                if captcha_state.detected:
                    steps.append({"step": "captcha_detected", "type": captcha_state.type})
                    logger.warning(f"检测到验证码: {captcha_state.type}")
                    # 验证码需要人工处理，返回提示
                    return LoginResult(
                        success=False,
                        error=f"需要处理验证码: {captcha_state.type}，请手动完成",
                        captcha_detected=True,
                        captcha_type=captcha_state.type,
                        elapsed=time.time() - start_time,
                        steps=steps,
                    )
            
            # 步骤5: 提交表单
            steps.append({"step": "submit_form"})
            await self._submit_login_form(selectors)
            
            # 步骤6: 等待登录完成
            if wait_for_login:
                login_state = await self._wait_for_login_complete(
                    timeout=timeout or self.config.max_wait_timeout
                )
                steps.append({"step": "wait_complete", "logged_in": login_state.is_logged_in, "confidence": login_state.confidence})
                
                return LoginResult(
                    success=login_state.is_logged_in,
                    state=login_state,
                    elapsed=time.time() - start_time,
                    steps=steps,
                )
            
            return LoginResult(
                success=None,
                elapsed=time.time() - start_time,
                steps=steps,
            )
            
        except Exception as e:
            logger.error(f"登录流程异常: {e}")
            return LoginResult(
                success=False,
                error=str(e),
                elapsed=time.time() - start_time,
                steps=steps,
            )
    
    # =========================================================================
    # 内部方法
    # =========================================================================
    
    async def _detect_state(self) -> LoginState:
        """检测当前页面登录状态"""
        js = """
        (function() {
            var result = { isLoggedIn: false, confidence: 0, methods: [], details: {} };
            var pathname = window.location.pathname.toLowerCase();
            var params = new URLSearchParams(window.location.search);
            
            // OAuth 回调检测
            if (params.get('code') || params.get('state') || params.get('error')) {
                result.details.oauthCallback = true;
            }
            
            // 登录页面检测
            var loginPaths = {login_paths};
            var isOnLoginPage = loginPaths.some(function(p) { return pathname.indexOf(p) !== -1; });
            var loginForms = document.querySelectorAll('form[action*="login"], form[action*="signin"], input[type="password"]');
            result.details.isOnLoginPage = isOnLoginPage || loginForms.length > 0;
            
            // Token 检测
            var tokenKeys = ['token', 'access_token', 'auth_token', 'jwt', 'session_token'];
            var foundTokens = [];
            try {
                for (var i = 0; i < localStorage.length; i++) {
                    var key = localStorage.key(i);
                    if (tokenKeys.some(function(tk) { return key.toLowerCase().indexOf(tk) !== -1; })) {
                        var val = localStorage.getItem(key);
                        if (val && val.length > 10) {
                            foundTokens.push({ key: key, length: val.length });
                            if (val.split('.').length === 3) result.details.jwtDetected = true;
                        }
                    }
                }
            } catch(e) {}
            result.details.tokens = foundTokens;
            
            // Cookie 检测
            var cookies = document.cookie.split(';').map(function(c) { return c.trim().split('=')[0].toLowerCase(); });
            var sessionKeywords = ['session', 'token', 'auth', 'user', 'sid', 'connect.sid'];
            result.details.sessionCookies = cookies.filter(function(c) {
                return sessionKeywords.some(function(k) { return c.indexOf(k) !== -1; });
            });
            
            // 登录态 UI 特征
            var loggedInSelectors = {login_selectors};
            var loggedInElements = 0;
            loggedInSelectors.forEach(function(sel) {{
                try {{ loggedInElements += document.querySelectorAll(sel).length; }} catch(e) {{}}
            }});
            result.details.loggedInElements = loggedInElements;
            
            // 综合评分
            var score = 0;
            if (result.details.sessionCookies.length > 0) score += 25;
            if (foundTokens.length > 0) score += 35;
            if (loggedInElements > 0) score += 25;
            if (result.details.jwtDetected) score += 10;
            
            result.confidence = Math.min(score / 100, 1.0);
            result.isLoggedIn = score >= 50 && !result.details.isOnLoginPage;
            result.loginRequired = result.details.isOnLoginPage;
            
            return result;
        })()
        """
        js = js.replace('{login_paths}', json.dumps(self.config.login_url_patterns))
        js = js.replace('{login_selectors}', json.dumps(self.config.success_element_selectors))
        
        try:
            result = await self.session.eval_js(js)
            return LoginState(
                is_logged_in=result.get('isLoggedIn', False),
                confidence=result.get('confidence', 0.0),
                method='js_comprehensive_v2',
                details=result,
            )
        except Exception as e:
            logger.error(f"登录状态检测失败: {e}")
            return LoginState(is_logged_in=False, confidence=0.0, method='error', details={'error': str(e)})
    
    async def _detect_form_selectors(self) -> Dict[str, str]:
        """自动检测登录表单选择器"""
        js = """
        (function() {
            var selectors = {};
            
            // 检测用户名输入框
            var usernameInputs = document.querySelectorAll(
                'input[name="username"], input[name="email"], input[name="user"], '
                'input[name="phone"], input[name="mobile"], input[placeholder*="用户"], '
                'input[placeholder*="邮箱"], input[placeholder*="手机"]'
            );
            if (usernameInputs.length > 0) {
                selectors.username = usernameInputs[0].tagName + (usernameInputs[0].id ? '#' + usernameInputs[0].id : '') + (usernameInputs[0].className ? '.' + usernameInputs[0].className.split(' ')[0] : '');
            }
            
            // 检测密码输入框
            var passwordInputs = document.querySelectorAll('input[type="password"]');
            if (passwordInputs.length > 0) {
                selectors.password = passwordInputs[0].tagName + (passwordInputs[0].id ? '#' + passwordInputs[0].id : '') + (passwordInputs[0].className ? '.' + passwordInputs[0].className.split(' ')[0] : '');
            }
            
            // 检测提交按钮
            var submitBtns = document.querySelectorAll(
                'button[type="submit"], input[type="submit"], button[class*="login"], '
                'button[class*="submit"], a[class*="login"]'
            );
            if (submitBtns.length > 0) {
                selectors.submit = submitBtns[0].tagName + (submitBtns[0].id ? '#' + submitBtns[0].id : '') + (submitBtns[0].className ? '.' + submitBtns[0].className.split(' ')[0] : '');
            }
            
            return selectors;
        })()
        """
        try:
            return await self.session.eval_js(js)
        except Exception:
            return {}
    
    async def _fill_login_form(self, username: str, password: str, selectors: Dict[str, str]) -> None:
        """填写登录表单"""
        if 'username' in selectors:
            await self.session.eval_js(f"""
                (function() {{
                    var el = document.querySelector({selectors['username']!r});
                    if (el) {{
                        el.value = {username!r};
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }}
                }})()
            """)
        
        if 'password' in selectors:
            await self.session.eval_js(f"""
                (function() {{
                    var el = document.querySelector({selectors['password']!r});
                    if (el) {{
                        el.value = {password!r};
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }}
                }})()
            """)
    
    async def _submit_login_form(self, selectors: Dict[str, str]) -> None:
        """提交登录表单"""
        if 'submit' in selectors:
            await self.session.eval_js(f"""
                (function() {{
                    var btn = document.querySelector({selectors['submit']!r});
                    if (btn) {{
                        btn.click();
                    }} else {{
                        var form = document.querySelector('form[action*="login"], form[action*="signin"]');
                        if (form) form.submit();
                    }}
                }})()
            """)
    
    async def _check_captcha(self) -> Dict[str, Any]:
        """检测页面是否有验证码"""
        js = """
        (function() {
            var selectors = {captcha_selectors};
            var detected = false;
            var type = 'unknown';
            
            selectors.forEach(function(sel) {{
                if (document.querySelector(sel)) {{
                    detected = true;
                    if (sel.indexOf('slide') !== -1 || sel.indexOf('Geetest') !== -1) type = 'slider';
                    else if (sel.indexOf('captcha') !== -1) type = 'click';
                    else type = 'unknown';
                }}
            }});
            
            // 文本检测
            var text = document.body ? document.body.innerText : '';
            if (text.indexOf('滑动') !== -1 || text.indexOf('拖拽') !== -1) type = 'slider';
            if (text.indexOf('点选') !== -1 || text.indexOf('点击') !== -1) type = 'click';
            
            return {{ detected: detected, type: type }};
        })()
        """
        js = js.replace('{captcha_selectors}', json.dumps(self.config.captcha_selectors))
        try:
            return await self.session.eval_js(js)
        except Exception:
            return {'detected': False, 'type': 'unknown'}
    
    async def _wait_for_login_complete(self, timeout: float = 30.0) -> LoginState:
        """
        等待登录完成 — 综合检测登录成功特征
        """
        start_time = time.time()
        last_state = None
        
        while time.time() - start_time < timeout:
            state = await self._detect_state()
            
            if state.is_logged_in and state.confidence >= 0.7:
                # 额外验证：检查是否在登录成功后的典型页面
                if self._check_login_success_indicators():
                    logger.info(f"登录成功确认 (confidence={state.confidence:.2f}, elapsed={time.time()-start_time:.1f}s)")
                    return state
            
            # 检测是否被重定向到登录页（登录失败）
            if last_state and last_state.is_logged_in and state.login_required:
                logger.warning("登录失败：被重定向到登录页")
                return state
            
            last_state = state
            await asyncio.sleep(self.config.check_interval)
        
        logger.warning(f"等待登录超时 ({timeout}s)")
        return last_state or LoginState(is_logged_in=False, confidence=0.0, method='timeout')
    
    def _check_login_success_indicators(self) -> bool:
        """检查登录成功后的典型特征"""
        js = """
        (function() {
            var url = window.location.href;
            var patterns = {success_patterns};
            var hasSuccessUrl = patterns.some(function(p) { return url.indexOf(p) !== -1; });
            
            var selectors = {success_selectors};
            var hasUserElement = selectors.some(function(sel) {{
                try {{ return document.querySelector(sel) !== null; }} catch(e) {{ return false; }}
            }});
            
            return hasSuccessUrl || hasUserElement;
        })()
        """
        js = js.replace('{success_patterns}', json.dumps(self.config.success_url_patterns))
        js = js.replace('{success_selectors}', json.dumps(self.config.success_element_selectors))
        
        try:
            return self.session.eval_js(js)
        except Exception:
            return False
    
    # =========================================================================
    # OAuth 登录预留接口
    # =========================================================================
    
    async def oauth_login(
        self,
        provider: str,
        callback_url: str = None,
        timeout: float = 120.0,
    ) -> LoginResult:
        """
        OAuth/SSO 登录（预留接口）
        
        支持: Google, GitHub, 微信, 钉钉
        
        Args:
            provider: OAuth 提供商
            callback_url: 回调 URL
            timeout: 超时时间
        
        Returns:
            LoginResult
        """
        steps = [{"step": "oauth_init", "provider": provider}]
        
        # TODO: 实现完整的 OAuth Authorization Code Flow + PKCE
        # 当前仅返回占位结果
        return LoginResult(
            success=False,
            error=f"OAuth {provider} 登录尚未实现，请手动完成登录",
            elapsed=0.0,
            steps=steps,
        )
    
    # =========================================================================
    # 2FA 处理预留接口
    # =========================================================================
    
    async def wait_for_2fa(
        self,
        timeout: float = 120.0,
    ) -> LoginResult:
        """
        等待 2FA 验证码输入
        
        Args:
            timeout: 超时时间
        
        Returns:
            LoginResult
        """
        steps = [{"step": "2fa_wait_started"}]
        start_time = time.time()
        
        # 检测 2FA 页面
        js = """
        (function() {
            var text = document.body ? document.body.innerText : '';
            var has2FA = text.indexOf('验证码') !== -1 || text.indexOf('2FA') !== -1 ||
                         text.indexOf('OTP') !== -1 || text.indexOf('two-factor') !== -1 ||
                         document.querySelector('input[name="code"]') ||
                         document.querySelector('input[name="otp"]') ||
                         document.querySelector('input[type="text"][maxlength="6"]');
            return { detected: has2FA, text: text.substring(0, 200) };
        })()
        """
        
        try:
            result = await self.session.eval_js(js)
        except Exception:
            result = {'detected': False}
        
        if not result.get('detected'):
            return LoginResult(
                success=False,
                error="未检测到 2FA 页面",
                elapsed=time.time() - start_time,
                steps=steps,
            )
        
        steps.append({"step": "2fa_detected", "hint": result.get('text', '')})
        logger.warning(f"检测到 2FA 页面，请手动输入验证码。页面文本: {result.get('text', '')[:100]}")
        
        # 轮询检测登录状态变化
        while time.time() - start_time < timeout:
            state = await self._detect_state()
            if state.is_logged_in and state.confidence >= 0.7:
                return LoginResult(
                    success=True,
                    state=state,
                    elapsed=time.time() - start_time,
                    steps=steps + [{"step": "2fa_completed"}],
                )
            await asyncio.sleep(2.0)
        
        return LoginResult(
            success=False,
            error="2FA 等待超时",
            elapsed=time.time() - start_time,
            steps=steps,
        )


# 便捷函数
def create_login_flow_manager(session, config: LoginFlowConfig = None) -> LoginFlowManager:
    """创建登录流程管理器实例"""
    return LoginFlowManager(session, config)


def quick_login(session, username: str, password: str, login_url: str = None, **kwargs) -> LoginResult:
    """快速登录入口"""
    mgr = LoginFlowManager(session)
    return mgr.login(username, password, login_url=login_url, **kwargs)
