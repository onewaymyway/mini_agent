"""
评估指标计算器

负责计算各项评估指标，生成综合评分。
"""

import logging
from typing import Dict, List, Optional

from .models import TestResult, TestRun

logger = logging.getLogger(__name__)


class EvaluationMetrics:
    """评估指标计算器"""

    # 指标权重
    WEIGHTS = {
        "page_access_success_rate": 0.20,
        "element_locate_accuracy": 0.20,
        "data_extraction_success_rate": 0.30,
        "stability": 0.20,
        "anti_detection_ability": 0.10,
    }

    # 评级标准
    GRADE_THRESHOLDS = {
        "A+": 0.95,
        "A": 0.90,
        "B": 0.80,
        "C": 0.70,
        "D": 0.0,
    }

    def __init__(self):
        self._metrics_history: List[Dict[str, float]] = []

    def calculate_metrics(self, result: TestResult) -> Dict[str, float]:
        """
        计算评估指标

        Args:
            result: 测试结果

        Returns:
            评估指标字典
        """
        metrics = {
            "page_access_success_rate": result.metrics.get(
                "page_access_success_rate", 1.0
            ),
            "element_locate_accuracy": result.metrics.get(
                "element_locate_accuracy", 1.0
            ),
            "data_extraction_success_rate": result.metrics.get(
                "data_extraction_success_rate", 1.0
            ),
            "stability": result.metrics.get("stability", 1.0),
            "anti_detection_ability": result.metrics.get(
                "anti_detection_ability", 1.0
            ),
        }

        self._metrics_history.append(metrics)
        return metrics

    def calculate_composite_score(self, metrics: Dict[str, float]) -> float:
        """
        计算综合评分

        Args:
            metrics: 评估指标字典

        Returns:
            综合评分 (0-1)
        """
        score = 0.0
        for metric_name, weight in self.WEIGHTS.items():
            value = metrics.get(metric_name, 0.0)
            score += value * weight

        return score

    def get_grade(self, score: float) -> str:
        """
        根据综合评分获取评级

        Args:
            score: 综合评分

        Returns:
            评级 (A+/A/B/C/D)
        """
        for grade, threshold in self.GRADE_THRESHOLDS.items():
            if score >= threshold:
                return grade
        return "D"

    def evaluate_run(self, run: TestRun) -> Dict[str, any]:
        """
        评估整个测试执行

        Args:
            run: 测试执行记录

        Returns:
            评估结果
        """
        if not run.results:
            return {
                "composite_score": 0.0,
                "grade": "D",
                "metrics": {},
                "summary": "无测试结果",
            }

        # 计算平均指标
        avg_metrics = {
            "page_access_success_rate": 0.0,
            "element_locate_accuracy": 0.0,
            "data_extraction_success_rate": 0.0,
            "stability": 0.0,
            "anti_detection_ability": 0.0,
        }

        for result in run.results:
            metrics = result.metrics
            for key in avg_metrics:
                avg_metrics[key] += metrics.get(key, 0.0)

        # 计算平均值
        total = len(run.results)
        for key in avg_metrics:
            avg_metrics[key] /= total

        # 计算综合评分
        composite_score = self.calculate_composite_score(avg_metrics)
        grade = self.get_grade(composite_score)

        # 生成摘要
        summary = self._generate_summary(run, avg_metrics, composite_score, grade)

        return {
            "composite_score": composite_score,
            "grade": grade,
            "metrics": avg_metrics,
            "summary": summary,
        }

    def _generate_summary(
        self,
        run: TestRun,
        metrics: Dict[str, float],
        score: float,
        grade: str,
    ) -> str:
        """
        生成评估摘要

        Args:
            run: 测试执行记录
            metrics: 平均指标
            score: 综合评分
            grade: 评级

        Returns:
            摘要文本
        """
        lines = [
            f"网站: {run.website_name}",
            f"评级: {grade} ({score:.1%})",
            f"通过率: {run.success_rate:.1%} ({run.passed_cases}/{run.total_cases})",
            "",
            "评估指标:",
        ]

        for metric_name, value in metrics.items():
            display_name = self._get_metric_display_name(metric_name)
            lines.append(f"  - {display_name}: {value:.1%}")

        return "\n".join(lines)

    def _get_metric_display_name(self, metric_name: str) -> str:
        """
        获取指标显示名称

        Args:
            metric_name: 指标名称

        Returns:
            显示名称
        """
        names = {
            "page_access_success_rate": "页面访问成功率",
            "element_locate_accuracy": "元素定位准确率",
            "data_extraction_success_rate": "数据提取成功率",
            "stability": "稳定性",
            "anti_detection_ability": "反检测能力",
        }
        return names.get(metric_name, metric_name)

    def get_trend(self, metric_name: str, window: int = 5) -> List[float]:
        """
        获取指标趋势

        Args:
            metric_name: 指标名称
            window: 窗口大小

        Returns:
            趋势数据列表
        """
        if not self._metrics_history:
            return []

        recent = self._metrics_history[-window:]
        return [m.get(metric_name, 0.0) for m in recent]

    def compare_runs(
        self, run1: TestRun, run2: TestRun
    ) -> Dict[str, float]:
        """
        比较两次测试结果

        Args:
            run1: 第一次测试结果
            run2: 第二次测试结果

        Returns:
            差异字典
        """
        eval1 = self.evaluate_run(run1)
        eval2 = self.evaluate_run(run2)

        diff = {}
        for key in eval1["metrics"]:
            diff[key] = eval2["metrics"][key] - eval1["metrics"][key]

        diff["composite_score"] = eval2["composite_score"] - eval1["composite_score"]

        return diff
