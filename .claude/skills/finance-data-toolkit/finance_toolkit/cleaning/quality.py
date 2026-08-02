"""
数据质量监控模块
计算质量指标、生成质量报告、告警检测
"""

from typing import Dict, List
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json


class QualityDimension(Enum):
    """质量维度"""
    COMPLETENESS = "completeness"      # 完整性
    VALIDITY = "validity"              # 有效性
    CONSISTENCY = "consistency"        # 一致性
    TIMELINESS = "timeliness"          # 时效性
    ACCURACY = "accuracy"              # 准确性


@dataclass
class QualityThreshold:
    """质量阈值配置"""
    completeness: float = 0.95
    validity: float = 0.98
    consistency: float = 0.95
    timeliness_hours: float = 2.0
    accuracy: float = 0.95


@dataclass
class QualityMetrics:
    """质量指标"""
    completeness: float = 0.0
    validity: float = 0.0
    consistency: float = 0.0
    timeliness: float = 0.0
    accuracy: float = 0.0
    
    total_records: int = 0
    passed_records: int = 0
    failed_records: int = 0
    
    # 详细统计
    field_completeness: Dict[str, float] = field(default_factory=dict)
    field_validity: Dict[str, float] = field(default_factory=dict)
    
    # 时间
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> Dict:
        return {
            'completeness': self.completeness,
            'validity': self.validity,
            'consistency': self.consistency,
            'timeliness': self.timeliness,
            'accuracy': self.accuracy,
            'total_records': self.total_records,
            'passed_records': self.passed_records,
            'failed_records': self.failed_records,
            'field_completeness': self.field_completeness,
            'field_validity': self.field_validity,
            'computed_at': self.computed_at.isoformat(),
        }
    
    def overall_score(self) -> float:
        """综合质量分 (加权平均)"""
        weights = {
            'completeness': 0.25,
            'validity': 0.25,
            'consistency': 0.20,
            'timeliness': 0.15,
            'accuracy': 0.15,
        }
        return (
            self.completeness * weights['completeness'] +
            self.validity * weights['validity'] +
            self.consistency * weights['consistency'] +
            self.timeliness * weights['timeliness'] +
            self.accuracy * weights['accuracy']
        )


class QualityMetricsCalculator:
    """质量指标计算器"""
    
    @staticmethod
    def compute(batch: List[Dict], 
                clean_reports: List[Dict] = None) -> QualityMetrics:
        """计算批次质量指标"""
        total = len(batch)
        if total == 0:
            return QualityMetrics()
        
        # 1. 有效性: 通过清洗校验的比例
        passed = 0
        if clean_reports:
            passed = sum(1 for r in clean_reports if r.get('final_passed', False))
        else:
            # 无清洗报告时，基于 payload 非空判断
            passed = sum(1 for d in batch if d.get('payload'))
        
        validity = passed / total if total > 0 else 0
        
        # 2. 完整性: 非空字段比例
        non_null_fields = 0
        total_fields = 0
        field_non_null = {}
        field_total = {}
        
        for d in batch:
            payload = d.get('payload', {})
            for k, v in payload.items():
                if k.startswith('_'):
                    continue
                total_fields += 1
                field_total[k] = field_total.get(k, 0) + 1
                if v is not None and v != '':
                    non_null_fields += 1
                    field_non_null[k] = field_non_null.get(k, 0) + 1
        
        completeness = non_null_fields / total_fields if total_fields > 0 else 0
        
        # 字段级完整性
        field_completeness = {}
        for k in field_total:
            field_completeness[k] = field_non_null.get(k, 0) / field_total[k]
        
        # 3. 一致性: 多源数据一致性 (简化版)
        # 实际需要对比多源数据，这里返回默认值
        consistency = 0.0
        
        # 4. 时效性: 数据延迟
        timeliness = 0.0
        if batch:
            delays = []
            for d in batch:
                pub_time = d.get('payload', {}).get('publish_time') or d.get('payload', {}).get('timestamp')
                crawl_time = d.get('crawl_time')
                if pub_time and crawl_time:
                    if isinstance(pub_time, str):
                        pub_time = datetime.fromisoformat(pub_time.replace('Z', '+00:00'))
                    if isinstance(crawl_time, str):
                        crawl_time = datetime.fromisoformat(crawl_time.replace('Z', '+00:00'))
                    delay = (crawl_time - pub_time).total_seconds() / 3600  # 小时
                    delays.append(delay)
            
            if delays:
                avg_delay = sum(delays) / len(delays)
                # 延迟越小越好，2小时内为满分
                timeliness = max(0, 1 - avg_delay / 24)  # 24小时归一化
        
        # 5. 准确性: 需基准源对比，简化版
        accuracy = 0.0
        
        return QualityMetrics(
            completeness=completeness,
            validity=validity,
            consistency=consistency,
            timeliness=timeliness,
            accuracy=accuracy,
            total_records=total,
            passed_records=passed,
            failed_records=total - passed,
            field_completeness=field_completeness,
        )
    
    @staticmethod
    def check_alerts(metrics: QualityMetrics, 
                     thresholds: QualityThreshold = None) -> List[str]:
        """检查是否触发告警"""
        thresholds = thresholds or QualityThreshold()
        alerts = []
        
        if metrics.completeness < thresholds.completeness:
            alerts.append(f"完整性过低: {metrics.completeness:.2%} < {thresholds.completeness:.2%}")
        
        if metrics.validity < thresholds.validity:
            alerts.append(f"有效性过低: {metrics.validity:.2%} < {thresholds.validity:.2%}")
        
        if metrics.consistency < thresholds.consistency:
            alerts.append(f"一致性过低: {metrics.consistency:.2%} < {thresholds.consistency:.2%}")
        
        if metrics.timeliness < (1 - thresholds.timeliness_hours / 24):
            alerts.append("时效性过低: 平均延迟过大")
        
        if metrics.accuracy < thresholds.accuracy:
            alerts.append(f"准确性过低: {metrics.accuracy:.2%} < {thresholds.accuracy:.2%}")
        
        return alerts


