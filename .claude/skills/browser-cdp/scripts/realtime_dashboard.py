#!/usr/bin/env python3
"""
实时监控看板

提供浏览器抓取系统的实时监控能力：
- 实时成功率监控
- 网站健康状态展示
- 告警状态追踪
- 1 小时问题发现保障
"""

import json
import time
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class RealtimeDashboard:
    """
    实时监控看板。
    
    提供：
    - 实时成功率监控
    - 网站健康状态展示
    - 告警状态追踪
    - 1 小时问题发现保障
    """
    
    # 1 小时发现目标
    DETECTION_TARGET_SECONDS = 3600
    
    def __init__(
        self,
        data_dir: Optional[str] = None,
        check_interval: float = 60.0,
    ):
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "data" / "monitoring")
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.check_interval = check_interval
        
        # 指标存储
        self._metrics: Dict[str, Any] = {}
        self._website_status: Dict[str, Dict[str, Any]] = {}
        self._alerts: List[Dict[str, Any]] = []
        self._detection_log: List[Dict[str, Any]] = []
        
        # 监控状态
        self._running = False
        self._thread: Optional[threading.Thread] = None
        
        # 加载历史数据
        self._load_history()
    
    def _load_history(self):
        """加载历史数据"""
        # 加载指标
        metrics_file = self.data_dir / "metrics.json"
        if metrics_file.exists():
            try:
                with open(metrics_file, 'r', encoding='utf-8') as f:
                    self._metrics = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load metrics: {e}")
        
        # 加载网站状态
        status_file = self.data_dir / "website_status.json"
        if status_file.exists():
            try:
                with open(status_file, 'r', encoding='utf-8') as f:
                    self._website_status = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load website status: {e}")
        
        # 加载告警历史
        alerts_file = self.data_dir / "alerts.jsonl"
        if alerts_file.exists():
            try:
                with open(alerts_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                self._alerts.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
            except Exception as e:
                logger.warning(f"Failed to load alerts: {e}")
    
    def _save_metrics(self):
        """保存指标"""
        metrics_file = self.data_dir / "metrics.json"
        try:
            with open(metrics_file, 'w', encoding='utf-8') as f:
                json.dump(self._metrics, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save metrics: {e}")
    
    def _save_website_status(self):
        """保存网站状态"""
        status_file = self.data_dir / "website_status.json"
        try:
            with open(status_file, 'w', encoding='utf-8') as f:
                json.dump(self._website_status, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save website status: {e}")
    
    def _save_alert(self, alert: Dict[str, Any]):
        """保存告警"""
        alerts_file = self.data_dir / "alerts.jsonl"
        try:
            with open(alerts_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(alert, ensure_ascii=False) + '\n')
            self._alerts.append(alert)
            # 只保留最近 1000 条
            self._alerts = self._alerts[-1000:]
        except Exception as e:
            logger.error(f"Failed to save alert: {e}")
    
    def update_metric(self, metric_name: str, value: float):
        """更新指标"""
        self._metrics[metric_name] = {
            "value": value,
            "timestamp": datetime.now().isoformat(),
        }
        self._save_metrics()
    
    def update_website_status(self, website: str, status: Dict[str, Any]):
        """更新网站状态"""
        self._website_status[website] = {
            **status,
            "last_update": datetime.now().isoformat(),
        }
        self._save_website_status()
    
    def check_alerts(self) -> List[Dict[str, Any]]:
        """检查告警条件"""
        triggered = []
        now = datetime.now()
        
        # 检查成功率
        success_rate = self._metrics.get("success_rate", {}).get("value", 1.0)
        if success_rate < 0.70:
            alert = self._create_alert(
                "success_rate_critical",
                "成功率严重低于阈值",
                "critical",
                success_rate,
                0.70,
            )
            triggered.append(alert)
        elif success_rate < 0.85:
            alert = self._create_alert(
                "success_rate_warning",
                "成功率低于预期",
                "warning",
                success_rate,
                0.85,
            )
            triggered.append(alert)
        
        # 检查网站健康状态
        for website, status in self._website_status.items():
            if status.get("consecutive_failures", 0) >= 3:
                alert = self._create_alert(
                    f"website_failure:{website}",
                    f"{website} 连续失败",
                    "error",
                    status.get("consecutive_failures", 0),
                    3,
                )
                triggered.append(alert)
        
        # 检查问题发现时间
        if self._alerts:
            latest_alert = self._alerts[-1]
            alert_time = datetime.fromisoformat(latest_alert["timestamp"])
            detection_time = (now - alert_time).total_seconds()
            
            if detection_time > self.DETECTION_TARGET_SECONDS:
                alert = self._create_alert(
                    "detection_delay",
                    "问题发现时间超过 1 小时",
                    "critical",
                    detection_time,
                    self.DETECTION_TARGET_SECONDS,
                )
                triggered.append(alert)
                
                # 记录发现时间
                self._detection_log.append({
                    "timestamp": now.isoformat(),
                    "detection_time_seconds": detection_time,
                    "within_target": detection_time <= self.DETECTION_TARGET_SECONDS,
                })
        
        # 保存新告警
        for alert in triggered:
            self._save_alert(alert)
        
        return triggered
    
    def _create_alert(
        self,
        alert_id: str,
        message: str,
        severity: str,
        current_value: float,
        threshold: float,
    ) -> Dict[str, Any]:
        """创建告警记录"""
        return {
            "alert_id": alert_id,
            "message": message,
            "severity": severity,
            "current_value": current_value,
            "threshold": threshold,
            "timestamp": datetime.now().isoformat(),
            "acknowledged": False,
            "resolved": False,
        }
    
    def get_dashboard_data(self) -> Dict[str, Any]:
        """获取看板数据"""
        # 计算汇总指标
        total_tests = sum(
            s.get("total_tests", 0) for s in self._website_status.values()
        )
        passed_tests = sum(
            s.get("passed_tests", 0) for s in self._website_status.values()
        )
        overall_success_rate = passed_tests / total_tests if total_tests > 0 else 0
        
        # 网站健康统计
        healthy_websites = sum(
            1 for s in self._website_status.values()
            if s.get("status") == "healthy"
        )
        unhealthy_websites = sum(
            1 for s in self._website_status.values()
            if s.get("status") == "unhealthy"
        )
        
        # 待处理告警
        pending_alerts = [
            a for a in self._alerts
            if not a.get("acknowledged") and not a.get("resolved")
        ]
        
        # 最近发现时间统计
        recent_detections = self._detection_log[-10:] if self._detection_log else []
        avg_detection_time = (
            sum(d["detection_time_seconds"] for d in recent_detections) / len(recent_detections)
            if recent_detections
            else 0
        )
        
        return {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total_websites": len(self._website_status),
                "healthy_websites": healthy_websites,
                "unhealthy_websites": unhealthy_websites,
                "overall_success_rate": overall_success_rate,
                "total_tests": total_tests,
                "passed_tests": passed_tests,
            },
            "metrics": {
                "success_rate": self._metrics.get("success_rate", {}).get("value", overall_success_rate),
                "error_rate": 1 - overall_success_rate,
                "avg_detection_time": avg_detection_time,
                "detection_target_met": avg_detection_time <= self.DETECTION_TARGET_SECONDS,
            },
            "websites": self._website_status,
            "alerts": {
                "total": len(self._alerts),
                "pending": len(pending_alerts),
                "recent": self._alerts[-10:],
            },
            "detection_log": recent_detections,
        }
    
    def generate_html_report(self, output_path: Optional[str] = None) -> str:
        """生成 HTML 报告"""
        data = self.get_dashboard_data()
        
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Browser-CDP 实时监控看板</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f7fa; padding: 20px; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        h1 {{ color: #333; margin-bottom: 20px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 20px; }}
        .card {{ background: white; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
        .card h2 {{ color: #555; font-size: 16px; margin-bottom: 15px; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        .metric {{ display: flex; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid #f0f0f0; }}
        .metric:last-child {{ border-bottom: none; }}
        .metric-label {{ color: #666; }}
        .metric-value {{ font-weight: bold; color: #333; }}
        .metric-value.success {{ color: #28a745; }}
        .metric-value.warning {{ color: #ffc107; }}
        .metric-value.danger {{ color: #dc3545; }}
        .timestamp {{ color: #999; font-size: 12px; margin-top: 20px; text-align: center; }}
        .status-badge {{ display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: bold; }}
        .status-badge.healthy {{ background: #d4edda; color: #155724; }}
        .status-badge.unhealthy {{ background: #f8d7da; color: #721c24; }}
        .status-badge.unknown {{ background: #fff3cd; color: #856404; }}
        .alert-item {{ padding: 12px; margin-bottom: 10px; border-radius: 8px; font-size: 14px; }}
        .alert-item.critical {{ background: #f8d7da; border-left: 4px solid #dc3545; }}
        .alert-item.error {{ background: #fff3cd; border-left: 4px solid #ffc107; }}
        .alert-item.warning {{ background: #e7f3ff; border-left: 4px solid #007bff; }}
        .progress-bar {{ height: 8px; background: #e9ecef; border-radius: 4px; overflow: hidden; margin-top: 8px; }}
        .progress-bar-fill {{ height: 100%; background: #28a745; transition: width 0.3s; }}
        .progress-bar-fill.warning {{ background: #ffc107; }}
        .progress-bar-fill.danger {{ background: #dc3545; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🛡️ Browser-CDP 实时监控看板</h1>
        <p class="timestamp">更新时间：{data['timestamp']}</p>
        
        <div class="grid">
            <div class="card">
                <h2>📊 总体概况</h2>
                <div class="metric">
                    <span class="metric-label">监控网站数</span>
                    <span class="metric-value">{data['summary']['total_websites']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">健康网站</span>
                    <span class="metric-value success">{data['summary']['healthy_websites']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">异常网站</span>
                    <span class="metric-value {'danger' if data['summary']['unhealthy_websites'] > 0 else 'success'}">{data['summary']['unhealthy_websites']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">总测试数</span>
                    <span class="metric-value">{data['summary']['total_tests']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">通过测试</span>
                    <span class="metric-value success">{data['summary']['passed_tests']}</span>
                </div>
            </div>
            
            <div class="card">
                <h2>🎯 成功率监控</h2>
                <div class="metric">
                    <span class="metric-label">当前成功率</span>
                    <span class="metric-value {'success' if data['metrics']['success_rate'] >= 0.85 else 'warning' if data['metrics']['success_rate'] >= 0.70 else 'danger'}">{data['metrics']['success_rate']:.1%}</span>
                </div>
                <div class="progress-bar">
                    <div class="progress-bar-fill {'warning' if data['metrics']['success_rate'] < 0.85 else 'danger' if data['metrics']['success_rate'] < 0.70}"
                         style="width: {data['metrics']['success_rate'] * 100:.1f}%"></div>
                </div>
                <div class="metric" style="margin-top: 15px;">
                    <span class="metric-label">错误率</span>
                    <span class="metric-value {'danger' if data['metrics']['error_rate'] > 0.3 else 'warning'}">{data['metrics']['error_rate']:.1%}</span>
                </div>
            </div>
            
            <div class="card">
                <h2>⏱️ 问题发现时间</h2>
                <div class="metric">
                    <span class="metric-label">平均发现时间</span>
                    <span class="metric-value {'success' if data['metrics']['avg_detection_time'] <= 3600 else 'danger'}">{data['metrics']['avg_detection_time'] / 60:.1f} 分钟</span>
                </div>
                <div class="metric">
                    <span class="metric-label">目标阈值</span>
                    <span class="metric-value">60 分钟</span>
                </div>
                <div class="metric">
                    <span class="metric-label">目标达成</span>
                    <span class="metric-value {'success' if data['metrics']['detection_target_met'] else 'danger'}">{'✅ 是' if data['metrics']['detection_target_met'] else '❌ 否'}</span>
                </div>
            </div>
            
            <div class="card">
                <h2>🔔 告警状态</h2>
                <div class="metric">
                    <span class="metric-label">总告警数</span>
                    <span class="metric-value">{data['alerts']['total']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">待处理告警</span>
                    <span class="metric-value {'danger' if data['alerts']['pending'] > 0 else 'success'}">{data['alerts']['pending']}</span>
                </div>
            </div>
        </div>
        
        <div class="grid">
            <div class="card" style="grid-column: span 2;">
                <h2>🌐 网站健康状态</h2>
                <div style="max-height: 300px; overflow-y: auto;">
                    {''.join(f'''
                    <div class="metric">
                        <span class="metric-label">{website}</span>
                        <span class="status-badge {status.get('status', 'unknown')}">{status.get('status', 'unknown').title()}</span>
                        <span class="metric-value {'success' if status.get('success_rate', 0) >= 0.85 else 'warning' if status.get('success_rate', 0) >= 0.70 else 'danger'}">{status.get('success_rate', 0):.1%}</span>
                    </div>''') for website, status in data['websites'].items()}
                </div>
            </div>
            
            <div class="card">
                <h2>🚨 最近告警</h2>
                <div style="max-height: 300px; overflow-y: auto;">
                    {''.join(f'''
                    <div class="alert-item {alert['severity']}">
                        <div style="font-weight: bold;">{alert['message']}</div>
                        <div style="font-size: 12px; color: #666; margin-top: 4px;">{alert['timestamp'][:19]}</div>
                    </div>''') for alert in data['alerts']['recent']}
                    {'' if data['alerts']['recent'] else '<div style="color: #999; text-align: center; padding: 20px;">暂无告警</div>'}
                </div>
            </div>
        </div>
        
        <p class="timestamp">Browser-CDP 监控系统 | 问题发现目标：≤ 60 分钟</p>
    </div>
</body>
</html>"""
        
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(html)
            logger.info(f"Dashboard HTML saved to {output_path}")
        
        return html
    
    def start_monitoring(self):
        """启动监控"""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("Realtime dashboard monitoring started")
    
    def _monitor_loop(self):
        """监控循环"""
        logger.info("Dashboard monitor loop started")
        
        while self._running:
            try:
                # 检查告警
                alerts = self.check_alerts()
                if alerts:
                    logger.warning(f"Dashboard alerts triggered: {len(alerts)}")
            except Exception as e:
                logger.error(f"Dashboard monitor error: {e}")
            
            time.sleep(self.check_interval)
        
        logger.info("Dashboard monitor loop stopped")
    
    def stop_monitoring(self):
        """停止监控"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Dashboard monitoring stopped")


# 全局看板实例
_global_dashboard: Optional[RealtimeDashboard] = None


def get_dashboard(data_dir: Optional[str] = None) -> RealtimeDashboard:
    """获取监控看板实例"""
    global _global_dashboard
    if _global_dashboard is None:
        _global_dashboard = RealtimeDashboard(data_dir)
    return _global_dashboard


def start_dashboard_monitoring(data_dir: Optional[str] = None):
    """启动监控看板"""
    dashboard = get_dashboard(data_dir)
    dashboard.start_monitoring()
    return dashboard


def stop_dashboard_monitoring():
    """停止监控看板"""
    global _global_dashboard
    if _global_dashboard:
        _global_dashboard.stop_monitoring()
        _global_dashboard = None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # 启动监控
    dashboard = start_dashboard_monitoring()
    
    print("Browser-CDP Realtime Dashboard started. Press Ctrl+C to stop.")
    
    try:
        while True:
            time.sleep(60)
            data = dashboard.get_dashboard_data()
            print(f"\n{'='*60}")
            print(f"Success Rate: {data['metrics']['success_rate']:.1%}")
            print(f"Healthy Websites: {data['summary']['healthy_websites']}/{data['summary']['total_websites']}")
            print(f"Pending Alerts: {data['alerts']['pending']}")
            print(f"{'='*60}\n")
    except KeyboardInterrupt:
        print("Stopping dashboard...")
        stop_dashboard_monitoring()
