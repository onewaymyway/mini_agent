"""
监控面板

提供可靠性指标的可视化面板，支持：
- 文本面板（终端显示）
- HTML 面板（浏览器查看）
- JSON 面板（API 集成）
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .metrics import ReliabilityMetrics
from .logging import OperationLogger
from .alert import AlertManager, AlertSeverity


class ReliabilityDashboard:
    """
    可靠性监控面板。
    
    提供多种格式的监控面板输出：
    - text: 终端文本面板
    - html: HTML 面板（可嵌入网页）
    - json: JSON 面板（API 集成）
    """
    
    def __init__(
        self,
        metrics: Optional[ReliabilityMetrics] = None,
        logger: Optional[OperationLogger] = None,
        alert_manager: Optional[AlertManager] = None,
    ):
        self.metrics = metrics or ReliabilityMetrics()
        self.logger = logger or OperationLogger()
        self.alert_manager = alert_manager
    
    def get_text_panel(self) -> str:
        """生成文本面板（终端显示）"""
        m = self.metrics.get_metrics()
        
        lines = [
            "=" * 60,
            "  Browser-CDP 可靠性监控面板",
            f"  时间: {m['timestamp']}",
            f"  运行时长: {m['uptime_seconds']:.1f}s",
            "=" * 60,
            "",
            "【重试统计】",
            f"  总重试次数: {m['retry']['total']}",
            f"  成功次数:   {m['retry']['success']}",
            f"  失败次数:   {m['retry']['failure']}",
            f"  成功率:     {m['retry']['success_rate']:.1%}",
            "",
            "【熔断器】",
            f"  触发次数:   {m['circuit_breaker']['trips']}",
            f"  重置次数:   {m['circuit_breaker']['resets']}",
            "",
            "【连接状态】",
            f"  丢失次数:   {m['connection']['losses']}",
            f"  恢复次数:   {m['connection']['recovered']}",
            f"  恢复率:     {m['connection']['recovery_rate']:.1%}",
            "",
            "【错误分类】",
        ]
        
        for category, count in m["errors_by_category"].items():
            lines.append(f"  {category:15s}: {count}")
        
        lines.append("")
        lines.append("【等待策略】")
        for strategy, count in m["wait_strategies"]["success"].items():
            failures = m["wait_strategies"]["failure"].get(strategy, 0)
            total = count + failures
            rate = count / total * 100 if total > 0 else 0
            lines.append(f"  {strategy:20s}: {count}/{total} ({rate:.0f}%)")
        
        lines.append("")
        lines.append("【操作耗时】")
        for op, stats in m["operation_durations"].items():
            lines.append(f"  {op:20s}: avg={stats['avg']:.2f}s, min={stats['min']:.2f}s, max={stats['max']:.2f}s")
        
        lines.append("")
        lines.append("【最近错误】")
        for error in m["recent_errors"][-5:]:
            lines.append(f"  [{error['timestamp']}] {error['error_type']}: {error['message'][:50]}")
        
        # 告警状态
        lines.append("")
        lines.append("【告警状态】")
        if self.alert_manager:
            alert_stats = self.alert_manager.get_alert_stats()
            lines.append(f"  总告警数: {alert_stats['total_alerts']}")
            for severity, count in alert_stats['by_severity'].items():
                lines.append(f"  {severity:10s}: {count}")
            recent = alert_stats.get('recent_alerts', [])
            if recent:
                lines.append("  最近告警:")
                for alert in recent[-3:]:
                    lines.append(f"    [{alert['severity']}] {alert['rule_name']}: {alert['current_value']}")
        else:
            lines.append("  未配置告警管理器")
        
        lines.append("")
        lines.append("=" * 60)
        
        return "\n".join(lines)
    
    def get_html_panel(self) -> str:
        """生成 HTML 面板"""
        m = self.metrics.get_metrics()
        
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Browser-CDP 可靠性监控面板</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; padding: 20px; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        h1 {{ color: #333; margin-bottom: 20px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }}
        .card {{ background: white; border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .card h2 {{ color: #555; font-size: 16px; margin-bottom: 15px; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        .metric {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #f0f0f0; }}
        .metric:last-child {{ border-bottom: none; }}
        .metric-label {{ color: #666; }}
        .metric-value {{ font-weight: bold; color: #333; }}
        .metric-value.success {{ color: #28a745; }}
        .metric-value.warning {{ color: #ffc107; }}
        .metric-value.danger {{ color: #dc3545; }}
        .timestamp {{ color: #999; font-size: 12px; margin-top: 20px; text-align: center; }}
        .error-list {{ max-height: 200px; overflow-y: auto; }}
        .error-item {{ padding: 8px; margin-bottom: 8px; background: #fff3f3; border-radius: 4px; font-size: 12px; }}
        .error-item .time {{ color: #999; }}
        .error-item .type {{ color: #dc3545; font-weight: bold; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🛡️ Browser-CDP 可靠性监控面板</h1>
        <p class="timestamp">更新时间: {m['timestamp']} | 运行时长: {m['uptime_seconds']:.1f}s</p>
        <div class="grid">
            <div class="card">
                <h2>🔄 重试统计</h2>
                <div class="metric">
                    <span class="metric-label">总重试次数</span>
                    <span class="metric-value">{m['retry']['total']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">成功次数</span>
                    <span class="metric-value success">{m['retry']['success']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">失败次数</span>
                    <span class="metric-value {'danger' if m['retry']['failure'] > 0 else 'success'}">{m['retry']['failure']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">成功率</span>
                    <span class="metric-value {'success' if m['retry']['success_rate'] > 0.8 else 'warning' if m['retry']['success_rate'] > 0.5 else 'danger'}">{m['retry']['success_rate']:.1%}</span>
                </div>
            </div>
            <div class="card">
                <h2>⚡ 熔断器</h2>
                <div class="metric">
                    <span class="metric-label">触发次数</span>
                    <span class="metric-value {'danger' if m['circuit_breaker']['trips'] > 0 else 'success'}">{m['circuit_breaker']['trips']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">重置次数</span>
                    <span class="metric-value">{m['circuit_breaker']['resets']}</span>
                </div>
            </div>
            <div class="card">
                <h2>🔌 连接状态</h2>
                <div class="metric">
                    <span class="metric-label">丢失次数</span>
                    <span class="metric-value {'danger' if m['connection']['losses'] > 0 else 'success'}">{m['connection']['losses']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">恢复次数</span>
                    <span class="metric-value success">{m['connection']['recovered']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">恢复率</span>
                    <span class="metric-value {'success' if m['connection']['recovery_rate'] > 0.8 else 'warning'}">{m['connection']['recovery_rate']:.1%}</span>
                </div>
            </div>
            <div class="card">
                <h2>📊 错误分类</h2>
                {''.join(f'<div class="metric"><span class="metric-label">{cat}</span><span class="metric-value">{count}</span></div>' for cat, count in m['errors_by_category'].items()) if m['errors_by_category'] else '<div class="metric"><span class="metric-label">暂无错误</span><span class="metric-value success">0</span></div>'}
            </div>
            <div class="card">
                <h2>⏱️ 等待策略</h2>
                {''.join(f'<div class="metric"><span class="metric-label">{s}</span><span class="metric-value">{count}/{count + m["wait_strategies"]["failure"].get(s, 0)}</span></div>' for s, count in m['wait_strategies']['success'].items()) if m['wait_strategies']['success'] else '<div class="metric"><span class="metric-label">暂无数据</span><span class="metric-value">-</span></div>'}
            </div>
            <div class="card">
                <h2>📈 操作耗时</h2>
                {''.join(f'<div class="metric"><span class="metric-label">{op}</span><span class="metric-value">avg={stats["avg"]:.2f}s</span></div>' for op, stats in m['operation_durations'].items()) if m['operation_durations'] else '<div class="metric"><span class="metric-label">暂无数据</span><span class="metric-value">-</span></div>'}
            </div>
            <div class="card" style="grid-column: span 2;">
                <h2>🚨 最近错误</h2>
                <div class="error-list">
                    {''.join(f'<div class="error-item"><span class="time">{e["timestamp"]}</span> <span class="type">{e["error_type"]}</span>: {e["message"][:80]}</div>' for e in m['recent_errors']) if m['recent_errors'] else '<div class="error-item">暂无错误</div>'}
                </div>
            </div>
            <div class="card" style="grid-column: span 2;">
                <h2>🔔 告警状态</h2>
                {'<div class="metric"><span class="metric-label">总告警数</span><span class="metric-value">0</span></div><div class="metric"><span class="metric-label">状态</span><span class="metric-value success">正常</span></div>' if not self.alert_manager else ''.join(f'<div class="metric"><span class="metric-label">{k}</span><span class="metric-value">{v}</span></div>' for k, v in self.alert_manager.get_alert_stats().items())}
            </div>
        </div>
    </div>
</body>
</html>"""
        
        return html
    
    def get_json_panel(self) -> str:
        """生成 JSON 面板"""
        return self.metrics.to_json(pretty=True)
    
    def save_html(self, path: str):
        """保存 HTML 面板到文件"""
        html = self.get_html_panel()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(html, encoding="utf-8")
        self.logger.info(f"Dashboard saved to {path}")
    
    def print_panel(self, format: str = "text"):
        """打印面板到控制台"""
        if format == "text":
            print(self.get_text_panel())
        elif format == "html":
            print("HTML panel saved to dashboard.html")
            self.save_html("dashboard.html")
        elif format == "json":
            print(self.get_json_panel())


# 全局面板实例
_global_dashboard: Optional[ReliabilityDashboard] = None


def get_dashboard(
    metrics: Optional[ReliabilityMetrics] = None,
    logger: Optional[OperationLogger] = None,
    alert_manager: Optional[AlertManager] = None,
) -> ReliabilityDashboard:
    """获取监控面板实例"""
    global _global_dashboard
    if _global_dashboard is None:
        _global_dashboard = ReliabilityDashboard(metrics, logger, alert_manager)
    return _global_dashboard


def reset_dashboard():
    """重置全局面板实例"""
    global _global_dashboard
    _global_dashboard = None
