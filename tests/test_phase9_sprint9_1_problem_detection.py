"""tests/test_phase9_sprint9_1_problem_detection.py

对应 `next_doc/refactor_plan/10-phase9-self-evolution-sprint-plan.md`
Sprint 9-1 验收标准：“Analyzer 能从 Phase 3-8 累积的真实 Experience 数据
中，识别出至少一种重复出现的问题模式，并生成结构化的 `Problem` 记录”。
"""

from __future__ import annotations

from pathlib import Path

from mini_agent.core.experience import Experience
from mini_agent.core.experience_patterns import (
    Problem,
    detect_problems,
    detect_problems_from_experience,
    detect_problems_from_failure_pattern_store,
    problem_from_failure_pattern,
)
from mini_agent.core.experience_store import ExperienceStore
from mini_agent.evolution.failure_pattern_store import (
    FailurePattern,
    run_failure_pattern_aggregation_once,
)
from mini_agent.storage.paths import AgentPaths


def _make(store: ExperienceStore, goal_text: str, status: str, lesson: str = "") -> Experience:
    exp = Experience(
        source="goal_mode",
        goal_text=goal_text,
        status=status,
        rounds_used=1,
        final_report="",
        lesson=lesson,
    )
    store.append(exp)
    return exp


def test_detect_problems_from_experience_finds_recurring_failure(tmp_path: Path):
    # 归一化规则对中文按空白分词（不做语义聚类，见 `_normalize_category`
    # 文档说明），因此“同一类别”这里用完全相同的 goal_text 来模拟“同一个
    # 目标反复重试都失败”这一最常见的重复问题场景。
    store = ExperienceStore(path=tmp_path / "experience_store.db")
    _make(store, "部署新版本到生产环境", "stuck", lesson="部署前先检查环境变量")
    _make(store, "部署新版本到生产环境", "failed", lesson="部署前先检查环境变量")
    _make(store, "部署新版本到测试环境", "done")  # 成功，不计入失败
    _make(store, "写一份周报发给团队", "done")  # 不同类别，不应混入

    problems = detect_problems_from_experience(store=store, min_occurrence=2)

    assert len(problems) == 1
    problem = problems[0]
    assert isinstance(problem, Problem)
    assert problem.source == "experience"
    assert problem.occurrence_count == 2
    assert problem.category == "部署新版本到生产环境"
    assert "部署前先检查环境变量" in problem.description
    assert len(problem.evidence) == 2


def test_detect_problems_from_experience_respects_min_occurrence(tmp_path: Path):
    store = ExperienceStore(path=tmp_path / "experience_store.db")
    _make(store, "只失败过一次的任务", "stuck")

    problems = detect_problems_from_experience(store=store, min_occurrence=2)
    assert problems == []


def test_problem_from_failure_pattern_adapts_fields_without_recompute():
    pattern = FailurePattern(
        pattern_id="objective:部署 新版本:timeout",
        source="objective",
        task_category="部署 新版本",
        root_cause_tag="timeout",
        occurrence_count=5,
        first_seen=1.0,
        last_seen=2.0,
        example_summary="连接数据库超时",
    )

    problem = problem_from_failure_pattern(pattern)

    assert problem.source == "failure_pattern_store"
    assert problem.category == "部署 新版本"
    assert problem.occurrence_count == 5
    assert "timeout" in problem.description
    assert "连接数据库超时" in problem.description
    assert problem.evidence == ["连接数据库超时"]


def test_detect_problems_from_failure_pattern_store_reads_existing_aggregation(tmp_path: Path):
    paths = AgentPaths(project_root=tmp_path)
    exec_path = paths.workdir_dir / "objective_executions.json"
    exec_path.parent.mkdir(parents=True, exist_ok=True)
    exec_path.write_text(
        """{"executions": [
            {"objective_title": "抓取外部网站数据", "finished_at": 1.0,
             "steps": [{"error_msg": "connection timeout"}]},
            {"objective_title": "抓取外部网站数据", "finished_at": 2.0,
             "steps": [{"error_msg": "connection timeout"}]},
            {"objective_title": "抓取外部网站数据", "finished_at": 3.0,
             "steps": [{"error_msg": "connection timeout"}]}
        ]}""",
        encoding="utf-8",
    )

    summary = run_failure_pattern_aggregation_once(paths)
    assert summary.ok
    assert summary.patterns  # 确认底层聚合确实产出了 pattern

    problems = detect_problems_from_failure_pattern_store(paths, min_occurrence=2)

    assert len(problems) == 1
    assert problems[0].source == "failure_pattern_store"
    assert problems[0].occurrence_count == 3


def test_detect_problems_merges_both_sources_without_double_counting(tmp_path: Path):
    store = ExperienceStore(path=tmp_path / "experience_store.db")
    _make(store, "部署新版本到生产环境", "stuck")
    _make(store, "部署新版本到生产环境", "failed")

    paths = AgentPaths(project_root=tmp_path)
    exec_path = paths.workdir_dir / "objective_executions.json"
    exec_path.parent.mkdir(parents=True, exist_ok=True)
    exec_path.write_text(
        """{"executions": [
            {"objective_title": "抓取外部网站数据", "finished_at": 1.0,
             "steps": [{"error_msg": "connection timeout"}]},
            {"objective_title": "抓取外部网站数据", "finished_at": 2.0,
             "steps": [{"error_msg": "connection timeout"}]},
            {"objective_title": "抓取外部网站数据", "finished_at": 3.0,
             "steps": [{"error_msg": "connection timeout"}]}
        ]}""",
        encoding="utf-8",
    )
    run_failure_pattern_aggregation_once(paths)

    problems = detect_problems(store=store, paths=paths, min_occurrence_experience=2, min_occurrence_pattern=2)

    sources = {p.source for p in problems}
    assert sources == {"experience", "failure_pattern_store"}
    # 两路各自独立保留，不合并计数（见 detect_problems 文档说明）
    assert len(problems) == 2


def test_detect_problems_without_paths_only_uses_experience(tmp_path: Path):
    store = ExperienceStore(path=tmp_path / "experience_store.db")
    _make(store, "部署新版本到生产环境", "stuck")
    _make(store, "部署新版本到生产环境", "failed")

    problems = detect_problems(store=store, paths=None, min_occurrence_experience=2)

    assert len(problems) == 1
    assert problems[0].source == "experience"
