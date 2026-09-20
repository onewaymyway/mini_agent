"""world_simulator/relationship.py — 关系（Relationship）结构化声明
（阶段三十一起步，阶段三十六第二批扩展为"提示引擎记得住"完整版）

设计依据：`next_doc/world_simulator_universal_simulator_gap_analysis_
and_roadmap_v2_plan.md` 4.24 节（最小起步版）、
`next_doc/world_simulator_event_driven_engine_and_full_architecture_
plan.md` 2.2 节（第二批，完整机制）。

**范围克制（重要，第二批之后仍然成立）**：这不是参考文档设想的、
带精确影响半径公式和自动数值传播的完整 Influence Field 引擎——没有
图遍历、没有强度衰减公式，`engine.advance()` 仍然不做任何自动化的
数值传播计算。第二批新增的 `delay_steps`/`propagation_path`/
`reversible` 三个字段，以及 `queue_pending_effect`/
`due_pending_effects` 这对轻量函数，做的事情始终是"把结构化信息
转成喂给 LLM 的提示文本，延迟到期时提醒 LLM 该体现效果了"，不是
"引擎自己算出效果、写进 vars"——是否触发、触发后具体怎么体现，仍然
完全由 LLM 在 `advance_step`/`world_evolve` 里自行判断，这一点和
`declared_causal_graph`/`causal_lines` 的既有设计哲学完全一致，
避免"看起来是精确的因果引擎、实际是规则拍脑袋"的伪确定性。

和 `causal_graph.py`/`attribution.py` 一样，本模块是纯函数、不缓存、
不落盘：数据来源是 `manifest.settings.relationships`（用户/skill 在
"模拟设置"里手动声明的 JSON），调用方（`app.py`/`spec_generator.py`/
`engine/advance.py`）现算现展示或现算现拼提示文本。`queue_pending_
effect()`/`due_pending_effects()` 操作的
`manifest.settings.relationship_pending_effects` 由调用方负责落盘
（`SimStore.save_manifest()`），本模块本身不做任何文件 IO。
"""

from __future__ import annotations

from typing import Any, Dict, List

_VALID_KINDS = ("ally", "rival", "dependency", "authority", "other")
_VALID_STRENGTHS = ("high", "medium", "low")
_VALID_REVERSIBLE = ("reversible", "hard_to_reverse", "irreversible")
_REVERSIBLE_LABELS = {
    "reversible": "易撤销",
    "hard_to_reverse": "难以撤销",
    "irreversible": "不可撤销",
}

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

    第二批（`next_doc/world_simulator_event_driven_engine_and_full_
    architecture_plan.md` 2.2 节）新增三个可选字段，同样"不认识/
    不合法就退化为安全默认值"：
    - `delay_steps`：非负整数，这条关系的影响延迟几步后才体现，默认
      `0`（即时生效）；给出负数、非数字或缺省一律归一化为 `0`。
    - `propagation_path`：字符串数组，间接影响时经过的中间实体/因果
      线 id；非列表或缺省归一化为空列表（表示直接影响）。
    - `reversible`：`_VALID_REVERSIBLE` 三选一，不认识的取值/缺省归一
      化为 `None`（表示未声明，不强行猜一个默认档位——不同于
      `strength` 有\"中等\"这种自然的默认值，可逆性没有同等地位的
      默认档，宁可留空)。
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

        try:
            delay_steps = int(item.get("delay_steps") or 0)
        except (TypeError, ValueError):
            delay_steps = 0
        if delay_steps < 0:
            delay_steps = 0

        raw_path = item.get("propagation_path")
        propagation_path = (
            [str(p).strip() for p in raw_path if str(p).strip()]
            if isinstance(raw_path, list)
            else []
        )

        reversible = str(item.get("reversible") or "").strip() or None
        if reversible not in _VALID_REVERSIBLE:
            reversible = None

        entry: Dict[str, Any] = {
            "from": from_,
            "to": to,
            "kind": kind,
            "kind_label": _KIND_LABELS.get(kind, kind),
            "strength": strength,
            "strength_label": _STRENGTH_LABELS.get(strength, strength),
            "note": str(item.get("note") or "").strip(),
            "delay_steps": delay_steps,
            "propagation_path": propagation_path,
        }
        if reversible is not None:
            entry["reversible"] = reversible
            entry["reversible_label"] = _REVERSIBLE_LABELS.get(reversible, reversible)
        result.append(entry)
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


