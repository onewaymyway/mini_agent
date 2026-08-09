"""
evolution/objective_trend.py — Objective 完成率每日趋势快照
（kanban_perception_gaps_improvement_plan.md 方向 D.1）

背景：看板"📌 目标看板"Tab 只有"当下"的状态分列展示，看不出"这周完成
的 Objective 比上周多还是少""平均一个 Objective 要重试几次才能完成"
这类趋势。这里复用 `growth_advisor.py::_record_health_snapshot()`
"daemon 每日收尾时记一条快照"的既有模式（通过 `perception/
daily_snapshot.py` 抽出的通用小工具），不新增任何线程/独立 cron——
挂载点是 `POST /v1/growth/scan`（cron `sys:growth_advisor_daily` 已经
每日调用的既有路由），成长顾问的每日周期跑完之后顺带记一条 Objective
快照，best-effort、不影响成长顾问自身的返回结果。

字段选取原则（跟 `_record_health_snapshot()` 一致）：只取
`.agent/objective_executions.json` 里已经存在的字段直接统计，不引入
新的统计口径。
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from mini_agent.perception.daily_snapshot import (
    append_daily_snapshot,
    compact_daily_snapshot_storage,
    read_daily_snapshot_series,
)

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

_DEFAULT_TREND_MAX_POINTS = 30
_RAW_WINDOW_DAYS = 60.0


def _load_executions(paths: "AgentPaths") -> list[dict]:
    exec_path = paths.workdir_dir / "objective_executions.json"
    if not exec_path.exists():
        return []
    try:
        data = json.loads(exec_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data.get("executions") or []


def compute_objective_completion_snapshot(paths: "AgentPaths", *, now: float = None) -> dict[str, Any]:
    """纯计算：从 `.agent/objective_executions.json` 统计当天完成/失败数
    + 平均重试次数 + 当前活跃 Objective 数，不做任何写盘。供
    `record_objective_completion_snapshot()` 和单测复用。

    "今天"以 `now`（默认当前时间）所在自然日的 [00:00, 24:00) 本地时区
    窗口为准，跟 `finished_at` 落在这个窗口内的 execution 才计入
    completed/failed 计数——`active_goals_count` 则是"当下"的快照，不
    受时间窗口限制（跟 completed/failed 的语义不同：一个是"今天发生了
    多少次"，一个是"现在还有多少个在跑"）。
    """
    now = now if now is not None else time.time()
    day_start = int(now // 86400) * 86400
    day_end = day_start + 86400

    executions = _load_executions(paths)
    completed_today = 0
    failed_today = 0
    retry_counts: list[int] = []
    active_count = 0

    for ex in executions:
        status = ex.get("status", "")
        finished_at = ex.get("finished_at") or 0.0
        if status == "completed" and day_start <= finished_at < day_end:
            completed_today += 1
        elif status == "failed" and day_start <= finished_at < day_end:
            failed_today += 1
        if status in ("running", "pending", "paused", "paused_for_fairness", "paused_by_user"):
            active_count += 1
        for step in (ex.get("steps") or []):
            rc = step.get("retry_count")
            if rc:
                retry_counts.append(int(rc))

    avg_retry_count = round(sum(retry_counts) / len(retry_counts), 2) if retry_counts else 0.0

    return {
        "recorded_at": now,
        "objectives_completed_today": completed_today,
        "objectives_failed_today": failed_today,
        "avg_retry_count": avg_retry_count,
        "active_goals_count": active_count,
    }


def record_objective_completion_snapshot(paths: "AgentPaths") -> dict[str, Any]:
    """计算并追加一条快照到 `objective_completion_trend.jsonl`，返回写入
    的快照字典。只应该在既有的每日调用点（`POST /v1/growth/scan`）
    触发，不应该被其它地方高频调用——原则跟
    `growth_advisor._record_health_snapshot()` 一致。"""
    row = compute_objective_completion_snapshot(paths)
    append_daily_snapshot(paths.objective_completion_trend_path, row)
    return row


def objective_completion_trend_series(paths: "AgentPaths", *, limit: int = _DEFAULT_TREND_MAX_POINTS) -> list[dict]:
    """返回最近 `limit` 个快照，按时间正序，供看板画折线图 / API
    `GET /v1/objectives/completion_trend` 直接返回。"""
    return read_daily_snapshot_series(paths.objective_completion_trend_path, limit=limit)


def compact_objective_completion_trend_storage(paths: "AgentPaths", *, now: float = None) -> int:
    """对落盘的 `objective_completion_trend.jsonl` 做一次降采样压缩，
    返回被压缩掉的行数。幂等操作。"""
    return compact_daily_snapshot_storage(
        paths.objective_completion_trend_path, raw_window_days=_RAW_WINDOW_DAYS, now=now,
    )
