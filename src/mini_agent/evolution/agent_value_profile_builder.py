"""evolution/agent_value_profile_builder.py — Agent 自身价值观归纳
（next_doc/self_awareness_identity_evolution_plan.md §2.1）。

`self_assessment`（perception/global_knowledge.py）回答"我能不能做到"，
但没有任何字段回答"我认为什么状态是好的"。本模块与 `self_assessment`
平级，观察 agent **自己的历史选择行为**（而不是用户的决策，那是
`decision_profile_builder.py::user_value_profile.md` 的对象），归纳出
一份关于自身偏好倾向的表示。

复用 `decision_profile_builder.py` 已经确立的三层结构与克制原则：
  1. 已落盘的单条事实（不新增采集点，直接读现有数据源）
  2. 周期性归纳（LLM 语义总结，少于 MIN_EVIDENCE_COUNT 条独立证据的
     模式不落地）
  3. `agent_value_profile.md`（矛盾证据不覆盖旧模式，只记录 + 降权）

当前落地的证据源（阶段一）：
  - `StateRepo`（evolution/state_repo.py）的 commit 历史——每次
    `apply()` 落盘的 tier（T0-T3，见 `_build_commit_message`）是
    agent 已经做过的\"风险取舍\"决策，直接反映"更看重稳健推进还是
    更愿意承担较高风险变更"这类主体性倾向，不需要新增采集点。

方案 §2.1 提到的另外两个证据源（Goal/Objective 优先级选择、
`soft_goal_deriver` 候选取舍记录）本阶段未接入：`GoalNode` 当前不
持久化候选的 `source_tag`（capability/workthread/lesson/...），无法
从落盘的 `goal_backlog.json` 可靠反查"当初是因为哪类信号被选中"，
强行从标题/优先级反推容易引入不实归因；如实记录为已知限制，留待
`GoalNode` 补上 `source_tag` 持久化字段后再接入，不臆造证据。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Optional

from mini_agent.storage.paths import AgentPaths

MIN_EVIDENCE_COUNT = 3  # 与 decision_profile_builder 一致：少于 3 条独立证据的模式不落地
SCAN_WINDOW_COMMITS = 60  # 一次归纳最多回看的 commit 数，避免全量历史每次都重新归纳


@dataclass
class AgentValuePattern:
    pattern: str
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 0.0
    first_observed: str = ""
    last_reinforced: str = ""
    contradicted_by: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pattern": self.pattern,
            "evidence_refs": self.evidence_refs,
            "confidence": round(self.confidence, 2),
            "first_observed": self.first_observed,
            "last_reinforced": self.last_reinforced,
            "contradicted_by": self.contradicted_by,
        }

    @staticmethod
    def from_dict(d: dict) -> "AgentValuePattern":
        return AgentValuePattern(
            pattern=d.get("pattern", ""),
            evidence_refs=list(d.get("evidence_refs", [])),
            confidence=float(d.get("confidence", 0.0)),
            first_observed=d.get("first_observed", ""),
            last_reinforced=d.get("last_reinforced", ""),
            contradicted_by=list(d.get("contradicted_by", [])),
        )


def _load_state(paths: AgentPaths) -> dict:
    p = paths.agent_value_profile_state_path
    if not p.exists():
        return {"last_scan_at": 0.0, "patterns": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"last_scan_at": 0.0, "patterns": []}


def _save_state(paths: AgentPaths, state: dict) -> None:
    p = paths.agent_value_profile_state_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_risk_tier_evidence(paths: AgentPaths, limit: int = SCAN_WINDOW_COMMITS) -> list[dict]:
    """读取 `StateRepo` 最近 limit 条 commit，提取 tier（T0-T3）+ subject。
    仓库不存在/无 commit/StateRepo 不可用时返回空列表（自我修改是可选
    子系统，不是每个 workdir 都已经产生过 commit 历史）。"""
    try:
        from mini_agent.evolution.state_repo import StateRepo
    except Exception:
        return []

    try:
        repo = StateRepo(paths.project_root)
        if not repo.has_commits():
            return []
        commits = repo.log(limit=limit)
    except Exception:
        return []

    out = []
    for c in commits:
        subject = c.subject or ""
        if subject.startswith("[T") and "]" in subject:
            tier = subject[1:subject.index("]")]
            if tier in ("T0", "T1", "T2", "T3"):
                out.append({"commit": c.commit[:12], "tier": tier, "subject": subject, "date": c.date})
    return out


def _llm_summarize_value_patterns(
    tier_evidence: list[dict], llm_helper, min_evidence_count: int = MIN_EVIDENCE_COUNT
) -> list[dict]:
    """要求 LLM 只做归纳：每条候选模式必须列出至少 min_evidence_count 个
    commit hash 作为证据，不满足的模式由本函数事后过滤掉（与
    decision_profile_builder 同样不完全信任 LLM 自己声称的证据数量）。"""
    entries = [
        {"commit": e["commit"], "tier": e["tier"], "subject": e["subject"]}
        for e in tier_evidence
    ]
    prompt = (
        "以下是一个 AI agent 自身历史上对自己代码/技能库做修改时的 commit 记录，"
        "每条都标注了风险分级 tier（T0 最保守、T3 风险最高，分级越高代表这次"
        "自我修改的影响范围/不可逆性越大）。请你归纳出这个 agent 在自我修改这件"
        "事上表现出的、反复出现的倾向或偏好模式——例如是否明显更倾向于选择稳健"
        "的小步修改而不是激进变更，或者在某类改动上愿意承担更高风险，等等。"
        f"每条模式必须能被至少 {min_evidence_count} 条独立记录支持，不要凭单条"
        "记录臆断。每条模式给出 pattern（一句话，第一人称，比如\"我倾向于...\"）"
        "和 evidence_refs（引用的 commit 列表，必须真实来自输入数据）。"
        "只返回 JSON 数组，不要其他文字：\n" + json.dumps(entries, ensure_ascii=False)
    )
    try:
        raw = llm_helper.ask(prompt)
    except Exception:
        return []

    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        parsed = json.loads(raw[start : end + 1])
    except Exception:
        return []

    valid_commits = {e["commit"] for e in entries}
    out = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        refs = [r for r in item.get("evidence_refs", []) if r in valid_commits]
        if len(refs) < min_evidence_count:
            continue
        pattern = str(item.get("pattern", "")).strip()
        if pattern:
            out.append({"pattern": pattern, "evidence_refs": refs})
    return out


def _apply_contradiction(existing: list[AgentValuePattern], new_raw: list[dict], now_str: str) -> list[AgentValuePattern]:
    """与 decision_profile_builder._apply_contradiction 完全一致的合并策略：
    同一模式证据增加则强化置信度，矛盾/不同模式只新增，不覆盖旧模式。

    [next_doc/personal_ai_alignment_upgrade_plan.md 阶段一] 实际合并算法已
    抽取为 `evolution/evidence_pattern.merge_evidence_patterns()`，供用户侧
    `user_signal_profile_builder.py` 复用同一套逻辑；本函数只做
    `AgentValuePattern` dataclass 与通用 dict 形状之间的转换，对外行为/
    返回类型保持不变。"""
    from mini_agent.evolution.evidence_pattern import merge_evidence_patterns

    merged_dicts = merge_evidence_patterns(
        [p.to_dict() for p in existing], new_raw, now_label=now_str,
    )
    return [AgentValuePattern.from_dict(d) for d in merged_dicts]


def generate_agent_value_profile(
    paths: AgentPaths, *, llm_helper=None, min_evidence_count: int = MIN_EVIDENCE_COUNT
) -> Optional[dict]:
    """归纳一轮 agent 自身价值观。llm_helper 为 None 时直接返回 None（本层
    归纳依赖 LLM 做语义总结，规则层无法替代，对齐 decision_profile_builder
    的同一取舍）。"""
    if llm_helper is None:
        return None

    tier_evidence = _load_risk_tier_evidence(paths)
    if len(tier_evidence) < min_evidence_count:
        return None  # 自我修改记录本身不足，归纳没有意义

    state = _load_state(paths)
    existing = [AgentValuePattern.from_dict(d) for d in state.get("patterns", [])]

    raw_patterns = _llm_summarize_value_patterns(tier_evidence, llm_helper, min_evidence_count=min_evidence_count)
    if not raw_patterns:
        return None

    now_str = time.strftime("%Y-%m-%d", time.localtime())
    merged = _apply_contradiction(existing, raw_patterns, now_str)

    state["last_scan_at"] = time.time()
    state["patterns"] = [p.to_dict() for p in merged]
    _save_state(paths, state)

    _write_profile_md(paths, merged)
    return state


def _write_profile_md(paths: AgentPaths, patterns: list[AgentValuePattern]) -> None:
    lines = [
        "---",
        "title: Agent 自身价值观",
        "source_kind: agent_value_profile",
        f"updated: {time.strftime('%Y-%m-%d', time.localtime())}",
        "tags: [agent-value-profile]",
        "---",
        "",
        "# Agent 自身价值观",
        "",
        "> 本文档由 agent_value_profile_builder 周期性归纳生成，观察对象是 agent"
        "自己的历史选择行为（当前仅接入 StateRepo 自我修改 commit 的风险分级），"
        "不是用户的决策（那是 user_value_profile.md）。每条模式必须能追溯到具体"
        "的历史 commit，少于 3 条独立证据的模式不会出现在这里。",
        "",
    ]
    for p in sorted(patterns, key=lambda x: -x.confidence):
        lines.append(f"## {p.pattern}")
        lines.append(f"- 置信度：{p.confidence:.2f}")
        lines.append(f"- 首次观察：{p.first_observed}　最近强化：{p.last_reinforced}")
        lines.append(f"- 证据：{', '.join(p.evidence_refs)}")
        if p.contradicted_by:
            lines.append(f"- ⚠️ 存在矛盾证据：{', '.join(p.contradicted_by)}（置信度已相应下调）")
        lines.append("")

    paths.wiki_dir.mkdir(parents=True, exist_ok=True)
    paths.agent_value_profile_path.write_text("\n".join(lines), encoding="utf-8")


def load_agent_value_profile(paths: AgentPaths) -> list[dict]:
    """供 `/self/portrait` 等只读端点消费：返回当前已落盘的全部 pattern。"""
    state = _load_state(paths)
    return state.get("patterns", [])
