"""core/experience_patterns.py — Analyzer：同类失败 Experience 聚合统计 +
重复问题模式（Problem）识别。

见 `next_doc/refactor_plan/04-phase3-experience-layer-sprint-plan.md`
Sprint 3-2：“对同类失败的 Experience 做简单聚合统计（比如‘过去 5 次同类
Goal 失败原因分布’），为 Phase 9 的 Self Evolution 打基础”。

Phase 3 阶段（`FailurePatternSummary`/`summarize_failures()`）只做统计，
不产出结构化记录。`next_doc/refactor_plan/10-phase9-self-evolution-
sprint-plan.md` Sprint 9-1 在此基础上补上“识别出至少一种重复出现的问题
模式，并生成结构化的 `Problem` 记录”这一环，对应原方案 §42 闭环的第一
环（Experience → Pattern → **Problem** → ...）。

Sprint 9-1 任务表第二项“接入现有 Pattern 逻辑”：`evolution/
failure_pattern_store.py` 已经是一套成熟的失败模式聚合实现（扫描
`objective_executions.json`/`goal_state.json` dead_ends/TurnJudge stuck
事件），因此本文件**不重新实现**同类聚合逻辑，而是新增一个 Adapter
函数 `problem_from_failure_pattern()`，把它产出的 `FailurePattern` 转换
成本文件定义的 `Problem` 结构——与 Phase 1 `GoalAdapter`/Phase 5
`HistoryAdapter` 等既有 Adapter 同一种模式（`Old -> New` 单向转换，不
改动被转换模块内部实现一行）。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from .experience_retrieval import _tokenize, _similarity
from .experience_store import ExperienceStore

if TYPE_CHECKING:
    from mini_agent.evolution.failure_pattern_store import FailurePattern
    from mini_agent.storage.paths import AgentPaths

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
    # 只统计 Goal 执行产生的记录：lesson 导入的 Experience（source="memory_lesson"）
    # 不是一次 Goal 执行，计入分母会稀释 failure_rate（Phase 3 lesson Adapter 的副作用修正）。
    all_exp = [e for e in reversed(store.all()) if e.source == "goal_mode"]  # 最近的在前

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


# ─────────────────────────────────────────────────────────────────────
# Sprint 9-1：Problem（重复出现的问题模式）
# ─────────────────────────────────────────────────────────────────────

def _normalize_category(text: str) -> str:
    """标题归一化：小写、去标点、取前若干词。

    与 `evolution/failure_pattern_store.py::_normalize_category()` 同样
    的思路（不做语义聚类），本文件单独实现一份而不是 import 那个私有
    函数——两个模块统计的输入不完全一样（这里是 `Experience.goal_text`，
    那边是 `objective_title`/`goal_text` 原始字符串），保持各自模块内部
    自洽，避免跨模块 import 私有实现造成不必要的耦合（对应 Sprint 1.5/
    5.0.5 的教训：迁移链的 Adapter 只转换公开产出的数据结构，不深入
    依赖对方内部私有函数）。
    """
    import re

    if not text:
        return "unknown"
    cleaned = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", text.lower())
    words = cleaned.split()
    return " ".join(words[:6]) or "unknown"


@dataclass
class Problem:
    """一个结构化的“重复出现的问题模式”，对应原方案 §42 闭环第二环。

    `source` 标注这条 Problem 是从哪类数据识别出来的，供后续 Sprint 9-2
    生成 `Hypothesis`/`EvolutionProposal` 时判断证据强度：
      - "experience"：直接从 `core/experience_store.py` 里的 Experience
        记录聚合而来（本 Phase 新增的识别路径）。
      - "failure_pattern_store"：从既有 `evolution/failure_pattern_
        store.py` 的 `FailurePattern` 转换而来（Adapter 对接，不是
        重新计算）。
    """

    problem_id: str
    source: str
    category: str
    description: str
    occurrence_count: int
    evidence: list = field(default_factory=list)


def detect_problems_from_experience(
    store: Optional[ExperienceStore] = None,
    min_occurrence: int = 2,
    limit: int = 10,
) -> list[Problem]:
    """从 Experience 记录里识别重复出现的失败问题模式。

    按 `goal_text` 归一化后的类别分组，同一类别下失败状态
    （`_FAILURE_STATUSES`）出现次数达到 `min_occurrence` 才算一个
    `Problem`（避免偶发的一次失败被当成“模式”）。只读统计，不写回任何
    存储、不触发任何自动决策——与 `summarize_failures()` 同样的边界。
    """
    store = store or ExperienceStore()
    all_exp = store.all()

    grouped: dict[str, list] = {}
    for exp in all_exp:
        if exp.status not in _FAILURE_STATUSES:
            continue
        category = _normalize_category(exp.goal_text)
        grouped.setdefault(category, []).append(exp)

    problems: list[Problem] = []
    for category, exps in grouped.items():
        if len(exps) < min_occurrence:
            continue
        exps_sorted = sorted(exps, key=lambda e: e.created_at, reverse=True)
        lesson_counter = Counter(e.lesson for e in exps_sorted if e.lesson)
        top_lesson = lesson_counter.most_common(1)
        description = (
            f"“{category}”类 Goal 反复失败 {len(exps_sorted)} 次"
            + (f"，最常见教训：{top_lesson[0][0]}" if top_lesson else "")
        )
        problems.append(Problem(
            problem_id=f"experience:{category}",
            source="experience",
            category=category,
            description=description,
            occurrence_count=len(exps_sorted),
            evidence=[e.id for e in exps_sorted[:limit]],
        ))

    problems.sort(key=lambda p: -p.occurrence_count)
    return problems


def problem_from_failure_pattern(pattern: "FailurePattern") -> Problem:
    """Adapter：把 `evolution/failure_pattern_store.py::FailurePattern`
    转换为本文件的 `Problem` 结构，不修改 `FailurePattern` 或其产出方
    `failure_pattern_store.py` 内部任何实现（对应 Phase 9 现状盘点里
    “安全设施/决策逻辑模块只新增调用方，不改内部”的原则，虽然
    `failure_pattern_store.py` 本身属于“决策逻辑”而非“安全设施”，但
    同样遵循“先对接、后决定是否需要重写”的止损顺序）。
    """
    return Problem(
        problem_id=f"failure_pattern_store:{pattern.pattern_id}",
        source="failure_pattern_store",
        category=pattern.task_category,
        description=(
            f"“{pattern.task_category}”类任务反复因「{pattern.root_cause_tag}」"
            f"失败 {pattern.occurrence_count} 次"
            + (f"：{pattern.example_summary}" if pattern.example_summary else "")
        ),
        occurrence_count=pattern.occurrence_count,
        evidence=[pattern.example_summary] if pattern.example_summary else [],
    )


def detect_problems_from_failure_pattern_store(
    paths: "AgentPaths",
    min_occurrence: int = 3,
) -> list[Problem]:
    """从既有 `failure_pattern_store.py` 落盘的聚合结果里读取，转换为
    `Problem` 列表。只调用该模块公开的 `load_failure_patterns()`（只读
    查询接口），不重新扫描 `objective_executions.json`/`goal_state.json`
    等原始数据源——那部分聚合逻辑完全复用，不重写。
    """
    from mini_agent.evolution.failure_pattern_store import (
        FailurePattern,
        load_failure_patterns,
    )

    raw_patterns = [FailurePattern.from_dict(d) for d in load_failure_patterns(paths)]
    return [
        problem_from_failure_pattern(p)
        for p in raw_patterns
        if p.occurrence_count >= min_occurrence
    ]


def detect_problems(
    store: Optional[ExperienceStore] = None,
    paths: Optional["AgentPaths"] = None,
    min_occurrence_experience: int = 2,
    min_occurrence_pattern: int = 3,
) -> list[Problem]:
    """合并两路来源的 Problem：Experience 直接聚合 + 既有
    `failure_pattern_store.py` 的 Adapter 转换。`paths` 为 None 时跳过
    第二路（例如测试环境没有真实 `AgentPaths` 落盘目录），只返回
    Experience 路径识别出的 Problem，不报错。

    同一 `category` 在两路都命中时不去重合并计数——两路数据源和统计口径
    不同（Experience 状态 vs. objective/dead_end/turn_judge 原始事件），
    强行合并会丢失“这是两种独立证据都指向同一类问题”这一更强的信号，
    对 Sprint 9-2 生成 Hypothesis 时反而更有参考价值，因此按
    `problem_id`（含 source 前缀）区分保留，不合并。
    """
    problems = detect_problems_from_experience(
        store=store, min_occurrence=min_occurrence_experience,
    )
    if paths is not None:
        try:
            problems += detect_problems_from_failure_pattern_store(
                paths, min_occurrence=min_occurrence_pattern,
            )
        except Exception:
            # 只读旁路统计，不能因为 failure_pattern_store 缺文件/格式
            # 异常而影响 Experience 路径已经拿到的结果。
            pass
    problems.sort(key=lambda p: -p.occurrence_count)
    return problems
