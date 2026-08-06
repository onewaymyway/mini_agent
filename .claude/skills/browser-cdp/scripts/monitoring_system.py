"""
监控告警系统

提供完整的监控告警能力：
- 核心指标监控（成功率、错误率、耗时）
- 异常告警（阈值触发、多级通知）
- 监控覆盖率追踪（目标 90%）
- 告警响应时间追踪（目标 <5 分钟）
"""

import json
import logging
import time
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from .monitoring_coverage import MonitoringCoverageTracker, get_coverage_tracker
except ImportError:
    from monitoring_coverage import MonitoringCoverageTracker, get_coverage_tracker

try:
    from ..reliability.metrics import ReliabilityMetrics, get_metrics
except ImportError:
    ReliabilityMetrics = None
    get_metrics = None

logger = logging.getLogger(__name__)


class MetricThreshold:
    """指标阈值定义"""
    
    def __init__(
        self,
        metric_name: str,
        threshold_type: str,  # "gt", "lt", "gte", "lte"
        threshold_value: float,
        severity: str = "warning",
        description: str = "",
    ):
        self.metric_name = metric_name
        self.threshold_type = threshold_type
        self.threshold_value = threshold_value
        self.severity = severity
        self.description = description
    
    def check(self, current_value: float) -> bool:
        """检查是否触发阈值"""
        if self.threshold_type == "gt":
            return current_value > self.threshold_value
        elif self.threshold_type == "lt":
            return current_value < self.threshold_value
        elif self.threshold_type == "gte":
            return current_value >= self.threshold_value
        elif self.threshold_type == "lte":
            return current_value <= self.threshold_value
        return False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "threshold_type": self.threshold_type,
            "threshold_value": self.threshold_value,
            "severity": self.severity,
            "description": self.description,
        }


class Alert:
    """告警记录"""
    
    def __init__(
        self,
        alert_id: str,
        metric_name: str,
        severity: str,
        current_value: float,
        threshold_value: float,
        message: str,
        timestamp: Optional[float] = None,
    ):
        self.alert_id = alert_id
        self.metric_name = metric_name
        self.severity = severity
        self.current_value = current_value
        self.threshold_value = threshold_value
        self.message = message
        self.timestamp = timestamp or time.time()
        self.acknowledged = False
        self.resolved = False
        self.acknowledged_at: Optional[float] = None
        self.resolved_at: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "metric_name": self.metric_name,
            "severity": self.severity,
            "current_value": self.current_value,
            "threshold_value": self.threshold_value,
            "message": self.message,
            "timestamp": datetime.fromtimestamp(self.timestamp).isoformat(),
            "acknowledged": self.acknowledged,
            "resolved": self.resolved,
        }


