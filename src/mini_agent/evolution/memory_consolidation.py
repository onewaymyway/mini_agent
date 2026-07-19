"""
evolution/memory_consolidation.py — 记忆巩固：归纳而非纯淘汰（方案二）

对应用户反馈的缺口：MemoryStore 淘汰旧条目时是纯粹的"最旧优先删除"，
没有像人类记忆巩固那样"多条具体经历 -> 一条抽象规律"的归纳过程。

设计原则：
  - 不替换现有淘汰机制，只在淘汰发生前插入一步"尝试归纳"；归纳失败时
    完全退化为原有行为（物理删除），保证这是纯增量、可关闭的改动。
  - 只对 entry_type == "lesson" 的条目做归纳（summary 型条目是"这次
    session 发生了什么"的记录，归纳会丢失时间线信息，价值不同，不适用
    同样的巩固逻辑，继续走原有淘汰）。
  - 复用 lesson_review.py::group_lessons() 的聚类能力，不重新实现一套
    相似度判断。
  - 复用方案一的 embedding（若可用）辅助判断"是否值得合并"，不可用时
    退化为纯关键词 Jaccard（与 group_lessons 现有行为一致）。
  - 归纳产物 occurrence_count 累加原有条目之和，confidence 取最高置信度，
    半衰期基准沿用 memory_aging.py 里的 "consolidated" source。
  - 归纳是有损压缩：原始条目的具体 trigger 文本会被合并摘要覆盖。因此
    默认要求聚类规模 >= min_group_size（默认3）才触发归纳，避免"仅两条
    偶然相似的经历"被过度抽象成误导性规则。
"""

from __future__ import annotations

from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.perception.memory_store import MemoryEntry

MIN_CONSOLIDATE_GROUP_SIZE = 3   # 至少3条才归纳，避免小样本过度抽象（可被 min_group_size 参数覆盖）
CONSOLIDATE_TRIGGER_RATIO = 0.9  # 预留：淘汰候选达到 max_entries 的这个比例时才触发归纳扫描

_SUMMARY_PROMPT_TEMPLATE = """以下是若干条相似的历史经验记录（同一类场景反复出现），请归纳成一条通用规律：

{entries_text}

请用一到两句话概括：这一类场景反复出现的共性触发条件是什么，以及下次遇到时建议怎么做。
只输出归纳结果本身，不要额外解释。"""


def _format_entries_for_prompt(entries: list) -> str:
    lines = []
    for i, e in enumerate(entries, 1):
        lines.append(
            f"{i}. 触发：{getattr(e, 'trigger', '')}；"
            f"结果：{getattr(e, 'outcome', '')}；"
            f"建议：{getattr(e, 'suggested_action', '')}"
        )
    return "\n".join(lines)


def _rule_based_merge(group_entries: list) -> "MemoryEntry":
    """无 llm_call 时的降级路径：取聚类里 occurrence_count 最高的一条作为
    代表，其余条目的 occurrence_count 累加到它身上，不生成新的抽象摘要文本，
    只做"多条计数合一"，仍然优于纯粹丢弃。"""
    from mini_agent.perception.memory_store import MemoryEntry
    import copy

    representative = max(group_entries, key=lambda e: e.occurrence_count)
    total_occurrence = sum(e.occurrence_count for e in group_entries)
    max_confidence = max(e.confidence for e in group_entries)

    new_entry = copy.deepcopy(representative)
    new_entry.entry_type = "consolidated_lesson"
    new_entry.occurrence_count = total_occurrence
    new_entry.confidence = max_confidence
    new_entry.source = "consolidated"
    # entry_id 重新生成，避免与原条目冲突
    import uuid
    new_entry.entry_id = uuid.uuid4().hex[:12]
    return new_entry


def _llm_based_merge(group_entries: list, llm_call: Callable[[str], str]) -> Optional["MemoryEntry"]:
    """有 llm_call 时：生成一条抽象化摘要，写成新的 MemoryEntry。"""
    from mini_agent.perception.memory_store import MemoryEntry

    prompt = _SUMMARY_PROMPT_TEMPLATE.format(entries_text=_format_entries_for_prompt(group_entries))
    summary_text = (llm_call(prompt) or "").strip()
    if not summary_text:
        return None

    total_occurrence = sum(e.occurrence_count for e in group_entries)
    max_confidence = max(e.confidence for e in group_entries)
    representative = group_entries[0]

    return MemoryEntry(
        session_id=representative.session_id,
        summary=summary_text,
        key_outcomes=[],
        tags=list({t for e in group_entries for t in getattr(e, "tags", [])}),
        model=representative.model,
        entry_type="consolidated_lesson",
        trigger=summary_text,
        outcome="",
        root_cause="",
        suggested_action=summary_text,
        confidence=max_confidence,
        occurrence_count=total_occurrence,
        source="consolidated",
        scope=representative.scope,
    )


def consolidate_before_eviction(
    entries_to_evict: list,
    *,
    embed_call: Optional[Callable[[str], list]] = None,
    llm_call: Optional[Callable[[str], str]] = None,
    min_group_size: int = MIN_CONSOLIDATE_GROUP_SIZE,
) -> "tuple[list, list]":
    """
    输入：即将被淘汰的旧条目列表（MemoryStore 按 created_at 排序后超出
    max_entries 的那一批）。

    返回：(consolidated_entries, truly_evicted_entries)
      consolidated_entries — 归纳产生的新条目（entry_type="consolidated_lesson"），
                              应该被保留写入（替代原有的一批旧条目）
      truly_evicted_entries — 未能归纳、按原逻辑物理删除的条目

    全程失败静默降级：任何异常直接返回 ([], entries_to_evict)，等价于
    完全跳过归纳步骤。
    """
    try:
        from mini_agent.perception.lesson_review import group_lessons

        lesson_entries = [e for e in entries_to_evict if getattr(e, "entry_type", "summary") == "lesson"]
        non_lesson_entries = [e for e in entries_to_evict if getattr(e, "entry_type", "summary") != "lesson"]

        groups = group_lessons(lesson_entries, min_group_size=1, embed_call=embed_call)

        consolidated: list = []
        truly_evicted: list = list(non_lesson_entries)
        grouped_entry_ids: set = set()

        for group in groups:
            if len(group.entries) < min_group_size:
                continue
            grouped_entry_ids.update(e.entry_id for e in group.entries)

            new_entry = None
            if llm_call is not None:
                try:
                    new_entry = _llm_based_merge(group.entries, llm_call)
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.evolution.memory_consolidation.consolidate_before_eviction')
                    new_entry = None
            if new_entry is None:
                try:
                    new_entry = _rule_based_merge(group.entries)
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.evolution.memory_consolidation.consolidate_before_eviction')
                    new_entry = None

            if new_entry is not None:
                consolidated.append(new_entry)
            else:
                truly_evicted.extend(group.entries)

        # 未参与任何达标分组的 lesson 条目，原样淘汰
        for e in lesson_entries:
            if e.entry_id not in grouped_entry_ids:
                truly_evicted.append(e)

        return consolidated, truly_evicted
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.memory_consolidation.consolidate_before_eviction')
        return [], entries_to_evict


__all__ = [
    "MIN_CONSOLIDATE_GROUP_SIZE",
    "CONSOLIDATE_TRIGGER_RATIO",
    "consolidate_before_eviction",
]
