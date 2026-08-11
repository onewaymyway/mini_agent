# -*- coding: utf-8 -*-
"""
Finance Data Toolkit - 定时调度器模块

提供任务调度、定时执行、任务管理功能。

使用示例：
    from finance_toolkit.scheduler import TaskScheduler
    
    scheduler = TaskScheduler()
    scheduler.add_job(fetch_hot_stocks, 'interval', minutes=30, args=['sz'])
    scheduler.add_job(generate_report, 'cron', hour=16, minute=0)
    scheduler.start()
"""

import asyncio
import logging
import time
import json
import os
from datetime import datetime, timedelta
from typing import Callable, Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class JobStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


class ScheduleType(Enum):
    INTERVAL = "interval"      # 固定间隔
    CRON = "cron"              # cron 表达式
    ONCE = "once"              # 一次性


@dataclass
class JobConfig:
    """任务配置"""
    job_id: str
    name: str
    func: Callable
    schedule_type: ScheduleType
    interval_seconds: Optional[int] = None
    cron_expression: Optional[str] = None
    next_run_at: Optional[float] = None
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    enabled: bool = True
    timeout: int = 300
    max_retries: int = 3
    retry_delay: int = 10

    def to_dict(self) -> dict:
        d = asdict(self)
        d['schedule_type'] = self.schedule_type.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> 'JobConfig':
        data['schedule_type'] = ScheduleType(data['schedule_type'])
        return cls(**data)


@dataclass
class JobExecution:
    """任务执行记录"""
    job_id: str
    start_time: float
    end_time: float = 0.0
    status: JobStatus = JobStatus.PENDING
    duration: float = 0.0
    error: str = ""
    retry_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class CronParser:
    """简易 cron 表达式解析器"""

    @staticmethod
    def parse(cron_expr: str) -> Dict[str, int]:
        """解析 cron 表达式，返回 {minute, hour, day, month, weekday}"""
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron expression: {cron_expr}")
        return {
            'minute': int(parts[0]),
            'hour': int(parts[1]),
            'day': int(parts[2]) if parts[2] != '*' else -1,
            'month': int(parts[3]) if parts[3] != '*' else -1,
            'weekday': int(parts[4]) if parts[4] != '*' else -1,
        }

    @staticmethod
    def next_run(cron_expr: str, from_time: Optional[datetime] = None) -> datetime:
        """计算下次执行时间"""
        from_time = from_time or datetime.now()
        expr = CronParser.parse(cron_expr)

        # 简单实现：找到下一个匹配的时间
        candidate = from_time.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(1440 * 7):  # 最多搜索一周
            if (expr['minute'] == -1 or candidate.minute == expr['minute']) and \
               (expr['hour'] == -1 or candidate.hour == expr['hour']) and \
               (expr['day'] == -1 or candidate.day == expr['day']) and \
               (expr['month'] == -1 or candidate.month == expr['month']) and \
               (expr['weekday'] == -1 or candidate.weekday() == expr['weekday']):
                return candidate
            candidate += timedelta(minutes=1)
        raise ValueError(f"Cannot find next run time for: {cron_expr}")


