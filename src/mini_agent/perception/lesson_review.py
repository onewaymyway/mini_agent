"""
perception/lesson_review.py — lesson 阈值扫描（Stage 3.1 / Phase C）

对应 self_evolution_implementation_plan.md Stage 3.1 / 设计文档 6.7 节
"演化节奏治理"：

| Tier | 触发条件 |
|---|---|
| T0 | occurrence_count ≥ 1 即自动 apply |
| T1 | occurrence_count ≥ 3 且来自不止一个 session |
| T2/T3 | occurrence_count ≥ 5，且至少一条来源为 human_feedback |

本模块只负责"扫描 + 分组 + 判定是否达标"，不负责生成提案内容——那是
evolution-agent（.agent/agents/evolution-agent.md）的职责，本模块产出的
分组结果会作为该 profile 的 `lessons` input。

实现取舍（Stage 3.1 范围内的简化）：
  - MemoryEntry 目前没有跨条目的去重/聚类机制（设计文档 6.4 节，明确留给
    后续 Phase G 的后台循环）；每条 lesson 各自独立存储，occurrence_count
    字段语义是"同一 session 内连续失败次数"（见 perception/lesson_rules.py），
    不是"跨 session 重复出现次数"。
  - 因此"是否达到 T1 门槛"在本模块里按【相似 trigger 文本分组】实现：
    用 trigger 文本的归一化形式（小写、去标点、提取关键词）做分组 key，
    这是一个轻量级、非语义的分组手段，不是设计文档 6.4 节描述的完整聚类——
    Stage 3.1 的目标是打通"lesson → 提案"闭环，聚类精度留给后续迭代提升。
  - 一个分组的"有效 occurrence_count"= 组内各条目 occurrence_count 之和；
    "是否来自不止一个 session" = 组内 session_id 去重后数量 > 1。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# T1 门槛：occurrence_count 之和 ≥ 3，且来自不止一个 session（设计文档 6.7 节）
T1_MIN_OCCURRENCE = 3
T1_MIN_SESSIONS = 2

# T2/T3 门槛：occurrence_count 之和 ≥ 5，且至少一条来源为 human_feedback
T2_T3_MIN_OCCURRENCE = 5

_STOPWORDS = {
    "the", "a", "an", "to", "of", "in", "on", "for", "with", "and", "or",
    "是", "的", "了", "在", "和", "与", "及", "了", "要", "应", "该",
}


@dataclass
class LessonGroup:
    """一组被判定为"同一主题"的 lesson 条目，及其聚合统计。"""
    key: str
    entries: list = field(default_factory=list)   # list[MemoryEntry]

    @property
    def total_occurrence(self) -> int:
        return sum(e.occurrence_count for e in self.entries)

    @property
    def session_ids(self) -> set:
        return {e.session_id for e in self.entries if e.session_id}

    @property
    def has_human_feedback(self) -> bool:
        return any(e.source == "human_feedback" for e in self.entries)

    @property
    def meets_t1_threshold(self) -> bool:
        return self.total_occurrence >= T1_MIN_OCCURRENCE and len(self.session_ids) >= T1_MIN_SESSIONS

    @property
    def meets_t2_t3_threshold(self) -> bool:
        return self.total_occurrence >= T2_T3_MIN_OCCURRENCE and self.has_human_feedback

    def to_dict(self) -> dict:
        """转为适合传给 evolution-agent profile 的 lessons input 结构。"""
        return {
            "group_key": self.key,
            "total_occurrence": self.total_occurrence,
            "session_count": len(self.session_ids),
            "has_human_feedback": self.has_human_feedback,
            "entries": [
                {
                    "entry_id": e.entry_id,
                    "session_id": e.session_id,
                    "trigger": e.trigger,
                    "outcome": e.outcome,
                    "root_cause": e.root_cause,
                    "suggested_action": e.suggested_action,
                    "occurrence_count": e.occurrence_count,
                    "confidence": e.confidence,
                    "source": e.source,
                }
                for e in self.entries
            ],
        }


def _normalize_trigger(trigger: str) -> str:
    """把 trigger 文本归一化为分组 key：小写、去标点、去停用词、按词排序。

    这是一个故意保持简单的启发式分组手段（见模块文档"实现取舍"一节），
    不追求语义准确，只追求把明显重复的同类 lesson 聚到一起。
    """
    text = trigger.lower()
    text = re.sub(r"[`'\"“”‘’.,;:!?()\[\]{}]", " ", text)
    words = [w for w in re.split(r"\s+", text) if w and w not in _STOPWORDS]
    return " ".join(sorted(set(words)))


def _trigger_keywords(trigger: str) -> frozenset:
    """提取 trigger 文本的关键词集合（小写、去标点、去停用词），用于相似度分组。"""
    text = trigger.lower()
    text = re.sub(r"[`'\"“”‘’.,;:!?()\[\]{}]", " ", text)
    words = {w for w in re.split(r"\s+", text) if w and w not in _STOPWORDS}
    return frozenset(words)


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# 关键词集合 Jaccard 相似度高于此阈值，视为"同一主题"。
# 0.3 是经验取值：要求至少三分之一左右的关键词重合，足以把"rm -rf 没确认就执行"
# 和"rm -rf 执行前没有先确认路径"这类措辞不同但讲的是同一件事的 lesson 聚到一起，
# 同时不至于把明显无关的 lesson（如"venv 版本错误" vs "rm -rf 误删"）混进同一组。
_SIMILARITY_THRESHOLD = 0.3


def group_lessons(entries: list, min_group_size: int = 1) -> list[LessonGroup]:
    """
    按 trigger 文本的关键词 Jaccard 相似度对 lesson 条目分组（贪心聚类：
    依次把每条 lesson 并入第一个相似度达标的已有分组，否则新开一组）。

    这是一个故意保持简单的启发式分组手段（见模块文档"实现取舍"一节），
    不是设计文档 6.4 节描述的完整语义聚类，但比"精确字符串匹配"更能合并
    措辞不同、主题相同的 lesson——Stage 3.1 的目标是打通"lesson → 提案"
    闭环，聚类精度留给后续迭代提升（例如换成 embedding 相似度）。

    只接受 entry_type == "lesson" 的条目（summary/capability_map 类型不参与分组）。
    min_group_size 用于过滤掉分组后条目数不足的噪声分组（默认 1，即不过滤，
    由调用方根据 tier 阈值自行判断是否达标）。
    """
    groups: list[tuple[frozenset, LessonGroup]] = []  # (代表性关键词集合, 分组)

    for e in entries:
        if getattr(e, "entry_type", "summary") != "lesson":
            continue
        if not e.trigger:
            continue
        kw = _trigger_keywords(e.trigger)
        if not kw:
            continue

        best_group = None
        best_score = 0.0
        for rep_kw, group in groups:
            score = _jaccard(kw, rep_kw)
            if score >= _SIMILARITY_THRESHOLD and score > best_score:
                best_group = group
                best_score = score

        if best_group is not None:
            best_group.entries.append(e)
        else:
            new_group = LessonGroup(key=_normalize_trigger(e.trigger))
            new_group.entries.append(e)
            groups.append((kw, new_group))

    return [g for _, g in groups if len(g.entries) >= min_group_size]


def scan_for_proposals(
    entries: list,
    tier: str = "T1",
) -> list[LessonGroup]:
    """
    扫描全部 lesson 条目，返回达到指定 tier 证据门槛的分组列表（按
    total_occurrence 降序，最值得优先审查的排在前面）。

    tier="T1" → meets_t1_threshold
    tier in ("T2", "T3") → meets_t2_t3_threshold
    其他值视为 T1（最保守的非 T0 门槛）。
    """
    groups = group_lessons(entries)

    if tier in ("T2", "T3"):
        qualifying = [g for g in groups if g.meets_t2_t3_threshold]
    else:
        qualifying = [g for g in groups if g.meets_t1_threshold]

    qualifying.sort(key=lambda g: g.total_occurrence, reverse=True)
    return qualifying


__all__ = [
    "LessonGroup",
    "group_lessons",
    "scan_for_proposals",
    "T1_MIN_OCCURRENCE",
    "T1_MIN_SESSIONS",
    "T2_T3_MIN_OCCURRENCE",
]
