"""
login 模块 - 登录场景优化

提供：
- Cookie 持久化管理
- 自动登录表单识别
- 会话状态保持
- 登录状态检测（基础版 + 增强版 v2）
- 验证码与登录流程集成
- OAuth/SSO 登录支持
"""
from .cookie_manager import CookieManager, get_cookie_manager
from .login_form_detector import LoginFormDetector, detect_login_form
from .session_manager import SessionManager, get_session_manager
from .login_state_detector import LoginStateDetector, check_login_state
from .login_state_detector_v2 import LoginStateDetectorV2, check_login_state_v2, is_logged_in_v2
from .login_flow_manager import LoginFlowManager, LoginResult
from .session_manager_v2 import SessionManagerV2, create_session_manager_v2, ensure_profile_clean
from .captcha_login_integrator import CaptchaLoginIntegrator, CaptchaLoginResult, integrate_captcha_with_login

__all__ = [
    # Cookie 管理
    "CookieManager",
    "get_cookie_manager",
    # 登录表单检测
    "LoginFormDetector",
    "detect_login_form",
    # 会话管理
    "SessionManager",
    "get_session_manager",
    "SessionManagerV2",
    "create_session_manager_v2",
    "ensure_profile_clean",
    # 登录状态检测
    "LoginStateDetector",
    "check_login_state",
    "LoginStateDetectorV2",
    "check_login_state_v2",
    "is_logged_in_v2",
    # 登录流程
    "LoginFlowManager",
    "LoginResult",
    # 验证码集成
    "CaptchaLoginIntegrator",
    "CaptchaLoginResult",
    "integrate_captcha_with_login",
]
