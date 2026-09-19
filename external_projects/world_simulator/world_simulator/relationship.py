"""world_simulator/relationship.py — 关系（Relationship）最小版结构化
声明（阶段三十一）

设计依据：`next_doc/world_simulator_universal_simulator_gap_analysis_
and_roadmap_v2_plan.md` 4.24 节。

**范围克制（重要）**：这不是参考文档设想的完整 Influence Field /
Relationship 图架构——没有"影响半径"、没有传播路径/时间延迟建模、
没有"谁的行动能自动改变谁"的推进逻辑，`engine.advance()` 完全不读取
也不消费本模块的输出。这只是把"多主体之间的关系"从纯自由文本（叙事）
升级为一份可以独立查看、可以按主体聚合的结构化列表，验证"关系作为
一等公民"这个信息组织方式本身是否有用；真正的传播/影响建模仍然按
4.24 节原方案标注的触发条件（"多主体数量明显增多、且现有 entities/
shared_vars 结构不足以表达"）评估。

和 `causal_graph.py`/`attribution.py` 一样，本模块是纯函数、不缓存、
不落盘：数据来源是 `manifest.settings.relationships`（用户/skill 在
"模拟设置"里手动声明的 JSON），调用方（`app.py`）现算现展示。
"""

from __future__ import annotations

from typing import Any, Dict, List

_VALID_KINDS = ("ally", "rival", "dependency", "authority", "other")
_VALID_STRENGTHS = ("high", "medium", "low")

_KIND_LABELS = {
    "ally": "同盟/合作",
    "rival": "竞争/对立",
    "dependency": "依赖",
    "authority": "支配/权威",
    "other": "其它",
}
_STRENGTH_LABELS = {
    "high": "强",
    "medium": "中",
    "low": "弱",
}


def normalize_relationships(raw: Any) -> List[Dict[str, Any]]:
    """把 `settings.relationships` 的原始 JSON 清洗成规范形式，丢弃
    `from`/`to` 缺失的无效条目（不报错，容忍用户手填的不完整数据）。

    `kind` 不在 `_VALID_KINDS` 里的一律归一化为 `"other"`，`strength`
    不在 `_VALID_STRENGTHS` 里的归一化为 `"medium"`——延续项目"不认识
    的取值给一个安全默认值，而不是报错中断"的一贯风格。
    """
    result: List[Dict[str, Any]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        from_ = str(item.get("from") or "").strip()
        to = str(item.get("to") or "").strip()
        if not from_ or not to:
            continue
        kind = str(item.get("kind") or "other").strip() or "other"
        if kind not in _VALID_KINDS:
            kind = "other"
        strength = str(item.get("strength") or "medium").strip() or "medium"
        if strength not in _VALID_STRENGTHS:
            strength = "medium"
        result.append(
            {
                "from": from_,
                "to": to,
                "kind": kind,
                "kind_label": _KIND_LABELS.get(kind, kind),
                "strength": strength,
                "strength_label": _STRENGTH_LABELS.get(strength, strength),
                "note": str(item.get("note") or "").strip(),
            }
        )
    return result


def summarize_by_subject(raw: Any) -> Dict[str, List[Dict[str, Any]]]:
    """按发起主体（`from`）分组，供展示层"点一个主体，看它和谁有什么
    关系"这种最朴素的浏览方式，不做力导向图/关系网可视化（同
    `causal_tree.py`/`causal_graph.py` 一贯的"先验证信息组织方式本身
    有没有用，再考虑要不要上复杂可视化"的取舍）。
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for rel in normalize_relationships(raw):
        grouped.setdefault(rel["from"], []).append(rel)
    return grouped
