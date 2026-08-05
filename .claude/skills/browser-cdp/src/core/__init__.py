"""
browser-cdp skill - 增强版 CDP 浏览器控制

新增模块：
- smart_wait: 智能等待策略
- retry_handler: 重试与熔断
- dynamic_loader: 动态内容加载
- complex_dom: 复杂 DOM 处理
- stealth: 反检测模式
- enhanced_cdp_session: 统一增强 API
"""
from src.core.smart_wait import SmartWait, WaitConfig
from src.core.retry_handler import RetryHandler, RetryConfig, FailureReason, retry
from src.core.dynamic_loader import DynamicLoader, ScrollConfig, LazyLoadConfig
from src.core.complex_dom import ComplexDOMHandler, DOMScanConfig
from src.core.stealth import StealthMode, StealthConfig
from src.core.enhanced_cdp_session import EnhancedCDPSession
from src.core.captcha_handler import CaptchaHandler, CaptchaType, CaptchaResult, AntiDetection
from src.core.turnstile_handler import TurnstileHandler, TurnstileResult, detect_and_solve_turnstile
from src.core.oauth_handler import OAuthHandler, OAuthResult, oauth_login
from src.core.spa_detector import SPADetector, SPAFramework, SPAInfo, detect_spa, wait_for_spa_route
from src.core.virtual_list_loader import VirtualListLoader, VirtualListConfig, ListItem, load_virtual_list

__all__ = [
    # 智能等待
    "SmartWait",
    "WaitConfig",
    # 重试处理
    "RetryHandler",
    "RetryConfig",
    "FailureReason",
    "retry",
    # 动态加载
    "DynamicLoader",
    "ScrollConfig",
    "LazyLoadConfig",
    # 复杂 DOM
    "ComplexDOMHandler",
    "DOMScanConfig",
    # 反检测
    "StealthMode",
    "StealthConfig",
    # 验证码处理
    "CaptchaHandler",
    "CaptchaType",
    "CaptchaResult",
    "AntiDetection",
    # Turnstile 验证码
    "TurnstileHandler",
    "TurnstileResult",
    "detect_and_solve_turnstile",
    # OAuth 登录
    "OAuthHandler",
    "OAuthResult",
    "oauth_login",
    # SPA 检测
    "SPADetector",
    "SPAFramework",
    "SPAInfo",
    "detect_spa",
    "wait_for_spa_route",
    # 虚拟列表
    "VirtualListLoader",
    "VirtualListConfig",
    "ListItem",
    "load_virtual_list",
    # 增强会话
    "EnhancedCDPSession",
]
