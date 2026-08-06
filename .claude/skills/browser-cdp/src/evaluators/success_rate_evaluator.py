"""
抓取成功率评估器

评估指标：
- 页面访问成功率
- 数据提取准确率
- 字段完整率
- 综合抓取得分
"""

import logging
from typing import Any, Dict, List, Optional

from .base_evaluator import BaseEvaluator, MetricResult

logger = logging.getLogger(__name__)


class SuccessRateEvaluator(BaseEvaluator):
    """抓取成功率评估器"""

    def __init__(self):
        super().__init__(name="抓取成功率", weight=0.30)

    def evaluate(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        评估抓取成功率

        context 参数:
            - total_attempts: 总尝试次数
            - successful_accesses: 成功访问次数
            - total_data_items: 总数据条目数
            - correct_extractions: 正确提取数
            - expected_fields: 预期字段数
            - extracted_fields: 已提取字段数
        """
        total_attempts = context.get("total_attempts", 0)
        successful_accesses = context.get("successful_accesses", 0)
        total_data_items = context.get("total_data_items", 0)
        correct_extractions = context.get("correct_extractions", 0)
        expected_fields = context.get("expected_fields", 0)
        extracted_fields = context.get("extracted_fields", 0)

        # 计算各项指标
        page_access_rate = self._safe_divide(
            successful_accesses, total_attempts, 0.0
        ) * 100

        data_extraction_accuracy = self._safe_divide(
            correct_extractions, total_data_items, 0.0
        ) * 100

        field_completeness = self._safe_divide(
            extracted_fields, expected_fields, 0.0
        ) * 100

        # 添加指标
        self.add_metric(MetricResult(
            name="页面访问成功率",
            value=page_access_rate,
            unit="%",
            target=90.0,
            weight=0.4,
            details={"successful": successful_accesses, "total": total_attempts}
        ))

        self.add_metric(MetricResult(
            name="数据提取准确率",
            value=data_extraction_accuracy,
            unit="%",
            target=85.0,
            weight=0.4,
            details={"correct": correct_extractions, "total": total_data_items}
        ))

        self.add_metric(MetricResult(
            name="字段完整率",
            value=field_completeness,
            unit="%",
            target=80.0,
            weight=0.2,
            details={"extracted": extracted_fields, "expected": expected_fields}
        ))

        # 计算综合得分
        comprehensive_score = (
            page_access_rate * 0.4 +
            data_extraction_accuracy * 0.4 +
            field_completeness * 0.2
        )

        self.add_metric(MetricResult(
            name="综合抓取得分",
            value=comprehensive_score,
            unit="分",
            target=85.0,
            weight=1.0,
            details={
                "page_access_rate": round(page_access_rate, 2),
                "extraction_accuracy": round(data_extraction_accuracy, 2),
                "field_completeness": round(field_completeness, 2),
            }
        ))

        # 添加观察记录
        if page_access_rate < 90:
            self.add_observation(f"页面访问成功率较低 ({page_access_rate:.1f}%)，可能存在网络或反爬问题")
        if data_extraction_accuracy < 85:
            self.add_observation(f"数据提取准确率较低 ({data_extraction_accuracy:.1f}%)，需优化选择器")
        if field_completeness < 80:
            self.add_observation(f"字段完整率较低 ({field_completeness:.1f}%)，部分字段提取失败")

        return self.get_result().to_dict()

    @staticmethod
    def _safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
        """安全除法，避免除零错误"""
        if denominator == 0:
            return default
        return numerator / denominator

    def add_observation(self, observation: str):
        """添加观察记录"""
        super().add_observation(observation)
