"""
稳定性评估器

评估指标：
- 重复执行一致性
- 异常恢复率
- 内存稳定性
- 连接稳定性
"""

import logging
from typing import Any, Dict

from .base_evaluator import BaseEvaluator, MetricResult

logger = logging.getLogger(__name__)


class StabilityEvaluator(BaseEvaluator):
    """稳定性评估器"""

    def __init__(self):
        super().__init__(name="稳定性", weight=0.10)

    def evaluate(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        评估稳定性

        context 参数:
            - total_runs: 总执行次数
            - consistent_runs: 结果一致次数
            - total_errors: 总异常次数
            - recovered_errors: 成功恢复次数
            - memory_growth_mb_per_hour: 内存增长速率（MB/h）
            - total_connection_time: 总连接时间（秒）
            - disconnected_time: 断开连接时间（秒）
        """
        total_runs = context.get("total_runs", 0)
        consistent_runs = context.get("consistent_runs", 0)
        total_errors = context.get("total_errors", 0)
        recovered_errors = context.get("recovered_errors", 0)
        memory_growth = context.get("memory_growth_mb_per_hour", 0.0)
        total_connection_time = context.get("total_connection_time", 0.0)
        disconnected_time = context.get("disconnected_time", 0.0)

        # 计算各项指标
        consistency_rate = self._safe_divide(
            consistent_runs, total_runs, 0.0
        ) * 100

        error_recovery_rate = self._safe_divide(
            recovered_errors, total_errors, 0.0
        ) * 100 if total_errors > 0 else 100.0

        memory_stability = max(0.0, 100.0 - memory_growth * 5)  # 每MB/h扣5分
        connection_stability = self._safe_divide(
            total_connection_time - disconnected_time,
            total_connection_time,
            0.0
        ) * 100 if total_connection_time > 0 else 100.0

        # 添加指标
        self.add_metric(MetricResult(
            name="重复执行一致性",
            value=consistency_rate,
            unit="%",
            target=90.0,
            weight=0.30,
            details={"consistent": consistent_runs, "total": total_runs}
        ))

        self.add_metric(MetricResult(
            name="异常恢复率",
            value=error_recovery_rate,
            unit="%",
            target=80.0,
            weight=0.25,
            details={"recovered": recovered_errors, "total": total_errors}
        ))

        self.add_metric(MetricResult(
            name="内存稳定性",
            value=memory_stability,
            unit="分",
            target=90.0,
            weight=0.25,
            details={"growth_mb_per_hour": memory_growth}
        ))

        self.add_metric(MetricResult(
            name="连接稳定性",
            value=connection_stability,
            unit="%",
            target=95.0,
            weight=0.20,
            details={
                "connected_time": total_connection_time - disconnected_time,
                "total_time": total_connection_time
            }
        ))

        # 计算综合得分
        comprehensive_score = (
            consistency_rate * 0.30 +
            error_recovery_rate * 0.25 +
            memory_stability * 0.25 +
            connection_stability * 0.20
        )

        self.add_metric(MetricResult(
            name="综合稳定性得分",
            value=comprehensive_score,
            unit="分",
            target=85.0,
            weight=1.0,
            details={
                "consistency": round(consistency_rate, 2),
                "recovery": round(error_recovery_rate, 2),
                "memory": round(memory_stability, 2),
                "connection": round(connection_stability, 2),
            }
        ))

        # 添加观察记录
        if consistency_rate < 90:
            self.add_observation(f"重复执行一致性较低 ({consistency_rate:.1f}%)，存在不稳定因素")
        if error_recovery_rate < 80:
            self.add_observation(f"异常恢复率较低 ({error_recovery_rate:.1f}%)，需增强容错机制")
        if memory_stability < 90:
            self.add_observation(f"内存稳定性较差 (增长 {memory_growth:.1f} MB/h)，可能存在内存泄漏")
        if connection_stability < 95:
            self.add_observation(f"连接稳定性较低 ({connection_stability:.1f}%)，需优化 CDP 连接管理")

        return self.get_result().to_dict()

    @staticmethod
    def _safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
        """安全除法，避免除零错误"""
        if denominator == 0:
            return default
        return numerator / denominator
