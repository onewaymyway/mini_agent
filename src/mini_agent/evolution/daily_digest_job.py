"""evolution/daily_digest_job.py — 每日融合日报的本地 cron handler（sys:daily_digest）。

此前 sys:daily_digest 的 task_template 是\"执行一次 /digest daily\"，由 agent 以
LLM 对话方式执行，既耗 token 又不确定。这里改为本地回调：直接调用
generate_daily_digest() 落盘，然后 notify_digest_ready() 推送看板 + 桌面弹窗。

调度时间保持 cron:0 22 * * *（每天 22:00，生成当天数据）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mini_agent.evolution.cron_scheduler import compute_next_run

if TYPE_CHECKING:
    from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler
    from mini_agent.storage.paths import AgentPaths

JOB_ID = "sys:daily_digest"
SCHEDULE = "cron:0 22 * * *"


def ensure_daily_digest_job(
    paths: "AgentPaths",
    cron_scheduler: "CronScheduler",
    *,
    enabled: bool = True,
) -> dict[str, Any]:
    """daemon 启动时调用：保证 sys:daily_digest 存在、调度对齐，并注册本地 handler。"""
    existing = {j.id: j for j in cron_scheduler.list_jobs()}.get(JOB_ID)
    created = existing is None
    job = cron_scheduler.ensure_job(
        job_id=JOB_ID,
        name="每日融合日报",
        schedule=SCHEDULE,
        description="每天 22:00 生成当天融合日报（行为+目标+提交+异常+会话），落盘后推送看板与桌面弹窗；零 LLM 成本。",
        tags=["digest", "behavior", "goals"],
    )
    if job.schedule != SCHEDULE:
        job.schedule = SCHEDULE
        job.next_run_at = compute_next_run(SCHEDULE, job.last_run_at)
    job.enabled = bool(enabled)
    cron_scheduler.save()

    def _handler(_job: "CronJob") -> bool:
        from mini_agent.errors import log_exception
        from mini_agent.evolution.daily_digest import generate_daily_digest, notify_digest_ready

        try:
            data = generate_daily_digest(paths)
            notify_digest_ready(paths, data)
            return True
        except Exception as exc:
            log_exception(exc, where="mini_agent.evolution.daily_digest_job.handler")
            return False

    cron_scheduler.register_local_handler(JOB_ID, _handler)
    return {"created": created, "schedule": SCHEDULE, "enabled": bool(enabled)}
