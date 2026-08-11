# -*- coding: utf-8 -*-
"""
自适应超时模块

基于历史响应时间动态调整超时配置，支持：
- 滑动窗口统计
- 百分位数计算
- 自动超时调整
- 异常值过滤
"""

import time
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ResponseSample:
    """响应样本"""
    timestamp: float
    duration: float
    success: bool
    operation: str


class AdaptiveTimeout:
    """
    自适应超时管理器
    
    根据历史响应时间动态调整超时配置，避免固定超时导致的：
    1. 超时过短：正常请求被误判为超时
    2. 超时过长：无效等待浪费时间
    """
    
    # 默认配置
    DEFAULT_WINDOW_SIZE = 50  # 滑动窗口大小
    DEFAULT_PERCENTILE = 95  # 使用 95 分位数
    DEFAULT_MIN_TIMEOUT = 1.0  # 最小超时（秒）
    DEFAULT_MAX_TIMEOUT = 120.0  # 最大超时（秒）
    DEFAULT_ADJUSTMENT_FACTOR = 1.2  # 调整因子（百分位数 * 1.2 = 超时）
    
    def __init__(
        self,
        window_size: int = DEFAULT_WINDOW_SIZE,
        percentile: int = DEFAULT_PERCENTILE,
        min_timeout: float = DEFAULT_MIN_TIMEOUT,
        max_timeout: float = DEFAULT_MAX_TIMEOUT,
        adjustment_factor: float = DEFAULT_ADJUSTMENT_FACTOR,
    ):
        self.window_size = window_size
        self.percentile = percentile
        self.min_timeout = min_timeout
        self.max_timeout = max_timeout
        self.adjustment_factor = adjustment_factor
        
        # 存储历史响应时间（按操作类型分组）
        self._history: Dict[str, deque] = {}
        
        # 当前计算的超时配置
        self._timeouts: Dict[str, float] = {}
        
        # 统计信息
        self._stats: Dict[str, Dict[str, float]] = {}
    
    def record_response(
        self,
        operation: str,
        duration: float,
        success: bool = True,
    ):
        """
        记录一次响应
        
        Args:
            operation: 操作类型
            duration: 响应时间（秒）
            success: 是否成功
        """
        if operation not in self._history:
            self._history[operation] = deque(maxlen=self.window_size)
        
        sample = ResponseSample(
            timestamp=time.time(),
            duration=duration,
            success=success,
            operation=operation,
        )
        self._history[operation].append(sample)
        
        # 重新计算超时
        self._update_timeout(operation)
        
        logger.debug(
            f"[AdaptiveTimeout] Recorded {operation}: {duration:.2f}s, "
            f"window_size={len(self._history[operation])}, "
            f"timeout={self._timeouts.get(operation, 'N/A')}"
        )
    
    def _update_timeout(self, operation: str):
        """根据历史数据更新超时配置"""
        if operation not in self._history or len(self._history[operation]) < 3:
            # 样本不足，使用默认值
            return
        
        samples = [s.duration for s in self._history[operation] if s.success]
        
        if not samples:
            # 没有成功样本
            return
        
        # 计算百分位数
        p95 = self._percentile(samples, self.percentile)
        
        # 应用调整因子
        timeout = p95 * self.adjustment_factor
        
        # 限制在合理范围内
        timeout = max(self.min_timeout, min(timeout, self.max_timeout))
        
        self._timeouts[operation] = timeout
        
        # 更新统计
        self._stats[operation] = {
            "mean": sum(samples) / len(samples),
            "p50": self._percentile(samples, 50),
            "p95": p95,
            "p99": self._percentile(samples, 99),
            "max": max(samples),
            "count": len(samples),
            "timeout": timeout,
        }
        
        logger.info(
            f"[AdaptiveTimeout] Updated timeout for {operation}: "
            f"p95={p95:.2f}s, timeout={timeout:.2f}s, samples={len(samples)}"
        )
    
    def _percentile(self, data: List[float], percentile: int) -> float:
        """计算百分位数"""
        if not data:
            return 0.0
        
        sorted_data = sorted(data)
        k = (len(sorted_data) - 1) * (percentile / 100.0)
        f = int(k)
        c = f + 1
        
        if c >= len(sorted_data):
            return sorted_data[f]
        
        return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])
    
    def get_timeout(self, operation: str, default: float = 30.0) -> float:
        """
        获取操作的最优超时时间
        
        Args:
            operation: 操作类型
            default: 默认超时（当没有历史数据时）
        
        Returns:
            建议的超时时间（秒）
        """
        if operation in self._timeouts:
            return self._timeouts[operation]
        return default
    
    def get_timeout_config(self, operation: str) -> Dict[str, float]:
        """
        获取操作的完整超时配置
        
        Returns:
            包含 connect_timeout, read_timeout, total_timeout 的字典
        """
        total = self.get_timeout(operation, 30.0)
        
        return {
            "connect_timeout": total * 0.3,
            "read_timeout": total * 0.5,
            "total_timeout": total,
        }
    
    def get_stats(self, operation: Optional[str] = None) -> Dict[str, Any]:
        """
        获取统计信息
        
        Args:
            operation: 指定操作类型（可选）
        
        Returns:
            统计信息字典
        """
        if operation:
            return {
                operation: self._stats.get(operation, {}),
                "current_timeout": self._timeouts.get(operation),
            }
        
        return {
            "operations": {
                op: {
                    **self._stats.get(op, {}),
                    "current_timeout": self._timeouts.get(op),
                }
                for op in self._history.keys()
            },
            "total_operations": len(self._history),
        }
    
    def reset(self, operation: Optional[str] = None):
        """
        重置统计信息
        
        Args:
            operation: 指定操作类型（可选，不传则重置所有）
        """
        if operation:
            self._history.pop(operation, None)
            self._timeouts.pop(operation, None)
            self._stats.pop(operation, None)
        else:
            self._history.clear()
            self._timeouts.clear()
            self._stats.clear()
    
    def is_adaptive_ready(self, operation: str) -> bool:
        """检查操作是否已有足够数据支持自适应"""
        if operation not in self._history:
            return False
        return len(self._history[operation]) >= 10


