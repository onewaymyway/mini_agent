"""evolution/evidence_pattern.py — 通用"证据 → LLM 归纳 → 矛盾不覆盖只降权"
合并算法（next_doc/personal_ai_alignment_upgrade_plan.md 阶段一）。

背景：`agent_value_profile_builder.py`（归纳 Agent 自身价值观）已经跑通了
一套三层结构：已落盘证据 → LLM 语义归纳 → 落盘时"同一模式追加证据强化
置信度，不同/矛盾模式只新增不覆盖"。`personal_ai_alignment_upgrade_plan.md`
要求把这套治理范式原样复用到用户侧画像（`profile.py::UserProfile.derived`
下的 `values`/`risk_preference` 等维度），而不是重新发明一套合并逻辑。

本模块把原本写死在 `agent_value_profile_builder._apply_contradiction()`
里的合并算法抽成一个不依赖任何具体调用方数据结构的纯函数：输入输出都是
`{"pattern": str, "evidence_refs": list[str], "confidence": float,
"first_observed": str, "last_reinforced": str, "contradicted_by": list[str]}`
形状的 dict，调用方自己决定要不要包一层 dataclass（`agent_value_profile_
builder.AgentValuePattern` 就是这样一个薄包装，保持对外接口不变）。

不做语义级别的"矛盾检测"（比如判断两条 pattern 文本是否语义相反）——
`contradicted_by` 字段仍然只是预留位，由调用方在有能力做语义矛盾判断后
自行填充；本函数只保证"同一 pattern 文本被再次印证时强化置信度、不同
pattern 文本不覆盖旧记录"这个最基础、不涉及语义判断的合并规则。
"""

from __future__ import annotations

# 与原 agent_value_profile_builder 保持一致的默认合并参数。
DEFAULT_GAIN_CONFIDENCE_STEP = 0.1   # 已有模式每新增 1 条独立证据，置信度 +0.1
DEFAULT_NEW_CONF_PER_EVIDENCE = 0.15  # 新模式：每条证据贡献的初始置信度
DEFAULT_NEW_CONF_CAP = 0.6           # 新模式：仅凭初次归纳能拿到的置信度上限
                                       # （更高的置信度只能靠后续被反复印证累积）


def merge_evidence_patterns(
    existing: list[dict],
    new_raw: list[dict],
    *,
    now_label: str,
    gain_confidence_step: float = DEFAULT_GAIN_CONFIDENCE_STEP,
    new_conf_per_evidence: float = DEFAULT_NEW_CONF_PER_EVIDENCE,
    new_conf_cap: float = DEFAULT_NEW_CONF_CAP,
) -> list[dict]:
    """合并已有 pattern 列表与本轮 LLM 归纳出的新 pattern 列表。

    Args:
        existing: 已落盘的 pattern dict 列表（每条至少含 pattern/
            evidence_refs/confidence/first_observed/last_reinforced/
            contradicted_by）。
        new_raw: 本轮归纳出的候选，每条至少含 pattern（str）与
            evidence_refs（list[str]，且已由调用方过滤到确实满足最小
            证据数量要求——本函数不做证据数量门槛检查）。
        now_label: 本轮归纳发生的时间标签（调用方决定格式，如
            `time.strftime("%Y-%m-%d")`），用于 first_observed/
            last_reinforced。

    Returns:
        合并后的 pattern dict 列表（新对象，不修改入参）。同一 pattern
        文本视为"被再次印证"：证据取并集，若证据数量确实增加则按
        `gain_confidence_step` 每条 +confidence（封顶 1.0）并刷新
        last_reinforced；不同 pattern 文本一律新增，不覆盖已有记录。
    """
    by_pattern: dict[str, dict] = {p["pattern"]: dict(p) for p in existing}
    for item in new_raw:
        pattern = str(item.get("pattern", "")).strip()
        refs = list(item.get("evidence_refs", []))
        if not pattern:
            continue
        if pattern in by_pattern:
            node = by_pattern[pattern]
            merged_refs = sorted(set(node.get("evidence_refs", [])) | set(refs))
            gained = len(merged_refs) - len(node.get("evidence_refs", []))
            node["evidence_refs"] = merged_refs
            if gained > 0:
                node["confidence"] = min(
                    1.0, float(node.get("confidence", 0.0)) + gain_confidence_step * gained
                )
                node["last_reinforced"] = now_label
        else:
            conf = min(new_conf_cap, new_conf_per_evidence * len(refs))
            by_pattern[pattern] = {
                "pattern": pattern,
                "evidence_refs": refs,
                "confidence": conf,
                "first_observed": now_label,
                "last_reinforced": now_label,
                "contradicted_by": list(item.get("contradicted_by", [])),
            }
    return list(by_pattern.values())
