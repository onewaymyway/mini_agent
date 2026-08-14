# -*- coding: utf-8 -*-
"""
CDP 异常捕获模块

覆盖所有 CDP 调用点的异常处理，提供统一的错误分类和重试策略。

设计目标：
1. 捕获所有 CDP 调用异常（连接、超时、元素、导航等）
2. 自动分类错误类型并记录详细日志
3. 支持智能重试策略
4. 提供异常上下文追踪
"""

import asyncio
import functools
import logging
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum

# 导入已有的错误分类体系
from ..error import (
    ErrorCategory,
    ReliabilityError,
    CDPConnectionLostError,
    CDPCommandTimeoutError,
    WebSocketDisconnectedError,
    CDPChannelClosedError,
    ElementNotFoundError,
    NavigationTimeoutError,
    PageLoadError,
    CaptchaDetectedError,
    BlockedByAntiBotError,
    RateLimitError,
    AuthenticationError,
    is_retryable,
    categorize_error,
)

logger = logging.getLogger(__name__)


class CDPOperationType(Enum):
    """CDP 操作类型枚举"""
    CONNECT = "connect"
    DISCONNECT = "disconnect"
    NAVIGATE = "navigate"
    EVAL_JS = "eval_js"
    QUERY_SELECTOR = "query_selector"
    CLICK = "click"
    TYPE = "type"
    SCROLL = "scroll"
    SCREENSHOT = "screenshot"
    GET_COOKIES = "get_cookies"
    SET_COOKIE = "set_cookie"
    WAIT_EVENT = "wait_event"
    CUSTOM = "custom"


@dataclass
class CDPExceptionContext:
    """CDP 异常上下文"""
    operation: str = ""
    operation_type: CDPOperationType = CDPOperationType.CUSTOM
    target_url: str = ""
    selector: str = ""
    method: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    start_time: float = 0.0
    end_time: float = 0.0
    attempt: int = 0
    max_attempts: int = 3
    error_history: List[Dict[str, Any]] = field(default_factory=list)
    
    def record_error(self, error: Exception, category: ErrorCategory):
        """记录错误历史"""
        self.error_history.append({
            "timestamp": time.time(),
            "error_type": type(error).__name__,
            "error_message": str(error),
            "category": category.value,
            "traceback": traceback.format_exc(),
        })
    
    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "operation": self.operation,
            "operation_type": self.operation_type.value,
            "target_url": self.target_url,
            "selector": self.selector,
            "method": self.method,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.end_time - self.start_time if self.end_time > 0 else 0,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "error_count": len(self.error_history),
            "last_error": self.error_history[-1] if self.error_history else None,
        }


