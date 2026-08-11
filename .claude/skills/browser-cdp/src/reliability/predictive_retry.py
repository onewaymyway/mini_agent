# -*- coding: utf-8 -*-
"""
预测性重试模块

基于历史成功率预测最佳重试次数，支持：
- 成功率预测
- 最优重试次数计算
- 成本效益分析
- 动态策略调整
"""

import time
import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RetryPrediction:
    """重试预测结果"""
    optimal_retries: int
    expected_success_rate: float
    expected_cost: float
    confidence: float
    recommendation: str


class PredictiveRetry:
    """
    预测性重试管理器
    
    基于历史数据预测最优重试策略：
    1. 分析历史成功率
    2. 预测不同重试次数的成功率
    3. 计算成本效益
    4. 给出最优重试次数建议
    """
    
    # 默认配置
    DEFAULT_WINDOW_SIZE = 50
    DEFAULT_MIN_RETRIES = 1
    DEFAULT_MAX_RETRIES = 10
    DEFAULT_COST_PER_RETRY = 1.0  # 每次重试的成本单位
    
    def __init__(
        self,
        window_size: int = DEFAULT_WINDOW_SIZE,
        min_retries: int = DEFAULT_MIN_RETRIES,
        max_retries: int = DEFAULT_MAX_RETRIES,
        cost_per_retry: float = DEFAULT_COST_PER_RETRY,
    ):
        self.window_size = window_size
        self.min_retries = min_retries
        self.max_retries = max_retries
        self.cost_per_retry = cost_per_retry
        
        # 历史记录
        self._history: Dict[str, deque] = {}
        
        # 预测模型
        self._models: Dict[str, Dict[str, float]] = {}
    
    def record_attempt(
        self,
        operation: str,
        success: bool,
        retries_used: int = 0,
        duration: float = 0.0,
    ):
        """
        记录一次重试尝试
        
        Args:
            operation: 操作类型
            success: 是否成功
            retries_used: 使用的重试次数
            duration: 耗时（秒）
        """
        if operation not in self._history:
            self._history[operation] = deque(maxlen=self.window_size)
        
        self._history[operation].append({
            "timestamp": time.time(),
            "success": success,
            "retries_used": retries_used,
            "duration": duration,
        })
        
        # 更新预测模型
        self._update_model(operation)
        
        logger.debug(
            f"[PredictiveRetry] Recorded attempt for {operation}: "
            f"success={success}, retries={retries_used}, duration={duration:.2f}s"
        )
    
    def _update_model(self, operation: str):
        """更新预测模型"""
        if operation not in self._history or len(self._history[operation]) < 5:
            return
        
        history = list(self._history[operation])
        
        # 计算基础统计
        total = len(history)
        successes = sum(1 for h in history if h["success"])
        success_rate = successes / total if total > 0 else 0.0
        
        # 计算平均重试次数
        avg_retries = sum(h["retries_used"] for h in history) / total if total > 0 else 0
        
        # 计算平均耗时
        avg_duration = sum(h["duration"] for h in history) / total if total > 0 else 0
        
        # 计算最近成功率（滑动窗口）
        recent_size = min(10, len(history))
        recent_successes = sum(1 for h in history[-recent_size:] if h["success"])
        recent_success_rate = recent_successes / recent_size if recent_size > 0 else 0.0
        
        self._models[operation] = {
            "success_rate": success_rate,
            "recent_success_rate": recent_success_rate,
            "avg_retries": avg_retries,
            "avg_duration": avg_duration,
            "total_attempts": total,
        }
    
    def predict(
        self,
        operation: str,
        max_retries: Optional[int] = None,
    ) -> RetryPrediction:
        """
        预测最优重试策略
        
        Args:
            operation: 操作类型
            max_retries: 最大重试次数限制（可选）
        
        Returns:
            RetryPrediction: 预测结果
        """
        effective_max = max_retries or self.max_retries
        
        # 获取模型数据
        model = self._models.get(operation)
        if not model or model["total_attempts"] < 5:
            # 数据不足，使用默认值
            return RetryPrediction(
                optimal_retries=3,
                expected_success_rate=0.8,
                expected_cost=3.0,
                confidence=0.3,
                recommendation="insufficient_data",
            )
        
        base_success_rate = model["success_rate"]
        recent_rate = model["recent_success_rate"]
        
        # 预测不同重试次数的成功率
        predictions = []
        for retries in range(self.min_retries, effective_max + 1):
            # 简化模型：每次重试增加的成功率递减
            success_probability = self._predict_success_probability(
                base_success_rate, retries, model["avg_retries"]
            )
            cost = retries * self.cost_per_retry
            predictions.append((retries, success_probability, cost))
        
        # 找到最优重试次数（成功率/成本比最高）
        best_option = max(
            predictions,
            key=lambda x: x[1] / (1 + x[2] * 0.1),  # 成功率 / (1 + 成本*0.1)
        )
        
        optimal_retries = best_option[0]
        expected_success_rate = best_option[1]
        expected_cost = best_option[2]
        
        # 计算置信度
        confidence = min(1.0, model["total_attempts"] / self.window_size)
        
        # 生成建议
        recommendation = self._generate_recommendation(
            optimal_retries, expected_success_rate, confidence, model
        )
        
        return RetryPrediction(
            optimal_retries=optimal_retries,
            expected_success_rate=expected_success_rate,
            expected_cost=expected_cost,
            confidence=confidence,
            recommendation=recommendation,
        )
    
    def _predict_success_probability(
        self,
        base_rate: float,
        retries: int,
        avg_retries: float,
    ) -> float:
        """
        预测指定重试次数下的成功率
        
        使用简化模型：每次重试增加的成功率递减
        """
        # 基础成功率
        base = base_rate
        
        # 每次重试的增量成功率（递减）
        increment = (1.0 - base) / (avg_retries + 1)
        
        # 计算累计成功率
        cumulative = base
        for i in range(1, retries + 1):
            incremental = increment * (1 - i / (avg_retries * 2))  # 递减因子
            cumulative += incremental * (1 - cumulative)  # 基于当前成功率的增量
            cumulative = min(1.0, cumulative)
        
        return cumulative
    
    def _generate_recommendation(
        self,
        optimal_retries: int,
        success_rate: float,
        confidence: float,
        model: Dict[str, float],
    ) -> str:
        """生成重试建议"""
        if confidence < 0.3:
            return "insufficient_data"
        
        if success_rate < 0.5:
            return "low_success_rate_check_manual"
        
        if optimal_retries <= 2:
            return "minimal_retries_sufficient"
        elif optimal_retries <= 5:
            return "moderate_retries_recommended"
        else:
            return "aggressive_retries_needed_investigate"
    
    def get_optimal_retries(
        self,
        operation: str,
        max_retries: Optional[int] = None,
    ) -> int:
        """
        获取最优重试次数
        
        Args:
            operation: 操作类型
            max_retries: 最大重试次数限制（可选）
        
        Returns:
            最优重试次数
        """
        prediction = self.predict(operation, max_retries)
        return prediction.optimal_retries
    
    def get_stats(self, operation: Optional[str] = None) -> Dict[str, Any]:
        """获取统计信息"""
        if operation:
            model = self._models.get(operation, {})
            return {
                operation: {
                    **model,
                    "history_count": len(self._history.get(operation, [])),
                }
            }
        
        return {
            op: {
                **self._models.get(op, {}),
                "history_count": len(self._history.get(op, [])),
            }
            for op in self._history.keys()
        }
    
    def reset(self, operation: Optional[str] = None):
        """重置统计信息"""
        if operation:
            self._history.pop(operation, None)
            self._models.pop(operation, None)
        else:
            self._history.clear()
            self._models.clear()


# 全局实例
_global_predictive_retry: Optional[PredictiveRetry] = None


def get_predictive_retry() -> PredictiveRetry:
    """获取全局预测性重试管理器实例"""
    global _global_predictive_retry
    if _global_predictive_retry is None:
        _global_predictive_retry = PredictiveRetry()
    return _global_predictive_retry


def record_attempt(
    operation: str,
    success: bool,
    retries_used: int = 0,
    duration: float = 0.0,
):
    """便捷函数：记录重试尝试"""
    get_predictive_retry().record_attempt(operation, success, retries_used, duration)


def predict_optimal_retries(
    operation: str,
    max_retries: Optional[int] = None,
) -> int:
    """便捷函数：预测最优重试次数"""
    return get_predictive_retry().get_optimal_retries(operation, max_retries)


def get_retry_prediction(
    operation: str,
    max_retries: Optional[int] = None,
) -> RetryPrediction:
    """便捷函数：获取重试预测"""
    return get_predictive_retry().predict(operation, max_retries)
