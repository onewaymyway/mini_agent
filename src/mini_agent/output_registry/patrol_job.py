"""output_registry/patrol_job.py — 产出项目定期巡检（cron job: sys:output_patrol）。

对应 next_doc/management_output_project_model_impl_p7.md 阶段三。

- job 是零 LLM 成本的本地回调，直接调用 patrol.run_report；
- 间隔来自 agent_config.json 的 output_registry.patrol_interval_days（默认 3 天）；
- 启动时把 job 的 schedule 与配置对齐：配置改了，下次 daemon 启动生效；
- patrol_schedule_enabled=false 时 job 保留但 disabled；
- 巡检的通知与阈值由 run_report 自己读取配置，这里不重复处理。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mini_agent.evolution.cron_scheduler import compute_next_run
from mini_agent.output_registry.patrol import run_report

if TYPE_CHECKING:
    from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler
    from mini_agent.storage.paths import AgentPaths

JOB_ID = "sys:output_patrol"
SECONDS_PER_DAY = 86400
_log = logging.getLogger(__name__)


def interval_schedule(interval_days: float) -> str:
    """把天数转成 cron 的 interval 表达式。非正数回退为 3 天。"""
    days = float(interval_days) if interval_days and float(interval_days) > 0 else 3.0
    return f"interval:{int(days * SECONDS_PER_DAY)}"


def ensure_output_patrol_job(
    paths: "AgentPaths",
    cron_scheduler: "CronScheduler",
    *,
    interval_days: float = 3.0,
    enabled: bool = True,
) -> dict[str, Any]:
    """daemon 启动时调用：保证 sys:output_patrol 存在，并与配置对齐。

    返回 {"created": bool, "schedule": str, "enabled": bool}，供日志与测试使用。
    """
    schedule = interval_schedule(interval_days)
    existing = {j.id: j for j in cron_scheduler.list_jobs()}.get(JOB_ID)
    created = existing is None
    job = cron_scheduler.ensure_job(
        job_id=JOB_ID,
        name="产出项目定期巡检",
        schedule=schedule,
        description=(
            "按 output_registry.patrol_interval_days 定期巡检产出项目（停滞、版本缺口、"
            "未登记等），只生成建议，新建议推送到关注与通知；零 LLM 成本。"
        ),
        tags=["maintenance", "output_registry"],
    )
    # ensure_job 不覆盖已有 job 的 schedule，这里按配置对齐（与 upsert_external_entrypoint_job 同样的做法）
    if job.schedule != schedule:
        job.schedule = schedule
        job.next_run_at = compute_next_run(schedule, job.last_run_at)
    job.enabled = bool(enabled)
    cron_scheduler.save()

    project_root = paths.project_root

    def _handler(_job: "CronJob") -> bool:
        try:
            run_report(project_root)
            return True
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.output_registry.patrol_job.handler")
            return False

    cron_scheduler.register_local_handler(JOB_ID, _handler)
    return {"created": created, "schedule": schedule, "enabled": bool(enabled)}
