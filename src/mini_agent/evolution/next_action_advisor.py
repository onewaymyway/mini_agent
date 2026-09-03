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

配置化（本轮新增）：所有阈值此前是模块级常量，现在改为可选从
config/models.py::DigestAdvisorConfig 读取（agent_config.json 的
"digest_advisor" 字段），不传 cfg 时回退到模块级默认常量，保持向后兼容。

decision_profile 加权（本轮新增，见 4.4 节"初期用法 2"）：next_action_enabled
且 cfg.next_action_profile_weighting_enabled=True 时，读取
decision_profile_builder 产出的高置信度模式，对候选做"排序内加权"——
只调整同类候选之间的相对顺序，不改变候选本身、不引入新候选，符合方案里
"仅影响排序，不替代候选本身"的限定。

注意力错配 daemon 推送（本轮新增，见 4.3 节）：check_persistent_attention_mismatch()
供 AutonomousLoop._tick_passive() 调用，跟踪同一错配信号连续被检测到的时长，
超过阈值且未超过每会话推送次数上限时才返回待推送内容，避免打断式骚扰。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from mini_agent.storage.paths import AgentPaths

if TYPE_CHECKING:
    from mini_agent.config.models import DigestAdvisorConfig

# 停滞判定：高优先级（priority >= STALE_PRIORITY_FLOOR）且超过此天数无 last_touched_at 更新
# （以下模块级常量作为 cfg 未提供时的默认值，保持向后兼容）
STALE_DAYS = 7
STALE_PRIORITY_FLOOR = 1

# 注意力错配判定：最近窗口内，若某个应用/域名的时长占比超过该阈值，
# 且其名称/域名与任何 active Goal 的 title/tags 都没有关键词重合，则判定为可能错配。
ATTENTION_WINDOW_HOURS = 6
ATTENTION_MISMATCH_RATIO = 0.5

# daemon 推送默认阈值（cfg 未提供时使用）
PUSH_THRESHOLD_HOURS = 2.0
PUSH_MAX_PER_SESSION = 1


@dataclass
class Candidate:
    kind: str  # "stale_goal" | "attention_mismatch"
    ref_id: str
    title: str
    reason: str
    evidence_refs: list[str] = field(default_factory=list)
    rank: int = 0


def _find_stale_active_goals(
    paths: AgentPaths, *, stale_days: float = STALE_DAYS, priority_floor: int = STALE_PRIORITY_FLOOR
) -> list[Candidate]:
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
        if node.priority < priority_floor:
            continue
        last_touched = node.last_touched_at or node.created_at
        if not last_touched:
            continue
        days_since = (now - last_touched) / 86400
        if days_since >= stale_days:
            out.append(
                Candidate(
                    kind="stale_goal",
                    ref_id=node.id,
                    title=node.title,
                    reason=f"已 {days_since:.0f} 天无进展记录，优先级 {node.priority}",
                    evidence_refs=[f"goal:{node.id}"],
                )
            )
    return out


def _find_momentum_goals(
    paths: AgentPaths, *, window_days: float, min_recent_events: int, priority_floor: int = STALE_PRIORITY_FLOOR
) -> list[Candidate]:
    """[personal_researcher_and_coach_capability_gap_plan.md C3] 第三条
    规则："最近活跃度走势上升"的 Goal——跟 `_find_stale_active_goals`
    回答的是相反方向的问题：那条规则找"太久没碰、可能被遗忘了"的，
    这条规则找"最近正在被频繁推进、可能值得趁热打铁"的。

    信号来源：`GoalNode.status_history` 里的时间戳序列（Goal 没有像
    成长顾问那样独立的"证据快照"文件，状态变更次数的时间分布是目前
    最省成本的活跃度替代信号）。复用 `growth_advisor._recent_delta_
    from_series()` 做"最新累计数 - 窗口基线累计数"的趋势计算，跟
    P5-4 判断报告要不要刷新用的是同一套算法，避免另发明一套统计口径。

    只从 `stale_active_goals` 覆盖不到的活跃 Goal 里找（即"最近有触碰、
    没有停滞"的那些）——已经被判定为停滞的 Goal 不可能同时又"最近活跃
    度上升"，两条规则的候选集合天然不重叠，不需要额外去重。
    """
    try:
        from mini_agent.perception.goal_backlog import load_goal_backlog
        from mini_agent.evolution.growth_advisor import _recent_delta_from_series
    except Exception:
        return []

    try:
        backlog = load_goal_backlog(paths)
    except Exception:
        return []

    out: list[Candidate] = []
    for node in backlog.active_goals() + backlog.active_objectives():
        if node.priority < priority_floor:
            continue
        history = node.status_history or []
        if len(history) < 2:
            continue
        # 累计计数序列：第 i 条历史记录出现时，"到此为止一共发生过 i+1
        # 次状态变更"，用这个累计量而不是原始事件本身去套用
        # `_recent_delta_from_series` 的"最新点减基线点"算法。
        series: list[tuple[float, int]] = [
            (float(h.get("at", 0.0)), idx + 1)
            for idx, h in enumerate(sorted(history, key=lambda h: h.get("at", 0.0)))
        ]
        delta = _recent_delta_from_series(series, window_days=window_days)
        if delta is None or delta < min_recent_events:
            continue
        out.append(
            Candidate(
                kind="momentum_goal",
                ref_id=node.id,
                title=node.title,
                reason=f"最近 {window_days:.0f} 天内有 {delta} 次状态变更，活跃度正在上升，可能值得趁热打铁",
                evidence_refs=[f"goal:{node.id}"],
            )
        )
    return out


