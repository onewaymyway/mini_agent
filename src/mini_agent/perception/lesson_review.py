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
    后续 巩固循环 的后台循环）；每条 lesson 各自独立存储，occurrence_count
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


_EMBEDDING_SIMILARITY_THRESHOLD = 0.75  # embedding cosine similarity 判定"同一主题"的阈值


def group_lessons(
    entries: list,
    min_group_size: int = 1,
    embed_call=None,
) -> list[LessonGroup]:
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

    embed_call（方案一新增，可选）：Optional[Callable[[str], list[float]]]。
    传入时，聚类判定从"关键词 Jaccard ≥ 阈值"改为"关键词 Jaccard ≥ 阈值
    或 embedding cosine similarity ≥ 阈值"（两路取并集）——语义聚类的假阳性
    比假阴性危害更大（错误合并两个不相关 lesson 比漏合并更糟），所以保留
    关键词路径作为兜底而非替换。embed_call 为 None（默认）或调用失败时，
    完全退化为原有纯关键词行为，逐条结果一致（回归保证）。
    """
    from mini_agent.perception.local_embedding import cosine_similarity

    groups: list[tuple[frozenset, LessonGroup, Optional[list]]] = []  # (代表性关键词集合, 分组, 代表性向量)

    for e in entries:
        if getattr(e, "entry_type", "summary") != "lesson":
            continue
        if not e.trigger:
            continue
        kw = _trigger_keywords(e.trigger)
        if not kw:
            continue

        vec = None
        if embed_call is not None:
            try:
                vec = embed_call(e.trigger)
            except Exception:
                vec = None

        best_group = None
        best_score = 0.0
        for rep_kw, group, rep_vec in groups:
            jaccard_score = _jaccard(kw, rep_kw)
            embed_score = 0.0
            if vec is not None and rep_vec is not None:
                try:
                    embed_score = cosine_similarity(vec, rep_vec)
                except Exception:
                    embed_score = 0.0
            matched = (
                jaccard_score >= _SIMILARITY_THRESHOLD
                or embed_score >= _EMBEDDING_SIMILARITY_THRESHOLD
            )
            score = max(jaccard_score, embed_score)
            if matched and score > best_score:
                best_group = group
                best_score = score

        if best_group is not None:
            best_group.entries.append(e)
        else:
            new_group = LessonGroup(key=_normalize_trigger(e.trigger))
            new_group.entries.append(e)
            groups.append((kw, new_group, vec))

    return [g for _, g, _ in groups if len(g.entries) >= min_group_size]


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


def scan_lesson_groups(paths) -> list["LessonGroup"]:
    """
    [修复] soft_goal_deriver.py 的信号3（_from_lesson_review）和事件总线的
    goal.candidate_unvalidated 复核逻辑（_reverify_candidate_signal）都在
    调用 `scan_lesson_groups(self._paths)`，但这个函数此前根本不存在——
    本模块只有 group_lessons(entries)/scan_for_proposals(entries, tier)
    两个函数，且都接受"已加载的 entries 列表"而不是 paths，签名对不上，
    必然 ImportError，被外层 except 静默吞掉。

    这里补一个真正"只传 paths 就能用"的便捷包装：从 workdir 记忆文件
    独立构造一个只读 MemoryStore（不依赖调用方持有 memory_backend 实例——
    SoftGoalDeriver 目前确实没有持有 memory_backend），读取全部条目后
    委托给 group_lessons()，返回全部分组（不按 tier 过滤，由调用方
    自己判断 meets_t1_threshold/meets_t2_t3_threshold，与 _from_lesson_review()
    原有的调用习惯一致）。

    独立构造 MemoryStore 而不是复用调用方已有的实例，会有一次额外的磁盘
    读取开销——SoftGoalDeriver 的调用节奏是 tick 级（默认 60s 一次），
    这个开销可接受；如果后续这个函数被更高频的路径调用，应该改成接受
    可选的 memory_backend 参数、传入时直接复用。
    """
    from mini_agent.perception.memory_store import MemoryStore

    store = MemoryStore(paths.workdir_memory)
    entries = store.all_entries()
    return group_lessons(entries)


__all__ = [
    "LessonGroup",
    "group_lessons",
    "scan_for_proposals",
    "scan_lesson_groups",
    "T1_MIN_OCCURRENCE",
    "T1_MIN_SESSIONS",
    "T2_T3_MIN_OCCURRENCE",
]
