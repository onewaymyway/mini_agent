"""
evolution/next_action_advisor.py — 主动推荐排序层（设计方案第 4.2 节，阶段二）

明确定位：这是 soft_goal_deriver.py 的"排序 + 讲道理"层，而不是重新做候选发现。
soft_goal_deriver 负责"发现该不该新建一个 Goal"（写入 GoalBacklog），
本模块负责"在已经存在的候选里，这次该优先提醒用户哪一个、为什么"（只读，不写 GoalBacklog）。

按改进计划分两步：
  1. 规则层（本文件已实现，默认路径）：只用结构化规则筛出候选，不接 LLM。
     先跑一段时间观察规则本身是否准，再决定是否进入第 2 步。
  2. LLM 排序层（rank_with_llm=True 时启用）：对规则筛出的候选做一次 LLM 调用，
     要求输出必须带 evidence_refs，不允许无引用的理由。

克制阈值：候选为空时返回 None，不生成"凑数"的建议。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Optional

from mini_agent.storage.paths import AgentPaths

# 停滞判定：高优先级（priority >= STALE_PRIORITY_FLOOR）且超过此天数无 last_touched_at 更新
STALE_DAYS = 7
STALE_PRIORITY_FLOOR = 1

# 注意力错配判定：最近窗口内，若某个应用/域名的时长占比超过该阈值，
# 且其名称/域名与任何 active Goal 的 title/tags 都没有关键词重合，则判定为可能错配。
ATTENTION_WINDOW_HOURS = 6
ATTENTION_MISMATCH_RATIO = 0.5


@dataclass
class Candidate:
    kind: str  # "stale_goal" | "attention_mismatch"
    ref_id: str
    title: str
    reason: str
    evidence_refs: list[str] = field(default_factory=list)
    rank: int = 0


def _find_stale_active_goals(paths: AgentPaths) -> list[Candidate]:
    try:
        from mini_agent.perception.goal_backlog import load_goal_backlog
    except Exception:
        return []

    try:
        backlog = load_goal_backlog(paths)
    except Exception:
        return []

    now = time.time()
    out: list[Candidate] = []
    for node in backlog.active_goals() + backlog.active_objectives():
        if node.priority < STALE_PRIORITY_FLOOR:
            continue
        last_touched = node.last_touched_at or node.created_at
        if not last_touched:
            continue
        stale_days = (now - last_touched) / 86400
        if stale_days >= STALE_DAYS:
            out.append(
                Candidate(
                    kind="stale_goal",
                    ref_id=node.id,
                    title=node.title,
                    reason=f"已 {stale_days:.0f} 天无进展记录，优先级 {node.priority}",
                    evidence_refs=[f"goal:{node.id}"],
                )
            )
    return out


def _keyword_overlap(text_a: str, tags: list[str], text_b: str) -> bool:
    hay = (text_a + " " + " ".join(tags)).lower()
    for token in text_b.lower().replace("_", " ").replace("-", " ").split():
        if len(token) >= 2 and token in hay:
            return True
    return False


def _find_attention_mismatch(paths: AgentPaths) -> list[Candidate]:
    try:
        from mini_agent.perception.behavior.manager import BehaviorPerceptionManager
        from mini_agent.perception.goal_backlog import load_goal_backlog
    except Exception:
        return []

    try:
        mgr = BehaviorPerceptionManager(project_root=paths.project_root)
        backlog = load_goal_backlog(paths)
    except Exception:
        return []

    since = time.time() - ATTENTION_WINDOW_HOURS * 3600
    try:
        events = mgr.query(since=since, limit=100000)
    except Exception:
        return []

    duration: dict[str, float] = {}
    for e in events:
        key = getattr(e, "app_name", None) or getattr(e, "domain", None)
        dur = getattr(e, "duration_sec", 0.0) or 0.0
        if key and dur:
            duration[key] = duration.get(key, 0.0) + dur

    total = sum(duration.values())
    if total <= 0:
        return []

    active_goals = backlog.active_goals() + backlog.active_objectives()
    out: list[Candidate] = []
    for key, dur in duration.items():
        ratio = dur / total
        if ratio < ATTENTION_MISMATCH_RATIO:
            continue
        matched = any(_keyword_overlap(g.title, g.tags, key) for g in active_goals)
        if matched:
            continue
        out.append(
            Candidate(
                kind="attention_mismatch",
                ref_id=key,
                title=key,
                reason=(
                    f"最近 {ATTENTION_WINDOW_HOURS} 小时内 {ratio:.0%} 的时间花在"
                    f" {key} 上，但没有关联到任何登记中的目标"
                ),
                evidence_refs=[f"behavior_window:{int(since)}-{int(time.time())}:{key}"],
            )
        )
    return out


def _rule_based_rank(candidates: list[Candidate]) -> list[Candidate]:
    """规则层排序：stale_goal 优先于 attention_mismatch（前者是明确的既定目标，
    后者只是"可能"分心，确定性更低），同类内部按 ref_id 稳定排序。
    """
    order = {"stale_goal": 0, "attention_mismatch": 1}
    ranked = sorted(candidates, key=lambda c: (order.get(c.kind, 9), c.ref_id))
    for i, c in enumerate(ranked):
        c.rank = i + 1
    return ranked


def _llm_rank(candidates: list[Candidate], llm_helper) -> list[Candidate]:
    """LLM 排序层（阶段二第 2 步，默认不启用）。要求模型只对已有候选重新排序、
    补充理由，理由必须引用 evidence_refs 中已有的条目，不允许引入新事实。
    llm_helper 失败时静默回退到规则排序，不让整个 advisor 因为一次 LLM 调用
    失败而无法产出结果。
    """
    if llm_helper is None or not candidates:
        return _rule_based_rank(candidates)

    payload = [
        {"ref_id": c.ref_id, "title": c.title, "reason": c.reason, "evidence_refs": c.evidence_refs}
        for c in candidates
    ]
    prompt = (
        "以下是若干条基于用户真实行为/目标数据生成的候选建议，请只对它们重新排序"
        "（不要新增候选、不要编造未在 evidence_refs 中出现的理由），"
        "返回 JSON 数组，元素为 {ref_id, reason}，按建议优先级从高到低排列：\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    try:
        raw = llm_helper.complete(prompt)
        parsed = json.loads(_extract_json_array(raw))
        by_id = {c.ref_id: c for c in candidates}
        ranked = []
        for i, item in enumerate(parsed):
            c = by_id.get(item.get("ref_id"))
            if c is None:
                continue
            if item.get("reason"):
                c.reason = item["reason"]
            c.rank = i + 1
            ranked.append(c)
        # 兜底：LLM 漏掉的候选追加在末尾，保证不丢建议
        seen = {c.ref_id for c in ranked}
        for c in candidates:
            if c.ref_id not in seen:
                c.rank = len(ranked) + 1
                ranked.append(c)
        return ranked
    except Exception:
        return _rule_based_rank(candidates)


def _extract_json_array(text: str) -> str:
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return "[]"
    return text[start : end + 1]


def generate_next_actions(
    paths: AgentPaths, *, rank_with_llm: bool = False, llm_helper=None
) -> Optional[dict]:
    """生成一批推荐候选并落盘。候选为空时返回 None（克制阈值，见模块头注释）。"""
    candidates = _find_stale_active_goals(paths) + _find_attention_mismatch(paths)
    if not candidates:
        return None

    ranked = _llm_rank(candidates, llm_helper) if rank_with_llm else _rule_based_rank(candidates)

    data = {
        "generated_at": time.time(),
        "shown_at": None,
        "items": [
            {
                "rank": c.rank,
                "kind": c.kind,
                "ref_id": c.ref_id,
                "title": c.title,
                "reason": c.reason,
                "evidence_refs": c.evidence_refs,
            }
            for c in ranked
        ],
    }

    paths.next_actions_path.parent.mkdir(parents=True, exist_ok=True)
    paths.next_actions_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data


def render_startup_summary(data: dict) -> Optional[str]:
    if not data or not data.get("items"):
        return None
    top = data["items"][0]
    return f"💡 建议：{top['title']}——{top['reason']}（`/next` 查看全部）"


def load_pending_next_actions(paths: AgentPaths) -> Optional[dict]:
    p = paths.next_actions_path
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data.get("shown_at"):
        return None
    return data


def mark_shown(paths: AgentPaths) -> None:
    p = paths.next_actions_path
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return
    data["shown_at"] = time.time()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
