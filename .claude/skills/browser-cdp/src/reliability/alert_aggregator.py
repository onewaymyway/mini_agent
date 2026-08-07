# -*- coding: utf-8 -*-
"""
告警聚合模块

提供告警聚合、抑制和分级通知机制，解决告警风暴问题。
"""

import time
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    """告警级别"""
    CRITICAL = "critical"      # 成功率 < 50%
    ERROR = "error"            # 成功率 < 76%
    WARNING = "warning"        # 重试失败率 > 30%
    INFO = "info"              # 性能指标异常


@dataclass
class Alert:
    """告警数据类"""
    metric_name: str
    severity: AlertSeverity
    message: str
    timestamp: float = field(default_factory=time.time)
    source: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "severity": self.severity.value,
            "message": self.message,
            "timestamp": self.timestamp,
            "source": self.source,
            "details": self.details,
        }


class AlertAggregator:
    """
    告警聚合器
    
    聚合相同类型的告警，避免告警风暴。
    """
    
    def __init__(self, window_seconds: int = 300):
        self.window = window_seconds
        self._alerts: Dict[str, List[Alert]] = defaultdict(list)
        self._suppressed: Dict[str, float] = {}  # 抑制规则
        self._handlers: List[Callable[[Alert], None]] = []
    
    def add_alert(self, alert: Alert):
        """添加告警并检查是否需要聚合"""
        key = f"{alert.metric_name}:{alert.severity.value}"
        
        # 检查抑制规则
        if self._is_suppressed(key, alert):
            logger.debug(f"Alert suppressed: {key}")
            return
        
        # 清理过期告警
        self._cleanup_alerts(key)
        
        # 添加新告警
        self._alerts[key].append(alert)
        
        # 检查是否需要发送聚合告警
        if len(self._alerts[key]) >= 3:
            self._send_aggregated_alert(key, self._alerts[key])
    
    def _is_suppressed(self, key: str, alert: Alert) -> bool:
        """检查告警是否被抑制"""
        # 同一指标 5 分钟内重复告警 → 抑制
        now = time.time()
        if key in self._suppressed:
            if now - self._suppressed[key] < self.window:
                return True
        # 记录本次告警时间
        self._suppressed[key] = now
        return False
    
    def _cleanup_alerts(self, key: str):
        """清理过期告警"""
        now = time.time()
        self._alerts[key] = [
            a for a in self._alerts[key]
            if now - a.timestamp < self.window
        ]
    
    def _send_aggregated_alert(self, key: str, alerts: List[Alert]):
        """发送聚合告警"""
        summary = f"{key} 在 {self.window}s 内发生 {len(alerts)} 次"
        logger.warning(f"Aggregated alert: {summary}")
        
        # 调用所有处理器
        for handler in self._handlers:
            try:
                handler(alerts[0])  # 发送第一条告警作为代表
            except Exception as e:
                logger.error(f"Alert handler error: {e}")
        
        # 清空聚合队列
        self._alerts[key] = []
    
    def register_handler(self, handler: Callable[[Alert], None]):
        """注册告警处理器"""
        self._handlers.append(handler)
    
    def get_alert_count(self, metric_name: Optional[str] = None) -> int:
        """获取告警数量"""
        if metric_name:
            # Key format is "metric_name:severity", so filter by prefix
            return sum(len(alerts) for k, alerts in self._alerts.items() if k.startswith(f"{metric_name}:"))
        return sum(len(alerts) for alerts in self._alerts.values())
    
    def get_status(self) -> Dict[str, Any]:
        """获取聚合状态"""
        return {
            "total_alerts": self.get_alert_count(),
            "active_keys": list(self._alerts.keys()),
            "window_seconds": self.window,
        }


class AlertSuppressionRule:
    """告警抑制规则"""
    
    def __init__(self, metric_name: str, window_seconds: int = 300, max_count: int = 1):
        self.metric_name = metric_name
        self.window = window_seconds
        self.max_count = max_count
        self._counts: Dict[str, int] = defaultdict(int)
        self._last_reset: Dict[str, float] = {}
    
    def should_suppress(self, source: str) -> bool:
        """检查是否应该抑制"""
        now = time.time()
        
        # 重置过期计数
        if source in self._last_reset:
            if now - self._last_reset[source] > self.window:
                self._counts[source] = 0
                self._last_reset[source] = now
        
        # 检查是否超过阈值
        self._counts[source] += 1
        return self._counts[source] > self.max_count


class AlertNotifier:
    """告警通知器 - 支持多种通知方式"""
    
    def __init__(self):
        self._channels: Dict[str, Callable[[Alert], None]] = {}
    
    def register_channel(self, name: str, handler: Callable[[Alert], None]):
        """注册通知渠道"""
        self._channels[name] = handler
        logger.info(f"Registered alert channel: {name}")
    
    def notify(self, alert: Alert):
        """发送告警通知"""
        for name, handler in self._channels.items():
            try:
                handler(alert)
            except Exception as e:
                logger.error(f"Alert notification failed for {name}: {e}")


# 分级告警阈值配置
ALERT_THRESHOLDS = {
    AlertSeverity.CRITICAL: {
        "success_rate": 0.50,
        "retry_failure_rate": 0.50,
        "response_time": 60.0,
    },
    AlertSeverity.ERROR: {
        "success_rate": 0.76,
        "retry_failure_rate": 0.30,
        "response_time": 30.0,
    },
    AlertSeverity.WARNING: {
        "success_rate": 0.90,
        "retry_failure_rate": 0.20,
        "response_time": 15.0,
    },
    AlertSeverity.INFO: {
        "success_rate": 0.95,
        "retry_failure_rate": 0.10,
        "response_time": 10.0,
    },
}


# 全局实例
_global_aggregator: Optional[AlertAggregator] = None
_global_notifier: Optional[AlertNotifier] = None


def get_alert_aggregator() -> AlertAggregator:
    """获取全局告警聚合器"""
    global _global_aggregator
    if _global_aggregator is None:
        _global_aggregator = AlertAggregator()
    return _global_aggregator


def get_alert_notifier() -> AlertNotifier:
    """获取全局告警通知器"""
    global _global_notifier
    if _global_notifier is None:
        _global_notifier = AlertNotifier()
    return _global_notifier


def reset_alert_system():
    """重置告警系统（用于测试）"""
    global _global_aggregator, _global_notifier
    _global_aggregator = None
    _global_notifier = None