class TimeoutPredictor:
    """
    超时预测器
    
    基于历史数据预测下次请求的预期超时时间
    """
    
    def __init__(self, adaptive_timeout: AdaptiveTimeout):
        self.adaptive_timeout = adaptive_timeout
    
    def predict_timeout(
        self,
        operation: str,
        confidence: float = 0.8,
    ) -> Tuple[float, str]:
        """
        预测超时时间
        
        Args:
            operation: 操作类型
            confidence: 置信度（0-1）
        
        Returns:
            (预测超时时间, 预测状态)
        """
        if not self.adaptive_timeout.is_adaptive_ready(operation):
            return 30.0, "insufficient_data"
        
        stats = self.adaptive_timeout.get_stats(operation)
        if operation not in stats or not stats[operation]:
            return 30.0, "no_data"
        
        p95 = stats[operation].get("p95", 10.0)
        mean = stats[operation].get("mean", 5.0)
        count = stats[operation].get("count", 0)
        
        # 根据样本数量调整置信度
        adjusted_confidence = min(1.0, count / 50.0)
        
        if adjusted_confidence < confidence:
            return p95 * 1.5, "low_confidence"
        
        # 检测趋势（最近 10 次 vs 之前 10 次）
        trend = self._detect_trend(operation)
        
        if trend == "increasing":
            return p95 * 1.3, "increasing_trend"
        elif trend == "decreasing":
            return p95 * 0.9, "decreasing_trend"
        else:
            return p95, "stable"
    
    def _detect_trend(self, operation: str) -> str:
        """检测响应时间趋势"""
        if operation not in self.adaptive_timeout._history:
            return "stable"
        
        samples = list(self.adaptive_timeout._history[operation])
        if len(samples) < 20:
            return "stable"
        
        # 最近 10 次
        recent = [s.duration for s in samples[-10:] if s.success]
        # 之前 10 次
        previous = [s.duration for s in samples[-20:-10] if s.success]
        
        if not recent or not previous:
            return "stable"
        
        recent_mean = sum(recent) / len(recent)
        previous_mean = sum(previous) / len(previous)
        
        if recent_mean > previous_mean * 1.2:
            return "increasing"
        elif recent_mean < previous_mean * 0.8:
            return "decreasing"
        else:
            return "stable"


# 全局实例
_global_adaptive_timeout: Optional[AdaptiveTimeout] = None
_global_predictor: Optional[TimeoutPredictor] = None


def get_adaptive_timeout() -> AdaptiveTimeout:
    """获取全局自适应超时管理器实例"""
    global _global_adaptive_timeout
    if _global_adaptive_timeout is None:
        _global_adaptive_timeout = AdaptiveTimeout()
    return _global_adaptive_timeout


def get_timeout_predictor() -> TimeoutPredictor:
    """获取全局超时预测器实例"""
    global _global_predictor
    if _global_predictor is None:
        _global_predictor = TimeoutPredictor(get_adaptive_timeout())
    return _global_predictor


def record_response(operation: str, duration: float, success: bool = True):
    """便捷函数：记录响应时间"""
    get_adaptive_timeout().record_response(operation, duration, success)


def get_optimal_timeout(operation: str, default: float = 30.0) -> float:
    """便捷函数：获取最优超时时间"""
    return get_adaptive_timeout().get_timeout(operation, default)
