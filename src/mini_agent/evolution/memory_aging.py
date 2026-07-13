"""
evolution/memory_aging.py — 时间加权记忆激活（具身改进 v3 C2）

问题：`MemoryStore._score_all()` 原先对所有条目使用同一个全局半衰期
（`_DECAY_HALF_LIFE_DAYS = 30`），不区分"被反复印证的旧知识"和"刚生成、
还未验证的新猜测"，也不区分"用户亲口纠正"和"Agent 自我反思猜测"——这违背
了一个朴素的认知规律：被反复印证的知识更稳固，应该衰减得更慢；一次性的、
环境相关的判断应该衰减得更快，给新信息让路。

具身来源：与 A2（lesson source 区分）同源——人类反馈是最高质量的社会信号，
不应该和 Agent 自我猜测用同一条衰减曲线对待；与此同时，被反复印证
（`occurrence_count` 高）的经验也应该比一次性记录更"抗遗忘"。

实现取舍（直接嵌入检索路径，而非批量预计算）：
  - v3 文档原计划是"巩固循环 tick 时批量更新所有条目的 temporal_weight 缓存
    字段"。但核对 `memory_store.py::_score_all()` 后发现，时间衰减本来就是
    在每次 `search()` 调用时按 `entry.age_days` 实时计算的（`age_days` 是
    一个 property，不是缓存字段）——没有"缓存字段过期"这个问题，批量预计算
    反而要多维护一份缓存一致性。本模块改为提供一个纯函数
    `compute_half_life_days(entry)`，由 `MemoryStore._score_all()` 直接调用，
    替换原来的全局 `self._decay_lambda`，复杂度和実时性都更好——不需要
    新增定时任务，也不需要修改 MemoryEntry 的持久化结构（半衰期是衍生量，
    不需要落盘）。
  - 半衰期基准按 `source` 区分（与 A2 的 LESSON_SOURCES 对应），被反复印证
    （`occurrence_count` 越高）的条目衰减更慢；`entry_type != "lesson"` 的
    summary 型条目沿用历史默认半衰期（30 天），不强行套用 lesson 的语义。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.perception.memory_store import MemoryEntry

# 默认半衰期（天）——与 memory_store.py 原有的全局默认值保持一致，
# 既是 summary 型条目的半衰期，也是未识别 source 时的兜底值。
DEFAULT_HALF_LIFE_DAYS = 30.0

# lesson 型条目按 source 区分的半衰期基准（天）。
# human_feedback 衰减最慢——用户亲自纠正的，价值不会因为时间流逝而快速贬值；
# revert_record 衰减最快——这是一次具体操作被回退的记录，环境/代码变化后
# 很可能不再适用，不应该长期占用检索权重。
_LESSON_HALF_LIFE_BASE: dict[str, float] = {
    "human_feedback":       90.0,
    "experiment_confirmed": 60.0,
    "consolidated":         45.0,  # [方案二] 归纳产物：反复验证过的规律，衰减介于 self_reflection 与 human_feedback 之间
    "self_reflection":      30.0,
    "eval_failure":         21.0,  # [事件总线接入] outcome_tracker 判定 verdict="worsened" 时
                                    # 自动回写的 lesson——有实测数据支持（不是单次用户操作），
                                    # 但仍是针对某次具体 commit 的观察，环境/代码变化后可能
                                    # 不再适用，衰减比 self_reflection 快、比 revert_record 慢。
    "revert_record":        14.0,
}

# occurrence_count 每增加 1，半衰期延长的比例（被反复印证的知识更稳固）。
# 封顶倍数，避免出现次数异常多的条目半衰期无限拉长。
_OCCURRENCE_BOOST_PER_COUNT = 0.3
_MAX_OCCURRENCE_MULTIPLIER = 4.0


def compute_half_life_days(entry: "MemoryEntry") -> float:
    """
    计算单条记忆的有效半衰期（天）。

    - entry_type == "lesson"：按 source 查基准半衰期，再按 occurrence_count
      做加成（被反复印证的经验更"抗遗忘"）。
    - 其他类型（summary / capability_map 等）：沿用历史默认半衰期，
      不区分 source——这些条目的 source 字段本来就不是为它们设计的语义
      （MemoryEntry.source 默认值 "self_reflection" 对 summary 条目而言
      只是占位，不代表真实来源）。
    """
    entry_type = getattr(entry, "entry_type", "summary")
    if entry_type not in ("lesson", "consolidated_lesson"):
        return DEFAULT_HALF_LIFE_DAYS

    source = getattr(entry, "source", "self_reflection")
    base = _LESSON_HALF_LIFE_BASE.get(source, DEFAULT_HALF_LIFE_DAYS)

    occurrence = max(1, int(getattr(entry, "occurrence_count", 1) or 1))
    multiplier = min(
        _MAX_OCCURRENCE_MULTIPLIER,
        1.0 + (occurrence - 1) * _OCCURRENCE_BOOST_PER_COUNT,
    )
    return base * multiplier


def compute_decay_factor(entry: "MemoryEntry") -> float:
    """
    计算单条记忆当前的时间衰减因子（0~1），用于替换
    `MemoryStore._score_all()` 中原有的全局 `exp(-λ * age_days)`。

    公式：0.5 ** (age_days / half_life_days)——与原实现的指数衰减形态
    一致，只是半衰期从全局常量变为按条目计算的值。
    """
    import math

    half_life = max(compute_half_life_days(entry), 0.1)
    age_days = getattr(entry, "age_days", 0.0)
    return 0.5 ** (age_days / half_life)


__all__ = [
    "DEFAULT_HALF_LIFE_DAYS",
    "compute_half_life_days",
    "compute_decay_factor",
]
