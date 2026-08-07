"""
统一错误处理中间件

集成到网站操作核心流程中，提供：
- 错误捕获与分类
- 自动重试（基于错误类型）
- 结构化日志记录
- 告警触发
"""
from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import Any, Callable, Optional, Type, Union
from dataclasses import dataclass, field
from enum import Enum

from src.reliability.error import (
    ReliabilityError,
    ErrorCategory,
    CDPConnectionLostError,
    CDPCommandTimeoutError,
    ElementNotFoundError,
    ElementIndexInvalidError,
    NavigationTimeoutError,
    CaptchaDetectedError,
    BlockedByAntiBotError,
    NetworkIdleTimeoutError,
    SmartWaitDegradedError,
    is_retryable,
    categorize_error,
    ERROR_RULES,
)
from src.reliability.retry import (
    RetryConfig,
    CircuitBreaker,
    retry_operation,
    retry_operation_async,
    BackoffStrategy,
    get_retry_config,
)
from src.reliability.alert import AlertManager

logger = logging.getLogger(__name__)


class OperationType(Enum):
    """操作类型枚举"""
    NAVIGATION = "navigation"
    SCREENSHOT = "screenshot"
    CLICK = "click"
    INPUT = "input"
    WAIT = "wait"
    EXTRACT = "extract"
    SCROLL = "scroll"
    TAB = "tab"
    CDP_COMMAND = "cdp_command"
    UNKNOWN = "unknown"


@dataclass
class ErrorContext:
    """错误上下文"""
    operation: str
    operation_type: OperationType
    attempt: int
    max_attempts: int
    error: Optional[Exception] = None
    category: Optional[ErrorCategory] = None
    recoverable: bool = True
    details: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    elapsed: float = 0.0

    def to_dict(self) -> dict:
        return {
            "operation": self.operation,
            "operation_type": self.operation_type.value,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "error": str(self.error) if self.error else None,
            "category": self.category.value if self.category else None,
            "recoverable": self.recoverable,
            "details": self.details,
            "timestamp": self.timestamp,
            "elapsed": round(self.elapsed, 2),
        }

    def __str__(self) -> str:
        return (f"[{self.operation_type.value}] {self.operation} "
                f"(尝试 {self.attempt}/{self.max_attempts}, "
                f"分类: {self.category.value if self.category else 'UNKNOWN'})")


