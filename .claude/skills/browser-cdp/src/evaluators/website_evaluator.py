#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
website_evaluator.py - 网站兼容性评估器

提供统一的网站兼容性评估能力，支持多维度评估和综合评分。
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import datetime
from pathlib import Path
import json


@dataclass
class EvaluationMetrics:
    """评估指标"""
    # 核心指标
    page_access_success_rate: float = 0.0      # 页面访问成功率
    element_locate_accuracy: float = 0.0       # 元素定位准确率
    data_extraction_success_rate: float = 0.0  # 数据抓取成功率
    stability: float = 0.0                     # 稳定性
    anti_detection_capability: float = 0.0     # 反检测能力
    response_time: float = 0.0                 # 响应时间 (秒)
    
    # 综合评分
    overall_score: float = 0.0                 # 综合评分
    grade: str = "D"                           # 评级 (A+/A/B/C/D)
    
    # 元数据
    evaluated_at: Optional[str] = None
    evaluation_version: str = "1.0"
    
    def to_dict(self) -> Dict:
        return {
            "page_access_success_rate": round(self.page_access_success_rate, 4),
            "element_locate_accuracy": round(self.element_locate_accuracy, 4),
            "data_extraction_success_rate": round(self.data_extraction_success_rate, 4),
            "stability": round(self.stability, 4),
            "anti_detection_capability": round(self.anti_detection_capability, 4),
            "response_time": round(self.response_time, 2),
            "overall_score": round(self.overall_score, 2),
            "grade": self.grade,
            "evaluated_at": self.evaluated_at or datetime.now().isoformat(),
            "evaluation_version": self.evaluation_version,
        }
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "EvaluationMetrics":
        return cls(
            page_access_success_rate=data.get("page_access_success_rate", 0.0),
            element_locate_accuracy=data.get("element_locate_accuracy", 0.0),
            data_extraction_success_rate=data.get("data_extraction_success_rate", 0.0),
            stability=data.get("stability", 0.0),
            anti_detection_capability=data.get("anti_detection_capability", 0.0),
            response_time=data.get("response_time", 0.0),
            overall_score=data.get("overall_score", 0.0),
            grade=data.get("grade", "D"),
            evaluated_at=data.get("evaluated_at"),
        )


class WebsiteEvaluator:
    """网站兼容性评估器"""
    
    # 权重配置
    WEIGHTS = {
        "page_access_success_rate": 0.2,
        "element_locate_accuracy": 0.2,
        "data_extraction_success_rate": 0.3,
        "stability": 0.2,
        "anti_detection_capability": 0.1,
    }
    
    # 评级阈值
    GRADE_THRESHOLDS = [
        (95, "A+"),
        (90, "A"),
        (80, "B"),
        (70, "C"),
        (0, "D"),
    ]
    
    def __init__(self, weights: Optional[Dict[str, float]] = None):
        self.weights = weights or self.WEIGHTS
        self._history: List[Dict] = []
    
    def evaluate(
        self,
        metrics: EvaluationMetrics,
        target_success_rate: float = 0.90,
        target_accuracy: float = 0.85,
    ) -> EvaluationMetrics:
        """
        执行评估
        
        Args:
            metrics: 原始评估指标
            target_success_rate: 目标成功率
            target_accuracy: 目标准确率
            
        Returns:
            EvaluationMetrics: 包含综合评分和评级的评估结果
        """
        # 计算综合评分
        overall_score = self._calculate_overall_score(metrics)
        metrics.overall_score = overall_score
        
        # 计算评级
        metrics.grade = self._calculate_grade(overall_score)
        
        # 记录评估历史
        self._history.append({
            "timestamp": datetime.now().isoformat(),
            "metrics": metrics.to_dict(),
            "target_success_rate": target_success_rate,
            "target_accuracy": target_accuracy,
        })
        
        return metrics
    
    def _calculate_overall_score(self, metrics: EvaluationMetrics) -> float:
        """计算综合评分"""
        score = 0.0
        for key, weight in self.weights.items():
            value = getattr(metrics, key, 0.0)
            score += value * weight
        return score
    
    def _calculate_grade(self, score: float) -> str:
        """计算评级"""
        for threshold, grade in self.GRADE_THRESHOLDS:
            if score >= threshold:
                return grade
        return "D"
    
    def get_grade_distribution(self, results: List[EvaluationMetrics]) -> Dict[str, int]:
        """获取评级分布"""
        distribution = {"A+": 0, "A": 0, "B": 0, "C": 0, "D": 0}
        for result in results:
            distribution[result.grade] += 1
        return distribution
    
    def get_trend(self, domain: str, days: int = 7) -> List[Dict]:
        """获取评估趋势"""
        cutoff = datetime.now().timestamp() - days * 86400
        return [
            h for h in self._history
            if datetime.fromisoformat(h["timestamp"]).timestamp() > cutoff
        ]
    
    def export_history(self, output_path: str) -> None:
        """导出评估历史"""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(self._history, f, ensure_ascii=False, indent=2)
    
    def import_history(self, input_path: str) -> None:
        """导入评估历史"""
        with open(input_path, "r", encoding="utf-8") as f:
            self._history = json.load(f)


# 导出公共接口
__all__ = [
    "EvaluationMetrics",
    "WebsiteEvaluator",
]
