"""core/experience_patterns.py — Analyzer 雏形：同类失败 Experience 聚合统计。

见 `next_doc/refactor_plan/04-phase3-experience-layer-sprint-plan.md`
Sprint 3-2：“对同类失败的 Experience 做简单聚合统计（比如‘过去 5 次同类
Goal 失败原因分布’），为 Phase 9 的 Self Evolution 打基础”。

“雏形”的含义（对应完成标志第 3 条“Analyzer 产出的聚合统计已经有雏形，
可以在 Phase 9 直接复用”）：本文件只做统计，不做任何决策或自动干预——
不根据统计结果自动调整 Goal 执行策略、不写回任何存储。Phase 9 落地
Self Evolution 时，`FailurePatternSummary` 这个数据结构和
`summarize_failures()` 这个函数签名预期可以直接复用或轻量包装，具体
"根据统计结果做什么"留给 Phase 9 决定。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from .experience_retrieval import _tokenize, _similarity
from .experience_store import ExperienceStore

# 与 goal_adapter.py::goal_run_result_to_experience() 里 GoalRunResult
# 可能产出的终止状态保持一致（见该文件 lesson 字段的判定条件）。
_FAILURE_STATUSES = frozenset({"stuck", "max_rounds_exhausted", "failed"})


@dataclass
class FailurePatternSummary:
    """一次聚合统计的结果。"""

    total_matched: int
    failure_count: int
    failure_rate: float
    status_distribution: dict = field(default_factory=dict)
    common_lessons: list = field(default_factory=list)


def summarize_failures(
    goal_text: Optional[str] = None,
    store: Optional[ExperienceStore] = None,
    limit: int = 5,
    min_similarity: float = 0.05,
) -> FailurePatternSummary:
    """统计"同类 Goal"里失败原因的分布。

    `goal_text` 为 None 时统计全部历史记录（不做相似度过滤）；给定时
    复用 `experience_retrieval.py` 同款关键词重叠度相似度判断"同类"，
    避免维护两套相似度实现。只取最近 `limit` 条同类失败记录做分布统计，
    与原文举例的"过去 5 次"一致（`limit` 默认 5）。
    """
    store = store or ExperienceStore()
    all_exp = list(reversed(store.all()))  # 最近的在前

    if goal_text:
        query_tokens = _tokenize(goal_text)
        matched = [
            exp for exp in all_exp
            if _similarity(query_tokens, _tokenize(exp.goal_text)) >= min_similarity
        ]
    else:
        matched = all_exp

    total_matched = len(matched)
    failures = [exp for exp in matched if exp.status in _FAILURE_STATUSES][:limit]
    failure_count = len(failures)

    status_counter: Counter = Counter(exp.status for exp in matched)
    lesson_counter: Counter = Counter(
        exp.lesson for exp in failures if exp.lesson
    )

    return FailurePatternSummary(
        total_matched=total_matched,
        failure_count=failure_count,
        failure_rate=(failure_count / total_matched) if total_matched else 0.0,
        status_distribution=dict(status_counter),
        common_lessons=[lesson for lesson, _ in lesson_counter.most_common(limit)],
    )