class TaskScheduler:
    """
    任务调度器

    支持 interval 和 cron 两种调度方式，提供任务管理、执行记录、告警功能。
    """

    def __init__(
        self,
        state_file: str = None,
        log_dir: str = None,
    ):
        self._jobs: Dict[str, JobConfig] = {}
        self._executions: Dict[str, List[JobExecution]] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._state_file = state_file or str(Path(__file__).parent.parent / 'data' / 'scheduler_state.json')
        self._log_dir = log_dir or str(Path(__file__).parent.parent / 'logs')
        self._alert_callbacks: List[Callable] = []

        os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
        os.makedirs(self._log_dir, exist_ok=True)

        self._load_state()

    def _load_state(self):
        """加载调度器状态"""
        if os.path.exists(self._state_file):
            try:
                with open(self._state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for job_data in data.get('jobs', []):
                    job = JobConfig.from_dict(job_data)
                    self._jobs[job.job_id] = job
                for job_id, execs in data.get('executions', {}).items():
                    self._executions[job_id] = [
                        JobExecution(**e) for e in execs
                    ]
                logger.info(f"已加载 {len(self._jobs)} 个任务")
            except Exception as e:
                logger.warning(f"加载调度器状态失败: {e}")

    def _save_state(self):
        """保存调度器状态"""
        try:
            data = {
                'updated_at': datetime.now().isoformat(),
                'jobs': [j.to_dict() for j in self._jobs.values()],
                'executions': {
                    jid: [e.to_dict() for e in execs]
                    for jid, execs in self._executions.items()
                }
            }
            with open(self._state_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存调度器状态失败: {e}")

    def add_job(
        self,
        job_id: str,
        name: str,
        func: Callable,
        schedule_type: ScheduleType,
        interval_seconds: Optional[int] = None,
        cron_expression: Optional[str] = None,
        args: tuple = (),
        kwargs: dict = None,
        enabled: bool = True,
        timeout: int = 300,
        max_retries: int = 3,
    ) -> JobConfig:
        """添加任务"""
        kwargs = kwargs or {}

        if schedule_type == ScheduleType.INTERVAL:
            if interval_seconds is None:
                raise ValueError("interval 类型需要指定 interval_seconds")
            next_run = time.time() + interval_seconds
        elif schedule_type == ScheduleType.CRON:
            if cron_expression is None:
                raise ValueError("cron 类型需要指定 cron_expression")
            next_run = CronParser.next_run(cron_expression).timestamp()
        elif schedule_type == ScheduleType.ONCE:
            next_run = time.time()
        else:
            raise ValueError(f"不支持的调度类型: {schedule_type}")

        job = JobConfig(
            job_id=job_id,
            name=name,
            func=func,
            schedule_type=schedule_type,
            interval_seconds=interval_seconds,
            cron_expression=cron_expression,
            next_run_at=next_run,
            args=args,
            kwargs=kwargs,
            enabled=enabled,
            timeout=timeout,
            max_retries=max_retries,
        )
        self._jobs[job_id] = job
        if job_id not in self._executions:
            self._executions[job_id] = []
        self._save_state()
        logger.info(f"添加任务: {job_id} ({name}), 下次执行: {datetime.fromtimestamp(next_run)}")
        return job

    def remove_job(self, job_id: str) -> bool:
        """移除任务"""
        if job_id in self._jobs:
            del self._jobs[job_id]
            self._executions.pop(job_id, None)
            self._save_state()
            logger.info(f"移除任务: {job_id}")
            return True
        return False

    def pause_job(self, job_id: str) -> bool:
        """暂停任务"""
        if job_id in self._jobs:
            self._jobs[job_id].enabled = False
            self._save_state()
            logger.info(f"暂停任务: {job_id}")
            return True
        return False

    def resume_job(self, job_id: str) -> bool:
        """恢复任务"""
        if job_id in self._jobs:
            self._jobs[job_id].enabled = True
            self._save_state()
            logger.info(f"恢复任务: {job_id}")
            return True
        return False

    async def _run_job(self, job: JobConfig):
        """执行单个任务"""
        exec_record = JobExecution(
            job_id=job.job_id,
            start_time=time.time(),
            status=JobStatus.RUNNING,
        )
        self._executions[job.job_id].append(exec_record)

        log_file = os.path.join(
            self._log_dir,
            f"{job.job_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )

        try:
            logger.info(f"开始执行任务: {job.job_id} ({job.name})")
            start = time.time()

            if asyncio.iscoroutinefunction(job.func):
                await asyncio.wait_for(
                    job.func(*job.args, **job.kwargs),
                    timeout=job.timeout
                )
            else:
                await asyncio.to_thread(
                    job.func, *job.args, **job.kwargs
                )

            duration = time.time() - start
            exec_record.end_time = time.time()
            exec_record.status = JobStatus.COMPLETED
            exec_record.duration = duration
            logger.info(f"任务完成: {job.job_id}, 耗时: {duration:.1f}s")

            # 记录日志
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write(f"任务: {job.name}\n")
                f.write(f"开始: {datetime.fromtimestamp(exec_record.start_time)}\n")
                f.write(f"结束: {datetime.fromtimestamp(exec_record.end_time)}\n")
                f.write(f"耗时: {duration:.1f}s\n")
                f.write(f"状态: 成功\n")

        except asyncio.TimeoutError:
            exec_record.status = JobStatus.FAILED
            exec_record.error = f"超时（{job.timeout}s）"
            logger.error(f"任务超时: {job.job_id}")
            self._trigger_alert(job.job_id, "timeout", exec_record)

        except Exception as e:
            exec_record.status = JobStatus.FAILED
            exec_record.error = str(e)
            logger.error(f"任务失败: {job.job_id}: {e}")
            self._trigger_alert(job.job_id, "error", exec_record)

            # 重试
            if exec_record.retry_count < job.max_retries:
                exec_record.retry_count += 1
                logger.info(f"重试任务: {job.job_id}, 第 {exec_record.retry_count} 次")
                await asyncio.sleep(job.retry_delay)
                await self._run_job(job)
                return

        finally:
            exec_record.end_time = time.time()
            if exec_record.duration == 0:
                exec_record.duration = exec_record.end_time - exec_record.start_time
            self._save_state()

        # 计算下次执行时间
        if job.enabled and exec_record.status == JobStatus.COMPLETED:
            if job.schedule_type == ScheduleType.INTERVAL:
                job.next_run_at = time.time() + job.interval_seconds
            elif job.schedule_type == ScheduleType.CRON and job.cron_expression:
                job.next_run_at = CronParser.next_run(
                    job.cron_expression, datetime.now()
                ).timestamp()
            elif job.schedule_type == ScheduleType.ONCE:
                job.next_run_at = None  # 一次性任务不再调度
            self._save_state()

    def _trigger_alert(self, job_id: str, alert_type: str, execution: JobExecution):
        """触发告警"""
        alert_data = {
            'job_id': job_id,
            'type': alert_type,
            'timestamp': datetime.now().isoformat(),
            'error': execution.error,
            'retry_count': execution.retry_count,
        }
        logger.warning(f"[告警] 任务 {job_id} - {alert_type}: {execution.error}")
        for cb in self._alert_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    asyncio.create_task(cb(alert_data))
                else:
                    cb(alert_data)
            except Exception as e:
                logger.error(f"告警回调失败: {e}")

    def add_alert_callback(self, callback: Callable):
        """添加告警回调"""
        self._alert_callbacks.append(callback)

    def add_health_check_job(
        self,
        health_monitor: Any,
        job_id: str = 'health_check',
        name: str = '数据源健康检查',
        interval_seconds: int = 300,
        enabled: bool = True,
    ) -> JobConfig:
        """
        添加健康检查任务到调度器

        参数：
            health_monitor: HealthMonitor 实例
            job_id: 任务ID
            name: 任务名称
            interval_seconds: 检查间隔（秒）
            enabled: 是否启用

        返回：
            创建的任务配置
        """
        async def _health_check_job():
            """健康检查任务"""
            import asyncio
            results = await health_monitor.check_all()
            unhealthy = health_monitor.get_unhealthy_sources()
            degraded = health_monitor.get_degraded_sources()

            if unhealthy:
                logger.warning(f"不健康的数据源: {unhealthy}")
            if degraded:
                logger.warning(f"降级的数据源: {degraded}")

            # 记录健康检查日志
            log_file = os.path.join(
                self._log_dir,
                f"health_check_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            )
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write(f"健康检查时间: {datetime.now()}\n")
                f.write(f"检查数据源数: {len(results)}\n")
                f.write(f"不健康: {unhealthy}\n")
                f.write(f"降级: {degraded}\n\n")
                for source, metrics in results.items():
                    status = '健康' if metrics.healthy else '不健康'
                    f.write(f"- {source}: {status} (成功率: {metrics.success_rate:.1f}%, 延迟: {metrics.avg_latency_ms:.0f}ms)\n")

            return results

        return self.add_job(
            job_id=job_id,
            name=name,
            func=_health_check_job,
            schedule_type=ScheduleType.INTERVAL,
            interval_seconds=interval_seconds,
            enabled=enabled,
        )

    async def _scheduler_loop(self):
        """调度循环"""
        logger.info("调度器循环已启动")
        while self._running:
            try:
                now = time.time()
                pending_jobs = [
                    j for j in self._jobs.values()
                    if j.enabled and j.next_run_at and j.next_run_at <= now
                ]

                if pending_jobs:
                    logger.info(f"发现 {len(pending_jobs)} 个待执行任务")
                    tasks = [self._run_job(j) for j in pending_jobs]
                    await asyncio.gather(*tasks, return_exceptions=True)

                # 检查超时任务
                for job_id, execs in self._executions.items():
                    for ex in reversed(execs):
                        if ex.status == JobStatus.RUNNING:
                            if now - ex.start_time > 600:  # 10分钟超时
                                logger.error(f"任务卡死，强制终止: {job_id}")
                                ex.status = JobStatus.FAILED
                                ex.error = "任务卡死（超过600s）"
                                self._trigger_alert(job_id, "stuck", ex)

                await asyncio.sleep(5)

            except Exception as e:
                logger.error(f"调度循环出错: {e}")
                await asyncio.sleep(10)

    def start(self):
        """启动调度器"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info(f"调度器已启动，管理 {len(self._jobs)} 个任务")

    def stop(self):
        """停止调度器"""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        self._save_state()
        logger.info("调度器已停止")

    def get_job_status(self, job_id: str) -> Optional[Dict]:
        """获取任务状态"""
        job = self._jobs.get(job_id)
        if not job:
            return None
        execs = self._executions.get(job_id, [])
        latest = execs[-1] if execs else None
        return {
            'job_id': job.job_id,
            'name': job.name,
            'enabled': job.enabled,
            'next_run_at': datetime.fromtimestamp(job.next_run_at).isoformat() if job.next_run_at else None,
            'schedule_type': job.schedule_type.value,
            'last_execution': latest.to_dict() if latest else None,
            'total_executions': len(execs),
        }

    def get_all_status(self) -> Dict[str, Dict]:
        """获取所有任务状态"""
        return {jid: self.get_job_status(jid) for jid in self._jobs}

    def get_summary(self) -> Dict[str, Any]:
        """获取调度器摘要"""
        total = len(self._jobs)
        enabled = sum(1 for j in self._jobs.values() if j.enabled)
        running = sum(
            1 for execs in self._executions.values()
            for e in execs if e.status == JobStatus.RUNNING
        )
        failed_today = sum(
            1 for execs in self._executions.values()
            for e in execs
            if e.status == JobStatus.FAILED
            and datetime.fromtimestamp(e.start_time).date() == datetime.now().date()
        )
        return {
            'total_jobs': total,
            'enabled_jobs': enabled,
            'running_jobs': running,
            'failed_today': failed_today,
            'is_running': self._running,
            'state_file': self._state_file,
        }


# 便捷函数
def create_default_scheduler(state_dir: str = None) -> TaskScheduler:
    """创建默认调度器"""
    base_dir = Path(__file__).parent.parent
    state_file = os.path.join(
        state_dir or str(base_dir / 'data'),
        'scheduler_state.json'
    )
    log_dir = str(base_dir / 'logs')
    return TaskScheduler(state_file=state_file, log_dir=log_dir)
