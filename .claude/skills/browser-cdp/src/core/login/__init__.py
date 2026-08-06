"""
login 模块 - 登录场景优化

提供：
- Cookie 持久化管理
- 自动登录表单识别
- 会话状态保持
- 登录状态检测
"""
from .cookie_manager import CookieManager, get_cookie_manager
from .login_form_detector import LoginFormDetector, detect_login_form
from .session_manager import SessionManager, get_session_manager
from .login_state_detector import LoginStateDetector, check_login_state

__all__ = [
    "CookieManager",
    "get_cookie_manager",
    "LoginFormDetector",
    "detect_login_form",
    "SessionManager",
    "get_session_manager",
    "LoginStateDetector",
    "check_login_state",
]
