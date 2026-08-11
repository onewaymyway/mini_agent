# -*- coding: utf-8 -*-
"""
增强异常处理集成模块

整合重试、超时、错误恢复、故障转移、会话恢复等能力，
提供统一的异常处理入口。
"""

import asyncio
import logging
import time
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

from .error import (
    ErrorCategory,
    ReliabilityError,
    categorize_error,
    is_retryable,
)
from .retry import get_retry_config
from .timeout_control import get_timeout_manager
from .error_recovery import get_recovery_manager
from .failover_manager import FailoverManager, get_failover_manager
from .session_recovery import SessionRecovery, get_session_recovery
from .enhanced_timeout import get_enhanced_timeout_manager

logger = logging.getLogger(__name__)


class EnhancedExceptionHandler:
    """
    增强异常处理器
    
    整合重试、超时、错误恢复、故障转移、会话恢复等能力。
    """
    
    def __init__(self):
        self.recovery = get_recovery_manager()
        self.timeout_manager = get_enhanced_timeout_manager()
        self._failover_managers: Dict[str, FailoverManager] = {}
        self._session_recoveries: Dict[str, SessionRecovery] = {}
    
    def handle(
        self,
        func: Callable,
        operation: str = "unknown",
        *args,
        **kwargs,
    ) -> Any:
        """
        同步异常处理包装器
        
        自动重试 + 超时控制 + 多级恢复
        """
        config = get_retry_config(operation)
        timeout_config = self.timeout_manager.get_timeout(operation)
        
        def wrapped_func():
            start = time.time()
            result = func(*args, **kwargs)
            duration = time.time() - start
            
            # 检查是否超时
            if duration > timeout_config.total_timeout:
                self.timeout_manager.record_timeout(operation)
                raise TimeoutError(
                    f"Operation {operation} exceeded timeout: {duration:.1f}s > {timeout_config.total_timeout}s"
                )
            
            self.timeout_manager.record_success(operation)
            return result
        
        try:
            return config.retry_operation(
                wrapped_func,
                config=config,
                operation=operation,
            )
        except Exception as e:
            context = {
                "operation": operation,
                "error_type": type(e).__name__,
                "error_category": categorize_error(e).value if isinstance(e, ReliabilityError) else "unknown",
            }
            
            success, result = self.recovery.recover(e, context)
            if success:
                logger.info(f"Recovery successful for {operation}")
                return result
            
            logger.error(f"All recovery levels exhausted for {operation}")
            raise
    
    async def handle_async(
        self,
        func: Callable,
        operation: str = "unknown",
        *args,
        **kwargs,
    ) -> Any:
        """
        异步异常处理包装器
        
        自动重试 + 超时控制 + 多级恢复
        """
        config = get_retry_config(operation)
        timeout_config = self.timeout_manager.get_timeout(operation)
        
        async def wrapped_func():
            start = time.time()
            result = await func(*args, **kwargs)
            duration = time.time() - start
            
            if duration > timeout_config.total_timeout:
                self.timeout_manager.record_timeout(operation)
                raise TimeoutError(
                    f"Operation {operation} exceeded timeout: {duration:.1f}s > {timeout_config.total_timeout}s"
                )
            
            self.timeout_manager.record_success(operation)
            return result
        
        try:
            return await asyncio.wait_for(
                wrapped_func(),
                timeout=timeout_config.total_timeout
            )
        except asyncio.TimeoutError:
            self.timeout_manager.record_timeout(operation)
            raise TimeoutError(f"Operation {operation} timed out after {timeout_config.total_timeout}s")
        except Exception as e:
            context = {
                "operation": operation,
                "error_type": type(e).__name__,
                "error_category": categorize_error(e).value if isinstance(e, ReliabilityError) else "unknown",
            }
            
            success, result = self.recovery.recover(e, context)
            if success:
                logger.info(f"Recovery successful for {operation}")
                return result
            
            logger.error(f"All recovery levels exhausted for {operation}")
            raise
    
    def register_failover(self, operation: str, primary: str, backups: List[str]):
        """注册故障转移配置"""
        self._failover_managers[operation] = FailoverManager(primary, backups)
        logger.info(f"Registered failover for {operation}: {primary} -> {backups}")
    
    def get_failover(self, operation: str) -> Optional[FailoverManager]:
        """获取故障转移管理器"""
        return self._failover_managers.get(operation)
    
    def register_session_recovery(self, operation: str, session_dir: str):
        """注册会话恢复配置"""
        self._session_recoveries[operation] = SessionRecovery(session_dir)
        logger.info(f"Registered session recovery for {operation}: {session_dir}")
    
    def get_session_recovery(self, operation: str) -> Optional[SessionRecovery]:
        """获取会话恢复管理器"""
        return self._session_recoveries.get(operation)
    
    def get_status(self) -> Dict[str, Any]:
        """获取处理器状态"""
        return {
            "timeout_manager": self.timeout_manager.get_status(),
            "failover_managers": {
                op: mgr.get_status() 
                for op, mgr in self._failover_managers.items()
            },
            "session_recoveries": list(self._session_recoveries.keys()),
        }


_global_handler: Optional[EnhancedExceptionHandler] = None


def get_enhanced_exception_handler() -> EnhancedExceptionHandler:
    """获取全局异常处理器"""
    global _global_handler
    if _global_handler is None:
        _global_handler = EnhancedExceptionHandler()
    return _global_handler


def reset_enhanced_exception_handler():
    """重置全局异常处理器"""
    global _global_handler
    _global_handler = None


# 便捷装饰器

def with_enhanced_exception_handling(operation: str = "unknown"):
    """
    装饰器：为函数添加统一异常处理
    
    Usage:
        @with_enhanced_exception_handling("search")
        def search(query: str):
            ...
    """
    def decorator(func: Callable) -> Callable:
        handler = get_enhanced_exception_handler()
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            return handler.handle(func, operation, *args, **kwargs)
        
        return wrapper
    return decorator


def async_with_enhanced_exception_handling(operation: str = "unknown"):
    """
    异步装饰器：为函数添加统一异常处理
    
    Usage:
        @async_with_enhanced_exception_handling("search")
        async def search(query: str):
            ...
    """
    def decorator(func: Callable) -> Callable:
        handler = get_enhanced_exception_handler()
        
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await handler.handle_async(func, operation, *args, **kwargs)
        
        return wrapper
    return decorator