def _relationship_ref(index: int, item: Dict[str, Any]) -> str:
    """一条关系的引用标识：优先用用户/skill 自己填的 `id`，没有则退化
    为它在 `settings.relationships` 列表里的下标（字符串形式）——延续
    `declared_causal_graph`/`causal_links.line_id` \"引用别处声明的 id，
    不做是否存在的强校验\"的既有取舍，关系列表本身不强制要求填 `id`。
    """
    explicit = str(item.get("id") or "").strip()
    return explicit or str(index)


def _resolve_relationship_ref(raw: Any, ref: str) -> "Dict[str, Any] | None":
    """按 `_relationship_ref()` 的规则，把一个引用字符串解析回
    `settings.relationships` 原始列表里的那一项（未归一化，供调用方
    自行决定是否要 `normalize_relationships()` 单项）。找不到返回
    `None`（不报错——`triggered_relationships` 里出现的引用有可能是
    LLM 编造的，同项目一贯\"宽松兜底、不因为脏数据中断推进\"的风格）。
    """
    items = [item for item in (raw or []) if isinstance(item, dict)]
    ref = str(ref or "").strip()
    if not ref:
        return None
    for index, item in enumerate(items):
        if _relationship_ref(index, item) == ref:
            return item
    return None


def queue_pending_effect(
    pending: "List[Dict[str, Any]] | None",
    *,
    relationships: Any,
    relationship_ref: str,
    triggered_at_step: int,
) -> List[Dict[str, Any]]:
    """把一条被声明为"源头已触发"的延迟关系记一笔"预计第 N 步生效"的
    待办，追加进 `manifest.settings.relationship_pending_effects`
    （调用方负责落盘）。

    只有 `delay_steps > 0` 的关系才有排队的意义——`delay_steps == 0`
    （即时生效，也是未声明这个字段时的默认值）不需要排队，调用方应该
    在调用前自行判断，本函数不做这层过滤，只负责\"给定一条要排队的
    引用，正确计算到期步数并追加\"，避免和调用方的判断逻辑耦合。

    同一个 `(relationship_ref, triggered_at_step)` 组合重复排队时会
    被去重（覆盖为最新一次的记录），避免同一条关系在同一步被多次
    声明触发时膨胀出重复的待办——现实里"反复确认同一个已经触发的
    事实"是常见的，不应该产生多条一模一样的待办。
    """
    result = [dict(item) for item in (pending or []) if isinstance(item, dict)]
    rel = _resolve_relationship_ref(relationships, relationship_ref)
    delay_steps = 0
    if rel is not None:
        try:
            delay_steps = max(0, int(rel.get("delay_steps") or 0))
        except (TypeError, ValueError):
            delay_steps = 0
    due_step = triggered_at_step + delay_steps

    result = [
        item
        for item in result
        if not (
            str(item.get("relationship_ref")) == str(relationship_ref)
            and item.get("triggered_at_step") == triggered_at_step
        )
    ]
    result.append(
        {
            "relationship_ref": str(relationship_ref),
            "triggered_at_step": triggered_at_step,
            "due_step": due_step,
        }
    )
    return result


def due_pending_effects(
    pending: "List[Dict[str, Any]] | None", *, current_step: int
) -> List[Dict[str, Any]]:
    """从待办列表里挑出"到了这一步该提醒 LLM 生效"的条目
    （`due_step <= current_step`），不修改/不清除传入的列表——是否把
    到期项从 `relationship_pending_effects` 里移除，由调用方
    （`engine/advance.py`）在落盘时决定（默认保留，允许同一条到期提醒
    连续出现几步，直到 LLM 真的在 `line_updates`/`narrative` 里体现
    出来为止，比"提醒一次就永久消失"更稳妥）。
    """
    return [
        dict(item)
        for item in (pending or [])
        if isinstance(item, dict) and int(item.get("due_step", 0)) <= current_step
    ]