class ErrorMiddleware:
    """
    统一错误处理中间件
    
    集成到核心操作流程中，提供：
    1. 错误捕获与分类
    2. 基于错误类型的自动重试
    3. 结构化日志记录
    4. 告警触发
    """
    
    def __init__(
        self,
        alert_manager: Optional[AlertManager] = None,
        default_max_retries: int = 3,
        enable_circuit_breaker: bool = True,
    ):
        self.alert_manager = alert_manager or AlertManager()
        self.default_max_retries = default_max_retries
        self.enable_circuit_breaker = enable_circuit_breaker
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
    
    def get_circuit_breaker(self, operation: str) -> CircuitBreaker:
        """获取或创建操作级别的熔断器"""
        if operation not in self._circuit_breakers:
            self._circuit_breakers[operation] = CircuitBreaker(
                failure_threshold=5,
                recovery_timeout=30.0,
            )
        return self._circuit_breakers[operation]
    
    def wrap_sync(
        self,
        func=None,
        operation: str = None,
        operation_type: OperationType = OperationType.UNKNOWN,
        max_retries: int = None,
    ) -> Callable:
        """
        包装同步函数，添加错误处理
        
        Usage (decorator with args):
            @middleware.wrap_sync(operation="navigate_to_page", operation_type=OperationType.NAVIGATION)
            def my_func(url):
                ...
        
        Usage (direct call):
            wrapped = middleware.wrap_sync(my_func, "navigate_to_page", OperationType.NAVIGATION)
        """
        # 支持两种调用方式：
        # 1. @middleware.wrap_sync(operation="...", ...)  -> func=None
        # 2. middleware.wrap_sync(func, "...", ...)       -> func is callable
        
        # OperationType → RetryConfig.OPERATION_DEFAULTS 键映射
        _OP_KEY_MAP = {
            OperationType.NAVIGATION: "navigation",
            OperationType.SCREENSHOT: "screenshot",
            OperationType.CLICK: "input_click",
            OperationType.INPUT: "input_click",
            OperationType.WAIT: "element_find",
            OperationType.EXTRACT: "element_find",
            OperationType.SCROLL: "element_find",
            OperationType.TAB: "cdp_command",
            OperationType.CDP_COMMAND: "cdp_command",
            OperationType.UNKNOWN: "cdp_command",
        }

        def _wrap(func):
            _max_retries = max_retries or self.default_max_retries
            op_key = _OP_KEY_MAP.get(operation_type, "cdp_command")
            config = RetryConfig.for_operation(op_key, max_retries=_max_retries)

            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                context = ErrorContext(
                    operation=operation,
                    operation_type=operation_type,
                    attempt=1,
                    max_attempts=_max_retries,
                )

                cb = self.get_circuit_breaker(operation) if self.enable_circuit_breaker else None

                try:
                    result = retry_operation(
                        func,
                        config,
                        cb,
                        operation,
                        *args,
                        **kwargs,
                    )
                    context.elapsed = time.time() - context.timestamp
                    logger.info(f"操作成功: {context}")
                    return result
                except ReliabilityError as e:
                    context.error = e
                    context.category = categorize_error(e)
                    context.recoverable = e.recoverable
                    context.elapsed = time.time() - context.timestamp
                    
                    logger.warning(f"操作失败: {context}")
                    
                    # 触发告警（不可恢复错误记录日志）
                    if not e.recoverable:
                        logger.error(f"不可恢复错误，触发告警: {context}")
                    
                    raise
                except Exception as e:
                    context.error = e
                    context.category = ErrorCategory.UNKNOWN
                    context.recoverable = False
                    context.elapsed = time.time() - context.timestamp
                    
                    logger.error(f"未知错误: {context}")
                    raise
            
            return wrapper
        
        if func is not None and callable(func):
            # 直接调用方式：wrap_sync(func, "op", ...)
            return _wrap(func)
        else:
            # 装饰器方式：@wrap_sync(operation="...", ...)
            return _wrap
    
    def wrap_async(
        self,
        func=None,
        operation: str = None,
        operation_type: OperationType = OperationType.UNKNOWN,
        max_retries: int = None,
    ) -> Callable:
        """
        包装异步函数，添加错误处理
        
        Usage (decorator with args):
            @middleware.wrap_async(operation="fetch_page", operation_type=OperationType.EXTRACT)
            async def my_async_func(url):
                ...
        
        Usage (direct call):
            wrapped = middleware.wrap_async(my_async_func, "fetch_page", OperationType.EXTRACT)
        """
        # 支持两种调用方式：
        # 1. @middleware.wrap_async(operation="...", ...)  -> func=None
        # 2. middleware.wrap_async(func, "...", ...)       -> func is callable
        
        def _wrap(func):
            _max_retries = max_retries or self.default_max_retries
            op_key = _OP_KEY_MAP.get(operation_type, "cdp_command")
            config = RetryConfig.for_operation(op_key, max_retries=_max_retries)

            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                context = ErrorContext(
                    operation=operation,
                    operation_type=operation_type,
                    attempt=1,
                    max_attempts=_max_retries,
                )

                cb = self.get_circuit_breaker(operation) if self.enable_circuit_breaker else None

                try:
                    result = await retry_operation_async(
                        func,
                        config,
                        cb,
                        operation,
                        *args,
                        **kwargs,
                    )
                    context.elapsed = time.time() - context.timestamp
                    logger.info(f"操作成功: {context}")
                    return result
                except ReliabilityError as e:
                    context.error = e
                    context.category = categorize_error(e)
                    context.recoverable = e.recoverable
                    context.elapsed = time.time() - context.timestamp

                    logger.warning(f"操作失败: {context}")

                    # 触发告警（不可恢复错误记录日志）
                    if not e.recoverable:
                        logger.error(f"不可恢复错误，触发告警: {context}")
                    
                    raise
                except Exception as e:
                    context.error = e
                    context.category = ErrorCategory.UNKNOWN
                    context.recoverable = False
                    context.elapsed = time.time() - context.timestamp
                    
                    logger.error(f"未知错误: {context}")
                    raise
            
            return wrapper
        
        if func is not None and callable(func):
            # 直接调用方式：wrap_async(func, "op", ...)
            return _wrap(func)
        else:
            # 装饰器方式：@wrap_async(operation="...", ...)
            return _wrap
    
    def handle_error(self, error: Exception, operation: str, operation_type: OperationType) -> ErrorContext:
        """
        处理错误并返回上下文
        
        用于手动错误处理场景
        """
        context = ErrorContext(
            operation=operation,
            operation_type=operation_type,
            attempt=1,
            max_attempts=self.default_max_retries,
            error=error,
        )
        
        if isinstance(error, ReliabilityError):
            context.category = categorize_error(error)
            context.recoverable = error.recoverable
        else:
            context.category = ErrorCategory.UNKNOWN
            context.recoverable = False
        
        logger.warning(f"错误处理: {context}")
        
        if not context.recoverable:
            logger.error(f"不可恢复错误，触发告警: {context}")
        
        return context