class QualityMonitor:
    """质量监控器：持续监控、告警、历史趋势"""
    
    def __init__(self, 
                 thresholds: QualityThreshold = None,
                 history_size: int = 1000):
        self.thresholds = thresholds or QualityThreshold()
        self.history: List[QualityMetrics] = []
        self.history_size = history_size
        self.alert_callbacks: List[callable] = []
    
    def record(self, metrics: QualityMetrics):
        """记录质量指标"""
        self.history.append(metrics)
        if len(self.history) > self.history_size:
            self.history.pop(0)
        
        # 检查告警
        alerts = QualityMetricsCalculator.check_alerts(metrics, self.thresholds)
        if alerts:
            self._trigger_alerts(alerts, metrics)
    
    def _trigger_alerts(self, alerts: List[str], metrics: QualityMetrics):
        """触发告警回调"""
        for callback in self.alert_callbacks:
            try:
                callback(alerts, metrics)
            except Exception:
                pass  # 忽略回调异常
    
    def add_alert_callback(self, callback: callable):
        """添加告警回调"""
        self.alert_callbacks.append(callback)
    
    def get_trend(self, dimension: QualityDimension, window: int = 100) -> List[float]:
        """获取质量维度趋势"""
        if not self.history:
            return []
        
        recent = self.history[-window:]
        attr = dimension.value
        return [getattr(m, attr) for m in recent]
    
    def get_summary(self) -> Dict:
        """获取质量摘要"""
        if not self.history:
            return {}
        
        latest = self.history[-1]
        
        # 计算趋势 (最近 10 个 vs 之前 10 个)
        trend = {}
        if len(self.history) >= 20:
            recent_10 = self.history[-10:]
            prev_10 = self.history[-20:-10]
            for dim in QualityDimension:
                attr = dim.value
                recent_avg = sum(getattr(m, attr) for m in recent_10) / 10
                prev_avg = sum(getattr(m, attr) for m in prev_10) / 10
                trend[attr] = recent_avg - prev_avg
        
        return {
            'latest': latest.to_dict(),
            'overall_score': latest.overall_score(),
            'trend': trend,
            'history_length': len(self.history),
            'alerts_triggered': sum(1 for m in self.history[-10:] 
                                   if QualityMetricsCalculator.check_alerts(m, self.thresholds)),
        }
    
    def export_history(self, filepath: str):
        """导出历史数据"""
        data = [m.to_dict() for m in self.history]
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


class DataQualityReport:
    """数据质量报告生成器"""
    
    @staticmethod
    def generate(batch: List[Dict], 
                 clean_reports: List[Dict] = None,
                 thresholds: QualityThreshold = None) -> Dict:
        """生成完整质量报告"""
        metrics = QualityMetricsCalculator.compute(batch, clean_reports)
        alerts = QualityMetricsCalculator.check_alerts(metrics, thresholds)
        
        # 按数据源分组统计
        source_stats = {}
        for d in batch:
            source = d.get('source', 'unknown')
            if source not in source_stats:
                source_stats[source] = {'total': 0, 'passed': 0}
            source_stats[source]['total'] += 1
            if clean_reports:
                # 需要匹配 clean_reports
                pass
        
        # 按数据类型分组
        type_stats = {}
        for d in batch:
            dtype = d.get('data_type', 'unknown')
            if dtype not in type_stats:
                type_stats[dtype] = {'total': 0, 'passed': 0}
            type_stats[dtype]['total'] += 1
        
        return {
            'report_time': datetime.now(timezone.utc).isoformat(),
            'summary': {
                'total_records': metrics.total_records,
                'passed': metrics.passed_records,
                'failed': metrics.failed_records,
                'overall_score': metrics.overall_score(),
            },
            'dimensions': {
                'completeness': metrics.completeness,
                'validity': metrics.validity,
                'consistency': metrics.consistency,
                'timeliness': metrics.timeliness,
                'accuracy': metrics.accuracy,
            },
            'field_completeness': metrics.field_completeness,
            'source_stats': source_stats,
            'type_stats': type_stats,
            'alerts': alerts,
            'recommendations': DataQualityReport._generate_recommendations(metrics, alerts),
        }
    
    @staticmethod
    def _generate_recommendations(metrics: QualityMetrics, alerts: List[str]) -> List[str]:
        """生成改进建议"""
        recs = []
        
        if metrics.completeness < 0.9:
            recs.append("建议检查上游数据源字段缺失原因，补充必要字段")
        
        if metrics.validity < 0.95:
            recs.append("建议加强清洗规则，修复数据格式/类型异常")
        
        if metrics.timeliness < 0.8:
            recs.append("建议优化抓取频率，减少数据延迟")
        
        if metrics.consistency < 0.9:
            recs.append("建议建立多源数据一致性校验机制")
        
        if not recs:
            recs.append("数据质量良好，继续保持")
        
        return recs