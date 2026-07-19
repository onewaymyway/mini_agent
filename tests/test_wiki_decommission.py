"""
tests/test_wiki_decommission.py — wiki/decommission.py 单元测试

覆盖：未达标时给出 blocking_reasons、达标时给出三步下线清单、报告落盘/
重新读取、check_ready_transition() 的状态翻转检测。
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

sys.path.insert(0, "src")

import pytest

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.decommission import check_and_plan, check_ready_transition, load_last_report
from mini_agent.wiki.promotion import record_daily_snapshot, record_search_comparison
from mini_agent.wiki.writer import write_page


@pytest.fixture()
def wiki_paths(tmp_path):
    paths = AgentPaths(project_root=tmp_path)
    paths.ensure_wiki_dirs()
    return paths


def _make_ready(wiki_paths):
    write_page(
        wiki_paths, page_id="e1", page_type="entity", body="x",
        extra_frontmatter={"source_kind": "world_model"},
    )
    base = date(2026, 7, 18)
    for i in range(14):
        record_daily_snapshot(wiki_paths, today=base - timedelta(days=i))
    for _ in range(25):
        record_search_comparison(wiki_paths, wiki_grounded=True, shelf_grounded=False)


def test_check_and_plan_not_ready_gives_blocking_reasons(wiki_paths):
    plan = check_and_plan(wiki_paths)
    assert plan.ready is False
    assert plan.steps == []
    assert len(plan.blocking_reasons) >= 1


def test_check_and_plan_ready_gives_three_steps(wiki_paths):
    _make_ready(wiki_paths)
    plan = check_and_plan(wiki_paths)
    assert plan.ready is True
    assert plan.blocking_reasons == []
    assert [s["step"] for s in plan.steps] == [1, 2, 3]
    assert plan.steps[2]["reversible"] is False


def test_check_and_plan_writes_readable_report(wiki_paths):
    plan = check_and_plan(wiki_paths)
    assert plan.ready is False
    loaded = load_last_report(wiki_paths)
    assert loaded is not None
    assert loaded["ready"] is False


def test_load_last_report_none_when_never_run(tmp_path):
    paths = AgentPaths(project_root=tmp_path)
    paths.ensure_wiki_dirs()
    assert load_last_report(paths) is None


def test_check_ready_transition_only_true_once(wiki_paths):
    # 第一次：未就绪 -> 未就绪，不算翻转
    assert check_ready_transition(wiki_paths) is False

    _make_ready(wiki_paths)

    # 第二次：未就绪 -> 就绪，算一次翻转
    assert check_ready_transition(wiki_paths) is True

    # 第三次：已经是就绪状态，不再重复提醒
    assert check_ready_transition(wiki_paths) is False