def _collect_current_focus_node_ids(backlog) -> list[str]:
    """收集全树里所有 active 结构节点 `current_focus_ids` 指向的直接子节点
    id（去重，不含结构节点自身）——即"现阶段焦点"覆盖到的全部节点，供
    `_find_focus_next_step_candidates()` 逐个生成建议。

    跟 `GoalBacklog.focus_research_nodes()`（阶段一新增）不是同一个概念：
    `focus_research_nodes()` 是"叶子 Goal + 焦点里的结构节点"这个特定
    组合，服务于外部调研相关性判断；这里要的是"current_focus_ids 里出现
    过的全部节点"（可能是 goal/objective，也可能是 domain/stage），服务
    于 §4.3 里针对不同 level 给不同建议的分支逻辑。
    """
    ids: list[str] = []
    seen: set[str] = set()
    for node in backlog.all_nodes():
        if not node.is_active or not node.is_structural:
            continue
        for child_id in node.current_focus_ids:
            if child_id not in seen:
                seen.add(child_id)
                ids.append(child_id)
    return ids


def _pending_focus_research_count(paths: AgentPaths, node_id: str) -> int:
    """[§4.3"有新调研素材待查看"] 复用阶段二 `FocusResearchTrigger` 落下的
    `origin=\"focus_research\"` 候选——只读查询，不触发新的调研、不改动
    `GrowthBacklog` 状态。`evidence_refs` 里带 `goal_tree:<node_id>` 前缀
    的候选即为该节点关联的调研素材（见 focus_research_trigger.py）。
    """
    try:
        from mini_agent.evolution.growth_advisor import GrowthBacklog
    except Exception:
        return 0
    try:
        backlog = GrowthBacklog(paths)
        pending = backlog.pending()
    except Exception:
        return 0
    ref = f"goal_tree:{node_id}"
    return sum(
        1 for c in pending
        if getattr(c, "origin", None) == "focus_research" and ref in (c.evidence_refs or [])
    )


