"""E5：目标与定时任务视图的数据归一化（纯逻辑，不依赖 Qt）。

输入是 /v1/goals 的 objectives 与 /v1/cron/jobs 的 jobs（原样 dict），输出稳定排序的行。
字段缺失时给出保守的占位文字，不抛异常。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# 目标状态的展示顺序：进行中在前，已结束的沉到最后
_STATUS_ORDER = {"active": 0, "running": 0, "paused": 1, "blocked": 2, "done": 3, "completed": 3,
                 "abandoned": 4, "cancelled": 4}
_STATUS_LABEL = {"active": "进行中", "running": "进行中", "paused": "已暂停", "blocked": "受阻",
                 "done": "已完成", "completed": "已完成", "abandoned": "已放弃", "cancelled": "已取消"}
# cron 执行阶段排序：正在跑 → 排队 → 其它
_PHASE_ORDER = {"running": 0, "queued": 1}
_PHASE_LABEL = {"running": "运行中", "queued": "排队中", "not_running": "空闲"}


@dataclass(frozen=True)
class ObjectiveRow:
    id: str
    title: str
    status: str
    status_label: str
    progress: str


@dataclass(frozen=True)
class CronRow:
    id: str
    name: str
    enabled: bool
    next_run: str
    phase: str
    phase_label: str


def _text(value, default: str = "") -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def summarize_objectives(objectives: list[dict]) -> list[ObjectiveRow]:
    rows: list[ObjectiveRow] = []
    for obj in objectives or []:
        if not isinstance(obj, dict):
            continue
        status = _text(obj.get("status"), "unknown")
        progress = (_text(obj.get("work_thread_progress")) or _text(obj.get("progress_notes"))
                    or "（暂无进展记录）")
        rows.append(ObjectiveRow(
            id=_text(obj.get("id")),
            title=_text(obj.get("title"), "（未命名目标）"),
            status=status,
            status_label=_STATUS_LABEL.get(status, status),
            progress=progress.replace("\n", " ")[:200],
        ))
    rows.sort(key=lambda r: (_STATUS_ORDER.get(r.status, 9), r.title))
    return rows


def _next_key(next_run: Optional[str]) -> tuple:
    # 无下次时间的排在有时间之后；字符串按 ISO 格式可直接比较
    return (0, next_run) if next_run else (1, "")


def summarize_cron(jobs: list[dict]) -> list[CronRow]:
    rows: list[CronRow] = []
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        enabled = bool(job.get("enabled"))
        phase = _text(job.get("execution_phase"), "not_running")
        rows.append(CronRow(
            id=_text(job.get("id")),
            name=_text(job.get("name"), "（未命名任务）"),
            enabled=enabled,
            next_run=_text(job.get("next_run_str")) or _text(job.get("next_run_at"), "—") if enabled else "已停用",
            phase=phase,
            phase_label=_PHASE_LABEL.get(phase, phase),
        ))
    # 运行中 → 排队 → 已启用（按下次运行）→ 已停用
    rows.sort(key=lambda r: (_PHASE_ORDER.get(r.phase, 2), 0 if r.enabled else 1,
                             _next_key(None if r.next_run in ("—", "已停用") else r.next_run), r.name))
    return rows
