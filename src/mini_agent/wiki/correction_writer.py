"""wiki/correction_writer.py — 用户纠正事件回灌通道（F4）

背景见 next_doc/system_connectivity_gaps_and_missing_capabilities_plan.md
断点 C5：用户当场纠正 agent 判断时，此前只能走通用 lesson memory 记录，
等下一次巩固循环扫描才可能间接触达对应的 wiki 决策页，链路长且不保证
命中。

本模块只解决"能定位到具体决策页时应该更快"这个子问题：
  - `route_correction()` — 给定本轮 GoalJudge/判定引用过的决策页 id
    （来自 `wiki/decision_consumption.py::DecisionConsumptionQuery` 或
    `run_goal_judge()` 记录的 `referenced_page_ids`）+ 用户的纠正文本，
    直接调用 `wiki/lifecycle.py::mark_page_state()` 把该页标记为
    `stale`（复用既有生命周期状态机，不新增状态值），并在页面正文追加
    一段结构化的纠正记录（不覆盖原内容，保留沿革）。

无法定位具体页面（绝大多数纠正场景）时，调用方应继续走原有 lesson
memory 路径，本模块不覆盖这类情况——`route_correction()` 在拿不到
page_id 时直接返回 False，不做任何操作、不抛异常。

不做：不判断"这段用户输入是不是在纠正 agent"（识别属于调用方职责，
比如未来接入 `role_agents/reminders_correction.py` 的分类逻辑），本模块
只负责"已经确定是纠正、且知道要改哪个页面"之后的落盘动作。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

MAX_CORRECTION_TEXT_CHARS = 500


@dataclass
class CorrectionRouteResult:
    routed: bool
    page_id: str = ""
    reason: str = ""  # 未路由时的原因（如 "no_page_id"、"mark_failed"）


def _correction_log_path(paths: "AgentPaths"):
    return paths.wiki_dir / "correction_events.jsonl"


def _append_correction_log(paths: "AgentPaths", record: dict) -> None:
    try:
        p = _correction_log_path(paths)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception

        log_exception(_mini_agent_exc, where="mini_agent.wiki.correction_writer._append_correction_log")


def route_correction(
    paths: "AgentPaths",
    page_id: Optional[str],
    correction_text: str,
    *,
    source: str = "user_turn",
) -> CorrectionRouteResult:
    """把一次用户纠正回灌到具体的 wiki 页面。

    page_id 为空/None 时直接返回 routed=False（调用方应回退到 lesson
    memory 路径），不算错误。

    命中页面时：
      1. 调用 `mark_page_state(page_id, confidence="stale", reason=...,
         validated_by="user_correction")` —— 复用既有生命周期状态机，
         该页面下次被检索/巩固扫描时会被标注为陈旧，提示需要复核。
      2. 追加一条记录到 `wiki/correction_events.jsonl`（只读追加，供
         `sys:wiki_gap_scan`/看板一类巡检消费，不是独立 cron job）。

    与巩固循环的关系：本函数只做"标记 + 记录"，不重写页面内容本身——
    实际内容修正仍然走既有的 wiki 巩固/人工编辑流程，这里只是让"这个
    页面需要被重新看一眼"这件事发生得更快（同一 session 内，而不是等
    下一次巡检）。
    """
    if not page_id:
        return CorrectionRouteResult(routed=False, reason="no_page_id")

    truncated = (correction_text or "").strip()[:MAX_CORRECTION_TEXT_CHARS]

    try:
        from mini_agent.wiki.lifecycle import mark_page_state

        ok = mark_page_state(
            paths,
            page_id,
            confidence="stale",
            reason=f"用户纠正（{source}）：{truncated}",
            validated_by="user_correction",
        )
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception

        log_exception(_mini_agent_exc, where="mini_agent.wiki.correction_writer.route_correction")
        ok = False

    _append_correction_log(paths, {
        "ts": time.time(),
        "page_id": page_id,
        "source": source,
        "correction_text": truncated,
        "marked_stale": ok,
    })

    if not ok:
        return CorrectionRouteResult(routed=False, page_id=page_id, reason="mark_failed")
    return CorrectionRouteResult(routed=True, page_id=page_id)


def recent_correction_events(paths: "AgentPaths", limit: int = 20) -> list[dict]:
    """供看板/晨报只读消费：返回最近若干条纠正事件记录。"""
    p = _correction_log_path(paths)
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()[-limit:]
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception

        log_exception(_mini_agent_exc, where="mini_agent.wiki.correction_writer.recent_correction_events")
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


__all__ = [
    "CorrectionRouteResult",
    "route_correction",
    "recent_correction_events",
]