def _find_focus_next_step_candidates(
    paths: AgentPaths, *, max_nodes: int = 20
) -> list[Candidate]:
    """[goal_tree_research_and_action_recommendation_plan.md §4.3] 焦点行动
    建议：只从既有信息（execution_spec_confirmed/decompose_candidates/
    focus_research 调研素材）里挑，不重新生成候选、不调用 LLM——延续
    `next_action_advisor` 一贯的"排序 + 讲道理"定位。

    每个焦点节点最多产出以下几类建议中命中的那些（同一节点可以同时命中
    多类，比如"待确认执行规范"+"有新调研素材"，各自成一条候选，`ref_id`
    带条件后缀区分，避免互相覆盖；§4.4 节奏治理层面的"同一节点同一时间
    只保留一份"由调用方/展示层收敛，这里只负责如实产出当前命中的建议）。
    """
    try:
        from mini_agent.perception.goal_backlog import load_goal_backlog
    except Exception:
        return []
    try:
        backlog = load_goal_backlog(paths)
    except Exception:
        return []

    focus_ids = _collect_current_focus_node_ids(backlog)[:max_nodes]
    out: list[Candidate] = []
    for node_id in focus_ids:
        node = backlog.get(node_id)
        if node is None or not node.is_active:
            continue

        if node.is_goal or node.is_objective:
            if getattr(node, "execution_spec_confirmed", False):
                progress_lines = [
                    line for line in (node.progress_notes or "").splitlines() if line.strip()
                ]
                latest = progress_lines[-1].strip() if progress_lines else None
                reason = "执行规范已确认，是现阶段焦点，建议继续推进"
                if latest:
                    reason += f"（最近进展：{latest}）"
                out.append(
                    Candidate(
                        kind="focus_next_step",
                        ref_id=f"{node.id}:continue",
                        title=node.title,
                        reason=reason,
                        evidence_refs=[f"goal:{node.id}"],
                    )
                )
            else:
                out.append(
                    Candidate(
                        kind="focus_next_step",
                        ref_id=f"{node.id}:spec",
                        title=node.title,
                        reason="是现阶段焦点，但还没有确认执行规范，建议先确认执行规范再推进",
                        evidence_refs=[f"goal:{node.id}"],
                    )
                )
        elif node.is_structural and node.decompose_candidates:
            n = len(node.decompose_candidates)
            out.append(
                Candidate(
                    kind="focus_next_step",
                    ref_id=f"{node.id}:decompose",
                    title=node.title,
                    reason=f"是现阶段焦点，有 {n} 个待确认的分解候选，建议去目标树页面处理",
                    evidence_refs=[f"goal:{node.id}"],
                )
            )

        material_count = _pending_focus_research_count(paths, node.id)
        if material_count:
            out.append(
                Candidate(
                    kind="focus_next_step",
                    ref_id=f"{node.id}:research",
                    title=node.title,
                    reason=f"是现阶段焦点，有 {material_count} 条待处理的调研素材待查看",
                    evidence_refs=[f"goal_tree:{node.id}"],
                )
            )
    return out


def _keyword_overlap(text_a: str, tags: list[str], text_b: str) -> bool:
    hay = (text_a + " " + " ".join(tags)).lower()
    for token in text_b.lower().replace("_", " ").replace("-", " ").split():
        if len(token) >= 2 and token in hay:
            return True
    return False


def _measure_attention_durations(paths: AgentPaths, window_hours: float) -> tuple[dict[str, float], float]:
    """扫描行为事件，返回 (每个 app/域名的累计时长, 窗口起始时间戳)。
    独立抽出来是为了让 check_persistent_attention_mismatch() 和
    _find_attention_mismatch() 共用同一份采集逻辑，不重复实现。
    """
    try:
        from mini_agent.perception.behavior.manager import BehaviorPerceptionManager
    except Exception:
        return {}, time.time() - window_hours * 3600

    since = time.time() - window_hours * 3600
    try:
        mgr = BehaviorPerceptionManager(project_root=paths.project_root)
        events = mgr.query(since=since, limit=100000)
    except Exception:
        return {}, since

    duration: dict[str, float] = {}
    for e in events:
        key = getattr(e, "app_name", None) or getattr(e, "domain", None)
        dur = getattr(e, "duration_sec", 0.0) or 0.0
        if key and dur:
            duration[key] = duration.get(key, 0.0) + dur
    return duration, since


def _find_attention_mismatch(
    paths: AgentPaths, *, window_hours: float = ATTENTION_WINDOW_HOURS, mismatch_ratio: float = ATTENTION_MISMATCH_RATIO
) -> list[Candidate]:
    try:
        from mini_agent.perception.goal_backlog import load_goal_backlog
        backlog = load_goal_backlog(paths)
    except Exception:
        return []

    duration, since = _measure_attention_durations(paths, window_hours)
    total = sum(duration.values())
    if total <= 0:
        return []

    active_goals = backlog.active_goals() + backlog.active_objectives()
    out: list[Candidate] = []
    for key, dur in duration.items():
        ratio = dur / total
        if ratio < mismatch_ratio:
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
                    f"最近 {window_hours:g} 小时内 {ratio:.0%} 的时间花在"
                    f" {key} 上，但没有关联到任何登记中的目标"
                ),
                evidence_refs=[f"behavior_window:{int(since)}-{int(time.time())}:{key}"],
            )
        )
    return out