class CDPExceptionHandler:
    """
    CDP 异常处理器
    
    提供统一的 CDP 异常捕获、分类、重试和上下文管理。
    """
    
    def __init__(self, default_max_retries: int = 3, default_timeout: float = 30.0):
        self.default_max_retries = default_max_retries
        self.default_timeout = default_timeout
        self._retry_counts: Dict[str, int] = {}
        self._error_stats: Dict[str, List[Dict[str, Any]]] = {}
    
    def handle(
        self,
        operation: str = "unknown",
        operation_type: CDPOperationType = CDPOperationType.CUSTOM,
        max_retries: Optional[int] = None,
        timeout: Optional[float] = None,
        **context_kwargs,
    ):
        """
        装饰器工厂：为 CDP 函数添加异常处理
        
        Args:
            operation: 操作名称（用于日志和统计）
            operation_type: 操作类型
            max_retries: 最大重试次数
            timeout: 超时时间
            **context_kwargs: 其他上下文信息
        
        Returns:
            装饰器函数
        """
        max_retries = max_retries or self.default_max_retries
        timeout = timeout or self.default_timeout
        
        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                context = CDPExceptionContext(
                    operation=operation,
                    operation_type=operation_type,
                    start_time=time.time(),
                    max_attempts=max_retries,
                    **context_kwargs,
                )
                
                last_error = None
                for attempt in range(1, max_retries + 1):
                    context.attempt = attempt
                    try:
                        result = func(*args, **kwargs)
                        context.end_time = time.time()
                        self._record_success(operation, context)
                        return result
                        
                    except Exception as e:
                        last_error = e
                        category = categorize_error(e)
                        context.record_error(e, category)
                        
                        logger.warning(
                            f"CDP 操作 '{operation}' 失败 (attempt {attempt}/{max_retries}): "
                            f"{type(e).__name__}: {e}"
                        )
                        
                        # 判断是否可重试
                        if not is_retryable(e) or attempt >= max_retries:
                            context.end_time = time.time()
                            self._record_failure(operation, context, e)
                            raise
                        
                        # 等待后重试
                        wait_time = self._calculate_wait_time(attempt, category)
                        logger.debug(f"等待 {wait_time:.2f}s 后重试...")
                        time.sleep(wait_time)
                
                # 所有重试失败
                context.end_time = time.time()
                self._record_failure(operation, context, last_error)
                raise
            
            return wrapper
        
        return decorator
    
    def handle_async(
        self,
        operation: str = "unknown",
        operation_type: CDPOperationType = CDPOperationType.CUSTOM,
        max_retries: Optional[int] = None,
        timeout: Optional[float] = None,
        **context_kwargs,
    ):
        """
        异步装饰器工厂：为 CDP 异步函数添加异常处理
        """
        max_retries = max_retries or self.default_max_retries
        timeout = timeout or self.default_timeout
        
        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                context = CDPExceptionContext(
                    operation=operation,
                    operation_type=operation_type,
                    start_time=time.time(),
                    max_attempts=max_retries,
                    **context_kwargs,
                )
                
                last_error = None
                for attempt in range(1, max_retries + 1):
                    context.attempt = attempt
                    try:
                        result = await func(*args, **kwargs)
                        context.end_time = time.time()
                        self._record_success(operation, context)
                        return result
                        
                    except Exception as e:
                        last_error = e
                        category = categorize_error(e)
                        context.record_error(e, category)
                        
                        logger.warning(
                            f"CDP 异步操作 '{operation}' 失败 (attempt {attempt}/{max_retries}): "
                            f"{type(e).__name__}: {e}"
                        )
                        
                        if not is_retryable(e) or attempt >= max_retries:
                            context.end_time = time.time()
                            self._record_failure(operation, context, e)
                            raise
                        
                        wait_time = self._calculate_wait_time(attempt, category)
                        await asyncio.sleep(wait_time)
                
                context.end_time = time.time()
                self._record_failure(operation, context, last_error)
                raise
            
            return wrapper
        
        return decorator
    
    def _calculate_wait_time(self, attempt: int, category: ErrorCategory) -> float:
        """计算重试等待时间（指数退避）"""
        base_delay = 0.5
        # 连接类错误等待更长时间
        if category in (ErrorCategory.CONNECTION, ErrorCategory.TIMEOUT):
            base_delay = 1.0
        return base_delay * (2 ** (attempt - 1))
    
    def _record_success(self, operation: str, context: CDPExceptionContext):
        """记录成功"""
        if operation not in self._error_stats:
            self._error_stats[operation] = []
        self._error_stats[operation].append({
            "type": "success",
            "timestamp": time.time(),
            "duration": context.end_time - context.start_time,
            "attempts": context.attempt,
        })
    
    def _record_failure(self, operation: str, context: CDPExceptionContext, error: Optional[Exception]):
        """记录失败"""
        if operation not in self._error_stats:
            self._error_stats[operation] = []
        self._error_stats[operation].append({
            "type": "failure",
            "timestamp": time.time(),
            "error": str(error) if error else "unknown",
            "context": context.to_dict(),
        })
    
    def get_stats(self, operation: Optional[str] = None) -> Dict[str, Any]:
        """获取统计信息"""
        if operation:
            return self._error_stats.get(operation, [])
        return self._error_stats
    
    def reset_stats(self, operation: Optional[str] = None):
        """重置统计信息"""
        if operation:
            self._error_stats.pop(operation, None)
        else:
            self._error_stats.clear()


# 全局实例
_global_handler: Optional[CDPExceptionHandler] = None


def get_cdp_exception_handler() -> CDPExceptionHandler:
    """获取全局 CDP 异常处理器"""
    global _global_handler
    if _global_handler is None:
        _global_handler = CDPExceptionHandler()
    return _global_handler


def reset_cdp_exception_handler():
    """重置全局 CDP 异常处理器"""
    global _global_handler
    _global_handler = None


# 便捷装饰器

def with_cdp_exception_handling(
    operation: str = "unknown",
    operation_type: CDPOperationType = CDPOperationType.CUSTOM,
    max_retries: Optional[int] = None,
    timeout: Optional[float] = None,
    **context_kwargs,
):
    """
    装饰器：为 CDP 函数添加异常处理

    Usage:
        @with_cdp_exception_handling("search", CDPOperationType.QUERY_SELECTOR, selector="#input")
        def search(query: str):
            ...
    """
    def decorator(func: Callable) -> Callable:
        handler = get_cdp_exception_handler()
        # 先获取装饰器，再应用到函数上
        decorator_fn = handler.handle(
            operation=operation,
            operation_type=operation_type,
            max_retries=max_retries,
            timeout=timeout,
            **context_kwargs,
        )
        return decorator_fn(func)
    return decorator


def async_with_cdp_exception_handling(
    operation: str = "unknown",
    operation_type: CDPOperationType = CDPOperationType.CUSTOM,
    max_retries: Optional[int] = None,
    timeout: Optional[float] = None,
    **context_kwargs,
):
    """
    异步装饰器：为 CDP 异步函数添加异常处理
    """
    def decorator(func: Callable) -> Callable:
        handler = get_cdp_exception_handler()
        # 先获取装饰器，再应用到函数上
        decorator_fn = handler.handle_async(
            operation=operation,
            operation_type=operation_type,
            max_retries=max_retries,
            timeout=timeout,
            **context_kwargs,
        )
        return decorator_fn(func)
    return decorator


