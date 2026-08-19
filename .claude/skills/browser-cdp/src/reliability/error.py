"""
错误分类体系

提供结构化的错误分类，区分可恢复/不可恢复错误，
支持错误序列化以便日志记录和监控。
"""

import time
from enum import Enum
from typing import Any, Dict, Optional


class ErrorCategory(Enum):
    """错误分类枚举"""
    CONNECTION = "connection"        # CDP 连接问题
    TIMEOUT = "timeout"              # 超时
    ELEMENT = "element"              # 元素相关
    NAVIGATION = "navigation"        # 页面导航
    CONTENT = "content"              # 内容/验证码
    PERMISSION = "permission"        # 权限/拦截
    AUTH = "auth"                    # 认证失败
    RESOURCE = "resource"            # 资源耗尽
    UNKNOWN = "unknown"              # 未知


class ReliabilityError(Exception):
    """可靠性错误基类"""

    def __init__(
        self,
        message: str,
        category: ErrorCategory,
        recoverable: bool = True,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.category = category
        self.recoverable = recoverable
        self.details = details or {}
        self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典，便于日志记录和监控"""
        return {
            "type": self.__class__.__name__,
            "message": str(self),
            "category": self.category.value,
            "recoverable": self.recoverable,
            "details": self.details,
            "timestamp": self.timestamp,
        }

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(category={self.category.value}, recoverable={self.recoverable}, message={self.message!r})"


class CDPConnectionLostError(ReliabilityError):
    """CDP 连接断开"""

    def __init__(self, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            "CDP connection lost",
            ErrorCategory.CONNECTION,
            recoverable=True,
            details=details,
        )


class CircuitBreakerOpenError(ReliabilityError):
    """熔断器触发异常"""

    def __init__(self, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            "Circuit breaker is open",
            ErrorCategory.CONNECTION,
            recoverable=True,
            details=details,
        )
        self.details = details or {}


class CDPCommandTimeoutError(ReliabilityError):
    """CDP 命令超时"""

    def __init__(self, command: str, timeout: float, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"CDP command '{command}' timed out after {timeout}s",
            ErrorCategory.TIMEOUT,
            recoverable=True,
            details=details,
        )
        self.command = command
        self.timeout = timeout


class ElementNotFoundError(ReliabilityError):
    """元素未找到"""

    def __init__(self, selector: str = "", strategy: str = "unknown", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Element not found: selector={selector!r}, strategy={strategy!r}",
            ErrorCategory.ELEMENT,
            recoverable=True,
            details=details,
        )
        self.selector = selector
        self.strategy = strategy


class ElementIndexInvalidError(ReliabilityError):
    """元素编号无效"""

    def __init__(self, index: int, available_count: int, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Element index {index} invalid, only {available_count} elements available",
            ErrorCategory.ELEMENT,
            recoverable=True,
            details=details,
        )
        self.index = index
        self.available_count = available_count


class NavigationTimeoutError(ReliabilityError):
    """页面导航超时"""

    def __init__(self, url: str, timeout: float, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Navigation to '{url}' timed out after {timeout}s",
            ErrorCategory.NAVIGATION,
            recoverable=True,
            details=details,
        )
        self.url = url
        self.timeout = timeout


class CaptchaDetectedError(ReliabilityError):
    """检测到验证码"""

    def __init__(self, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            "Captcha detected, manual intervention required",
            ErrorCategory.CONTENT,
            recoverable=False,
            details=details,
        )


class BlockedByAntiBotError(ReliabilityError):
    """被反爬机制拦截"""

    def __init__(self, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            "Page blocked by anti-bot mechanism",
            ErrorCategory.PERMISSION,
            recoverable=False,
            details=details,
        )


class NetworkIdleTimeoutError(ReliabilityError):
    """networkidle 等待超时"""

    def __init__(self, timeout: float, pending_requests: int = 0, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"networkidle timeout after {timeout}s, {pending_requests} requests still pending",
            ErrorCategory.TIMEOUT,
            recoverable=True,
            details=details,
        )
        self.timeout = timeout
        self.pending_requests = pending_requests


class SmartWaitDegradedError(ReliabilityError):
    """智能等待降级：所有策略均失败"""

    def __init__(self, strategies_tried: list, timeout: float, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Smart wait degraded: all {len(strategies_tried)} strategies failed within {timeout}s",
            ErrorCategory.TIMEOUT,
            recoverable=True,
            details=details,
        )
        self.strategies_tried = strategies_tried
        self.timeout = timeout


class PageLoadError(ReliabilityError):
    """页面加载失败"""

    def __init__(self, url: str, status_code: int = 0, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Page load failed: url={url!r}, status_code={status_code}",
            ErrorCategory.NAVIGATION,
            recoverable=True,
            details=details,
        )
        self.url = url
        self.status_code = status_code


class ElementInteractableError(ReliabilityError):
    """元素不可交互（被遮挡、不可见等）"""

    def __init__(self, selector: str, reason: str = "", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Element not interactable: selector={selector!r}, reason={reason!r}",
            ErrorCategory.ELEMENT,
            recoverable=True,
            details=details,
        )
        self.selector = selector
        self.reason = reason


class PopupDetectedError(ReliabilityError):
    """检测到弹窗/覆盖层"""

    def __init__(self, popup_type: str = "unknown", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Popup detected: type={popup_type!r}",
            ErrorCategory.ELEMENT,
            recoverable=True,
            details=details,
        )
        self.popup_type = popup_type


class RateLimitError(ReliabilityError):
    """速率限制（429）"""

    def __init__(self, retry_after: float = 0.0, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Rate limited (429), retry_after={retry_after}s",
            ErrorCategory.TIMEOUT,
            recoverable=True,
            details=details,
        )
        self.retry_after = retry_after


class AuthenticationError(ReliabilityError):
    """认证失败（401/403）"""

    def __init__(self, status_code: int = 401, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Authentication failed: status_code={status_code}",
            ErrorCategory.AUTH,
            recoverable=False,
            details=details,
        )
        self.status_code = status_code


class ResourceExhaustedError(ReliabilityError):
    """资源耗尽（内存/连接池）"""

    def __init__(self, resource_type: str = "unknown", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Resource exhausted: type={resource_type!r}",
            ErrorCategory.RESOURCE,
            recoverable=False,
            details=details,
        )
        self.resource_type = resource_type


# ══════════════════════════════════════════════════════════════════
# 新增异常类（Step 2 补齐，与 exception_enum.py 的 35 种子类型一一对应）
# ══════════════════════════════════════════════════════════════════


class WebSocketDisconnectedError(ReliabilityError):
    """WebSocket 通道意外关闭"""

    def __init__(self, url: str = "", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"WebSocket disconnected: url={url!r}",
            ErrorCategory.CONNECTION,
            recoverable=True,
            details=details,
        )
        self.url = url


class CDPChannelClosedError(ReliabilityError):
    """CDP 通道被服务端关闭（不可恢复）"""

    def __init__(self, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            "CDP channel closed by remote end",
            ErrorCategory.CONNECTION,
            recoverable=False,
            details=details,
        )


class PageLoadTimeoutError(ReliabilityError):
    """页面完全加载超时"""

    def __init__(self, url: str, timeout: float, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Page load timed out: url={url!r}, timeout={timeout}s",
            ErrorCategory.TIMEOUT,
            recoverable=True,
            details=details,
        )
        self.url = url
        self.timeout = timeout


class ElementVisibilityTimeoutError(ReliabilityError):
    """元素可见性等待超时"""

    def __init__(self, selector: str, timeout: float, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Element visibility timeout: selector={selector!r}, timeout={timeout}s",
            ErrorCategory.ELEMENT,
            recoverable=True,
            details=details,
        )
        self.selector = selector
        self.timeout = timeout


class ElementDetachedError(ReliabilityError):
    """元素被 DOM 操作移除（detached）"""

    def __init__(self, selector: str = "", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Element detached from DOM: selector={selector!r}",
            ErrorCategory.ELEMENT,
            recoverable=True,
            details=details,
        )
        self.selector = selector


class StaleElementReferenceError(ReliabilityError):
    """StaleElementReference — 元素引用已过期"""

    def __init__(self, selector: str = "", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Stale element reference: selector={selector!r}",
            ErrorCategory.ELEMENT,
            recoverable=True,
            details=details,
        )
        self.selector = selector


class NavigationAbortedError(ReliabilityError):
    """导航请求被主动中止"""

    def __init__(self, url: str = "", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Navigation aborted: url={url!r}",
            ErrorCategory.NAVIGATION,
            recoverable=True,
            details=details,
        )
        self.url = url


class NavigationHistoryOverflowError(ReliabilityError):
    """浏览器历史栈溢出"""

    def __init__(self, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            "Navigation history stack overflowed",
            ErrorCategory.NAVIGATION,
            recoverable=False,
            details=details,
        )


class SameOriginNavigationFailedError(ReliabilityError):
    """同源导航失败"""

    def __init__(self, url: str = "", reason: str = "", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Same-origin navigation failed: url={url!r}, reason={reason!r}",
            ErrorCategory.NAVIGATION,
            recoverable=False,
            details=details,
        )
        self.url = url
        self.reason = reason


class InvisiblePageContentError(ReliabilityError):
    """页面内容为空或不可见"""

    def __init__(self, url: str = "", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Invisible or empty page content: url={url!r}",
            ErrorCategory.CONTENT,
            recoverable=False,
            details=details,
        )
        self.url = url


class UnexpectedPageTitleError(ReliabilityError):
    """页面标题与预期不符"""

    def __init__(self, expected: str = "", actual: str = "", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Unexpected page title: expected={expected!r}, actual={actual!r}",
            ErrorCategory.CONTENT,
            recoverable=False,
            details=details,
        )
        self.expected = expected
        self.actual = actual


class BlockedByCloudflareError(ReliabilityError):
    """Cloudflare 挑战页拦截"""

    def __init__(self, challenge_type: str = "", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Blocked by Cloudflare challenge: type={challenge_type!r}",
            ErrorCategory.PERMISSION,
            recoverable=False,
            details=details,
        )
        self.challenge_type = challenge_type


class BlockedByTurnstileError(ReliabilityError):
    """Turnstile 人机验证拦截"""

    def __init__(self, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            "Blocked by Turnstile CAPTCHA, manual intervention required",
            ErrorCategory.PERMISSION,
            recoverable=False,
            details=details,
        )


class IPBlockedError(ReliabilityError):
    """IP 被封禁"""

    def __init__(self, ip: str = "", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"IP blocked: ip={ip!r}",
            ErrorCategory.PERMISSION,
            recoverable=False,
            details=details,
        )
        self.ip = ip


class SessionExpiredError(ReliabilityError):
    """会话 Cookie 已过期"""

    def __init__(self, domain: str = "", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Session expired: domain={domain!r}",
            ErrorCategory.AUTH,
            recoverable=False,
            details=details,
        )
        self.domain = domain


class OAuthTokenExpiredError(ReliabilityError):
    """OAuth Token 过期"""

    def __init__(self, provider: str = "", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"OAuth token expired: provider={provider!r}",
            ErrorCategory.AUTH,
            recoverable=False,
            details=details,
        )
        self.provider = provider


class MemoryLimitExceededError(ReliabilityError):
    """内存使用超限"""

    def __init__(self, current_mb: float = 0.0, limit_mb: float = 0.0, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Memory limit exceeded: current={current_mb}MB, limit={limit_mb}MB",
            ErrorCategory.RESOURCE,
            recoverable=False,
            details=details,
        )
        self.current_mb = current_mb
        self.limit_mb = limit_mb


class ConnectionPoolExhaustedError(ReliabilityError):
    """CDP 连接池无空闲连接"""

    def __init__(self, pool_size: int = 0, active: int = 0, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Connection pool exhausted: size={pool_size}, active={active}",
            ErrorCategory.RESOURCE,
            recoverable=False,
            details=details,
        )
        self.pool_size = pool_size
        self.active = active


class TabLimitReachedError(ReliabilityError):
    """浏览器标签页数量上限已到达"""

    def __init__(self, current_tabs: int = 0, max_tabs: int = 0, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Tab limit reached: current={current_tabs}, max={max_tabs}",
            ErrorCategory.RESOURCE,
            recoverable=False,
            details=details,
        )
        self.current_tabs = current_tabs
        self.max_tabs = max_tabs


class UnknownBrowserException(ReliabilityError):
    """无法归类到任何已知分类的未知异常"""

    def __init__(self, original_exception: Optional[Exception] = None, details: Optional[Dict[str, Any]] = None):
        msg = f"Unknown browser exception: {original_exception!r}" if original_exception else "Unknown browser exception"
        super().__init__(
            msg,
            ErrorCategory.UNKNOWN,
            recoverable=False,
            details=details,
        )
        self.original_exception = original_exception


def is_retryable(error: Exception) -> bool:
    """判断错误是否可重试"""
    if isinstance(error, ReliabilityError):
        return error.recoverable
    # CDP 相关异常默认可重试
    if "CDP" in type(error).__name__ or "websocket" in str(type(error)).lower():
        return True
    # HTTP 429 可重试
    if hasattr(error, 'status_code') and error.status_code == 429:
        return True
    return False


def categorize_error(error: Exception) -> ErrorCategory:
    """根据异常类型推断错误分类"""
    if isinstance(error, CDPConnectionLostError):
        return ErrorCategory.CONNECTION
    if isinstance(error, (CDPCommandTimeoutError, NetworkIdleTimeoutError, SmartWaitDegradedError)):
        return ErrorCategory.TIMEOUT
    if isinstance(error, (ElementNotFoundError, ElementIndexInvalidError, ElementInteractableError, PopupDetectedError)):
        return ErrorCategory.ELEMENT
    if isinstance(error, (NavigationTimeoutError, PageLoadError)):
        return ErrorCategory.NAVIGATION
    if isinstance(error, CaptchaDetectedError):
        return ErrorCategory.CONTENT
    if isinstance(error, BlockedByAntiBotError):
        return ErrorCategory.PERMISSION
    if isinstance(error, RateLimitError):
        return ErrorCategory.TIMEOUT
    if isinstance(error, AuthenticationError):
        return ErrorCategory.AUTH
    if isinstance(error, ResourceExhaustedError):
        return ErrorCategory.RESOURCE
    # 基于异常名启发式分类
    name = type(error).__name__
    if any(kw in name for kw in ["Timeout", "TimedOut"]):
        return ErrorCategory.TIMEOUT
    if any(kw in name for kw in ["Element", "Selector", "NotFound"]):
        return ErrorCategory.ELEMENT
    if any(kw in name for kw in ["Navigation", "Load", "Page"]):
        return ErrorCategory.NAVIGATION
    if any(kw in name for kw in ["Captcha", "AntiBot", "Blocked"]):
        return ErrorCategory.PERMISSION
    if any(kw in name for kw in ["Connection", "Connect", "WebSocket", "CDP"]):
        return ErrorCategory.CONNECTION
    return ErrorCategory.UNKNOWN


# 错误分类规则表（供文档和日志使用）
ERROR_RULES = {
    ErrorCategory.CONNECTION: {
        "examples": ["CDPConnectionLostError"],
        "recoverable": True,
        "action": "重建连接 + 重试",
    },
    ErrorCategory.TIMEOUT: {
        "examples": ["CDPCommandTimeoutError", "NetworkIdleTimeoutError", "SmartWaitDegradedError"],
        "recoverable": True,
        "action": "重试（1次）或降级",
    },
    ErrorCategory.ELEMENT: {
        "examples": ["ElementNotFoundError", "ElementIndexInvalidError", "ElementInteractableError", "PopupDetectedError"],
        "recoverable": True,
        "action": "重新扫描元素或等待",
    },
    ErrorCategory.NAVIGATION: {
        "examples": ["NavigationTimeoutError", "PageLoadError"],
        "recoverable": True,
        "action": "重试导航",
    },
    ErrorCategory.CONTENT: {
        "examples": ["CaptchaDetectedError"],
        "recoverable": False,
        "action": "停止 + 通知用户",
    },
    ErrorCategory.PERMISSION: {
        "examples": ["BlockedByAntiBotError"],
        "recoverable": False,
        "action": "停止 + 通知用户",
    },
    ErrorCategory.AUTH: {
        "examples": ["RateLimitError", "AuthenticationError"],
        "recoverable": True,
        "action": "等待后重试（429）或停止+通知（401/403）",
    },
    ErrorCategory.RESOURCE: {
        "examples": ["ResourceExhaustedError"],
        "recoverable": False,
        "action": "记录日志 + 人工介入",
    },
    ErrorCategory.UNKNOWN: {
        "examples": ["其他未分类异常"],
        "recoverable": False,
        "action": "记录日志 + 人工介入",
    },
}