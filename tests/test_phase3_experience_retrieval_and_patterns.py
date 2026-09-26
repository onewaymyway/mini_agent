"""tests/test_phase3_experience_retrieval_and_patterns.py

对应 `next_doc/refactor_plan/04-phase3-experience-layer-sprint-plan.md`
Sprint 3-2 验收标准：
1. 一个 Goal 执行结束后，可以生成结构化 Experience（Sprint 3-1 已验证）。
2. 下一个类似 Goal 执行时，Retriever 能检索到它，并且检索结果真实
   出现在传给 LLM 的 context 里。
3. 旧的 history/lesson/decision 相关测试仍然通过（本文件不涉及，由
   既有测试套件保证，见 Sprint 3-2 执行记录里的回归验证）。
"""

from __future__ import annotations

from pathlib import Path

from mini_agent.core.experience import Experience
from mini_agent.core.experience_patterns import summarize_failures
from mini_agent.core.experience_retrieval import (
    render_experiences_as_context,
    retrieve_similar_experiences,
)
from mini_agent.core.experience_store import ExperienceStore


def _make(store: ExperienceStore, goal_text: str, status: str, report: str, lesson: str = "") -> Experience:
    exp = Experience(
        source="goal_mode",
        goal_text=goal_text,
        status=status,
        rounds_used=1,
        final_report=report,
        lesson=lesson,
    )
    store.append(exp)
    return exp


def test_retrieve_similar_experiences_ranks_by_keyword_overlap(tmp_path: Path):
    store = ExperienceStore(path=tmp_path / "experience_store.db")
    _make(store, "部署新版本到生产环境", "done", "上线成功")
    _make(store, "写一份周报发给团队", "done", "已发送")
    _make(store, "部署新版本到测试环境", "stuck", "环境变量缺失", lesson="部署前先检查环境变量")

    results = retrieve_similar_experiences("部署新版本到预发布环境", store=store, limit=2)

    assert len(results) == 2
    goal_texts = [r.goal_text for r in results]
    assert "写一份周报发给团队" not in goal_texts
    assert "部署新版本到生产环境" in goal_texts
    assert "部署新版本到测试环境" in goal_texts


def test_retrieve_similar_experiences_empty_when_no_overlap(tmp_path: Path):
    store = ExperienceStore(path=tmp_path / "experience_store.db")
    _make(store, "写一份周报发给团队", "done", "已发送")

    results = retrieve_similar_experiences("修复登录页崩溃 bug", store=store, limit=3)
    assert results == []


def test_render_experiences_as_context_includes_lesson():
    exp = Experience(
        source="goal_mode",
        goal_text="部署新版本",
        status="stuck",
        rounds_used=2,
        final_report="环境变量缺失",
        lesson="部署前先检查环境变量",
    )
    text = render_experiences_as_context([exp])
    assert "部署新版本" in text
    assert "环境变量缺失" in text
    assert "部署前先检查环境变量" in text


def test_render_experiences_as_context_empty_list_returns_empty_string():
    assert render_experiences_as_context([]) == ""


def test_summarize_failures_aggregates_status_and_lessons(tmp_path: Path):
    store = ExperienceStore(path=tmp_path / "experience_store.db")
    _make(store, "部署新版本 A", "stuck", "环境变量缺失", lesson="先检查环境变量")
    _make(store, "部署新版本 B", "stuck", "环境变量缺失", lesson="先检查环境变量")
    _make(store, "部署新版本 C", "done", "上线成功")

    summary = summarize_failures(goal_text="部署新版本", store=store, limit=5)

    assert summary.total_matched == 3
    assert summary.failure_count == 2
    assert summary.failure_rate == 2 / 3
    assert summary.status_distribution.get("stuck") == 2
    assert "先检查环境变量" in summary.common_lessons


def test_summarize_failures_with_no_goal_text_covers_all_records(tmp_path: Path):
    store = ExperienceStore(path=tmp_path / "experience_store.db")
    _make(store, "目标 A", "done", "ok")
    _make(store, "目标 B", "failed", "失败")

    summary = summarize_failures(store=store)
    assert summary.total_matched == 2
    assert summary.failure_count == 1
