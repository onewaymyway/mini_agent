"""
role_agents/judge_calibration.py — 判官校准事件记录（阶段 0：观测先行）

背景：`next_doc/autonomous_execution_stability_and_self_learning_integration_plan.md`
方案 D.4 / 方案 E。TurnJudge、GoalJudge、Evaluator/Coach 各自独立运行、各自
判定，目前没有任何地方系统性地记录：

  1. 判官判定与"后续实际走向"是否一致（比如上一轮判 CONTINUE，下一轮立刻
     判 DONE，可能说明上一轮偏保守；或者用户在 NEED_USER 场景手动纠正了
     判官的结论）；
  2. 同一轮内多个判官给出的信号是否互相矛盾（比如 TurnJudge 判
     AUTO_CONTINUE，同轮 Evaluator 却给出很低的质量分）。

这两类信号目前只是"发生了就发生了"，没有留下任何痕迹，无法回答"判官整体
靠不靠谱、哪类场景容易误判"这样的问题。本模块只做最小成本的记录，不做任何
自动决策调整——是否要用这些数据去调整判官 prompt/阈值，属于后续阶段
（方案 D.4 后半段 / 阶段 4），且明确要求"先生成建议、人工确认"，不在本模块
自动发生。

设计取舍（参照 `evolution/recovery_event_log.py` 的"轻量、有容量上限、允许
非持久化"思路，但这里的事件对复盘更有价值，选择追加写 JSONL 落盘而非纯内存
环形缓冲，成本仍然很低——调用频率等同于判官调用频率，本身就不高）：

  - 总开关不存在——记录本身几乎零成本（一次 append），不需要像
    auto_quarantine 那样设计"默认关闭"的总开关；调用方（各判官接线点）
    决定什么时候调用，本模块只负责"存下来"这一件事。
  - 任何异常都不向上抛出，不能因为记录失败影响主流程。
  - 文件按项目 `.agent/` 目录落盘，与其它运行时状态文件放在一起。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


_CALIBRATION_LOG_NAME = "judge_calibration_events.jsonl"
_CONFLICT_LOG_NAME = "judge_conflict_events.jsonl"

# 单文件最多保留的事件数，超过后做一次"只保留最近 N 条"的裁剪，避免文件
# 无限增长（这是复盘/统计用的辅助数据，不是审计日志，裁剪掉的旧记录可以
# 接受丢失）。
_MAX_EVENTS_PER_FILE = 5000


def _log_path(paths: "AgentPaths", name: str) -> Path:
    agent_dir = Path(paths.project_root) / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    return agent_dir / name


def _append_event(path: Path, event: dict) -> None:
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        return

    # 简单的容量裁剪：文件行数远超上限时才触发（避免每次写入都读全文件），
    # 用一个粗粒度的文件大小阈值近似判断，省去精确计数的开销。
    try:
        if path.stat().st_size > 4 * 1024 * 1024:  # 粗略阈值：4MB
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > _MAX_EVENTS_PER_FILE:
                trimmed = lines[-_MAX_EVENTS_PER_FILE:]
                path.write_text("\n".join(trimmed) + "\n", encoding="utf-8")
    except Exception:
        return


def record_calibration_event(
    paths: "AgentPaths",
    *,
    judge_name: str,
    status: str,
    round_no: int = 0,
    session_id: str = "",
    note: str = "",
    outcome_hint: str = "",
) -> None:
    """记录一次判官判定事件（阶段 0：只记录，不驱动任何决策）。

    judge_name: "turn_judge" / "goal_judge" 等
    status: 判官给出的状态（如 AUTO_CONTINUE / CONTINUE / DONE）
    outcome_hint: 可选，调用方对"这次判定后续是否被证明合理"的简单描述
        （如 "下一轮立即转为 DONE，疑似上一轮偏保守"、"用户手动纠正为 NEED_USER"）。
        留空表示暂无可用的后续信号，只是记录判定本身。
    """
    try:
        event = {
            "ts": time.time(),
            "judge_name": judge_name,
            "status": status,
            "round": round_no,
            "session_id": session_id,
            "note": note,
            "outcome_hint": outcome_hint,
        }
        _append_event(_log_path(paths, _CALIBRATION_LOG_NAME), event)
    except Exception:
        return


def record_conflict_event(
    paths: "AgentPaths",
    *,
    judge_a: str,
    status_a: str,
    judge_b: str,
    status_b: str,
    round_no: int = 0,
    session_id: str = "",
    context: str = "",
) -> None:
    """记录一次"同一轮内多个判官信号矛盾"的事件（方案 E 阶段 0：仅记录）。

    调用方负责判断"矛盾"的语义（比如一方倾向继续、一方倾向打断/压缩），
    本函数不做任何语义判断，只负责落盘，供后续复盘评估冲突频率是否值得
    投入阶段 4 的"取最保守值"改造。
    """
    try:
        event = {
            "ts": time.time(),
            "judge_a": judge_a,
            "status_a": status_a,
            "judge_b": judge_b,
            "status_b": status_b,
            "round": round_no,
            "session_id": session_id,
            "context": context,
        }
        _append_event(_log_path(paths, _CONFLICT_LOG_NAME), event)
    except Exception:
        return


# ── 保守优先级：用于方案 E 阶段 4"取多判官最保守值"的静态映射 ──────────────
# 数值越小越保守（越倾向于打断/压缩而不是放行）。调用方（未来阶段 4）可以
# 用这个映射在检测到冲突时选择更保守的一方，本阶段暂不接入任何自动决策，
# 仅提供给 record_conflict_event 的调用方在需要时参考。
_CONSERVATISM_RANK = {
    "NEED_USER": 0,
    "NEED_COMPACT": 1,
    "CONTINUE": 2,
    "AUTO_CONTINUE_WITH_NOTE": 3,
    "AUTO_CONTINUE": 4,
    "DONE": 5,
}


def more_conservative_status(status_a: str, status_b: str) -> str:
    """给定两个判定状态，返回更保守的一个（用于未来阶段 4 的冲突消解，
    当前仅供调用方按需使用，不在本模块内自动生效）。未知状态一律视为
    "最保守"，避免因为状态名不认识而误判为可以放行。
    """
    rank_a = _CONSERVATISM_RANK.get(status_a, -1)
    rank_b = _CONSERVATISM_RANK.get(status_b, -1)
    return status_a if rank_a <= rank_b else status_b


# 公开别名，供同目录下其它轻量事件记录模块（如 execution_notes.py）复用同一套
# "落盘路径 + 追加写 + 容量裁剪"实现，避免重复造轮子。刻意不完全私有化这两个
# 函数，但仍然按"内部工具函数"对待，不建议外部直接依赖其签名细节。
log_path = _log_path
append_event = _append_event


__all__ = [
    "record_calibration_event",
    "record_conflict_event",
    "more_conservative_status",
    "log_path",
    "append_event",
]
