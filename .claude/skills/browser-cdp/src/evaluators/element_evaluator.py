"""
元素定位准确率评估器

评估指标：
- 元素定位成功率
- 交互成功率
- 动态元素识别率
- 定位策略覆盖率
"""

import logging
from typing import Any, Dict

from .base_evaluator import BaseEvaluator, MetricResult

logger = logging.getLogger(__name__)


class ElementEvaluator(BaseEvaluator):
    """元素定位准确率评估器"""

    def __init__(self):
        super().__init__(name="元素定位准确率", weight=0.20)

    def evaluate(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        评估元素定位准确率

        context 参数:
            - total_location_attempts: 总定位尝试次数
            - successful_locations: 成功定位次数
            - total_interactions: 总交互尝试次数
            - successful_interactions: 成功交互次数
            - total_dynamic_elements: 总动态元素数
            - identified_dynamic_elements: 已识别动态元素数
            - strategies_used: 使用的定位策略列表
            - available_strategies: 可用定位策略列表
        """
        total_location_attempts = context.get("total_location_attempts", 0)
        successful_locations = context.get("successful_locations", 0)
        total_interactions = context.get("total_interactions", 0)
        successful_interactions = context.get("successful_interactions", 0)
        total_dynamic_elements = context.get("total_dynamic_elements", 0)
        identified_dynamic_elements = context.get("identified_dynamic_elements", 0)
        strategies_used = context.get("strategies_used", [])
        available_strategies = context.get("available_strategies", [])

        # 计算各项指标
        location_success_rate = self._safe_divide(
            successful_locations, total_location_attempts, 0.0
        ) * 100

        interaction_success_rate = self._safe_divide(
            successful_interactions, total_interactions, 0.0
        ) * 100

        dynamic_element_recognition = self._safe_divide(
            identified_dynamic_elements, total_dynamic_elements, 0.0
        ) * 100

        strategy_coverage = self._safe_divide(
            len(strategies_used), len(available_strategies), 0.0
        ) * 100 if available_strategies else 0.0

        # 添加指标
        self.add_metric(MetricResult(
            name="元素定位成功率",
            value=location_success_rate,
            unit="%",
            target=90.0,
            weight=0.35,
            details={"successful": successful_locations, "total": total_location_attempts}
        ))

        self.add_metric(MetricResult(
            name="交互成功率",
            value=interaction_success_rate,
            unit="%",
            target=85.0,
            weight=0.35,
            details={"successful": successful_interactions, "total": total_interactions}
        ))

        self.add_metric(MetricResult(
            name="动态元素识别率",
            value=dynamic_element_recognition,
            unit="%",
            target=80.0,
            weight=0.15,
            details={"identified": identified_dynamic_elements, "total": total_dynamic_elements}
        ))

        self.add_metric(MetricResult(
            name="定位策略覆盖率",
            value=strategy_coverage,
            unit="%",
            target=70.0,
            weight=0.15,
            details={"used": strategies_used, "available": available_strategies}
        ))

        # 计算综合得分
        comprehensive_score = (
            location_success_rate * 0.35 +
            interaction_success_rate * 0.35 +
            dynamic_element_recognition * 0.15 +
            strategy_coverage * 0.15
        )

        self.add_metric(MetricResult(
            name="综合定位得分",
            value=comprehensive_score,
            unit="分",
            target=85.0,
            weight=1.0,
            details={
                "location_rate": round(location_success_rate, 2),
                "interaction_rate": round(interaction_success_rate, 2),
                "dynamic_recognition": round(dynamic_element_recognition, 2),
                "strategy_coverage": round(strategy_coverage, 2),
            }
        ))

        # 添加观察记录
        if location_success_rate < 90:
            self.add_observation(f"元素定位成功率较低 ({location_success_rate:.1f}%)，需优化选择器策略")
        if interaction_success_rate < 85:
            self.add_observation(f"交互成功率较低 ({interaction_success_rate:.1f}%)，可能存在时序问题")
        if dynamic_element_recognition < 80:
            self.add_observation(f"动态元素识别率较低 ({dynamic_element_recognition:.1f}%)，需增强等待策略")
        if strategy_coverage < 70:
            self.add_observation(f"定位策略覆盖率较低 ({strategy_coverage:.1f}%)，建议增加策略类型")

        return self.get_result().to_dict()

    @staticmethod
    def _safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
        """安全除法，避免除零错误"""
        if denominator == 0:
            return default
        return numerator / denominator
