"""
evolution/cron_scheduler.py — Daemon 模式定时任务调度器

支持两种 schedule 格式：
  "interval:<seconds>"   — 固定间隔，如 "interval:3600"（每小时）
  "cron:<expr>"          — 标准 cron 表达式，如 "cron:0 */6 * * *"（每6小时）
    cron 表达式字段：分 时 日 月 周（与 POSIX cron 一致）
    不依赖外部库，内置轻量 cron 解析器

内置 job（首次初始化时写入 cron_jobs.json，用户可修改 enabled/schedule，
但 sys: 前缀 job 不可删除，只可 disable）：

  sys:phase_g        — Phase G 扫描（技能剪枝/能力地图）  interval:21600
  sys:workdir_sync   — 工作区知识整合                     interval:3600
  sys:self_eval      — 能力自评（capability_map 更新）     interval:86400
  sys:goal_review    — 目标清理（已完成/过期 Goal）         interval:43200
  sys:digest_trim    — activity_digest 日志修剪            interval:604800

存储：<project_root>/.agent/cron_jobs.json
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


# ── CronJob 数据结构 ──────────────────────────────────────────────────────────

@dataclass
class CronJob:
    id: str
    name: str
    schedule: str               # "interval:<sec>" 或 "cron:<expr>"
    task_template: str          # 提交到 InputQueue 的消息模板
    enabled: bool = True
    last_run_at: float = 0.0
    next_run_at: float = 0.0    # 由 _compute_next() 计算
    run_count: int = 0
    tags: list[str] = field(default_factory=list)
    initiator: str = "cron"     # 区别于 "autonomous" / "user"
    description: str = ""       # 人类可读说明

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "schedule": self.schedule,
            "task_template": self.task_template,
            "enabled": self.enabled,
            "last_run_at": self.last_run_at,
            "next_run_at": self.next_run_at,
            "run_count": self.run_count,
            "tags": self.tags,
            "initiator": self.initiator,
            "description": self.description,
        }

    @staticmethod
    def from_dict(d: dict) -> "CronJob":
        return CronJob(
            id=d.get("id", ""),
            name=d.get("name", ""),
            schedule=d.get("schedule", "interval:3600"),
            task_template=d.get("task_template", ""),
            enabled=d.get("enabled", True),
            last_run_at=d.get("last_run_at", 0.0),
            next_run_at=d.get("next_run_at", 0.0),
            run_count=d.get("run_count", 0),
            tags=d.get("tags", []),
            initiator=d.get("initiator", "cron"),
            description=d.get("description", ""),
        )

    @property
    def is_system(self) -> bool:
        return self.id.startswith("sys:")

    def time_until_next(self) -> float:
        """距下次触发还有多少秒（负数表示已过期）。"""
        return self.next_run_at - time.time()

    def next_run_str(self) -> str:
        """人类可读的下次运行时间。"""
        delta = self.time_until_next()
        if delta <= 0:
            return "now / overdue"
        if delta < 60:
            return f"in {delta:.0f}s"
        if delta < 3600:
            return f"in {delta/60:.0f}m"
        if delta < 86400:
            return f"in {delta/3600:.1f}h"
        return f"in {delta/86400:.1f}d"


# ── 内置 Job 定义 ─────────────────────────────────────────────────────────────

_BUILTIN_JOBS: list[dict] = [
    {
        "id": "sys:phase_g",
        "name": "Phase G 扫描",
        "schedule": "interval:21600",
        "description": "技能剪枝、去重、能力地图更新（每 6 小时）",
        "task_template": "[系统维护] 执行 Phase G 扫描：检查技能库冗余、更新能力地图、评估晋升候选",
        "tags": ["maintenance", "evolution"],
        "enabled": True,
    },
    {
        "id": "sys:workdir_sync",
        "name": "工作区知识整合",
        "schedule": "interval:3600",
        "description": "同步工作区文件变化到 WorkdirKnowledge（每小时）",
        "task_template": "[系统维护] 整合工作区知识：扫描文件变化、更新 WorkThread 进展、刷新 next_suggested",
        "tags": ["maintenance"],
        "enabled": True,
    },
    {
        "id": "sys:self_eval",
        "name": "能力自评",
        "schedule": "interval:86400",
        "description": "评估当前能力边界，更新 capability_map 置信度（每 24 小时）",
        "task_template": "[自我评估] 回顾最近 24 小时的工具使用和任务结果，更新 capability_map：哪些能力变强/变弱，哪些场景仍不确定",
        "tags": ["evolution", "self_awareness"],
        "enabled": True,
    },
    {
        "id": "sys:goal_review",
        "name": "目标清理",
        "schedule": "interval:43200",
        "description": "清理已完成/长期无进展的 Goal 和 Objective（每 12 小时）",
        "task_template": "[目标管理] 审查 GoalBacklog：标记已实质完成的 Objective 为 completed，识别超过 7 天无进展的 Objective 并暂停",
        "tags": ["maintenance", "goals"],
        "enabled": True,
    },
    {
        "id": "sys:digest_trim",
        "name": "日志修剪",
        "schedule": "interval:604800",
        "description": "修剪 activity_digest.jsonl，保留最近 30 天（每 7 天）",
        "task_template": "[系统维护] 修剪 activity_digest.jsonl：删除 30 天前的记录，压缩历史统计",
        "tags": ["maintenance"],
        "enabled": True,
    },
]


# ── 轻量 Cron 解析器 ──────────────────────────────────────────────────────────

def _next_interval(last_run_at: float, interval_seconds: float) -> float:
    """interval 模式：下次触发时间（距上次运行 interval 秒后）。"""
    if last_run_at <= 0:
        return time.time()  # 从未运行过，立即可以跑
    return last_run_at + interval_seconds


def _cron_field_match(value: int, expr: str) -> bool:
    """
    判断单个 cron 字段是否匹配。
    支持：* / */n / n / n,m / n-m
    """
    if expr == "*":
        return True
    if expr.startswith("*/"):
        try:
            step = int(expr[2:])
            return value % step == 0
        except ValueError:
            return False
    if "," in expr:
        return any(_cron_field_match(value, part.strip()) for part in expr.split(","))
    if "-" in expr:
        parts = expr.split("-", 1)
        try:
            lo, hi = int(parts[0]), int(parts[1])
            return lo <= value <= hi
        except ValueError:
            return False
    try:
        return value == int(expr)
    except ValueError:
        return False


def _next_cron(expr: str, after: Optional[float] = None) -> float:
    """
    计算 cron 表达式的下次触发时间（Unix timestamp）。
    expr 格式：分 时 日 月 周（5 字段）
    最大向前搜索 1 年（8760 次分钟迭代），找不到则返回 now + 365d。
    """
    import time as _time
    parts = expr.strip().split()
    if len(parts) != 5:
        # 格式错误，退化为每小时
        return (_time.time() if after is None else after) + 3600

    m_expr, h_expr, dom_expr, mon_expr, dow_expr = parts
    start = math.ceil((after if after is not None else _time.time()) / 60) * 60 + 60
    # 从下一分钟开始搜索
    import calendar
    for _ in range(525600):  # 最多搜 1 年（分钟）
        t = start
        tm = _time.localtime(t)
        if (
            _cron_field_match(tm.tm_min, m_expr)
            and _cron_field_match(tm.tm_hour, h_expr)
            and _cron_field_match(tm.tm_mday, dom_expr)
            and _cron_field_match(tm.tm_mon, mon_expr)
            and _cron_field_match(tm.tm_wday, dow_expr)
        ):
            return float(t)
        start += 60

    return time.time() + 365 * 86400


def compute_next_run(schedule: str, last_run_at: float = 0.0) -> float:
    """
    根据 schedule 字符串计算下次运行时间。

    格式：
      "interval:<seconds>"   → 固定间隔
      "cron:<5-field-expr>"  → cron 表达式
    """
    schedule = schedule.strip()
    if schedule.startswith("interval:"):
        try:
            sec = float(schedule[9:])
        except ValueError:
            sec = 3600.0
        return _next_interval(last_run_at, sec)
    elif schedule.startswith("cron:"):
        expr = schedule[5:]
        return _next_cron(expr, after=time.time())
    else:
        # 未知格式，1 小时后
        return time.time() + 3600


# ── CronScheduler 主类 ────────────────────────────────────────────────────────

class CronScheduler:
    """
    Daemon 模式的定时任务调度器。

    - 存储：<workdir>/.agent/cron_jobs.json
    - tick() 由 AutonomousLoop._tick_passive() 调用
    - 触发的 Job 通过回调函数 _submit_fn 提交（注入 InputQueue.enqueue）
    """

    VERSION = 1

    def __init__(
        self,
        paths: "AgentPaths",
        submit_fn: Optional[Callable[[str, str, dict], bool]] = None,
    ) -> None:
        """
        paths       — AgentPaths，用于定位 cron_jobs.json
        submit_fn   — 触发 job 时的提交回调：submit_fn(message, initiator, meta) -> bool
                      通常注入 InputQueue.enqueue 的包装
        """
        self._paths = paths
        self._submit_fn = submit_fn
        self._jobs: dict[str, CronJob] = {}
        self._jobs_path = paths.workdir_dir / "cron_jobs.json"

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def load(self) -> None:
        """加载 cron_jobs.json，首次加载时注入内置 Job。"""
        existing: dict[str, CronJob] = {}
        if self._jobs_path.exists():
            try:
                data = json.loads(self._jobs_path.read_text(encoding="utf-8"))
                for jd in data.get("jobs", []):
                    j = CronJob.from_dict(jd)
                    if j.id:
                        existing[j.id] = j
            except Exception:
                pass

        # 注入内置 Job（已存在的不覆盖，保留用户修改的 enabled/schedule）
        for bd in _BUILTIN_JOBS:
            bid = bd["id"]
            if bid not in existing:
                j = CronJob.from_dict(bd)
                j.next_run_at = compute_next_run(j.schedule, 0.0)
                existing[bid] = j

        self._jobs = existing

    def save(self) -> None:
        """原子写入 cron_jobs.json。"""
        self._jobs_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": self.VERSION,
            "jobs": [j.to_dict() for j in self._jobs.values()],
        }
        text = json.dumps(data, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(self._jobs_path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        os.replace(tmp, self._jobs_path)

    # ── 主调度入口 ────────────────────────────────────────────────────────────

    def tick(self) -> list[str]:
        """
        检查所有 enabled Job 是否到期，触发到期 Job。
        返回本次触发的 job_id 列表。
        由 AutonomousLoop._tick_passive() 调用。
        """
        now = time.time()
        triggered: list[str] = []

        for job in list(self._jobs.values()):
            if not job.enabled:
                continue
            if job.next_run_at <= 0:
                # next_run_at 未初始化，重新计算
                job.next_run_at = compute_next_run(job.schedule, job.last_run_at)
                continue
            if now < job.next_run_at:
                continue

            # 到期，触发
            success = self._fire(job)
            if success:
                job.last_run_at = now
                job.run_count += 1
                job.next_run_at = compute_next_run(job.schedule, now)
                triggered.append(job.id)

        if triggered:
            try:
                self.save()
            except Exception:
                pass

        return triggered

    def _fire(self, job: CronJob) -> bool:
        """触发一个 Job：调用 submit_fn 提交到 InputQueue。"""
        if self._submit_fn is None:
            return False
        try:
            return self._submit_fn(
                job.task_template,
                job.initiator,
                {"cron_job_id": job.id, "cron_job_name": job.name},
            )
        except Exception:
            return False

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def add_job(
        self,
        name: str,
        schedule: str,
        task_template: str,
        description: str = "",
        tags: Optional[list[str]] = None,
        enabled: bool = True,
    ) -> CronJob:
        """添加用户自定义 Job。"""
        job_id = f"user:{uuid.uuid4().hex[:8]}"
        job = CronJob(
            id=job_id,
            name=name,
            schedule=schedule,
            task_template=task_template,
            description=description,
            tags=tags or ["user"],
            enabled=enabled,
            initiator="cron",
            next_run_at=compute_next_run(schedule, 0.0),
        )
        self._jobs[job_id] = job
        self.save()
        return job

    def remove_job(self, job_id: str) -> bool:
        """删除 Job（sys: 前缀不可删除，只可 disable）。"""
        job = self._jobs.get(job_id)
        if not job:
            return False
        if job.is_system:
            return False  # 系统 job 不可删
        del self._jobs[job_id]
        self.save()
        return True

    def enable(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.enabled = True
        # 重新计算下次运行时间
        job.next_run_at = compute_next_run(job.schedule, job.last_run_at)
        self.save()
        return True

    def disable(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.enabled = False
        self.save()
        return True

    def run_now(self, job_id: str) -> bool:
        """立即触发一次（不修改 next_run_at）。"""
        job = self._jobs.get(job_id)
        if not job:
            return False
        success = self._fire(job)
        if success:
            job.last_run_at = time.time()
            job.run_count += 1
            self.save()
        return success

    def update_schedule(self, job_id: str, schedule: str) -> bool:
        """更新 job 的 schedule 并重新计算 next_run_at。"""
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.schedule = schedule
        job.next_run_at = compute_next_run(schedule, job.last_run_at)
        self.save()
        return True

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def get(self, job_id: str) -> Optional[CronJob]:
        return self._jobs.get(job_id)

    def list_jobs(
        self,
        tags: Optional[list[str]] = None,
        enabled_only: bool = False,
    ) -> list[CronJob]:
        """列出 Job，按 next_run_at 升序排序。"""
        jobs = list(self._jobs.values())
        if enabled_only:
            jobs = [j for j in jobs if j.enabled]
        if tags:
            tag_set = set(tags)
            jobs = [j for j in jobs if set(j.tags) & tag_set]
        return sorted(jobs, key=lambda j: j.next_run_at)

    def next_run_summary(self) -> str:
        """简洁的下次运行总览（供 /cron status 和 daemon status 使用）。"""
        jobs = self.list_jobs(enabled_only=True)
        if not jobs:
            return "无启用的 cron job"
        lines = []
        for j in jobs[:8]:
            lines.append(f"  {j.id:<24}  {j.name:<18}  {j.next_run_str()}")
        return "\n".join(lines)


# ── 便捷函数 ──────────────────────────────────────────────────────────────────

def load_cron_scheduler(
    paths: "AgentPaths",
    submit_fn: Optional[Callable] = None,
) -> CronScheduler:
    """加载并返回 CronScheduler（便捷函数）。"""
    cs = CronScheduler(paths, submit_fn=submit_fn)
    cs.load()
    return cs


__all__ = [
    "CronJob",
    "CronScheduler",
    "compute_next_run",
    "load_cron_scheduler",
]