def _rule_based_rank(candidates: list[Candidate]) -> list[Candidate]:
    """规则层排序：stale_goal 优先于 momentum_goal，momentum_goal 优先于
    attention_mismatch。stale_goal 是明确的既定目标（用户已经承诺要做、
    只是被搁置了），momentum_goal 是"正在发生的积极信号"（值得趁热打铁，
    但不如"已经停滞"那么明确要处理），attention_mismatch 只是"可能"
    分心，确定性最低。同类内部按 ref_id 稳定排序。
    """
    # [goal_tree_research_and_action_recommendation_plan.md §4.3 阶段三]
    # focus_next_step 排在 stale_goal 之后——两者都是"明确该处理的事"，
    # 但 stale_goal 是"已经停滞、优先级更紧迫"，focus_next_step 只是
    # "现阶段焦点的常规下一步"，紧迫程度略低，排在 momentum_goal 之前是
    # 因为它挂在树的现阶段焦点上，比"最近活跃度上升"这种弱信号更明确。
    order = {"stale_goal": 0, "focus_next_step": 1, "momentum_goal": 2, "attention_mismatch": 3}
    ranked = sorted(candidates, key=lambda c: (order.get(c.kind, 9), c.ref_id))
    for i, c in enumerate(ranked):
        c.rank = i + 1
    return ranked


def _load_profile_patterns(paths: AgentPaths, min_confidence: float) -> list[dict]:
    """读取 decision_profile_builder 产出的模式列表，只取置信度达标、且没有
    未解决矛盾（或矛盾后置信度仍达标）的模式。读取失败（未生成过画像等）
    时静默返回空列表——加权是可选加成，不能因为画像不存在而让推荐报错。
    """
    try:
        state = json.loads(paths.decision_profile_state_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    patterns = state.get("patterns", [])
    return [p for p in patterns if float(p.get("confidence", 0.0)) >= min_confidence]


def _apply_profile_weighting(candidates: list[Candidate], patterns: list[dict]) -> list[Candidate]:
    """用画像模式对同类候选做"排序内加权"：候选的 title/reason 与某条高置信度
    模式的 pattern 文本有关键词重合时，视为该候选与用户已验证的价值取向相关，
    优先级略微提升（组内排序前移），但不跨类别提升（stale_goal 永远先于
    attention_mismatch，加权只影响同类内部顺序，遵循方案"仅影响排序，不替代
    候选本身"的限定）。
    """
    if not patterns:
        return candidates

    def _matches_any_pattern(c: Candidate) -> bool:
        hay = (c.title + " " + c.reason).lower()
        for p in patterns:
            for token in p.get("pattern", "").lower().replace("_", " ").replace("-", " ").split():
                if len(token) >= 2 and token in hay:
                    return True
        return False

    order = {"stale_goal": 0, "attention_mismatch": 1}
    ranked = sorted(
        candidates,
        key=lambda c: (order.get(c.kind, 9), 0 if _matches_any_pattern(c) else 1, c.ref_id),
    )
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
        # [BUGFIX] 同 decision_profile_builder.py 的问题：LLMHelper 没有
        # .complete()，只有 .ask()/.chat()。这里原本被 except Exception
        # 兜住静默退化成规则排序，所以不会崩，但也从来没真正用上 LLM 排序。
        raw = llm_helper.ask(prompt)
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
    paths: AgentPaths,
    *,
    rank_with_llm: bool = False,
    llm_helper=None,
    cfg: Optional["DigestAdvisorConfig"] = None,
) -> Optional[dict]:
    """生成一批推荐候选并落盘。候选为空时返回 None（克制阈值，见模块头注释）。

    cfg 不为 None 时，停滞天数/注意力窗口阈值/是否接 LLM/是否用画像加权
    均从 cfg 读取，覆盖调用方显式传入的 rank_with_llm（cfg 存在时以 cfg 为准，
    因为 cfg 反映的是用户在 agent_config.json 里的显式配置）。
    """
    stale_days = cfg.next_action_stale_days if cfg is not None else STALE_DAYS
    priority_floor = cfg.next_action_stale_priority_floor if cfg is not None else STALE_PRIORITY_FLOOR
    window_hours = cfg.next_action_attention_window_hours if cfg is not None else ATTENTION_WINDOW_HOURS
    mismatch_ratio = cfg.next_action_attention_mismatch_ratio if cfg is not None else ATTENTION_MISMATCH_RATIO
    use_llm = cfg.next_action_rank_with_llm if cfg is not None else rank_with_llm

    candidates = _find_stale_active_goals(
        paths, stale_days=stale_days, priority_floor=priority_floor
    ) + _find_attention_mismatch(paths, window_hours=window_hours, mismatch_ratio=mismatch_ratio)

    # [personal_researcher_and_coach_capability_gap_plan.md C3] 第三条
    # 规则，默认关闭（cfg 未传入或未显式开启时不生效，向后兼容）。
    if cfg is not None and cfg.next_action_momentum_enabled:
        candidates = candidates + _find_momentum_goals(
            paths,
            window_days=cfg.next_action_momentum_window_days,
            min_recent_events=cfg.next_action_momentum_min_recent_events,
            priority_floor=priority_floor,
        )

    # [goal_tree_research_and_action_recommendation_plan.md §4.3 阶段三]
    # 默认关闭，跟 momentum 规则一样先观察一段时间再决定是否默认开启。
    if cfg is not None and cfg.next_action_focus_next_step_enabled:
        candidates = candidates + _find_focus_next_step_candidates(
            paths, max_nodes=cfg.next_action_focus_next_step_max_nodes
        )

    if not candidates:
        return None

    ranked = _llm_rank(candidates, llm_helper) if use_llm else _rule_based_rank(candidates)

    if cfg is not None and cfg.next_action_profile_weighting_enabled:
        patterns = _load_profile_patterns(paths, cfg.next_action_profile_weighting_min_confidence)
        ranked = _apply_profile_weighting(ranked, patterns)

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


