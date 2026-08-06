"""
数据质量评估器

评估指标：
- 数据完整性：必填字段填充率
- 数据准确性：字段值合理性检查
- 数据时效性：数据新鲜度
- 数据一致性：跨页面数据一致性
"""

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

from .base_evaluator import BaseEvaluator, MetricResult

logger = logging.getLogger(__name__)


class DataQualityEvaluator(BaseEvaluator):
    """数据质量评估器"""

    def __init__(self):
        super().__init__(name="数据质量", weight=0.15)
        self._field_requirements = {}
        self._field_validators = {}

    def set_field_requirements(self, field_name: str, required: bool = True):
        """设置字段要求"""
        self._field_requirements[field_name] = required

    def set_field_validator(self, field_name: str, validator):
        """设置字段验证器"""
        self._field_validators[field_name] = validator

    def evaluate(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        评估数据质量

        context 参数:
            - total_records: 总记录数
            - complete_records: 完整记录数（所有必填字段都有值）
            - valid_records: 有效记录数（字段值通过验证）
            - fresh_records: 新鲜记录数（在时效窗口内）
            - consistent_records: 一致记录数（跨页面一致）
            - field_completeness: 各字段填充率 {field_name: fill_rate}
            - data_age_hours: 数据平均年龄（小时）
            - freshness_threshold_hours: 新鲜度阈值（小时）
        """
        total_records = context.get("total_records", 0)
        complete_records = context.get("complete_records", 0)
        valid_records = context.get("valid_records", 0)
        fresh_records = context.get("fresh_records", 0)
        consistent_records = context.get("consistent_records", 0)
        field_completeness = context.get("field_completeness", {})
        data_age_hours = context.get("data_age_hours", 0.0)
        freshness_threshold_hours = context.get("freshness_threshold_hours", 24.0)

        # 计算各项指标
        completeness_rate = self._safe_divide(complete_records, total_records, 0.0) * 100
        validity_rate = self._safe_divide(valid_records, total_records, 0.0) * 100
        freshness_rate = self._safe_divide(fresh_records, total_records, 0.0) * 100
        consistency_rate = self._safe_divide(consistent_records, total_records, 0.0) * 100

        # 计算字段平均填充率
        avg_field_completeness = sum(field_completeness.values()) / len(field_completeness) if field_completeness else 0.0

        # 计算数据新鲜度得分
        if data_age_hours <= freshness_threshold_hours * 0.5:
            freshness_score = 100.0
        elif data_age_hours <= freshness_threshold_hours:
            freshness_score = 100.0 - (data_age_hours - freshness_threshold_hours * 0.5) / freshness_threshold_hours * 50
        else:
            freshness_score = max(0.0, 50.0 - (data_age_hours - freshness_threshold_hours) / freshness_threshold_hours * 50)

        # 添加指标
        self.add_metric(MetricResult(
            name="数据完整性",
            value=completeness_rate,
            unit="%",
            target=90.0,
            weight=0.25,
            details={"complete": complete_records, "total": total_records}
        ))

        self.add_metric(MetricResult(
            name="数据有效性",
            value=validity_rate,
            unit="%",
            target=85.0,
            weight=0.25,
            details={"valid": valid_records, "total": total_records}
        ))

        self.add_metric(MetricResult(
            name="数据新鲜度",
            value=freshness_score,
            unit="分",
            target=80.0,
            weight=0.20,
            details={
                "age_hours": data_age_hours,
                "threshold_hours": freshness_threshold_hours
            }
        ))

        self.add_metric(MetricResult(
            name="数据一致性",
            value=consistency_rate,
            unit="%",
            target=85.0,
            weight=0.15,
            details={"consistent": consistent_records, "total": total_records}
        ))

        self.add_metric(MetricResult(
            name="字段平均填充率",
            value=avg_field_completeness,
            unit="%",
            target=90.0,
            weight=0.15,
            details=field_completeness
        ))

        # 计算综合得分
        comprehensive_score = (
            completeness_rate * 0.25 +
            validity_rate * 0.25 +
            freshness_score * 0.20 +
            consistency_rate * 0.15 +
            avg_field_completeness * 0.15
        )

        self.add_metric(MetricResult(
            name="数据质量综合得分",
            value=comprehensive_score,
            unit="分",
            target=85.0,
            weight=1.0,
            details={
                "completeness": round(completeness_rate, 2),
                "validity": round(validity_rate, 2),
                "freshness": round(freshness_score, 2),
                "consistency": round(consistency_rate, 2),
                "field_avg": round(avg_field_completeness, 2),
            }
        ))

        # 添加观察记录
        if completeness_rate < 90:
            self.add_observation(f"数据完整性较低 ({completeness_rate:.1f}%)，存在字段缺失")
        if validity_rate < 85:
            self.add_observation(f"数据有效性较低 ({validity_rate:.1f}%)，存在字段值异常")
        if freshness_score < 80:
            self.add_observation(f"数据新鲜度较低 (平均 {data_age_hours:.1f} 小时)，需增加抓取频率")
        if consistency_rate < 85:
            self.add_observation(f"数据一致性较低 ({consistency_rate:.1f}%)，存在跨页面数据不一致")
        if avg_field_completeness < 90:
            self.add_observation(f"字段平均填充率较低 ({avg_field_completeness:.1f}%)，需优化提取逻辑")

        return self.get_result().to_dict()

    @staticmethod
    def _safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
        """安全除法，避免除零错误"""
        if denominator == 0:
            return default
        return numerator / denominator


class DataQualityMonitor:
    """数据质量监控器"""

    def __init__(self, freshness_threshold_hours: float = 24.0):
        self._freshness_threshold = freshness_threshold_hours
        self._history: List[Dict[str, Any]] = []
        self._alerts: List[Dict[str, Any]] = []

    def record_quality(self, source: str, quality_data: Dict[str, Any]):
        """记录数据质量数据"""
        record = {
            "source": source,
            "timestamp": datetime.now().isoformat(),
            **quality_data
        }
        self._history.append(record)
        self._check_alerts(source, record)

    def _check_alerts(self, source: str, record: Dict[str, Any]):
        """检查是否需要触发告警"""
        completeness = record.get("completeness_rate", 100)
        validity = record.get("validity_rate", 100)
        freshness = record.get("freshness_score", 100)

        if completeness < 70:
            self._alerts.append({
                "source": source,
                "type": "completeness_low",
                "severity": "high",
                "message": f"数据完整性过低 ({completeness:.1f}%)",
                "timestamp": record["timestamp"]
            })

        if validity < 70:
            self._alerts.append({
                "source": source,
                "type": "validity_low",
                "severity": "high",
                "message": f"数据有效性过低 ({validity:.1f}%)",
                "timestamp": record["timestamp"]
            })

        if freshness < 60:
            self._alerts.append({
                "source": source,
                "type": "freshness_low",
                "severity": "medium",
                "message": f"数据新鲜度过低 ({freshness:.1f}分)",
                "timestamp": record["timestamp"]
            })

    def get_alerts(self, hours: int = 24) -> List[Dict[str, Any]]:
        """获取最近 N 小时的告警"""
        cutoff = datetime.now() - timedelta(hours=hours)
        return [
            a for a in self._alerts
            if datetime.fromisoformat(a["timestamp"]) > cutoff
        ]

    def get_trend(self, source: str, metric: str, days: int = 7) -> List[Dict[str, Any]]:
        """获取指标趋势"""
        cutoff = datetime.now() - timedelta(days=days)
        records = [
            r for r in self._history
            if r.get("source") == source
            and datetime.fromisoformat(r["timestamp"]) > cutoff
        ]
        return [
            {"timestamp": r["timestamp"], "value": r.get(metric)}
            for r in records
        ]

    def get_summary(self, source: Optional[str] = None, days: int = 7) -> Dict[str, Any]:
        """获取质量摘要"""
        cutoff = datetime.now() - timedelta(days=days)
        records = [
            r for r in self._history
            if datetime.fromisoformat(r["timestamp"]) > cutoff
            and (source is None or r.get("source") == source)
        ]

        if not records:
            return {"total_records": 0, "avg_completeness": 0, "avg_validity": 0}

        return {
            "total_records": len(records),
            "avg_completeness": sum(r.get("completeness_rate", 0) for r in records) / len(records),
            "avg_validity": sum(r.get("validity_rate", 0) for r in records) / len(records),
            "avg_freshness": sum(r.get("freshness_score", 0) for r in records) / len(records),
            "avg_consistency": sum(r.get("consistency_rate", 0) for r in records) / len(records),
        }

    def generate_report(self, source: str) -> str:
        """生成质量报告"""
        trend_data = self.get_trend(source, "completeness_rate", days=14)
        if not trend_data:
            return f"暂无 {source} 的质量数据"

        lines = [f"# {source} 数据质量报告", ""]
        lines.append("| 时间 | 完整性 | 有效性 | 新鲜度 | 一致性 |\n")
        lines.append("|------|--------|--------|--------|--------|\n")
        for d in trend_data[-10:]:
            ts = d["timestamp"][:19].replace("T", " ")
            lines.append(f"| {ts} | {d.get('completeness_rate', 0):.1f}% | {d.get('validity_rate', 0):.1f}% | {d.get('freshness_score', 0):.1f}分 | {d.get('consistency_rate', 0):.1f}% |\n")

        # 计算趋势
        if len(trend_data) >= 2:
            recent = trend_data[-5:] if len(trend_data) >= 5 else trend_data
            avg_recent = sum(d.get("completeness_rate", 0) for d in recent) / len(recent)
            avg_older = sum(d.get("completeness_rate", 0) for d in trend_data[:-5]) / (len(trend_data) - 5) if len(trend_data) > 5 else avg_recent
            trend = "上升" if avg_recent > avg_older else "下降" if avg_recent < avg_older else "稳定"
            lines.append(f"\n**趋势**: {trend} (近期平均: {avg_recent:.1f}%, 前期平均: {avg_older:.1f}%)")

        return "\n".join(lines)
