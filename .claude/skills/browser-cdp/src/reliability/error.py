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


def is_retryable(error: Exception) -> bool:
    """判断错误是否可重试"""
    if isinstance(error, ReliabilityError):
        return error.recoverable
    # CDP 相关异常默认可重试
    if "CDP" in type(error).__name__ or "websocket" in str(type(error)).lower():
        return True
    return False


def categorize_error(error: Exception) -> ErrorCategory:
    """根据异常类型推断错误分类"""
    if isinstance(error, CDPConnectionLostError):
        return ErrorCategory.CONNECTION
    if isinstance(error, (CDPCommandTimeoutError, NetworkIdleTimeoutError, SmartWaitDegradedError)):
        return ErrorCategory.TIMEOUT
    if isinstance(error, (ElementNotFoundError, ElementIndexInvalidError)):
        return ErrorCategory.ELEMENT
    if isinstance(error, NavigationTimeoutError):
        return ErrorCategory.NAVIGATION
    if isinstance(error, (CaptchaDetectedError, BlockedByAntiBotError)):
        return ErrorCategory.CONTENT if isinstance(error, CaptchaDetectedError) else ErrorCategory.PERMISSION
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
        "examples": ["ElementNotFoundError", "ElementIndexInvalidError"],
        "recoverable": True,
        "action": "重新扫描元素或等待",
    },
    ErrorCategory.NAVIGATION: {
        "examples": ["NavigationTimeoutError"],
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
    ErrorCategory.UNKNOWN: {
        "examples": ["其他未分类异常"],
        "recoverable": "视情况",
        "action": "记录日志 + 最多重试1次",
    },
}