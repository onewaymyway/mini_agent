"""单次调度 tick：运行所有到期的信息源，再生成到期的报告。

适合交给系统 cron 或 daemon 定期调用（例如每 5 分钟一次）。
调度状态写入 data/state/_scheduler.json，多次调用不会重复执行未到期的任务。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from core.digest import write_report
from core.runner import default_context, run_source
from core.scheduler import is_due, source_is_due
from core.settings import Settings
from core.sources import load_sources
from core.store import atomic_write_json, file_lock, read_json


@dataclass
class TickReport:
    ran_sources: list[str] = field(default_factory=list)
    failed_sources: list[str] = field(default_factory=list)
    skipped_sources: list[str] = field(default_factory=list)
    generated_reports: list[str] = field(default_factory=list)
    report_paths: dict[str, dict[str, str]] = field(default_factory=dict)


def tick(settings: Settings, now: Optional[datetime] = None, ctx=None) -> TickReport:
    now = now or datetime.now().astimezone()
    out = TickReport()
    state_path = settings.paths.state_dir / "_scheduler.json"
    default_every = settings.defaults.get("schedule_every", "60m")
    ctx = ctx or default_context(settings)

    with file_lock(state_path):
        sched = read_json(state_path, default={"sources": {}, "reports": {}}) or {"sources": {}, "reports": {}}
        loaded = load_sources(settings)

        for sid, spec in sorted(loaded.specs.items()):
            if not spec.enabled:
                continue
            every = spec.schedule.get("every", default_every)
            try:
                due = source_is_due(every, sched["sources"].get(sid), now)
            except ValueError:
                out.skipped_sources.append(sid)
                continue
            if not due:
                continue
            res = run_source(spec, settings, ctx)
            sched["sources"][sid] = now.isoformat(timespec="seconds")
            (out.ran_sources if res.ok else out.failed_sources).append(sid)

        for plan in settings.reports:
            try:
                due = is_due(plan, sched["reports"].get(plan["id"]), now)
            except ValueError:
                continue
            if not due:
                continue
            out.report_paths[plan["id"]] = write_report(settings, plan, now)
            sched["reports"][plan["id"]] = now.isoformat(timespec="seconds")
            out.generated_reports.append(plan["id"])

        atomic_write_json(state_path, sched)
    return out