# ── 注意力错配 daemon 主动推送（设计方案 4.3 节）──────────────────────────────

def _load_mismatch_state(paths: AgentPaths) -> dict:
    p = paths.attention_mismatch_state_path
    if not p.exists():
        return {"signals": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"signals": {}}


def _save_mismatch_state(paths: AgentPaths, state: dict) -> None:
    p = paths.attention_mismatch_state_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def check_persistent_attention_mismatch(
    paths: AgentPaths, cfg: Optional["DigestAdvisorConfig"] = None
) -> Optional[dict]:
    """供 AutonomousLoop._tick_passive() 周期性调用（不经过 LLM 对话轮次）。

    跟踪同一错配信号（按 ref_id，即 app/域名 key）连续被检测到的时长：
      - 首次检测到：记录 first_detected_at，本次不推送
      - 持续检测到且 (now - first_detected_at) >= push_threshold_hours
        且该信号历史推送次数 < push_max_per_session：返回待推送 payload，
        推送次数 +1
      - 信号消失（这次没检测到）：清除跟踪记录，下次重新计时

    cfg 为 None 或 cfg.next_action_push_enabled=False 时直接返回 None，
    不做任何跟踪/推送（总开关默认关闭，见方案 4.3 节"避免打断式骚扰"）。
    """
    if cfg is None or not cfg.next_action_push_enabled:
        return None

    window_hours = cfg.next_action_attention_window_hours
    mismatch_ratio = cfg.next_action_attention_mismatch_ratio
    threshold_hours = cfg.next_action_push_threshold_hours
    max_per_session = cfg.next_action_push_max_per_session

    current = {c.ref_id: c for c in _find_attention_mismatch(
        paths, window_hours=window_hours, mismatch_ratio=mismatch_ratio
    )}

    state = _load_mismatch_state(paths)
    signals: dict = state.setdefault("signals", {})
    now = time.time()
    to_push: Optional[dict] = None

    # 清理已消失的信号，避免跟踪记录无限增长
    for ref_id in list(signals.keys()):
        if ref_id not in current:
            del signals[ref_id]

    for ref_id, cand in current.items():
        rec = signals.get(ref_id)
        if rec is None:
            signals[ref_id] = {"first_detected_at": now, "push_count": 0}
            continue

        elapsed_hours = (now - rec["first_detected_at"]) / 3600
        if elapsed_hours < threshold_hours:
            continue
        if rec.get("push_count", 0) >= max_per_session:
            continue

        # 只推送第一个满足条件的信号（一次 tick 最多推一条，避免多条同时轰炸）
        if to_push is None:
            to_push = {
                "ref_id": ref_id,
                "title": cand.title,
                "reason": cand.reason,
                "elapsed_hours": round(elapsed_hours, 1),
            }
            rec["push_count"] = rec.get("push_count", 0) + 1

    _save_mismatch_state(paths, state)
    return to_push


def render_push_message(payload: dict) -> str:
    """把 check_persistent_attention_mismatch() 的返回值渲染成推送文案。"""
    return (
        f"⏰ 注意力提醒：最近 {payload['elapsed_hours']:.1f} 小时持续在"
        f" {payload['title']} 上，但这个活动没有关联到任何登记中的目标。"
        f"{payload['reason']}。如果这是有意为之可以忽略，否则可以 `/next` 查看建议"
        f"或 `/agent goals add` 补登记一个目标。"
    )
