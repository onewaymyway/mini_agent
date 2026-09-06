"""evolution/user_signal_profile_builder.py — 用户侧 `values`/
`risk_preference` 证据归纳（next_doc/personal_ai_alignment_upgrade_plan.md
阶段一 §4.1）。

与 `agent_value_profile_builder.py` 是同一套治理范式（证据 → LLM 归纳 →
矛盾不覆盖只降权）在不同受益人上的应用：那边观察 **agent 自己**的历史
选择行为（StateRepo commit 风险分级），这里观察**用户**对 AI 建议的
采纳/拒绝行为——证据源换成 `suggestion_feedback_ledger.py` 已经在维护、
覆盖 `soft_goal_deriver`/`improvement_backlog_merge` 等多路建议来源的
统一账本，不新增采集点。

归纳结果直接写入 `profile.py::UserProfile.derived["values"]` /
`derived["risk_preference"]`，`source` 固定为 `ai_inference`（LLM 语义
归纳出的模式，不是直接观察到的原始计数）、`confidence`/矛盾合并逻辑
复用 `evolution/evidence_pattern.merge_evidence_patterns()`，与
agent_value_profile_builder 共享同一套算法，不重复实现。

当前落地的证据源（阶段一）：
  - `suggestion_feedback_ledger.all_categories()`——每个 category 的
    accepted/rejected 累计计数，是用户已经做过的"接受/拒绝 AI 建议"
    行为的直接记录，不需要新增采集点。

方案 §4.1 提到的"用户对高风险操作确认/拒绝的历史"作为 risk_preference
的独立证据源，本阶段与 values 共用同一份账本证据（账本本身不区分
"高风险操作确认"与"普通建议采纳"），如实记录为已知限制：`category`
目前是调用方自定义的粗粒度字符串（dedupe_key 或 "source:kind"），账本
里没有风险等级字段，无法可靠区分。留待相关模块补上风险等级标注后再
细化，不臆造区分。
"""

from __future__ import annotations

import json
import time
from typing import Optional

from mini_agent.storage.paths import AgentPaths

MIN_EVIDENCE_COUNT = 3  # 与 agent_value_profile_builder 一致：少于 3 个独立类别的模式不落地
MIN_OUTCOME_COUNT = 1  # 一个 category 至少要有 1 次 accepted/rejected 才算有效证据


def _load_ledger_evidence(paths: AgentPaths) -> list[dict]:
    """读取 `suggestion_feedback_ledger` 中全部有过采纳/拒绝记录的类别，
    整理成归纳所需的证据条目。账本不存在/为空时返回空列表（与
    `agent_value_profile_builder._load_risk_tier_evidence` 同样的
    "可选证据源为空则不阻断"处理）。"""
    try:
        from mini_agent.evolution.suggestion_feedback_ledger import all_categories
    except Exception:
        return []

    try:
        entries = all_categories(paths)
    except Exception:
        return []

    out = []
    for category, entry in entries.items():
        total = entry.accepted + entry.rejected
        if total < MIN_OUTCOME_COUNT:
            continue
        out.append({
            "category": category,
            "accepted": entry.accepted,
            "rejected": entry.rejected,
        })
    return out


def _llm_summarize_user_signal(
    ledger_evidence: list[dict], llm_helper, min_evidence_count: int = MIN_EVIDENCE_COUNT
) -> Optional[dict]:
    """要求 LLM 分别归纳 values（决策取向）与 risk_preference（风险偏好）
    两组模式，每条模式必须引用至少 `min_evidence_count` 个不同 category
    作为证据，不满足的由本函数事后过滤（不完全信任 LLM 自称的证据数量，
    与 agent_value_profile_builder 同一克制原则）。"""
    prompt = (
        "以下是一份账本，记录了用户对 AI 主动提出的各类建议/目标（按 category "
        "分组）的采纳(accepted)次数与拒绝(rejected)次数。请你归纳两组模式：\n"
        "1) values：用户在决定是否采纳 AI 建议时表现出的、反复出现的价值取向或"
        "决策取向（例如更看重效率还是稳妥、更愿意让 AI 自主推进还是倾向于亲自"
        "把关，等等）。\n"
        "2) risk_preference：用户对不同风险程度的建议/操作表现出的接受倾向"
        "（例如是否明显更愿意接受影响范围小的建议、拒绝影响范围大或不可逆的"
        "建议，等等；无法判断风险高低时不要臆造，宁可不归纳）。\n"
        f"每条模式必须能被至少 {min_evidence_count} 个不同的 category 支持，"
        "不要凭单个 category 的数据臆断。每条模式给出 pattern（一句话，第一"
        "人称，例如\"我倾向于...\"）和 evidence_refs（引用的 category 列表，"
        "必须真实来自输入数据）。只返回 JSON 对象，形如 "
        '{"values": [...], "risk_preference": [...]}，不要其他文字：\n'
        + json.dumps(ledger_evidence, ensure_ascii=False)
    )
    try:
        raw = llm_helper.ask(prompt)
    except Exception:
        return None

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None

    valid_categories = {e["category"] for e in ledger_evidence}
    result: dict[str, list[dict]] = {}
    for dim in ("values", "risk_preference"):
        items = parsed.get(dim)
        if not isinstance(items, list):
            result[dim] = []
            continue
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            refs = [r for r in item.get("evidence_refs", []) if r in valid_categories]
            if len(refs) < min_evidence_count:
                continue
            pattern = str(item.get("pattern", "")).strip()
            if pattern:
                out.append({"pattern": pattern, "evidence_refs": refs})
        result[dim] = out
    return result


