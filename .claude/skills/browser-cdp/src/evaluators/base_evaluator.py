"""
评估器基类

定义评估器的通用接口和工具方法。
"""

import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MetricResult:
    """单个指标的计算结果"""
    name: str
    value: float
    unit: str = ""
    target: Optional[float] = None
    weight: float = 1.0
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def weighted_score(self) -> float:
        """计算加权得分"""
        return self.value * self.weight

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = {
            "name": self.name,
            "value": round(self.value, 2),
            "unit": self.unit,
            "weight": self.weight,
            "weighted_score": round(self.weighted_score, 2),
        }
        if self.target is not None:
            result["target"] = self.target
        if self.details:
            result["details"] = self.details
        return result


@dataclass
class DimensionResult:
    """维度评估结果"""
    name: str
    score: float
    weight: float
    metrics: List[MetricResult] = field(default_factory=list)
    observations: List[str] = field(default_factory=list)

    @property
    def weighted_score(self) -> float:
        """计算加权得分"""
        return self.score * self.weight

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = {
            "name": self.name,
            "score": round(self.score, 2),
            "weight": self.weight,
            "weighted_score": round(self.weighted_score, 2),
            "metrics": [m.to_dict() for m in self.metrics],
        }
        if self.observations:
            result["observations"] = self.observations
        return result


class BaseEvaluator(ABC):
    """评估器基类"""

    def __init__(self, name: str, weight: float):
        self.name = name
        self.weight = weight
        self._results: List[MetricResult] = []
        self._observations: List[str] = []

    @abstractmethod
    def evaluate(self, context: Dict[str, Any]) -> DimensionResult:
        """执行评估，返回维度结果"""
        pass

    def add_metric(self, metric: MetricResult):
        """添加指标结果"""
        self._results.append(metric)
        logger.debug(f"添加指标: {metric.name} = {metric.value}{metric.unit}")

    def add_observation(self, observation: str):
        """添加观察记录"""
        self._observations.append(observation)
        logger.info(f"观察记录: {observation}")

    def calculate_score(self, metrics: List[MetricResult]) -> float:
        """计算维度得分（加权平均）"""
        if not metrics:
            return 0.0
        total_weight = sum(m.weight for m in metrics)
        if total_weight == 0:
            return 0.0
        weighted_sum = sum(m.value * m.weight for m in metrics)
        return weighted_sum / total_weight

    def get_result(self) -> DimensionResult:
        """获取评估结果"""
        score = self.calculate_score(self._results)
        return DimensionResult(
            name=self.name,
            score=score,
            weight=self.weight,
            metrics=self._results,
            observations=self._observations,
        )

    def reset(self):
        """重置评估器状态"""
        self._results = []
        self._observations = []


class Timer:
    """计时工具"""

    def __init__(self, name: str = ""):
        self.name = name
        self._start_time = 0.0
        self._end_time = 0.0
        self._elapsed = 0.0

    def __enter__(self):
        self._start_time = time.time()
        return self

    def __exit__(self, *args):
        self._end_time = time.time()
        self._elapsed = self._end_time - self._start_time

    @property
    def elapsed(self) -> float:
        return self._elapsed

    def start(self):
        self._start_time = time.time()
        return self

    def stop(self):
        self._end_time = time.time()
        self._elapsed = self._end_time - self._start_time
        return self

    def to_metric(self, name: str, unit: str = "s", target: Optional[float] = None) -> MetricResult:
        """转换为 MetricResult"""
        return MetricResult(
            name=name,
            value=self._elapsed,
            unit=unit,
            target=target,
            weight=1.0,
        )
