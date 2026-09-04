"""
perception/initiative_inbox.py — 主动性候选统一聚合视图（只读，阶段一）

对应 `next_doc/initiative_systems_unification_plan.md` §4.1/§6 阶段一。

不新增任何持久化，不修改 `GoalBacklog`/`GrowthBacklog`/
`CapabilityQuestionStore`/`CapabilityOutlineSuggestionStore` 的内部数据
结构或行为，纯读取这四路现有存储已经落盘的数据，聚合成一个统一的
`InitiativeItem` 列表，供看板渲染成一个带分类筛选的"主动建议"收件箱。

四路来源到 `domain` 的映射：
    - `GoalBacklog` 里 `source == "agent_derived"` 且尚未被用户处理过的
      Goal（`soft_goal_deriver` 产出）→ domain="agent_behavior"
    - `GrowthBacklog.pending()`（`growth_advisor` 产出）→ domain="user_growth"
    - `CapabilityQuestionStore.list_questions(status="pending")`
      （`capability_learning` 的异步问答队列）→ domain="agent_knowledge"
    - `CapabilityOutlineSuggestionStore.list_suggestions(status="pending")`
      （`capability_learning` 的大纲生长建议）→ domain="agent_knowledge"

任何单路来源读取失败都不应影响其它三路——每路各自 try/except，失败时
该路贡献空列表，与 `sentinel.py::sentinel_summary()`、
`fairness_diagnostics.py::fairness_diagnostics_snapshot()` 等既有只读
聚合模块同一风格（宁可少展示一路，不要因为一路异常搞坏整个视图）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

# domain 枚举值，供调用方（kanban）做分类筛选。
DOMAIN_USER_GROWTH = "user_growth"          # 成长顾问：服务用户自己的成长
DOMAIN_AGENT_KNOWLEDGE = "agent_knowledge"  # 能力学习：服务 Agent 自身知识/人设
DOMAIN_AGENT_BEHAVIOR = "agent_behavior"    # soft_goal_deriver：服务 Agent 自身行为

_ALL_DOMAINS = (DOMAIN_USER_GROWTH, DOMAIN_AGENT_KNOWLEDGE, DOMAIN_AGENT_BEHAVIOR)


@dataclass
class InitiativeItem:
    """统一后的候选展示项——只读聚合结果，不是新的持久化实体。"""

    item_id: str
    domain: str                      # user_growth / agent_knowledge / agent_behavior
    source_system: str               # "growth_advisor" / "capability_learning" / "soft_goal_deriver"
    kind: str                        # 该来源内部的候选类型，纯展示，如 "candidate"/"question"/"outline_suggestion"/"goal"
    title: str
    detail: str = ""                 # rationale / question 正文 / description 等，纯展示
    confidence: Optional[float] = None
    created_at: float = 0.0
    # 原始记录在其来源存储里的主键，便于调用方回跳到对应模块的原生操作
    # （accept/dismiss/answer 等）——聚合层本身不提供写操作。
    native_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    # [next_doc/initiative_systems_unification_plan.md §4.5 阶段四，
    # 只读排序建议] 与当前"用户处境"（active WorkThread + active Goal）
    # 的相关度，0~1，`None` 表示未计算（`initiative_inbox_snapshot(
    # annotate_relevance=False)` 时的默认状态，向后兼容旧调用方——不
    # 传这个参数、或显式传 False 时，返回结构与阶段四改动前完全一致）。
    # 只是展示用的排序参考信号，**不改变**任何一路候选生成/排序的既有
    # 逻辑本身，也不影响 `items` 列表默认的按 `created_at` 排序。
    situational_relevance: Optional[float] = None
    situational_relevance_source: Optional[str] = None  # 最相关的处境信号标题，纯展示

    def to_dict(self) -> dict:
        d = {
            "item_id": self.item_id,
            "domain": self.domain,
            "source_system": self.source_system,
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "native_id": self.native_id,
        }
        if self.extra:
            d["extra"] = self.extra
        if self.situational_relevance is not None:
            d["situational_relevance"] = self.situational_relevance
            if self.situational_relevance_source:
                d["situational_relevance_source"] = self.situational_relevance_source
        return d


def _from_growth_advisor(paths) -> list[InitiativeItem]:
    try:
        from mini_agent.evolution.growth_advisor import GrowthBacklog

        backlog = GrowthBacklog(paths)
        items: list[InitiativeItem] = []
        for c in backlog.pending():
            items.append(
                InitiativeItem(
                    item_id=f"growth:{c.candidate_id}",
                    domain=DOMAIN_USER_GROWTH,
                    source_system="growth_advisor",
                    kind="candidate",
                    title=c.title,
                    detail=c.rationale,
                    confidence=c.confidence,
                    created_at=c.created_at,
                    native_id=c.candidate_id,
                    extra={"evidence_count": c.evidence_count, "origin": c.origin},
                )
            )
        return items
    except Exception:
        return []


def _from_capability_learning(paths) -> list[InitiativeItem]:
    try:
        from mini_agent.evolution.capability_learning import (
            CapabilityOutlineSuggestionStore,
            CapabilityQuestionStore,
        )

        items: list[InitiativeItem] = []

        q_store = CapabilityQuestionStore(paths)
        for q in q_store.list_questions(status="pending"):
            items.append(
                InitiativeItem(
                    item_id=f"capability_question:{q.question_id}",
                    domain=DOMAIN_AGENT_KNOWLEDGE,
                    source_system="capability_learning",
                    kind="question",
                    title=q.question,
                    detail=q.hint or "",
                    confidence=None,
                    created_at=q.created_at,
                    native_id=q.question_id,
                    extra={"track_id": q.track_id, "topic_id": q.topic_id},
                )
            )

        s_store = CapabilityOutlineSuggestionStore(paths)
        for s in s_store.list_suggestions(status="pending"):
            items.append(
                InitiativeItem(
                    item_id=f"capability_outline:{s.suggestion_id}",
                    domain=DOMAIN_AGENT_KNOWLEDGE,
                    source_system="capability_learning",
                    kind="outline_suggestion",
                    title=s.suggested_name,
                    detail=s.rationale,
                    confidence=None,
                    created_at=s.created_at,
                    native_id=s.suggestion_id,
                    extra={"track_id": s.track_id, "origin": s.source},
                )
            )
        return items
    except Exception:
        return []


def _from_soft_goal_deriver(paths) -> list[InitiativeItem]:
    """agent_derived 的 Goal 本身就是 soft_goal_deriver 的候选——它没有
    独立的 pending 队列，"候选"和"已写入 GoalBacklog"是同一件事（见
    soft_goal_deriver.py 模块 docstring）。这里只挑用户还没有明确表态过
    的（`status` 仍是初始的 "active"，且从未被 touch 过——`GoalBacklog.
    add_goal()` 创建时 `created_at`/`last_touched_at` 是两次几乎同时的
    `time.time()` 调用，允许几秒内的误差都视为"从未被单独 touch 过"，
    一旦差值明显变大就说明用户或执行引擎真的碰过这个节点，不再是
    "待处理候选"的语义）视为待处理候选，已经在推进/被处理过的不重复
    展示。"""
    try:
        from mini_agent.perception.goal_backlog import GoalBacklog

        backlog = GoalBacklog(paths)
        backlog.load()
        items: list[InitiativeItem] = []
        for node in backlog.all_nodes():
            if node.source != "agent_derived":
                continue
            if node.status != "active":
                continue
            if node.last_touched_at and abs(node.last_touched_at - node.created_at) > 5.0:
                continue
            items.append(
                InitiativeItem(
                    item_id=f"soft_goal:{node.id}",
                    domain=DOMAIN_AGENT_BEHAVIOR,
                    source_system="soft_goal_deriver",
                    kind="goal",
                    title=node.title,
                    detail=node.description,
                    confidence=None,
                    created_at=node.created_at,
                    native_id=node.id,
                    extra={"level": node.level},
                )
            )
        return items
    except Exception:
        return []


def initiative_inbox_snapshot(
    paths, *, domains: Optional[list[str]] = None, limit: int = 100,
    annotate_relevance: bool = True,
) -> dict[str, Any]:
    """聚合三条主动性管线（成长顾问 / 能力学习 / soft_goal_deriver）当前
    待用户处理的候选，按 `created_at` 倒序（最新在前）返回。

    `domains` 为 None 时返回全部三个 domain；传入子集时只聚合对应来源
    （跳过读取，不是先读全部再过滤，避免不必要的磁盘 I/O）。

    `annotate_relevance`：[next_doc/initiative_systems_unification_
    plan.md §4.5 阶段四] 默认 `True`——给每一项附加
    `situational_relevance`/`situational_relevance_source`（见
    `perception/situational_relevance.py`），供前端在候选旁标注"与你
    当前处境的相关度"，**不改变** `items` 的默认排序（仍按
    `created_at` 倒序，见上方说明），也不影响任何单路候选的生成逻辑。
    传 `False` 时完全跳过这一步（不读取 `work_index`/`GoalBacklog`），
    返回结构与阶段四改动前完全一致——旧调用方不传这个新参数时默认值是
    `True`，会多出这两个字段，但既有字段的值/顺序不受影响，属于纯新增
    字段的向后兼容扩展，不是破坏性变更。计算失败（比如
    `situational_relevance` 模块导入异常）时整体降级为不标注，不影响
    收件箱本身的展示，同 §4.1 阶段一"单路异常不搞坏整个视图"的一贯
    容错风格。

    任何异常都不向上抛出——单路失败返回空列表，见模块顶部说明。
    """
    want = set(domains) if domains else set(_ALL_DOMAINS)
    items: list[InitiativeItem] = []
    if DOMAIN_USER_GROWTH in want:
        items.extend(_from_growth_advisor(paths))
    if DOMAIN_AGENT_KNOWLEDGE in want:
        items.extend(_from_capability_learning(paths))
    if DOMAIN_AGENT_BEHAVIOR in want:
        items.extend(_from_soft_goal_deriver(paths))

    items.sort(key=lambda it: it.created_at, reverse=True)
    items = items[: max(0, limit)]

    if annotate_relevance and items:
        try:
            from mini_agent.perception import situational_relevance as sr

            context = sr.load_situational_context(paths)
            if not context.is_empty:
                for it in items:
                    score, best = sr.score_relevance(f"{it.title} {it.detail}", context)
                    it.situational_relevance = round(score, 4)
                    it.situational_relevance_source = best.title if best else None
        except Exception:
            pass

    counts_by_domain = {d: 0 for d in _ALL_DOMAINS}
    for it in items:
        counts_by_domain[it.domain] = counts_by_domain.get(it.domain, 0) + 1

    return {
        "generated_at": time.time(),
        "total": len(items),
        "counts_by_domain": counts_by_domain,
        "items": [it.to_dict() for it in items],
    }