def _patterns_to_profile_items(
    patterns: list[dict], *, prev_last_confirmed_at: dict[str, float], now_label: str, now_ts: float,
) -> list[dict]:
    """把 `evidence_pattern.merge_evidence_patterns()` 输出的通用 dict
    （pattern/evidence_refs/confidence/first_observed/last_reinforced/
    contradicted_by）转成 `profile.py` 里 `text + last_confirmed_at +
    source + confidence` 的存储结构。

    `last_confirmed_at`（数值时间戳，供 `stale_items()` 复用）只在这条
    pattern 本轮确实被新建或再次印证（`last_reinforced == now_label`）
    时刷新为 `now_ts`；未被本轮证据触及的旧 pattern 保留其原有时间戳，
    否则每次重新归纳都会无差别刷新所有条目，`stale_items()` 的"多久没
    被再次印证"判断就失去意义。
    """
    out = []
    for p in patterns:
        text = p["pattern"]
        touched_this_round = p.get("last_reinforced") == now_label
        ts = now_ts if touched_this_round else prev_last_confirmed_at.get(text, now_ts)
        out.append({
            "text": text,
            "last_confirmed_at": ts,
            "source": "ai_inference",
            "confidence": round(float(p.get("confidence", 0.0)), 2),
            "evidence_refs": list(p.get("evidence_refs", [])),
        })
    return out


def _profile_items_to_patterns(items: list[dict]) -> list[dict]:
    """反向转换：把已落盘的 profile 结构还原成 merge 函数需要的通用形状，
    用于把上一版结果作为 `existing` 参与合并。"""
    out = []
    for it in items or []:
        out.append({
            "pattern": it.get("text", ""),
            "evidence_refs": list(it.get("evidence_refs", [])),
            "confidence": float(it.get("confidence", 0.0)),
            "first_observed": "",
            "last_reinforced": "",
            "contradicted_by": [],
        })
    return out


def generate_user_signal_profile(
    paths: AgentPaths, *, llm_helper=None, min_evidence_count: int = MIN_EVIDENCE_COUNT
) -> Optional[dict]:
    """归纳一轮用户侧 values/risk_preference，写入
    `UserProfile.derived["values"]` / `derived["risk_preference"]`。

    `llm_helper` 为 None 时直接返回 None（本层归纳依赖 LLM 做语义总结，
    对齐 `agent_value_profile_builder` 的同一取舍）。返回值为写入后的两个
    维度内容，供调用方（CLI）展示；证据不足/LLM 未产出任何满足证据数量
    要求的模式时返回 None，不写入、不清空已有数据。
    """
    if llm_helper is None:
        return None

    ledger_evidence = _load_ledger_evidence(paths)
    if len(ledger_evidence) < min_evidence_count:
        return None  # 建议采纳/拒绝的历史记录本身不足，归纳没有意义

    summarized = _llm_summarize_user_signal(ledger_evidence, llm_helper, min_evidence_count=min_evidence_count)
    if not summarized or not (summarized.get("values") or summarized.get("risk_preference")):
        return None

    from mini_agent.evolution.evidence_pattern import merge_evidence_patterns
    from mini_agent.profile import UserProfileManager

    manager = UserProfileManager(paths)
    profile = manager.load()
    now_str = time.strftime("%Y-%m-%d", time.localtime())
    now_ts = time.time()

    result: dict[str, list[dict]] = {}
    for dim in ("values", "risk_preference"):
        raw_patterns = summarized.get(dim) or []
        if not raw_patterns:
            continue
        prev_items = profile.derived.get(dim) or []
        prev_last_confirmed_at = {it.get("text", ""): float(it.get("last_confirmed_at", now_ts)) for it in prev_items}
        existing_patterns = _profile_items_to_patterns(prev_items)
        merged_patterns = merge_evidence_patterns(existing_patterns, raw_patterns, now_label=now_str)
        profile_items = _patterns_to_profile_items(
            merged_patterns, prev_last_confirmed_at=prev_last_confirmed_at, now_label=now_str, now_ts=now_ts,
        )
        profile.derived[dim] = profile_items
        result[dim] = profile_items

    if not result:
        return None

    manager.save()
    return result
