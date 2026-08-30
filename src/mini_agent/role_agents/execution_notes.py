"""
role_agents/execution_notes.py — 低置信度自动继续场景的"事后可审阅摘要"

背景：`next_doc/autonomous_execution_stability_and_self_learning_integration_plan.md`
方案 C（分级响应）。TurnJudge 目前只有二元结果：AUTO_CONTINUE（不打断）或
NEED_USER（打断）。低置信度的 AUTO_CONTINUE——判官认为大概率不需要用户介入，
但把握不是特别足——目前只能"要么强行升级成 NEED_USER 打断用户"，"要么照常
AUTO_CONTINUE 但什么痕迹都不留"，两者都有成本：前者让用户被过度打扰，
后者让用户即使想审阅也无据可查。

本模块提供第三条路：不打断执行，但把这次低置信度判定的依据落一条可查阅的
"执行摘要"记录，用户可以事后批量查看，而不必每次都被迫实时响应。尤其适合
`goal_cron` 这类本来就是无人值守的场景。

设计取舍：
  - 纯追加 JSONL，容量有上限（复用 judge_calibration.py 的裁剪思路，避免
    重复实现，直接引入其内部裁剪函数）。
  - 只服务于"展示/审阅"，不驱动任何自动决策——高风险信号（process_flags、
    多判官冲突等）不允许走这条路径降级，必须继续直接 NEED_USER。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

_EXECUTION_NOTES_LOG_NAME = "execution_notes.jsonl"


def append_execution_note(
    paths: "AgentPaths",
    *,
    source: str,
    status: str,
    confidence: float,
    summary: str,
    round_no: int = 0,
    session_id: str = "",
) -> None:
    """记录一条"自动继续但置信度不高"的执行摘要，供用户事后审阅。

    source: 产生这条摘要的判官（如 "turn_judge"）
    status: 判官原本给出的状态（如 "AUTO_CONTINUE"）
    confidence: 判官自评的置信度（0-1）
    summary: 简短的判定依据/风险提示，供用户快速扫一眼判断是否需要深入看
    """
    try:
        from mini_agent.role_agents.judge_calibration import append_event, log_path

        event = {
            "ts": time.time(),
            "source": source,
            "status": status,
            "confidence": confidence,
            "summary": summary,
            "round": round_no,
            "session_id": session_id,
        }
        append_event(log_path(paths, _EXECUTION_NOTES_LOG_NAME), event)
    except Exception:
        return


def read_recent_execution_notes(paths: "AgentPaths", *, limit: int = 20) -> list[dict]:
    """读取最近 N 条执行摘要（供晨报/看板等展示层调用，本次改造暂不接入
    任何具体展示界面，只提供读取接口，接入界面留给后续独立改造）。"""
    import json

    try:
        path = Path(paths.project_root) / ".agent" / _EXECUTION_NOTES_LOG_NAME
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        rows: list[dict] = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        return rows
    except Exception:
        return []


__all__ = ["append_execution_note", "read_recent_execution_notes"]
