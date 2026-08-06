# Browser-CDP 可靠性监控体系使用指南

## 概述

Browser-CDP 可靠性监控体系提供完整的操作日志、指标收集和告警通知能力，帮助开发者实时监控浏览器操作的稳定性和可靠性。

## 核心组件

### 1. 指标收集 (ReliabilityMetrics)

收集以下关键指标：
- **重试统计**: 总重试次数、成功/失败次数、成功率
- **熔断器**: 触发次数、重置次数
- **连接状态**: 丢失次数、恢复次数、恢复率
- **错误分类**: 按类别和类型统计错误
- **等待策略**: 各策略的成功/失败次数
- **操作耗时**: 各操作的平均/最小/最大耗时

```python
from src.reliability.metrics import get_metrics

metrics = get_metrics()
metrics.record_retry(success=True)
metrics.record_error("timeout", "CDPCommandTimeoutError", "Test error")

# 获取指标快照
metrics_data = metrics.get_metrics()
print(metrics_data["retry"])
```

### 2. 操作日志 (OperationLogger)

提供结构化的 JSON 格式日志记录：
- 操作开始/结束记录
- 错误追踪和上下文关联
- 日志文件轮转（默认 10MB × 5 个备份）

```python
from src.reliability.logging import get_logger

logger = get_logger()
logger.operation_start("search", {"site": "zhihu"})
# ... 执行操作 ...
logger.operation_end("search", duration=2.5, success=True)
logger.operation_error("search", Exception("Timeout"), {"site": "zhihu"})
```

### 3. 告警系统 (AlertManager)

基于阈值的告警规则，支持多种通知渠道：

**默认告警规则：**
| 规则 ID | 名称 | 条件 | 阈值 | 级别 |
|---------|------|------|------|------|
| retry_failure_rate | 重试失败率过高 | > | 30% | warning |
| connection_loss_rate | 连接丢失率过高 | > | 20% | error |
| error_count | 错误数量过多 | > | 50 | warning |
| circuit_breaker_trips | 熔断器频繁触发 | > | 5 | error |
| operation_duration | 操作耗时过长 | > | 300s | warning |
| captcha_detected | 检测到验证码 | > | 3 | critical |
| anti_bot_detected | 被反爬机制拦截 | > | 1 | critical |

```python
from src.reliability.alert import get_alert_manager

alert_manager = get_alert_manager()

# 检查告警
alerts = alert_manager.check_alerts()

# 获取告警历史
history = alert_manager.get_alert_history(limit=50)

# 获取告警统计
stats = alert_manager.get_alert_stats()
```

**自定义告警规则：**
```python
from src.reliability.alert import AlertRule, AlertSeverity

rule = AlertRule(
    rule_id="custom_rule",
    name="自定义告警",
    condition="gt",
    threshold=100.0,
    severity=AlertSeverity.CRITICAL,
    description="自定义描述",
    cooldown_seconds=300.0,
)
alert_manager.add_rule(rule)
```

### 4. 日志查询 (LogQuery)

支持按时间范围、操作类型、错误分类等维度查询日志：

```python
from src.reliability.log_query import get_log_query
from datetime import datetime, timedelta

query = get_log_query()

# 查询最近 1 小时的日志
start_time = datetime.now() - timedelta(hours=1)
logs = query.query(start_time=start_time, limit=100)

# 聚合统计
stats = query.aggregate(group_by="hour")

# 错误摘要
error_summary = query.get_error_summary(hours=24)

# 操作统计
op_stats = query.get_operation_stats(hours=24)
```

### 5. 监控面板 (ReliabilityDashboard)

提供多种格式的监控面板输出：

**文本面板（终端显示）：**
```python
from src.reliability.dashboard import get_dashboard

dashboard = get_dashboard()
print(dashboard.get_text_panel())
```

**HTML 面板（浏览器查看）：**
```python
# 生成 HTML 面板
dashboard = get_dashboard()
html = dashboard.get_html_panel()

# 保存到文件
dashboard.save_html("dashboard.html")
```

**JSON 面板（API 集成）：**
```python
json_data = dashboard.get_json_panel()
```

## 配置告警通知

### Webhook 通知

```python
from src.reliability.alert import WebhookNotification

webhook = WebhookNotification(
    webhook_url="https://your-webhook-url.com/alerts",
    timeout=10.0,
)
alert_manager.register_notification(webhook)
```

### 邮件通知

```python
from src.reliability.alert import EmailNotification

email = EmailNotification(
    smtp_server="smtp.example.com",
    smtp_port=587,
    sender="alerts@example.com",
    password="your_password",
    recipients=["admin@example.com"],
)
alert_manager.register_notification(email)
```

## 配置文件

告警规则配置文件位于 `config/alert_rules.json`，支持自定义规则：

```json
{
  "version": "1.1.0",
  "rules": [
    {
      "rule_id": "retry_failure_rate",
      "name": "重试失败率过高",
      "condition": "gt",
      "threshold": 0.3,
      "severity": "warning",
      "cooldown_seconds": 600.0,
      "enabled": true
    }
  ]
}
```

## 使用示例

### 完整监控流程

```python
import asyncio
from src.reliability.metrics import get_metrics
from src.reliability.alert import get_alert_manager
from src.reliability.dashboard import get_dashboard
from src.reliability.logging import get_logger

async def main():
    # 获取各组件实例
    metrics = get_metrics()
    alert_manager = get_alert_manager(metrics)
    dashboard = get_dashboard(metrics, alert_manager=alert_manager)
    logger = get_logger()
    
    # 模拟操作
    logger.operation_start("search", {"site": "zhihu"})
    try:
        # 执行搜索操作
        for i in range(10):
            metrics.record_operation_duration("search", 2.5)
            metrics.record_retry(success=True)
        
        logger.operation_end("search", duration=25.0, success=True)
    except Exception as e:
        logger.operation_error("search", e, {"site": "zhihu"})
    
    # 检查告警
    alerts = alert_manager.check_alerts()
    if alerts:
        print(f"触发 {len(alerts)} 个告警")
    
    # 输出监控面板
    print(dashboard.get_text_panel())

asyncio.run(main())
```

### 定期监控任务

```python
import asyncio
from src.reliability.alert import get_alert_manager
from src.reliability.log_query import get_log_query

async def periodic_monitoring():
    alert_manager = get_alert_manager()
    log_query = get_log_query()
    
    while True:
        # 检查告警
        alerts = alert_manager.check_alerts()
        
        # 获取错误摘要
        error_summary = log_query.get_error_summary(hours=1)
        
        if alerts or error_summary["total_errors"] > 0:
            print(f"告警: {len(alerts)}, 错误: {error_summary['total_errors']}")
        
        await asyncio.sleep(60)  # 每分钟检查一次

asyncio.run(periodic_monitoring())
```

## 日志文件位置

- 操作日志: `logs/browser_cdp.log` (自动轮转)
- 告警历史: `logs/alerts.jsonl`

## 测试

运行监控体系测试：

```bash
cd .claude/skills/browser-cdp
python -m pytest tests/evaluation/test_reliability_monitoring.py -v
```

## 版本历史

- v1.1.0: 新增告警系统和日志查询模块
- v1.0.0: 初始版本，包含指标收集和监控面板
