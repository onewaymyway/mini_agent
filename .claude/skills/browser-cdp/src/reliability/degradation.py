"""
降级策略模块

提供操作失败时的降级处理机制：
- 跳过操作：记录错误但继续执行后续操作
- 返回错误：抛出结构化错误供上层处理
- 降级模式：使用备用策略（如静态缓存、简化提取）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from .error import (
    ReliabilityError,
    ErrorCategory,
    is_retryable,
    categorize_error,
)
from .middleware import ErrorContext, OperationType

logger = logging.getLogger(__name__)


class DegradationMode(Enum):
    """降级模式"""
    SKIP = "skip"           # 跳过操作，返回默认值
    ERROR = "error"         # 抛出结构化错误
    FALLBACK = "fallback"   # 使用备用策略
    CACHED = "cached"       # 返回缓存数据


@dataclass
class DegradationConfig:
    """降级配置"""
    mode: DegradationMode = DegradationMode.ERROR
    skip_on_categories: list[ErrorCategory] = field(default_factory=lambda: [
        ErrorCategory.CONTENT,
        ErrorCategory.PERMISSION,
    ])
    fallback_func: Optional[Callable] = None
    cache_key: Optional[str] = None
    default_value: Any = None


class DegradationHandler:
    """
    降级处理器

    决策逻辑：
    1. 检查错误是否可恢复
    2. 根据错误类别决定降级模式
    3. 执行降级策略（跳过/错误/备用/缓存）
    """

    def __init__(self, config: Optional[DegradationConfig] = None):
        self.config = config or DegradationConfig()
        self._cache: dict[str, Any] = {}

    def handle(
        self,
        error: Exception,
        operation: str,
        operation_type: OperationType,
        context: Optional[ErrorContext] = None,
    ) -> Any:
        """
        处理降级决策

        Returns:
            降级后的结果（可能是默认值、缓存数据或抛出错误）
        """
        # 分类错误
        if isinstance(error, ReliabilityError):
            category = categorize_error(error)
            recoverable = error.recoverable
        else:
            category = ErrorCategory.UNKNOWN
            recoverable = False

        # 决策降级模式
        # 优先检查是否配置了跳过该类别
        if category in self.config.skip_on_categories:
            mode = DegradationMode.SKIP
            logger.warning(f"[{operation}] 执行跳过降级: {category.value}")
        elif self.config.mode == DegradationMode.FALLBACK and self.config.fallback_func:
            mode = DegradationMode.FALLBACK
        elif self.config.mode == DegradationMode.CACHED and self.config.cache_key:
            mode = DegradationMode.CACHED
        else:
            mode = DegradationMode.ERROR

        # 执行降级
        if mode == DegradationMode.SKIP:
            return self._handle_skip(operation, category, error)
        elif mode == DegradationMode.ERROR:
            return self._handle_error(operation, category, error)
        elif mode == DegradationMode.FALLBACK:
            return self._handle_fallback(operation, error)
        elif mode == DegradationMode.CACHED:
            return self._handle_cached(operation)

    def _handle_skip(self, operation: str, category: ErrorCategory, error: Exception) -> Any:
        """跳过操作，返回默认值"""
        logger.warning(f"[{operation}] 跳过操作，返回默认值: {error}")
        return self.config.default_value

    def _handle_error(self, operation: str, category: ErrorCategory, error: Exception) -> Any:
        """抛出结构化错误"""
        raise error

    def _handle_fallback(self, operation: str, error: Exception) -> Any:
        """执行备用策略"""
        try:
            logger.info(f"[{operation}] 执行备用策略")
            return self.config.fallback_func()
        except Exception as fallback_error:
            logger.error(f"[{operation}] 备用策略也失败: {fallback_error}")
            raise error

    def _handle_cached(self, operation: str) -> Any:
        """返回缓存数据"""
        if self.config.cache_key and self.config.cache_key in self._cache:
            logger.info(f"[{operation}] 使用缓存数据")
            return self._cache[self.config.cache_key]
        logger.warning(f"[{operation}] 缓存未命中，返回默认值")
        return self.config.default_value

    def set_cache(self, key: str, value: Any):
        """设置缓存"""
        self._cache[key] = value

    def clear_cache(self):
        """清空缓存"""
        self._cache.clear()


# 预定义的降级处理器实例
_default_handler = DegradationHandler()


def get_degradation_handler() -> DegradationHandler:
    """获取默认降级处理器"""
    return _default_handler


def reset_degradation_handler():
    """重置降级处理器（用于测试）"""
    global _default_handler
    _default_handler = DegradationHandler()


# 便捷函数
def degrade_skip(operation: str, error: Exception, default: Any = None) -> Any:
    """跳过降级：返回默认值"""
    handler = DegradationHandler(DegradationConfig(
        mode=DegradationMode.SKIP,
        default_value=default,
    ))
    return handler.handle(error, operation, OperationType.UNKNOWN)


def degrade_error(operation: str, error: Exception) -> Any:
    """错误降级：抛出结构化错误"""
    handler = DegradationHandler(DegradationConfig(
        mode=DegradationMode.ERROR,
    ))
    return handler.handle(error, operation, OperationType.UNKNOWN)


def degrade_fallback(operation: str, error: Exception, fallback: Callable) -> Any:
    """备用降级：执行备用策略"""
    handler = DegradationHandler(DegradationConfig(
        mode=DegradationMode.FALLBACK,
        fallback_func=fallback,
    ))
    return handler.handle(error, operation, OperationType.UNKNOWN)
