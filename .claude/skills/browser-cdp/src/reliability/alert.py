"""
告警系统

提供可靠性指标的告警触发和通知功能，支持：
- 基于阈值的告警规则
- 多种通知渠道（Webhook、日志、回调）
- 告警去重和抑制
- 告警历史追踪
"""

import json
import time
import asyncio
import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .metrics import ReliabilityMetrics

logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    """告警级别"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertRule:
    """
    告警规则定义。
    
    支持基于阈值的告警触发：
    - 重试失败率超过阈值
    - 连接丢失频率过高
    - 错误数量超过阈值
    - 操作耗时超过阈值
    """
    
    def __init__(
        self,
        rule_id: str,
        name: str,
        condition: str,
        threshold: float,
        severity: AlertSeverity,
        description: str = "",
        cooldown_seconds: float = 300.0,
        enabled: bool = True,
    ):
        self.rule_id = rule_id
        self.name = name
        self.condition = condition
        self.threshold = threshold
        self.severity = severity
        self.description = description
        self.cooldown_seconds = cooldown_seconds
        self.enabled = enabled
        
        # 触发状态追踪
        self._last_trigger_time = 0.0
        self._trigger_count = 0
    
    def should_trigger(self, current_value: float) -> bool:
        """判断是否应该触发告警"""
        if not self.enabled:
            return False
        
        # 检查冷却时间
        now = time.time()
        if now - self._last_trigger_time < self.cooldown_seconds:
            return False
        
        # 根据条件判断
        if self.condition == "gt":
            return current_value > self.threshold
        elif self.condition == "gte":
            return current_value >= self.threshold
        elif self.condition == "lt":
            return current_value < self.threshold
        elif self.condition == "lte":
            return current_value <= self.threshold
        elif self.condition == "eq":
            return current_value == self.threshold
        
        return False
    
    def trigger(self):
        """记录告警触发"""
        self._last_trigger_time = time.time()
        self._trigger_count += 1
    
    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "condition": self.condition,
            "threshold": self.threshold,
            "severity": self.severity.value,
            "description": self.description,
            "cooldown_seconds": self.cooldown_seconds,
            "enabled": self.enabled,
            "trigger_count": self._trigger_count,
            "last_trigger_time": self._last_trigger_time,
        }


class AlertNotification:
    """告警通知处理器"""
    
    def __init__(self):
        self._handlers: List[Callable] = []
    
    def register(self, handler: Callable):
        """注册通知处理器"""
        self._handlers.append(handler)
    
    def notify(self, alert: Dict[str, Any]):
        """发送告警通知"""
        for handler in self._handlers:
            try:
                handler(alert)
            except Exception as e:
                logger.error(f"Alert notification handler error: {e}")


class WebhookNotification(AlertNotification):
    """Webhook 通知处理器"""
    
    def __init__(self, webhook_url: str, timeout: float = 10.0):
        super().__init__()
        self.webhook_url = webhook_url
        self.timeout = timeout
        self._http_client = None
    
    async def send(self, alert: Dict[str, Any]):
        """发送 Webhook 通知"""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.webhook_url,
                    json={"alert": alert},
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as response:
                    if response.status >= 400:
                        logger.warning(f"Webhook notification failed: HTTP {response.status}")
                    else:
                        logger.info(f"Webhook notification sent successfully")
        except ImportError:
            logger.warning("aiohttp not installed, skipping webhook notification")
        except Exception as e:
            logger.error(f"Webhook notification error: {e}")
    
    def register(self, handler: Callable):
        """注册异步通知处理器"""
        async def async_wrapper(alert: Dict[str, Any]):
            await self.send(alert)
        super().register(async_wrapper)


class EmailNotification(AlertNotification):
    """邮件通知处理器"""
    
    def __init__(self, smtp_server: str, smtp_port: int, sender: str, password: str, recipients: List[str]):
        super().__init__()
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.sender = sender
        self.password = password
        self.recipients = recipients
    
    def send(self, alert: Dict[str, Any]):
        """发送电子邮件通知"""
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            
            msg = MIMEMultipart()
            msg['From'] = self.sender
            msg['To'] = ', '.join(self.recipients)
            msg['Subject'] = f"[Browser-CDP Alert] {alert['severity']}: {alert['rule_name']}"
            
            body = f"""