# 全局中间件实例
_middleware: Optional[ErrorMiddleware] = None


def get_middleware() -> ErrorMiddleware:
    """获取全局中间件实例"""
    global _middleware
    if _middleware is None:
        _middleware = ErrorMiddleware()
    return _middleware


def reset_middleware():
    """重置全局中间件（用于测试）"""
    global _middleware
    _middleware = None


# 便捷装饰器

def with_error_handling(
    operation: str,
    operation_type: OperationType = OperationType.UNKNOWN,
    max_retries: int = None,
):
    """
    装饰器：为函数添加错误处理
    
    Usage:
        @with_error_handling("navigate", OperationType.NAVIGATION, max_retries=3)
        def navigate(url):
            ...
    """
    def decorator(func: Callable) -> Callable:
        middleware = get_middleware()
        return middleware.wrap_sync(func, operation, operation_type, max_retries)
    return decorator


def with_error_handling_async(
    operation: str,
    operation_type: OperationType = OperationType.UNKNOWN,
    max_retries: int = None,
):
    """
    装饰器：为异步函数添加错误处理
    
    Usage:
        @with_error_handling_async("fetch", OperationType.EXTRACT)
        async def fetch(url):
            ...
    """
    def decorator(func: Callable) -> Callable:
        middleware = get_middleware()
        return middleware.wrap_async(func, operation, operation_type, max_retries)
    return decorator


# 预定义的操作类型配置
OPERATION_CONFIGS = {
    OperationType.NAVIGATION: {
        "max_retries": 3,
        "base_delay": 2.0,
        "circuit_breaker": True,
    },
    OperationType.SCREENSHOT: {
        "max_retries": 2,
        "base_delay": 1.0,
        "circuit_breaker": False,
    },
    OperationType.CLICK: {
        "max_retries": 3,
        "base_delay": 1.0,
        "circuit_breaker": False,
    },
    OperationType.INPUT: {
        "max_retries": 3,
        "base_delay": 1.0,
        "circuit_breaker": False,
    },
    OperationType.WAIT: {
        "max_retries": 3,
        "base_delay": 1.0,
        "circuit_breaker": False,
    },
    OperationType.EXTRACT: {
        "max_retries": 3,
        "base_delay": 1.0,
        "circuit_breaker": False,
    },
}