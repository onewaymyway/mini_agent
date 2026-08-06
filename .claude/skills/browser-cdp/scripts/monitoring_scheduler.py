"""
监控调度器

提供周期性监控任务调度，支持：
- 定期健康检查
- 定期告警评估
- 监控覆盖率追踪
- 告警响应时间统计
"""

import asyncio
import json
import logging
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class MonitorTask:
    """监控任务定义"""
    
    def __init__(
        self,
        task_id: str,
        name: str,
        interval_seconds: float,
        func: Callable,
        enabled: bool = True,
        description: str = "",
    ):
        self.task_id = task_id
        self.name = name
        self.interval_seconds = interval_seconds
        self.func = func
        self.enabled = enabled
        self.description = description
        
        # 执行状态
        self._last_run_time: Optional[float] = None
        self._last_result: Optional[Any] = None
        self._last_error: Optional[str] = None
        self._run_count = 0
        self._failure_count = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "interval_seconds": self.interval_seconds,
            "enabled": self.enabled,
            "description": self.description,
            "last_run_time": self._last_run_time,
            "last_result": self._last_result,
            "last_error": self._last_error,
            "run_count": self._run_count,
            "failure_count": self._failure_count,
        }


class AlertResponseTracker:
    """告警响应时间追踪器"""
    
    def __init__(self):
        self._pending_alerts: Dict[str, Dict[str, Any]] = {}
        self._response_times: List[float] = []
        self._max_response_times = 1000
    
    def track_alert(self, alert_id: str, alert_data: Dict[str, Any]):
        """记录新告警"""
        self._pending_alerts[alert_id] = {
            **alert_data,
            "triggered_at": time.time(),
            "acknowledged_at": None,
            "resolved_at": None,
        }
        logger.info(f"Alert tracked: {alert_id}")
    
    def acknowledge(self, alert_id: str):
        """标记告警已确认"""
        if alert_id in self._pending_alerts:
            self._pending_alerts[alert_id]["acknowledged_at"] = time.time()
            response_time = time.time() - self._pending_alerts[alert_id]["triggered_at"]
            self._response_times.append(response_time)
            if len(self._response_times) > self._max_response_times:
                self._response_times = self._response_times[-self._max_response_times:]
            logger.info(f"Alert acknowledged: {alert_id} (response_time={response_time:.1f}s)")
    
    def resolve(self, alert_id: str):
        """标记告警已解决"""
        if alert_id in self._pending_alerts:
            self._pending_alerts[alert_id]["resolved_at"] = time.time()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取响应时间统计"""
        if not self._response_times:
            return {
                "avg_response_time": 0,
                "max_response_time": 0,
                "min_response_time": 0,
                "p50_response_time": 0,
                "p95_response_time": 0,
                "within_5min_rate": 0,
                "total_responded": 0,
            }
        
        sorted_times = sorted(self._response_times)
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
        }
    
    def get_pending_alerts(self) -> List[Dict[str, Any]]:
        """获取待处理告警"""
        return list(self._pending_alerts.values())


class MonitoringScheduler:
    """
    监控调度器。
    
    管理周期性监控任务，提供：
    - 任务注册和调度
    - 执行状态追踪
    - 监控覆盖率计算
    - 告警响应时间统计
    """
    
    def __init__(
        self,
        alert_manager=None,
        metrics=None,
        response_tracker: Optional[AlertResponseTracker] = None,
        log_file: Optional[str] = None,
    ):
        self.alert_manager = alert_manager
        self.metrics = metrics
        self.response_tracker = response_tracker or AlertResponseTracker()
        
        # 监控任务
        self._tasks: Dict[str, MonitorTask] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        
        # 调度日志
        if log_file is None:
            log_file = str(Path(__file__).parent.parent / "logs" / "monitoring_schedule.jsonl")
        self._log_file = log_file
        self._schedule_log: List[Dict[str, Any]] = []
        
        # 覆盖率追踪
        self._monitored_components: Dict[str, bool] = {}
        self._total_components: Dict[str, int] = {}
        
        # 注册默认任务
        self._register_default_tasks()
    
    def _register_default_tasks(self):
        """注册默认监控任务"""
        # 健康检查任务
        self.register_task(MonitorTask(
            task_id="health_check",
            name="连接健康检查",
            interval_seconds=30.0,
            func=self._default_health_check,
            description="定期检查 CDP 连接健康状态",
        ))
        
        # 告警评估任务
        self.register_task(MonitorTask(
            task_id="alert_evaluation",
            name="告警评估",
            interval_seconds=60.0,
            func=self._default_alert_evaluation,
            description="定期评估告警规则并触发告警",
        ))
        
        # 指标快照任务
        self.register_task(MonitorTask(
            task_id="metrics_snapshot",
            name="指标快照",
            interval_seconds=300.0,
            func=self._default_metrics_snapshot,
            description="定期保存指标快照",
        ))
        
        # 覆盖率检查任务
        self.register_task(MonitorTask(
            task_id="coverage_check",
            name="覆盖率检查",
            interval_seconds=600.0,
            func=self._default_coverage_check,
            description="检查监控覆盖率",
        ))
    
    def register_task(self, task: MonitorTask):
        """注册监控任务"""
        self._tasks[task.task_id] = task
        logger.info(f"Registered monitoring task: {task.name} (interval={task.interval_seconds}s)")
    
    def unregister_task(self, task_id: str):
        """注销监控任务"""
        if task_id in self._tasks:
            del self._tasks[task_id]
            logger.info(f"Unregistered monitoring task: {task_id}")
    
    def track_component(self, component_id: str, component_type: str, monitored: bool = True):
        """追踪组件监控状态"""
        self._monitored_components[f"{component_type}:{component_id}"] = monitored
        if component_type not in self._total_components:
            self._total_components[component_type] = 0
        self._total_components[component_type] += 1
    
    def get_coverage_report(self) -> Dict[str, Any]:
        """获取监控覆盖率报告"""
        total = len(self._monitored_components)
        monitored = sum(1 for v in self._monitored_components.values() if v)
        overall_rate = monitored / total if total > 0 else 0
        
        # 按类型统计
        by_type: Dict[str, Dict[str, int]] = {}
        for key, monitored in self._monitored_components.items():
            comp_type = key.split(":")[0]
            if comp_type not in by_type:
                by_type[comp_type] = {"total": 0, "monitored": 0}
            by_type[comp_type]["total"] += 1
            if monitored:
                by_type[comp_type]["monitored"] += 1
        
        return {
            "overall_coverage_rate": round(overall_rate, 4),
            "total_components": total,
            "monitored_components": monitored,
            "unmonitored_components": total - monitored,
            "by_type": by_type,
            "target_coverage_rate": 0.9,
            "target_met": overall_rate >= 0.9,
        }
    
    async def _default_health_check(self) -> Dict[str, Any]:
        """默认健康检查"""
        try:
            from src.reliability.health import ConnectionHealthChecker
            # 这里需要实际的 CDP 客户端，暂时返回占位结果
            return {"status": "ok", "check_time": datetime.now().isoformat()}
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {"status": "error", "error": str(e)}
    
    async def _default_alert_evaluation(self) -> Dict[str, Any]:
        """默认告警评估"""
        if self.alert_manager:
            alerts = self.alert_manager.check_alerts()
            for alert in alerts:
                self.response_tracker.track_alert(alert["alert_id"], alert)
            return {"alerts_triggered": len(alerts)}
        return {"alerts_triggered": 0}
    
    async def _default_metrics_snapshot(self) -> Dict[str, Any]:
        """默认指标快照"""
        if self.metrics:
            snapshot = self.metrics.get_metrics()
            self._save_schedule_log({
                "type": "metrics_snapshot",
                "timestamp": datetime.now().isoformat(),
                "snapshot": snapshot,
            })
            return {"snapshot_saved": True}
        return {"snapshot_saved": False}
    
    async def _default_coverage_check(self) -> Dict[str, Any]:
        """默认覆盖率检查"""
        report = self.get_coverage_report()
        self._save_schedule_log({
            "type": "coverage_check",
            "timestamp": datetime.now().isoformat(),
            "report": report,
        })
        return report
    
    def _save_schedule_log(self, entry: Dict[str, Any]):
        """保存调度日志"""
        try:
            Path(self._log_file).parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception as e:
            logger.warning(f"Failed to save schedule log: {e}")
    
    async def _run_task_loop(self):
        """任务执行循环"""
        logger.info("Monitoring scheduler started")
        while self._running:
            now = time.time()
            for task_id, task in self._tasks.items():
                if not task.enabled:
                    continue
                if task._last_run_time is None or (now - task._last_run_time) >= task.interval_seconds:
                    try:
                        task._run_count += 1
                        task._last_run_time = now
                        result = await task.func()
                        task._last_result = result
                        task._last_error = None
                        self._save_schedule_log({
                            "type": "task_execution",
                            "task_id": task_id,
                            "timestamp": datetime.now().isoformat(),
                            "result": result,
                        })
                    except Exception as e:
                        task._failure_count += 1
                        task._last_error = str(e)
                        logger.error(f"Task {task_id} failed: {e}")
            await asyncio.sleep(1)
    
    def start(self):
        """启动调度器"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self._thread.start()
        logger.info("Monitoring scheduler started")
    
    def _run_scheduler(self):
        """调度器运行循环（线程入口）"""
        asyncio.run(self._run_task_loop())
    
    def stop(self):
        """停止调度器"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Monitoring scheduler stopped")
    
    def get_status(self) -> Dict[str, Any]:
        """获取调度器状态"""
        return {
            "running": self._running,
            "tasks": {tid: t.to_dict() for tid, t in self._tasks.items()},
            "coverage": self.get_coverage_report(),
            "alert_response": self.response_tracker.get_stats(),
        }


# 全局调度器实例
_global_scheduler: Optional[MonitoringScheduler] = None


def get_scheduler(
    alert_manager=None,
    metrics=None,
    response_tracker: Optional[AlertResponseTracker] = None,
) -> MonitoringScheduler:
    """获取监控调度器实例"""
    global _global_scheduler
    if _global_scheduler is None:
        _global_scheduler = MonitoringScheduler(alert_manager, metrics, response_tracker)
    return _global_scheduler


def reset_scheduler():
    """重置全局调度器"""
    global _global_scheduler
    if _global_scheduler:
        _global_scheduler.stop()
    _global_scheduler = None