@contextmanager
def cdp_operation_context(
    operation: str,
    operation_type: CDPOperationType = CDPOperationType.CUSTOM,
    **context_kwargs,
):
    """
    上下文管理器：为 CDP 操作提供异常捕获和上下文管理
    
    Usage:
        with cdp_operation_context("search", CDPOperationType.QUERY_SELECTOR, selector="#input") as ctx:
            result = cdp_call()
            ctx.record_success()
    """
    handler = get_cdp_exception_handler()
    context = CDPExceptionContext(
        operation=operation,
        operation_type=operation_type,
        start_time=time.time(),
        **context_kwargs,
    )
    
    try:
        yield context
        context.end_time = time.time()
        handler._record_success(operation, context)
    except Exception as e:
        category = categorize_error(e)
        context.record_error(e, category)
        context.end_time = time.time()
        handler._record_failure(operation, context, e)
        raise


def wrap_cdp_call(
    func: Callable,
    operation: str,
    operation_type: CDPOperationType = CDPOperationType.CUSTOM,
    **context_kwargs,
) -> Callable:
    """
    包装 CDP 调用函数，添加异常处理

    Usage:
        wrapped = wrap_cdp_call(cdp_session.send, "navigate", CDPOperationType.NAVIGATE, target_url="https://example.com")
    """
    handler = get_cdp_exception_handler()
    # 先获取装饰器，再应用到函数上
    decorator_fn = handler.handle(
        operation=operation,
        operation_type=operation_type,
        **context_kwargs,
    )
    return decorator_fn(func)


class CDPTimedOperation:
    """
    CDP 定时操作封装器
    
    提供带超时的 CDP 操作执行和异常处理。
    """
    
    def __init__(self, timeout: float = 30.0, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        self.handler = get_cdp_exception_handler()
    
    def execute(
        self,
        func: Callable,
        operation: str,
        operation_type: CDPOperationType = CDPOperationType.CUSTOM,
        *args,
        **kwargs,
    ) -> Any:
        """执行带超时的 CDP 操作"""
        decorator_fn = self.handler.handle(
            operation=operation,
            operation_type=operation_type,
            max_retries=self.max_retries,
            timeout=self.timeout,
        )
        
        @decorator_fn
        def timed_execute():
            start = time.time()
            result = func(*args, **kwargs)
            duration = time.time() - start
            if duration > self.timeout:
                raise TimeoutError(f"操作超时: {duration:.2f}s > {self.timeout}s")
            return result
        
        return timed_execute()
    
    async def execute_async(
        self,
        func: Callable,
        operation: str,
        operation_type: CDPOperationType = CDPOperationType.CUSTOM,
        *args,
        **kwargs,
    ) -> Any:
        """执行带超时的异步 CDP 操作"""
        decorator_fn = self.handler.handle_async(
            operation=operation,
            operation_type=operation_type,
            max_retries=self.max_retries,
            timeout=self.timeout,
        )
        
        @decorator_fn
        async def timed_execute():
            start = time.time()
            result = await func(*args, **kwargs)
            duration = time.time() - start
            if duration > self.timeout:
                raise TimeoutError(f"操作超时: {duration:.2f}s > {self.timeout}s")
            return result
        
        return await timed_execute()


# 预定义的 CDP 操作封装函数

@with_cdp_exception_handling("cdp_connect", CDPOperationType.CONNECT)
def connect_cdp(ws_url: str, timeout: float = 15.0) -> Any:
    """连接 CDP 的包装函数"""
    # 实际实现需要调用 cdp_client.py 的连接逻辑
    pass


@with_cdp_exception_handling("cdp_navigate", CDPOperationType.NAVIGATE, target_url="")
def navigate_cdp(session: Any, url: str, timeout: float = 30.0) -> Any:
    """导航到 URL 的包装函数"""
    pass


@with_cdp_exception_handling("cdp_eval_js", CDPOperationType.EVAL_JS)
def eval_js_cdp(session: Any, expression: str, timeout: float = 15.0) -> Any:
    """执行 JS 的包装函数"""
    pass


@with_cdp_exception_handling("cdp_query_selector", CDPOperationType.QUERY_SELECTOR)
def query_selector_cdp(session: Any, selector: str, timeout: float = 10.0) -> Any:
    """查询元素的包装函数"""
    pass


__all__ = [
    "CDPExceptionHandler",
    "CDPExceptionContext",
    "CDPOperationType",
    "get_cdp_exception_handler",
    "reset_cdp_exception_handler",
    "with_cdp_exception_handling",
    "async_with_cdp_exception_handling",
    "cdp_operation_context",
    "wrap_cdp_call",
    "CDPTimedOperation",
    "connect_cdp",
    "navigate_cdp",
    "eval_js_cdp",
    "query_selector_cdp",
]