class MonitoringSystem:
    """
    监控告警系统。
    
    提供：
    - 核心指标监控
    - 阈值告警
    - 监控覆盖率追踪
    - 告警响应时间统计
    """
    
    # 默认阈值配置
    DEFAULT_THRESHOLDS = [
        # 成功率相关
        MetricThreshold("success_rate", "lt", 0.76, "warning", "成功率低于 76%"),
        MetricThreshold("success_rate", "lt", 0.60, "error", "成功率低于 60%"),
        MetricThreshold("success_rate", "lt", 0.40, "critical", "成功率低于 40%"),
        
        # 错误率相关
        MetricThreshold("error_rate", "gt", 0.24, "warning", "错误率高于 24%"),
        MetricThreshold("error_rate", "gt", 0.40, "error", "错误率高于 40%"),
        MetricThreshold("error_rate", "gt", 0.60, "critical", "错误率高于 60%"),
        
        # 重试失败率
        MetricThreshold("retry_failure_rate", "gt", 0.30, "warning", "重试失败率高于 30%"),
        MetricThreshold("retry_failure_rate", "gt", 0.50, "error", "重试失败率高于 50%"),
        
        # 告警响应时间
        MetricThreshold("alert_response_time", "gt", 180, "warning", "告警响应时间超过 3 分钟"),
        MetricThreshold("alert_response_time", "gt", 300, "error", "告警响应时间超过 5 分钟"),
        
        # 监控覆盖率
        MetricThreshold("monitoring_coverage_rate", "lt", 0.90, "warning", "监控覆盖率低于 90%"),
        MetricThreshold("monitoring_coverage_rate", "lt", 0.70, "error", "监控覆盖率低于 70%"),
    ]
    
    def __init__(
        self,
        coverage_tracker: Optional[MonitoringCoverageTracker] = None,
        alert_history_file: Optional[str] = None,
    ):
        self.coverage_tracker = coverage_tracker or get_coverage_tracker()
        
        # 阈值配置
        self._thresholds: List[MetricThreshold] = list(self.DEFAULT_THRESHOLDS)
        
        # 告警历史
        if alert_history_file is None:
            alert_history_file = str(Path(__file__).parent.parent / "logs" / "alerts.jsonl")
        self._alert_history_file = alert_history_file
        self._alerts: List[Alert] = []
        self._alert_handlers: List[Callable] = []
        
        # 指标存储
        self._metrics: Dict[str, float] = {}
        self._metric_history: Dict[str, List[Dict[str, Any]]] = {}
        
        # 告警响应追踪
        self._alert_response_times: List[float] = []
        
        # 加载历史告警
        self._load_alert_history()
    
    def add_threshold(self, threshold: MetricThreshold):
        """添加阈值规则"""
        self._thresholds.append(threshold)
        logger.info(f"Added threshold: {threshold.metric_name} {threshold.threshold_type} {threshold.threshold_value}")
    
    def remove_threshold(self, metric_name: str, threshold_type: str, threshold_value: float):
        """移除阈值规则"""
        self._thresholds = [
            t for t in self._thresholds
            if not (t.metric_name == metric_name and t.threshold_type == threshold_type and abs(t.threshold_value - threshold_value) < 0.0001)
        ]
    
    def set_metric(self, metric_name: str, value: float):
        """设置指标值"""
        self._metrics[metric_name] = value
        
        # 记录历史
        if metric_name not in self._metric_history:
            self._metric_history[metric_name] = []
        self._metric_history[metric_name].append({
            "timestamp": time.time(),
            "value": value,
        })
        # 只保留最近 1000 条
        self._metric_history[metric_name] = self._metric_history[metric_name][-1000:]
        
        # 检查阈值
        self._check_thresholds(metric_name, value)
    
    def _check_thresholds(self, metric_name: str, value: float):
        """检查阈值并触发告警"""
        for threshold in self._thresholds:
            if threshold.metric_name == metric_name and threshold.check(value):
                alert_id = f"{metric_name}_{int(time.time())}"
                alert = Alert(
                    alert_id=alert_id,
                    metric_name=metric_name,
                    severity=threshold.severity,
                    current_value=value,
                    threshold_value=threshold.threshold_value,
                    message=threshold.description,
                )
                self._alerts.append(alert)
                self._save_alert_history()
                
                logger.warning(f"Alert triggered: {alert_id} - {threshold.description} (value={value}, threshold={threshold.threshold_value})")
                
                # 通知处理器
                for handler in self._alert_handlers:
                    try:
                        handler(alert)
                    except Exception as e:
                        logger.error(f"Alert handler error: {e}")
    
    def acknowledge_alert(self, alert_id: str):
        """确认告警"""
        for alert in self._alerts:
            if alert.alert_id == alert_id:
                alert.acknowledged = True
                alert.acknowledged_at = time.time()
                
                # 追踪响应时间
                response_time = time.time() - alert.timestamp
                self._alert_response_times.append(response_time)
                self.coverage_tracker.track_alert_response(response_time)
                
                logger.info(f"Alert acknowledged: {alert_id} (response_time={response_time:.1f}s)")
                return True
        return False
    
    def resolve_alert(self, alert_id: str):
        """解决告警"""
        for alert in self._alerts:
            if alert.alert_id == alert_id:
                alert.resolved = True
                alert.resolved_at = time.time()
                return True
        return False
    
    def get_alerts(self, severity: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """获取告警列表"""
        alerts = self._alerts
        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        return [a.to_dict() for a in alerts[-limit:]]
    
    def get_pending_alerts(self) -> List[Dict[str, Any]]:
        """获取待处理告警"""
        return [a.to_dict() for a in self._alerts if not a.acknowledged and not a.resolved]
    
    def get_metric_history(self, metric_name: str, limit: int = 100) -> List[Dict[str, Any]]:
        """获取指标历史"""
        return self._metric_history.get(metric_name, [])[-limit:]
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        # 告警响应时间统计
        response_stats = self._get_response_stats()
        
        # 覆盖率报告
        coverage_report = self.coverage_tracker.get_coverage_report()
        
        # 告警统计
        alert_stats = self._get_alert_stats()
        
        return {
            "timestamp": datetime.now().isoformat(),
            "metrics": dict(self._metrics),
            "alerts": alert_stats,
            "alert_response": response_stats,
            "coverage": coverage_report,
        }
    
    def _get_response_stats(self) -> Dict[str, Any]:
        """获取告警响应时间统计"""
        if not self._alert_response_times:
            return {
                "avg_response_time": 0,
                "max_response_time": 0,
                "min_response_time": 0,
                "p50_response_time": 0,
                "p95_response_time": 0,
                "within_5min_rate": 0,
                "total_responded": 0,
            }
        
        sorted_times = sorted(self._alert_response_times)
        p50_idx = int(len(sorted_times) * 0.5)
        p95_idx = int(len(sorted_times) * 0.95)
        within_5min = sum(1 for t in sorted_times if t <= 300) / len(sorted_times)
        
        return {
            "avg_response_time": round(sum(sorted_times) / len(sorted_times), 2),
            "max_response_time": round(max(sorted_times), 2),
            "min_response_time": round(min(sorted_times), 2),
            "p50_response_time": round(sorted_times[p50_idx], 2),
            "p95_response_time": round(sorted_times[min(p95_idx, len(sorted_times) - 1)], 2),
            "within_5min_rate": round(within_5min, 4),
            "total_responded": len(sorted_times),
            "target_met": within_5min >= 0.9,
        }
    
    def _get_alert_stats(self) -> Dict[str, Any]:
        """获取告警统计"""
        total = len(self._alerts)
        pending = sum(1 for a in self._alerts if not a.acknowledged and not a.resolved)
        acknowledged = sum(1 for a in self._alerts if a.acknowledged and not a.resolved)
        resolved = sum(1 for a in self._alerts if a.resolved)
        
        by_severity = {}
        for alert in self._alerts:
            sev = alert.severity
            by_severity[sev] = by_severity.get(sev, 0) + 1
        
        return {
            "total": total,
            "pending": pending,
            "acknowledged": acknowledged,
            "resolved": resolved,
            "by_severity": by_severity,
        }
    
    def _load_alert_history(self):
        """加载历史告警"""
        path = Path(self._alert_history_file)
        if not path.exists():
            return
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            data = json.loads(line)
                            alert = Alert(
                                alert_id=data.get("alert_id", ""),
                                metric_name=data.get("metric_name", ""),
                                severity=data.get("severity", "warning"),
                                current_value=data.get("current_value", 0),
                                threshold_value=data.get("threshold", 0),
                                message=data.get("description", ""),
                                timestamp=datetime.fromisoformat(data["timestamp"]).timestamp() if "timestamp" in data else time.time(),
                            )
                            alert.acknowledged = data.get("acknowledged", False)
                            alert.resolved = data.get("resolved", False)
                            self._alerts.append(alert)
                        except (json.JSONDecodeError, KeyError):
                            continue
        except Exception as e:
            logger.warning(f"Failed to load alert history: {e}")
    
    def _save_alert_history(self):
        """保存告警历史"""
        try:
            Path(self._alert_history_file).parent.mkdir(parents=True, exist_ok=True)
            with open(self._alert_history_file, 'a', encoding='utf-8') as f:
                alert = self._alerts[-1]
                f.write(json.dumps(alert.to_dict(), ensure_ascii=False) + '\n')
        except Exception as e:
            logger.warning(f"Failed to save alert history: {e}")
    
    def register_handler(self, handler: Callable):
        """注册告警处理器"""
        self._alert_handlers.append(handler)

    def sync_reliability_metrics(self):
        """
        同步 ReliabilityMetrics 指标到监控系统

        从 reliability.metrics 模块获取指标并同步到监控系统，
        确保监控覆盖率目标达成。
        """
        if get_metrics is None:
            logger.debug("ReliabilityMetrics 不可用，跳过同步")
            return

        metrics = get_metrics()
        metrics_data = metrics.get_metrics()

        # 同步重试指标
        retry_data = metrics_data.get("retry", {})
        if retry_data.get("total", 0) > 0:
            retry_failure_rate = 1.0 - retry_data.get("success_rate", 1.0)
            self.set_metric("retry_failure_rate", retry_failure_rate)
            logger.debug(f"同步重试失败率: {retry_failure_rate:.4f}")

        # 同步连接指标
        connection_data = metrics_data.get("connection", {})
        if connection_data.get("losses", 0) > 0:
            connection_loss_rate = 1.0 - connection_data.get("recovery_rate", 0)
            self.set_metric("connection_loss_rate", connection_loss_rate)
            logger.debug(f"同步连接丢失率: {connection_loss_rate:.4f}")

        # 同步熔断器指标
        cb_data = metrics_data.get("circuit_breaker", {})
        self.set_metric("circuit_breaker_trips", cb_data.get("trips", 0))
        logger.debug(f"同步熔断器触发次数: {cb_data.get('trips', 0)}")

        # 同步错误统计
        errors_by_category = metrics_data.get("errors_by_category", {})
        total_errors = sum(errors_by_category.values())
        self.set_metric("error_count", total_errors)
        logger.debug(f"同步错误数量: {total_errors}")

        # 同步操作耗时
        op_durations = metrics_data.get("operation_durations", {})
        for op, stats in op_durations.items():
            avg_duration = stats.get("avg", 0)
            self.set_metric(f"operation_duration:{op}", avg_duration)

        logger.info(f"ReliabilityMetrics 同步完成")

    def sync_and_check_alerts(self):
        """
        同步指标并检查告警

        先同步 ReliabilityMetrics，然后检查所有阈值告警
        """
        self.sync_reliability_metrics()
        # 触发阈值检查
        for metric_name, value in self._metrics.items():
            self._check_thresholds(metric_name, value)
    
    def generate_report(self, output_path: Optional[str] = None) -> Dict[str, Any]:
        """生成监控报告"""
        stats = self.get_stats()
        
        # 评估目标达成情况
        stats["targets"] = {
            "monitoring_coverage": {
                "target": 0.9,
                "actual": stats["coverage"]["overall_coverage_rate"],
                "met": stats["coverage"]["target_met"],
            },
            "alert_response_time": {
                "target": 300,  # 5 分钟
                "actual": stats["alert_response"]["avg_response_time"],
                "met": stats["alert_response"]["within_5min_rate"] >= 0.9,
            },
        }
        
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(stats, f, indent=2, ensure_ascii=False)
            logger.info(f"Monitoring report saved to {output_path}")
        
        return stats


# 全局监控实例
_global_monitoring: Optional[MonitoringSystem] = None


def get_monitoring_system() -> MonitoringSystem:
    """获取监控告警系统实例"""
    global _global_monitoring
    if _global_monitoring is None:
        _global_monitoring = MonitoringSystem()
    return _global_monitoring


def reset_monitoring_system():
    """重置监控告警系统"""
    global _global_monitoring
    _global_monitoring = None
