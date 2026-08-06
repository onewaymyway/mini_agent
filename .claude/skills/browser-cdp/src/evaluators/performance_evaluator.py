"""
页面加载性能评估器

评估指标：
- 首屏加载时间
- 页面完全加载时间
- 元素等待时间
- 平均响应时间
"""

import logging
from typing import Any, Dict

from .base_evaluator import BaseEvaluator, MetricResult

logger = logging.getLogger(__name__)


class PerformanceEvaluator(BaseEvaluator):
    """页面加载性能评估器"""

    def __init__(self):
        super().__init__(name="页面加载性能", weight=0.20)

    def evaluate(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        评估页面加载性能

        context 参数:
            - first_paint_time: 首屏加载时间（秒）
            - full_load_time: 页面完全加载时间（秒）
            - element_wait_time: 元素等待时间（秒）
            - total_time: 总耗时（秒）
            - operation_count: 操作次数
        """
        first_paint_time = context.get("first_paint_time", 0.0)
        full_load_time = context.get("full_load_time", 0.0)
        element_wait_time = context.get("element_wait_time", 0.0)
        total_time = context.get("total_time", 0.0)
        operation_count = context.get("operation_count", 1)

        avg_response_time = total_time / operation_count if operation_count > 0 else 0.0

        # 计算各项得分（时间越短得分越高）
        first_paint_score = self._time_to_score(first_paint_time, target=5.0, max_penalty=10.0)
        full_load_score = self._time_to_score(full_load_time, target=15.0, max_penalty=10.0)
        element_wait_score = self._time_to_score(element_wait_time, target=3.0, max_penalty=10.0)
        avg_response_score = self._time_to_score(avg_response_time, target=10.0, max_penalty=10.0)

        # 添加指标
        self.add_metric(MetricResult(
            name="首屏加载时间",
            value=first_paint_time,
            unit="s",
            target=5.0,
            weight=0.3,
            details={"score": first_paint_score}
        ))

        self.add_metric(MetricResult(
            name="页面完全加载时间",
            value=full_load_time,
            unit="s",
            target=15.0,
            weight=0.3,
            details={"score": full_load_score}
        ))

        self.add_metric(MetricResult(
            name="元素等待时间",
            value=element_wait_time,
            unit="s",
            target=3.0,
            weight=0.2,
            details={"score": element_wait_score}
        ))

        self.add_metric(MetricResult(
            name="平均响应时间",
            value=avg_response_time,
            unit="s",
            target=10.0,
            weight=0.2,
            details={"score": avg_response_score, "operations": operation_count}
        ))

        # 计算综合得分
        comprehensive_score = (
            first_paint_score * 0.3 +
            full_load_score * 0.3 +
            element_wait_score * 0.2 +
            avg_response_score * 0.2
        )

        self.add_metric(MetricResult(
            name="综合性能得分",
            value=comprehensive_score,
            unit="分",
            target=80.0,
            weight=1.0,
            details={
                "first_paint_score": round(first_paint_score, 2),
                "full_load_score": round(full_load_score, 2),
                "element_wait_score": round(element_wait_score, 2),
                "avg_response_score": round(avg_response_score, 2),
            }
        ))

        # 添加观察记录
        if first_paint_time > 5.0:
            self.add_observation(f"首屏加载时间较长 ({first_paint_time:.2f}s)，可能影响用户体验")
        if full_load_time > 15.0:
            self.add_observation(f"页面完全加载时间过长 ({full_load_time:.2f}s)，需优化资源加载")
        if element_wait_time > 3.0:
            self.add_observation(f"元素等待时间较长 ({element_wait_time:.2f}s)，动态内容加载较慢")

        return self.get_result().to_dict()

    @staticmethod
    def _time_to_score(time_seconds: float, target: float, max_penalty: float = 10.0) -> float:
        """
        将时间转换为得分（时间越短得分越高）

        公式: score = max(0, 100 - (time / target) * max_penalty)
        """
        if time_seconds <= 0:
            return 100.0
        penalty = (time_seconds / target) * max_penalty
        return max(0.0, 100.0 - penalty)
