"""
错误恢复能力评估器

评估指标：
- 错误分类准确率
- 重试成功率
- 降级策略有效性
"""

import logging
from typing import Any, Dict

from .base_evaluator import BaseEvaluator, MetricResult

logger = logging.getLogger(__name__)


class ErrorRecoveryEvaluator(BaseEvaluator):
    """错误恢复能力评估器"""

    def __init__(self):
        super().__init__(name="错误恢复能力", weight=0.05)

    def evaluate(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        评估错误恢复能力

        context 参数:
            - total_errors: 总错误数
            - correctly_classified: 正确分类的错误数
            - total_retries: 总重试次数
            - successful_retries: 重试成功次数
            - total_fallbacks: 总降级尝试次数
            - successful_fallbacks: 降级成功次数
        """
        total_errors = context.get("total_errors", 0)
        correctly_classified = context.get("correctly_classified", 0)
        total_retries = context.get("total_retries", 0)
        successful_retries = context.get("successful_retries", 0)
        total_fallbacks = context.get("total_fallbacks", 0)
        successful_fallbacks = context.get("successful_fallbacks", 0)

        # 计算各项指标
        error_classification_accuracy = self._safe_divide(
            correctly_classified, total_errors, 0.0
        ) * 100 if total_errors > 0 else 100.0

        retry_success_rate = self._safe_divide(
            successful_retries, total_retries, 0.0
        ) * 100 if total_retries > 0 else 100.0

        fallback_effectiveness = self._safe_divide(
            successful_fallbacks, total_fallbacks, 0.0
        ) * 100 if total_fallbacks > 0 else 100.0

        # 添加指标
        self.add_metric(MetricResult(
            name="错误分类准确率",
            value=error_classification_accuracy,
            unit="%",
            target=85.0,
            weight=0.40,
            details={"classified": correctly_classified, "total": total_errors}
        ))

        self.add_metric(MetricResult(
            name="重试成功率",
            value=retry_success_rate,
            unit="%",
            target=70.0,
            weight=0.35,
            details={"successful": successful_retries, "total": total_retries}
        ))

        self.add_metric(MetricResult(
            name="降级策略有效性",
            value=fallback_effectiveness,
            unit="%",
            target=60.0,
            weight=0.25,
            details={"successful": successful_fallbacks, "total": total_fallbacks}
        ))

        # 计算综合得分
        comprehensive_score = (
            error_classification_accuracy * 0.40 +
            retry_success_rate * 0.35 +
            fallback_effectiveness * 0.25
        )

        self.add_metric(MetricResult(
            name="综合恢复得分",
            value=comprehensive_score,
            unit="分",
            target=75.0,
            weight=1.0,
            details={
                "classification_accuracy": round(error_classification_accuracy, 2),
                "retry_rate": round(retry_success_rate, 2),
                "fallback_rate": round(fallback_effectiveness, 2),
            }
        ))

        # 添加观察记录
        if error_classification_accuracy < 85:
            self.add_observation(f"错误分类准确率较低 ({error_classification_accuracy:.1f}%)，需优化错误识别逻辑")
        if retry_success_rate < 70:
            self.add_observation(f"重试成功率较低 ({retry_success_rate:.1f}%)，需调整重试策略")
        if fallback_effectiveness < 60:
            self.add_observation(f"降级策略有效性较低 ({fallback_effectiveness:.1f}%)，需增强降级方案")

        return self.get_result().to_dict()

    @staticmethod
    def _safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
        """安全除法，避免除零错误"""
        if denominator == 0:
            return default
        return numerator / denominator