告警详情：
- 规则: {alert['rule_name']}
- 级别: {alert['severity']}
- 当前值: {alert['current_value']}
- 阈值: {alert['threshold']}
- 时间: {alert['timestamp']}
- 描述: {alert.get('description', 'N/A')}
"""
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.sender, self.password)
                server.sendmail(self.sender, self.recipients, msg.as_string())
            
            logger.info(f"Email notification sent to {self.recipients}")
        except Exception as e:
            logger.error(f"Email notification error: {e}")
    
    def register(self, handler: Callable):
        """注册同步通知处理器"""
        async def async_wrapper(alert: Dict[str, Any]):
            self.send(alert)
        super().register(async_wrapper)


class AlertManager:
    """
    告警管理器。
    
    管理告警规则、触发告警、发送通知。
    """
    
    def __init__(
        self,
        metrics: Optional[ReliabilityMetrics] = None,
        alert_history_file: Optional[str] = None,
    ):
        self.metrics = metrics or ReliabilityMetrics()
        self._rules: Dict[str, AlertRule] = {}
        self._notifications = AlertNotification()
        self._alert_history: List[Dict[str, Any]] = []
        self._max_history = 1000
        
        # 告警历史文件
        if alert_history_file is None:
            alert_history_file = str(Path(__file__).parent.parent.parent / "logs" / "alerts.jsonl")
        self._alert_history_file = alert_history_file
        
        # 加载历史告警
        self._load_alert_history()
        
        # 注册默认规则
        self._register_default_rules()
    
    def _register_default_rules(self):
        """注册默认告警规则"""
        default_rules = [
            AlertRule(
                rule_id="retry_failure_rate",
                name="重试失败率过高",
                condition="gt",
                threshold=0.3,
                severity=AlertSeverity.WARNING,
                description="重试失败率超过 30%",
                cooldown_seconds=600.0,
            ),
            AlertRule(
                rule_id="connection_loss_rate",
                name="连接丢失率过高",
                condition="gt",
                threshold=0.2,
                severity=AlertSeverity.ERROR,
                description="连接丢失率超过 20%",
                cooldown_seconds=300.0,
            ),
            AlertRule(
                rule_id="error_count",
                name="错误数量过多",
                condition="gt",
                threshold=50,
                severity=AlertSeverity.WARNING,
                description="错误数量超过 50 次",
                cooldown_seconds=300.0,
            ),
            AlertRule(
                rule_id="circuit_breaker_trips",
                name="熔断器频繁触发",
                condition="gt",
                threshold=5,
                severity=AlertSeverity.ERROR,
                description="熔断器触发次数超过 5 次",
                cooldown_seconds=600.0,
            ),
            AlertRule(
                rule_id="operation_duration",
                name="操作耗时过长",
                condition="gt",
                threshold=300.0,
                severity=AlertSeverity.WARNING,
                description="操作平均耗时超过 5 分钟",
                cooldown_seconds=300.0,
            ),
        ]
        
        for rule in default_rules:
            self.add_rule(rule)
    
    def add_rule(self, rule: AlertRule):
        """添加告警规则"""
        self._rules[rule.rule_id] = rule
        logger.info(f"Added alert rule: {rule.name}")
    
    def remove_rule(self, rule_id: str):
        """移除告警规则"""
        if rule_id in self._rules:
            del self._rules[rule_id]
            logger.info(f"Removed alert rule: {rule_id}")
    
    def get_rules(self) -> Dict[str, AlertRule]:
        """获取所有告警规则"""
        return self._rules.copy()
    
    def check_alerts(self) -> List[Dict[str, Any]]:
        """
        检查所有告警规则，触发符合条件的告警。
        
        Returns:
            触发的告警列表
        """
        triggered_alerts = []
        metrics = self.metrics.get_metrics()
        
        # 检查重试失败率（至少有一次重试才检查）
        if "retry_failure_rate" in self._rules and metrics["retry"]["total"] > 0:
            rule = self._rules["retry_failure_rate"]
            retry_failure_rate = 1.0 - metrics["retry"]["success_rate"]
            if rule.should_trigger(retry_failure_rate):
                alert = self._create_alert(rule, retry_failure_rate, "retry_failure_rate")
                triggered_alerts.append(alert)
        
        # 检查连接丢失率
        if "connection_loss_rate" in self._rules:
            rule = self._rules["connection_loss_rate"]
            connection_losses = metrics["connection"]["losses"]
            if rule.should_trigger(connection_losses):
                alert = self._create_alert(rule, connection_losses, "connection_loss_rate")
                triggered_alerts.append(alert)
        
        # 检查错误数量
        if "error_count" in self._rules:
            rule = self._rules["error_count"]
            total_errors = sum(metrics["errors_by_category"].values())
            if rule.should_trigger(total_errors):
                alert = self._create_alert(rule, total_errors, "error_count")
                triggered_alerts.append(alert)
        
        # 检查熔断器触发次数
        if "circuit_breaker_trips" in self._rules:
            rule = self._rules["circuit_breaker_trips"]
            trips = metrics["circuit_breaker"]["trips"]
            if rule.should_trigger(trips):
                alert = self._create_alert(rule, trips, "circuit_breaker_trips")
                triggered_alerts.append(alert)
        
        # 检查操作耗时
        if "operation_duration" in self._rules:
            rule = self._rules["operation_duration"]
            for op, stats in metrics["operation_durations"].items():
                if rule.should_trigger(stats["avg"]):
                    alert = self._create_alert(rule, stats["avg"], f"operation_duration:{op}")
                    triggered_alerts.append(alert)
        
        # 发送通知
        for alert in triggered_alerts:
            self._notifications.notify(alert)
            self._alert_history.append(alert)
            self._save_alert_history()
            logger.warning(f"Alert triggered: {alert['rule_name']} - {alert['severity']}")
        
        return triggered_alerts
    
    def _create_alert(
        self,
        rule: AlertRule,
        current_value: float,
        metric_name: str,
    ) -> Dict[str, Any]:
        """创建告警记录"""
        alert = {
            "alert_id": f"{rule.rule_id}_{int(time.time())}",
            "rule_id": rule.rule_id,
            "rule_name": rule.name,
            "severity": rule.severity.value,
            "metric_name": metric_name,
            "current_value": current_value,
            "threshold": rule.threshold,
            "timestamp": datetime.fromtimestamp(time.time()).isoformat(),
            "description": rule.description,
        }
        rule.trigger()
        return alert
    
    def get_alert_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取告警历史"""
        return self._alert_history[-limit:]
    
    def get_alert_stats(self) -> Dict[str, Any]:
        """获取告警统计"""
        stats = {
            "total_alerts": len(self._alert_history),
            "by_severity": {},
            "by_rule": {},
            "recent_alerts": self.get_alert_history(10),
        }
        
        for alert in self._alert_history:
            severity = alert["severity"]
            rule_id = alert["rule_id"]
            
            stats["by_severity"][severity] = stats["by_severity"].get(severity, 0) + 1
            stats["by_rule"][rule_id] = stats["by_rule"].get(rule_id, 0) + 1
        
        return stats
    
    def _load_alert_history(self):
        """加载告警历史"""
        path = Path(self._alert_history_file)
        if not path.exists():
            return
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            alert = json.loads(line)
                            self._alert_history.append(alert)
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            logger.warning(f"Failed to load alert history: {e}")
    
    def _save_alert_history(self):
        """保存告警历史"""
        try:
            Path(self._alert_history_file).parent.mkdir(parents=True, exist_ok=True)
            with open(self._alert_history_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(self._alert_history[-1], ensure_ascii=False) + '\n')
            
            # 限制历史文件大小
            if len(self._alert_history) > self._max_history:
                self._alert_history = self._alert_history[-self._max_history:]
        except Exception as e:
            logger.warning(f"Failed to save alert history: {e}")
    
    def register_notification(self, notification: AlertNotification):
        """注册告警通知处理器"""
        self._notifications = notification


# 全局告警管理器实例
_global_alert_manager: Optional[AlertManager] = None


def get_alert_manager(metrics: Optional[ReliabilityMetrics] = None) -> AlertManager:
    """获取告警管理器实例"""
    global _global_alert_manager
    if _global_alert_manager is None:
        _global_alert_manager = AlertManager(metrics)
    return _global_alert_manager


def reset_alert_manager():
    """重置全局告警管理器"""
    global _global_alert_manager
    _global_alert_manager = None
