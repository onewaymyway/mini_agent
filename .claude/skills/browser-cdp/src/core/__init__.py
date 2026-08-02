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
    # 增强会话
    "EnhancedCDPSession",
]
