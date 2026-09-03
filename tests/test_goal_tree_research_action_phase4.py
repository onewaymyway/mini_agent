"""tests/test_goal_tree_research_action_phase4.py — 目标树 × 自主调研
阶段四（自动巡检 + 看板展示 + CLI/API 收尾）

覆盖 next_doc/goal_tree_research_and_action_recommendation_plan.md §4.2/
§4.6/§五 阶段四：
  - FocusResearchTrigger.load_focus_snapshot()/save_focus_snapshot()
  - run_focus_research_scan_cycle()：新进入焦点的节点被触发一次调研，
    快照按当前完整焦点集合刷新；max_nodes 截断；不重复触发已处理节点
  - list_pending_research_candidates()：按 node_id 过滤 pending
    focus_research 候选
  - ensure_goal_tree_focus_recompute_job() 的 handler：默认关闭时不产生
    额外调研候选（向后兼容）；显式开启 cfg 开关后自动触发
  - next_action_advisor.load_all_next_actions()/filter_focus_next_step_
    items()：不受 shown_at 影响、可选按 node_id 过滤
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_agent.config.models import GrowthAdvisorConfig
from mini_agent.evolution.focus_research_trigger import (
    DEFAULT_MAX_NODES_PER_SCAN,
    FocusResearchTrigger,
    list_pending_research_candidates,
    run_focus_research_scan_cycle,
)
from mini_agent.evolution.growth_advisor import GrowthBacklog
from mini_agent.evolution.next_action_advisor import (
    filter_focus_next_step_items,
    load_all_next_actions,
)
from mini_agent.perception.goal_backlog import (
    JOB_ID_FOCUS_RECOMPUTE,
    GoalBacklog,
    ensure_goal_tree_focus_recompute_job,
)
from mini_agent.storage.paths import AgentPaths


def _make_backlog(tmp) -> GoalBacklog:
    return GoalBacklog(AgentPaths(Path(tmp)))


class _FakeCronScheduler:
    """跟 tests/test_goal_tree_phase3.py 里的同名 fixture 同构，独立复制
    一份避免跨测试文件 import 私有辅助类。"""

    def __init__(self):
        self._jobs = {}
        self._handlers = {}

    def list_jobs(self):
        return list(self._jobs.values())

    def ensure_job(self, *, job_id, name, schedule, description="", tags=None):
        if job_id not in self._jobs:
            self._jobs[job_id] = type("J", (), {"id": job_id})()

    def register_local_handler(self, job_id, handler):
        self._handlers[job_id] = handler

    def run(self, job_id):
        handler = self._handlers.get(job_id)
        if handler is None:
            return False
        return handler(self._jobs.get(job_id))


# ── FocusResearchTrigger 焦点快照 ───────────────────────────────────────────


class TestFocusSnapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))
        self.backlog = GoalBacklog(self.paths)

    def tearDown(self):
        self._tmp.cleanup()

    def test_snapshot_empty_by_default(self):
        trigger = FocusResearchTrigger(self.paths, self.backlog)
        self.assertEqual(trigger.load_focus_snapshot(), set())

    def test_save_and_load_roundtrip(self):
        trigger = FocusResearchTrigger(self.paths, self.backlog)
        trigger.save_focus_snapshot({"a", "b"})
        self.assertEqual(trigger.load_focus_snapshot(), {"a", "b"})

    def test_snapshot_does_not_collide_with_node_timestamps(self):
        trigger = FocusResearchTrigger(self.paths, self.backlog)
        goal = self.backlog.add_goal("目标 A", priority=1)
        trigger.trigger(goal.id)
        trigger.save_focus_snapshot({goal.id})
        # 节点自己的触发时间戳不受快照写入影响。
        self.assertGreater(trigger.last_triggered_at(goal.id), 0)
        self.assertEqual(trigger.load_focus_snapshot(), {goal.id})


# ── run_focus_research_scan_cycle() ─────────────────────────────────────────


class TestRunFocusResearchScanCycle(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))
        self.backlog = GoalBacklog(self.paths)

    def tearDown(self):
        self._tmp.cleanup()

    def test_first_run_triggers_all_current_focus_nodes_within_max(self):
        root = self.backlog.get_root_node()
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        goal = self.backlog.add_node("goal", "找工作", parent_id=domain.id, priority=5)
        self.backlog.update_fields(root.id, current_focus_ids=[domain.id])
        self.backlog.update_fields(domain.id, current_focus_ids=[goal.id])

        summary = run_focus_research_scan_cycle(self.paths, self.backlog)

        self.assertTrue(summary.ok)
        self.assertEqual(summary.newly_focused_count, 2)
        self.assertEqual(summary.triggered_count, 2)

        pending = GrowthBacklog(self.paths).pending()
        pending_titles = {c.title for c in pending}
        self.assertIn(domain.title, pending_titles)
        self.assertIn(goal.title, pending_titles)

    def test_second_run_with_no_focus_change_triggers_nothing(self):
        root = self.backlog.get_root_node()
        domain = self.backlog.add_node("domain", "健康", parent_id=root.id)
        self.backlog.update_fields(root.id, current_focus_ids=[domain.id])

        run_focus_research_scan_cycle(self.paths, self.backlog)
        first_pending_count = len(GrowthBacklog(self.paths).pending())

        summary_second = run_focus_research_scan_cycle(self.paths, self.backlog)
        self.assertEqual(summary_second.newly_focused_count, 0)
        self.assertEqual(summary_second.triggered_count, 0)
        self.assertEqual(len(GrowthBacklog(self.paths).pending()), first_pending_count)

    def test_max_nodes_truncates_this_round(self):
        root = self.backlog.get_root_node()
        domains = [
            self.backlog.add_node("domain", f"领域{i}", parent_id=root.id)
            for i in range(3)
        ]
        self.backlog.update_fields(root.id, current_focus_ids=[d.id for d in domains])

        summary = run_focus_research_scan_cycle(self.paths, self.backlog, max_nodes=1)
        self.assertEqual(summary.newly_focused_count, 3)
        self.assertEqual(summary.triggered_count, 1)
        # 快照仍然刷新为完整焦点集合，不是只记被处理的那 1 个。
        trigger = FocusResearchTrigger(self.paths, self.backlog)
        self.assertEqual(trigger.load_focus_snapshot(), {d.id for d in domains})

    def test_default_max_nodes_constant_is_conservative(self):
        # 默认截断值保守（不会一次性打爆），对齐配置项默认值。
        self.assertEqual(DEFAULT_MAX_NODES_PER_SCAN, 5)


# ── list_pending_research_candidates() ──────────────────────────────────────


class TestListPendingResearchCandidates(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))
        self.backlog = GoalBacklog(self.paths)

    def tearDown(self):
        self._tmp.cleanup()

    def test_returns_only_matching_node_pending_candidates(self):
        goal_a = self.backlog.add_goal("目标 A", priority=1)
        goal_b = self.backlog.add_goal("目标 B", priority=1)
        trigger = FocusResearchTrigger(self.paths, self.backlog)
        trigger.trigger(goal_a.id)
        trigger.trigger(goal_b.id)

        result_a = list_pending_research_candidates(self.paths, goal_a.id)
        self.assertEqual(len(result_a), 1)
        self.assertEqual(result_a[0].title, goal_a.title)

    def test_returns_empty_for_node_without_candidates(self):
        goal = self.backlog.add_goal("目标 C", priority=1)
        result = list_pending_research_candidates(self.paths, goal.id)
        self.assertEqual(result, [])

    def test_accepted_candidate_no_longer_pending(self):
        goal = self.backlog.add_goal("目标 D", priority=1)
        trigger = FocusResearchTrigger(self.paths, self.backlog)
        candidate = trigger.trigger(goal.id)
        gb = GrowthBacklog(self.paths)
        gb.set_status(candidate.candidate_id, "accepted")
        result = list_pending_research_candidates(self.paths, goal.id)
        self.assertEqual(result, [])


# ── ensure_goal_tree_focus_recompute_job() handler 联动 ─────────────────────


class TestFocusRecomputeJobAutoTrigger(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.backlog = _make_backlog(self._tmp.name)
        self.scheduler = _FakeCronScheduler()

    def tearDown(self):
        self._tmp.cleanup()

    def test_default_disabled_does_not_auto_trigger_research(self):
        ensure_goal_tree_focus_recompute_job(self.backlog, self.scheduler)
        root = self.backlog.add_node("ultimate", "人生")
        self.backlog.add_node("domain", "事业", parent_id=root.id)
        ok = self.scheduler.run(JOB_ID_FOCUS_RECOMPUTE)
        self.assertTrue(ok)
        # 默认配置下 goal_tree_focus_research_auto_trigger_enabled=False，
        # 不应该产生任何 GrowthBacklog 候选（向后兼容，零改动）。
        pending = GrowthBacklog(self.backlog._paths).pending()
        self.assertEqual(pending, [])

    def test_enabled_via_config_file_auto_triggers_research(self):
        # 显式在 agent_config.json 里打开开关，模拟真实用户配置路径
        # （handler 内部通过 load_config(project_root) 读取，不接受直接
        # 注入 cfg，所以要落一份配置文件）。
        cfg_path = self.backlog._paths.project_root / "agent_config.json"
        cfg_path.write_text(
            json.dumps({
                "growth_advisor": {
                    "goal_tree_focus_research_auto_trigger_enabled": True,
                },
            }),
            encoding="utf-8",
        )
        ensure_goal_tree_focus_recompute_job(self.backlog, self.scheduler)
        root = self.backlog.add_node("ultimate", "人生")
        self.backlog.add_node("domain", "事业", parent_id=root.id)
        ok = self.scheduler.run(JOB_ID_FOCUS_RECOMPUTE)
        self.assertTrue(ok)
        pending = GrowthBacklog(self.backlog._paths).pending()
        self.assertGreaterEqual(len(pending), 1)


# ── next_action_advisor 只读查询辅助函数 ────────────────────────────────────


class TestNextActionAdvisorReadHelpers(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _write_next_actions(self, items, shown_at=None):
        self.paths.next_actions_path.parent.mkdir(parents=True, exist_ok=True)
        self.paths.next_actions_path.write_text(
            json.dumps({"generated_at": 0.0, "shown_at": shown_at, "items": items}),
            encoding="utf-8",
        )

    def test_load_all_next_actions_returns_none_when_missing(self):
        self.assertIsNone(load_all_next_actions(self.paths))

    def test_load_all_next_actions_ignores_shown_at(self):
        self._write_next_actions([{"kind": "stale_goal", "ref_id": "g1"}], shown_at=123.0)
        data = load_all_next_actions(self.paths)
        self.assertIsNotNone(data)
        self.assertEqual(len(data["items"]), 1)

    def test_filter_focus_next_step_items_by_kind(self):
        self._write_next_actions([
            {"kind": "stale_goal", "ref_id": "g1"},
            {"kind": "focus_next_step", "ref_id": "n1:spec"},
            {"kind": "focus_next_step", "ref_id": "n2:continue"},
        ])
        data = load_all_next_actions(self.paths)
        items = filter_focus_next_step_items(data)
        self.assertEqual(len(items), 2)

    def test_filter_focus_next_step_items_by_node_id(self):
        self._write_next_actions([
            {"kind": "focus_next_step", "ref_id": "n1:spec"},
            {"kind": "focus_next_step", "ref_id": "n2:continue"},
        ])
        data = load_all_next_actions(self.paths)
        items = filter_focus_next_step_items(data, node_id="n1")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["ref_id"], "n1:spec")

    def test_filter_handles_none_data(self):
        self.assertEqual(filter_focus_next_step_items(None), [])
        self.assertEqual(filter_focus_next_step_items(None, node_id="n1"), [])


if __name__ == "__main__":
    unittest.main()
