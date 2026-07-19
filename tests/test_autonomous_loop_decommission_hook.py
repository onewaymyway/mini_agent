"""
tests/test_autonomous_loop_decommission_hook.py

覆盖 wiki_next_phase_improvement_plan.md §1.2.3 遗留 TODO 的实现：
AutonomousLoop 巩固循环收尾（_record_consolidation_for_digest）现在会
顺带调用一次 wiki/decommission.py::check_ready_transition()，只在
"未就绪 -> 就绪" 翻转的瞬间往 activity_digest.jsonl 写一条
type=wiki_decommission_ready 的记录。
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

sys.path.insert(0, "src")

import pytest

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.autonomous_loop import AutonomousLoop
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


def _make_loop(paths) -> AutonomousLoop:
    # 只测巩固循环收尾这一段逻辑，不依赖 goal_backlog/input_queue/cfg 的真实实现，
    # _check_decommission_transition()/_record_digest() 都只用到 self._paths。
    return AutonomousLoop(
        goal_backlog=None,
        input_queue=None,
        paths=paths,
        cfg=None,
    )


def _read_digest(paths) -> list[dict]:
    import json
    p = paths.workdir_dir / "activity_digest.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


class _FakeReport:
    prune_candidates = []
    promotion_candidates = []
    capability_map = []


def test_no_transition_when_not_ready(wiki_paths):
    loop = _make_loop(wiki_paths)
    loop._record_consolidation_for_digest(_FakeReport())

    records = _read_digest(wiki_paths)
    types = [r.get("type") for r in records]
    assert "consolidation_completed" in types
    assert "wiki_decommission_ready" not in types


def test_transition_recorded_once(wiki_paths):
    _make_ready(wiki_paths)
    loop = _make_loop(wiki_paths)

    # 第一次巩固循环收尾：应该记录一次翻转提醒
    loop._record_consolidation_for_digest(_FakeReport())
    records = _read_digest(wiki_paths)
    ready_records = [r for r in records if r.get("type") == "wiki_decommission_ready"]
    assert len(ready_records) == 1

    # 第二次巩固循环收尾：已经是就绪状态，不重复提醒
    loop._record_consolidation_for_digest(_FakeReport())
    records = _read_digest(wiki_paths)
    ready_records = [r for r in records if r.get("type") == "wiki_decommission_ready"]
    assert len(ready_records) == 1


def test_hook_exception_does_not_raise(monkeypatch, wiki_paths):
    loop = _make_loop(wiki_paths)

    import mini_agent.wiki.decommission as decommission_mod

    def _boom(_paths):
        raise RuntimeError("boom")

    monkeypatch.setattr(decommission_mod, "check_ready_transition", _boom)
    # 不应该向上抛出，也不应该影响 consolidation_completed 记录本身。
    loop._record_consolidation_for_digest(_FakeReport())
    records = _read_digest(wiki_paths)
    types = [r.get("type") for r in records]
    assert "consolidation_completed" in types
