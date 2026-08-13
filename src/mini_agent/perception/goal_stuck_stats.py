"""
perception/goal_stuck_stats.py — Goal Stuck 历史统计（只读聚合）

（next_doc/goal_stuck_stats_and_llm_progress_judge_plan.md §1）

不新增任何存储，纯读取 `goal_mode/state.py::list_resumable_sessions()`
已经在扫描的 `goal_state.json`（`status=="stuck"` 是 `goal_mode/runner.py`
恢复次数耗尽后写入的终态）做聚合，回答"这个项目历史上到底有多少次
Goal 被判定卡住"，供 `goal_execution_fairness_improvement_plan.md` 改造
项四（并行多路径择优）之类更高成本机制的立项决策提供真实频率参考。

任何异常/目录不存在都返回全零结构，不抛异常，与既有
`sentinel.py::sentinel_summary()` 的只读聚合风格一致。
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any


def _empty_summary(recent_days: int) -> dict[str, Any]:
    return {
        "total_sessions": 0,
        "stuck_count": 0,
        "stuck_ratio": 0.0,
        "recent_stuck_count": 0,
        "recent_window_days": recent_days,
        "top_stuck_goal_texts": [],
        "generated_at": time.time(),
    }


def stuck_stats_summary(project_root, recent_days: int = 30) -> dict[str, Any]:
    """聚合 sessions_dir 下全部 `goal_state.json` 的 stuck 统计。

    - `total_sessions`：有 goal_state.json 的会话总数（不限状态）。
    - `stuck_count`/`stuck_ratio`：status=="stuck" 的会话数量/占比。
    - `recent_stuck_count`：最近 `recent_days` 天内更新的 stuck 会话数量
      （`updated_at` 缺失的记录不计入，视为无法判断新旧）。
    - `top_stuck_goal_texts`：按 `goal_text`（去除首尾空白后完全匹配）
      归并出现次数最多的若干条，同一个目标反复被判 stuck 往往说明目标
      描述/验收标准本身有问题，比孤立的一次更值得关注；每条附
      `count`/`last_updated_at`/`last_final_report_excerpt`（截断至 120
      字，供面板一行摘要展示，不做完整正文渲染）。
    """
    if project_root is None:
        return _empty_summary(recent_days)
    try:
        from mini_agent.goal_mode.state import list_resumable_sessions

        sessions = list_resumable_sessions(project_root, include_all=True)
    except Exception:
        return _empty_summary(recent_days)

    total = len(sessions)
    stuck_sessions = [s for s in sessions if s.get("status") == "stuck"]
    stuck_count = len(stuck_sessions)

    now = time.time()
    cutoff = now - recent_days * 86400
    recent_stuck = [
        s for s in stuck_sessions
        if isinstance(s.get("updated_at"), (int, float)) and s["updated_at"] >= cutoff
    ]

    by_text: dict[str, dict[str, Any]] = {}
    for s in stuck_sessions:
        text = (s.get("goal_text") or "").strip()
        if not text:
            text = "（未记录目标描述）"
        entry = by_text.setdefault(text, {"count": 0, "last_updated_at": None, "final_report": ""})
        entry["count"] += 1
        updated_at = s.get("updated_at")
        if isinstance(updated_at, (int, float)) and (
            entry["last_updated_at"] is None or updated_at > entry["last_updated_at"]
        ):
            entry["last_updated_at"] = updated_at
            entry["final_report"] = s.get("final_report") or ""

    top_texts = sorted(by_text.items(), key=lambda kv: (-kv[1]["count"], -(kv[1]["last_updated_at"] or 0)))
    top_stuck_goal_texts = [
        {
            "goal_text": text,
            "count": info["count"],
            "last_updated_at": info["last_updated_at"],
            "last_final_report_excerpt": (info["final_report"] or "")[:120],
        }
        for text, info in top_texts[:10]
    ]

    return {
        "total_sessions": total,
        "stuck_count": stuck_count,
        "stuck_ratio": (stuck_count / total) if total else 0.0,
        "recent_stuck_count": len(recent_stuck),
        "recent_window_days": recent_days,
        "top_stuck_goal_texts": top_stuck_goal_texts,
        "generated_at": now,
    }
