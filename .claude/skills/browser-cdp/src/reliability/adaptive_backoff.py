# -*- coding: utf-8 -*-
"""
自适应退避算法模块

基于失败模式动态调整退避策略，支持：
- 失败模式识别
- 动态退避策略选择
- 退避参数自适应调整
- 失败模式追踪
"""

import random
import time
import logging
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class BackoffStrategy(Enum):
    """退避策略枚举"""
    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    EXPONENTIAL_JITTER = "exponential_jitter"
    WAIT = "wait"


class FailurePattern(Enum):
    """失败模式枚举"""
    CONSECUTIVE = "consecutive"      # 连续失败
    INTERMITTENT = "intermittent"    # 间歇性失败
    TIMEOUT = "timeout"              # 超时模式
    RATE_LIMIT = "rate_limit"        # 速率限制
    UNKNOWN = "unknown"


@dataclass
class BackoffConfig:
    """退避配置"""
    strategy: BackoffStrategy
    base_delay: float
    max_delay: float
    jitter_factor: float = 0.5


class AdaptiveBackoff:
    """
    自适应退避管理器
    
    根据失败模式动态调整退避策略，优化重试效果：
    1. 识别失败模式（连续/间歇/超时/速率限制）
    2. 动态选择最优退避策略
    3. 自适应调整退避参数
    4. 追踪策略效果
    """
    
    # 默认配置
    DEFAULT_WINDOW_SIZE = 20
    DEFAULT_CONSECUTIVE_THRESHOLD = 3
    DEFAULT_INTERMITTENT_THRESHOLD = 0.5
    
    # 失败模式到退避策略的映射
    PATTERN_STRATEGY_MAP = {
        FailurePattern.CONSECUTIVE: BackoffConfig(
            strategy=BackoffStrategy.EXPONENTIAL_JITTER,
            base_delay=1.0,
            max_delay=30.0,
        ),
        FailurePattern.INTERMITTENT: BackoffConfig(
            strategy=BackoffStrategy.EXPONENTIAL,
            base_delay=2.0,
            max_delay=60.0,
        ),
        FailurePattern.TIMEOUT: BackoffConfig(
            strategy=BackoffStrategy.LINEAR,
            base_delay=1.0,
            max_delay=20.0,
        ),
        FailurePattern.RATE_LIMIT: BackoffConfig(
            strategy=BackoffStrategy.WAIT,
            base_delay=5.0,
            max_delay=60.0,
        ),
        FailurePattern.UNKNOWN: BackoffConfig(
            strategy=BackoffStrategy.EXPONENTIAL_JITTER,
            base_delay=1.0,
            max_delay=30.0,
        ),
    }
    
    def __init__(
        self,
        window_size: int = DEFAULT_WINDOW_SIZE,
        consecutive_threshold: int = DEFAULT_CONSECUTIVE_THRESHOLD,
        intermittent_threshold: float = DEFAULT_INTERMITTENT_THRESHOLD,
    ):
        self.window_size = window_size
        self.consecutive_threshold = consecutive_threshold
        self.intermittent_threshold = intermittent_threshold
        
        # 失败历史记录
        self._failure_history: Dict[str, deque] = {}
        
        # 当前失败模式
        self._patterns: Dict[str, FailurePattern] = {}
        
        # 当前退避配置
        self._configs: Dict[str, BackoffConfig] = {}
        
        # 策略效果追踪
        self._strategy_stats: Dict[str, Dict[str, int]] = {}
    
    def record_failure(
        self,
        operation: str,
        error_type: str = "unknown",
        is_timeout: bool = False,
        is_rate_limit: bool = False,
    ):
        """
        记录一次失败
        
        Args:
            operation: 操作类型
            error_type: 错误类型
            is_timeout: 是否超时
            is_rate_limit: 是否速率限制
        """
        if operation not in self._failure_history:
            self._failure_history[operation] = deque(maxlen=self.window_size)
        
        self._failure_history[operation].append({
            "timestamp": time.time(),
            "error_type": error_type,
            "is_timeout": is_timeout,
            "is_rate_limit": is_rate_limit,
        })
        
        # 检测失败模式
        pattern = self._detect_pattern(operation)
        self._patterns[operation] = pattern
        
        # 更新退避配置
        self._update_config(operation, pattern)
        
        logger.info(
            f"[AdaptiveBackoff] Recorded failure for {operation}: "
            f"pattern={pattern.value}, error={error_type}"
        )
    
    def record_success(self, operation: str):
        """记录一次成功，清空失败历史"""
        if operation in self._failure_history:
            self._failure_history[operation].clear()
        self._patterns.pop(operation, None)
        logger.debug(f"[AdaptiveBackoff] Success recorded for {operation}, history cleared")
    
    def _detect_pattern(self, operation: str) -> FailurePattern:
        """检测失败模式"""
        if operation not in self._failure_history or len(self._failure_history[operation]) < 2:
            return FailurePattern.UNKNOWN
        
        history = list(self._failure_history[operation])
        
        # 检查是否速率限制
        if any(h.get("is_rate_limit") for h in history):
            return FailurePattern.RATE_LIMIT
        
        # 检查是否超时模式
        timeout_count = sum(1 for h in history if h.get("is_timeout"))
        if timeout_count / len(history) > 0.7:
            return FailurePattern.TIMEOUT
        
        # 检查连续失败
        consecutive_count = 0
        max_consecutive = 0
        for h in history:
            if True:  # 所有记录都是失败
                consecutive_count += 1
                max_consecutive = max(max_consecutive, consecutive_count)
            else:
                consecutive_count = 0
        
        if max_consecutive >= self.consecutive_threshold:
            return FailurePattern.CONSECUTIVE
        
        # 检查间歇性失败
        if len(history) >= 5:
            # 计算失败间隔
            timestamps = [h["timestamp"] for h in history]
            intervals = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
            
            if intervals:
                avg_interval = sum(intervals) / len(intervals)
                interval_variance = sum((i - avg_interval) ** 2 for i in intervals) / len(intervals)
                
                # 高方差表示间歇性失败
                if interval_variance > avg_interval ** 2 * 0.5:
                    return FailurePattern.INTERMITTENT
        
        return FailurePattern.UNKNOWN
    
    def _update_config(self, operation: str, pattern: FailurePattern):
        """根据失败模式更新退避配置"""
        config = self.PATTERN_STRATEGY_MAP.get(pattern, self.PATTERN_STRATEGY_MAP[FailurePattern.UNKNOWN])
        
        # 根据历史数据调整参数
        if operation in self._failure_history:
            history = list(self._failure_history[operation])
            
            # 如果是超时模式，增加基础延迟
            if pattern == FailurePattern.TIMEOUT and len(history) >= 3:
                config = BackoffConfig(
                    strategy=config.strategy,
                    base_delay=config.base_delay * 1.5,
                    max_delay=config.max_delay * 1.2,
                )
            
            # 如果是连续失败，增加最大延迟
            elif pattern == FailurePattern.CONSECUTIVE and len(history) >= 5:
                config = BackoffConfig(
                    strategy=config.strategy,
                    base_delay=config.base_delay,
                    max_delay=config.max_delay * 1.5,
                )
        
        self._configs[operation] = config
        logger.debug(f"[AdaptiveBackoff] Updated config for {operation}: {config.strategy.value}")
    
    def get_config(self, operation: str) -> BackoffConfig:
        """获取操作的最优退避配置"""
        if operation in self._configs:
            return self._configs[operation]
        
        # 如果没有配置，使用默认
        return self.PATTERN_STRATEGY_MAP[FailurePattern.UNKNOWN]
    
    def calculate_delay(
        self,
        operation: str,
        attempt: int,
    ) -> float:
        """
        计算退避延迟
        
        Args:
            operation: 操作类型
            attempt: 当前重试次数（从1开始）
        
        Returns:
            退避延迟（秒）
        """
        config = self.get_config(operation)
        
        if config.strategy == BackoffStrategy.FIXED:
            return min(config.base_delay, config.max_delay)
        elif config.strategy == BackoffStrategy.LINEAR:
            return min(config.base_delay * attempt, config.max_delay)
        elif config.strategy == BackoffStrategy.EXPONENTIAL:
            return min(config.base_delay ** attempt, config.max_delay)
        elif config.strategy == BackoffStrategy.EXPONENTIAL_JITTER:
            delay = min(config.base_delay ** attempt, config.max_delay)
            jitter = delay * config.jitter_factor * (2 * random.random() - 1)  # -50% ~ +50%
            return max(0, delay + jitter)
        elif config.strategy == BackoffStrategy.WAIT:
            return min(config.base_delay, config.max_delay)
        
        return config.base_delay
    
    def get_pattern(self, operation: str) -> FailurePattern:
        """获取操作的当前失败模式"""
        return self._patterns.get(operation, FailurePattern.UNKNOWN)
    
    def get_stats(self, operation: Optional[str] = None) -> Dict[str, Any]:
        """获取统计信息"""
        if operation:
            return {
                operation: {
                    "pattern": self._patterns.get(operation, FailurePattern.UNKNOWN).value,
                    "config": {
                        "strategy": self._configs.get(operation, BackoffConfig(
                            BackoffStrategy.EXPONENTIAL_JITTER, 1.0, 30.0
                        )).strategy.value,
                        "base_delay": self._configs.get(operation, BackoffConfig(
                            BackoffStrategy.EXPONENTIAL_JITTER, 1.0, 30.0
                        )).base_delay,
                        "max_delay": self._configs.get(operation, BackoffConfig(
                            BackoffStrategy.EXPONENTIAL_JITTER, 1.0, 30.0
                        )).max_delay,
                    },
                    "failure_count": len(self._failure_history.get(operation, [])),
                }
            }
        
        return {
            op: {
                "pattern": self._patterns.get(op, FailurePattern.UNKNOWN).value,
                "failure_count": len(self._failure_history.get(op, [])),
            }
            for op in self._failure_history.keys()
        }
    
    def reset(self, operation: Optional[str] = None):
        """重置统计信息"""
        if operation:
            self._failure_history.pop(operation, None)
            self._patterns.pop(operation, None)
            self._configs.pop(operation, None)
        else:
            self._failure_history.clear()
            self._patterns.clear()
            self._configs.clear()


# 全局实例
_global_adaptive_backoff: Optional[AdaptiveBackoff] = None


def get_adaptive_backoff() -> AdaptiveBackoff:
    """获取全局自适应退避管理器实例"""
    global _global_adaptive_backoff
    if _global_adaptive_backoff is None:
        _global_adaptive_backoff = AdaptiveBackoff()
    return _global_adaptive_backoff


def record_failure(
    operation: str,
    error_type: str = "unknown",
    is_timeout: bool = False,
    is_rate_limit: bool = False,
):
    """便捷函数：记录失败"""
    get_adaptive_backoff().record_failure(operation, error_type, is_timeout, is_rate_limit)


def record_success(operation: str):
    """便捷函数：记录成功"""
    get_adaptive_backoff().record_success(operation)


def get_backoff_delay(operation: str, attempt: int) -> float:
    """便捷函数：获取退避延迟"""
    return get_adaptive_backoff().calculate_delay(operation, attempt)


def get_failure_pattern(operation: str) -> FailurePattern:
    """便捷函数：获取失败模式"""
    return get_adaptive_backoff().get_pattern(operation)
