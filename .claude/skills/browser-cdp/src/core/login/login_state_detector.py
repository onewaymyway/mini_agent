"""
登录状态检测模块

支持：
- 检测页面是否已登录
- 检测登录状态变化
- 支持多种检测策略
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class LoginState:
    """登录状态"""
    is_logged_in: bool
    confidence: float  # 0.0 - 1.0
    method: str  # 检测方法
    details: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.details is None:
            self.details = {}
    
    def to_dict(self) -> dict:
        return {
            "is_logged_in": self.is_logged_in,
            "confidence": self.confidence,
            "method": self.method,
            "details": self.details,
        }


class LoginStateDetector:
    """
    登录状态检测器
    
    通过多种策略检测页面登录状态。
    """
    
    # 已登录特征选择器
    LOGGED_IN_SELECTORS = [
        "[class*='user-menu']",
        "[class*='user-profile']",
        "[class*='account']",
        "[id*='user-menu']",
        "[id*='user-profile']",
        "[id*='account']",
        "button[class*='logout']",
        "a[class*='logout']",
        "[class*='sign-out']",
        "[class*='logout']",
        "[data-testid='user-menu']",
        ".user-avatar",
        ".profile-dropdown",
        "[class*='welcome']",
    ]
    
    # 未登录特征选择器
    LOGGED_OUT_SELECTORS = [
        "form[action*='login']",
        "form[action*='signin']",
        "button[class*='login']",
        "a[class*='login']",
        "[class*='sign-in']",
        "[class*='signin']",
        "[class*='login-form']",
        "input[type='password'][name*='password']",
    ]
    
    # 登录检测 JS 模式
    LOGIN_CHECK_JS = """
    (function() {
        var result = {
            isLoggedIn: false,
            confidence: 0,
            methods: []
        };
        
        // 1. 检查 URL 是否包含登录相关路径
        var loginPaths = ['/login', '/signin', '/auth', '/account/login'];
        var currentPath = window.location.pathname.toLowerCase();
        var isOnLoginPage = loginPaths.some(function(path) {
            return currentPath.indexOf(path) !== -1;
        });
        
        // 2. 检查是否有登录表单
        var loginForms = document.querySelectorAll('form[action*="login"], form[action*="signin"]');
        var hasLoginForm = loginForms.length > 0;
        
        // 3. 检查是否有登出按钮
        var logoutBtns = document.querySelectorAll('[class*="logout"], [class*="sign-out"]');
        var hasLogoutBtn = logoutBtns.length > 0;
        
        // 4. 检查用户菜单
        var userMenus = document.querySelectorAll('[class*="user-menu"], [class*="user-profile"], [class*="account"]');
        var hasUserMenu = userMenus.length > 0;
        
        // 5. 检查 Cookie 中的会话标识
        var sessionCookies = document.cookie.split(';').filter(function(cookie) {
            var name = cookie.trim().split('=')[0].toLowerCase();
            return name.indexOf('session') !== -1 || 
                   name.indexOf('token') !== -1 || 
                   name.indexOf('auth') !== -1 ||
                   name.indexOf('user') !== -1;
        });
        var hasSessionCookie = sessionCookies.length > 0;
        
        // 6. 检查 localStorage/SessionStorage
        var hasStorageData = false;
        try {
            hasStorageData = localStorage.getItem('user') || 
                            sessionStorage.getItem('user') ||
                            localStorage.getItem('token') ||
                            sessionStorage.getItem('token');
        } catch(e) {}
        
        // 综合判断
        if (hasLogoutBtn || hasUserMenu || hasSessionCookie || hasStorageData) {
            result.isLoggedIn = true;
            result.confidence = 0.9;
            result.methods.push('logout_button', 'user_menu', 'session_cookie', 'storage');
        } else if (isOnLoginPage || hasLoginForm) {
            result.isLoggedIn = false;
            result.confidence = 0.85;
            result.methods.push('login_page', 'login_form');
        } else {
            // 默认认为未登录，除非有明确证据
            result.isLoggedIn = false;
            result.confidence = 0.5;
            result.methods.push('default_assumption');
        }
        
        return result;
    })()
    """
    
    def __init__(self, session):
        self.session = session
    
    def check_login_state(self) -> LoginState:
        """
        检测当前页面登录状态
        
        Returns:
            LoginState 对象
        """
        try:
            result = self.session.eval_js(self.LOGIN_CHECK_JS)
            
            return LoginState(
                is_logged_in=result.get("isLoggedIn", False),
                confidence=result.get("confidence", 0.5),
                method="js_comprehensive",
                details=result,
            )
        except Exception as e:
            logger.error(f"登录状态检测失败: {e}")
            return LoginState(
                is_logged_in=False,
                confidence=0.0,
                method="error",
                details={"error": str(e)},
            )
    
    def wait_for_login(self, timeout: float = 30.0, check_interval: float = 1.0) -> LoginState:
        """
        等待登录完成
        
        Args:
            timeout: 超时时间（秒）
            check_interval: 检查间隔（秒）
        
        Returns:
            LoginState 对象
        """
        import time
        
        start_time = time.time()
        last_state = None
        
        while time.time() - start_time < timeout:
            state = self.check_login_state()
            
            if last_state and last_state.is_logged_in != state.is_logged_in:
                logger.info(f"登录状态变化: {last_state.is_logged_in} -> {state.is_logged_in}")
            
            if state.is_logged_in and state.confidence >= 0.7:
                logger.info(f"检测到登录成功 (confidence={state.confidence})")
                return state
            
            last_state = state
            time.sleep(check_interval)
        
        logger.warning(f"等待登录超时 ({timeout}s)")
        return last_state or LoginState(is_logged_in=False, confidence=0.0, method="timeout")
    
    def detect_login_form(self) -> bool:
        """
        检测页面是否有登录表单
        
        Returns:
            是否有登录表单
        """
        js = """
        (function() {
            var forms = document.querySelectorAll('form[action*="login"], form[action*="signin"], input[type="password"]');
            return forms.length > 0;
        })()
        """
        try:
            return self.session.eval_js(js)
        except Exception:
            return False
    
    def get_login_url(self) -> Optional[str]:
        """
        获取登录页面 URL
        
        Returns:
            登录页面 URL，未找到返回 None
        """
        js = """
        (function() {
            var links = document.querySelectorAll('a[href*="login"], a[href*="signin"]');
            if (links.length > 0) {
                return links[0].href;
            }
            var forms = document.querySelectorAll('form[action*="login"], form[action*="signin"]');
            if (forms.length > 0) {
                return forms[0].action;
            }
            return null;
        })()
        """
        try:
            return self.session.eval_js(js)
        except Exception:
            return None
    
    def get_user_info(self) -> Optional[Dict[str, Any]]:
        """
        获取当前用户信息
        
        Returns:
            用户信息字典，未登录返回 None
        """
        js = """
        (function() {
            var info = {};
            
            // 尝试从常见位置获取用户信息
            var userElements = document.querySelectorAll('[class*="username"], [class*="user-name"], [class*="display-name"]');
            if (userElements.length > 0) {
                info.username = userElements[0].textContent.trim();
            }
            
            // 检查 localStorage
            try {
                var user = localStorage.getItem('user');
                if (user) {
                    info.user = JSON.parse(user);
                }
                var token = localStorage.getItem('token');
                if (token) {
                    info.hasToken = true;
                }
            } catch(e) {}
            
            // 检查 sessionStorage
            try {
                var sessUser = sessionStorage.getItem('user');
                if (sessUser) {
                    info.sessUser = JSON.parse(sessUser);
                }
            } catch(e) {}
            
            return Object.keys(info).length > 0 ? info : null;
        })()
        """
        try:
            return self.session.eval_js(js)
        except Exception:
            return None


# 便捷函数
def check_login_state(session) -> LoginState:
    """检测当前页面登录状态"""
    detector = LoginStateDetector(session)
    return detector.check_login_state()


def wait_for_login(session, timeout: float = 30.0) -> LoginState:
    """等待登录完成"""
    detector = LoginStateDetector(session)
    return detector.wait_for_login(timeout)


def is_logged_in(session) -> bool:
    """快速检查是否已登录"""
    state = check_login_state(session)
    return state.is_logged_in and state.confidence >= 0.7
