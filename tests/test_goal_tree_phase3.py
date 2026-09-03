"""
tests/test_goal_tree_phase3.py — 目标树系统阶段三（现阶段焦点）测试

覆盖 next_doc/goal_tree_system_plan.md §4.3、§五 阶段三：
  - compute_current_focus()：pin 优先并入、按 priority + aging 排序补足、
    top_n 截断、全终态返回空列表
  - GoalBacklog.set_focus_pin() / recompute_current_focus_tree()
  - ensure_goal_tree_focus_recompute_job()（sys:goal_tree_focus_recompute）
  - goal_tree_decomposer.run_decompose_scan_cycle()：停滞巡检 + 完成态联动
    两路命中合并去重、逐节点调用 decompose()
  - ensure_goal_tree_decompose_scan_job()（sys:goal_tree_decompose_scan，
    包含"没有可用 llm_helper 时静默跳过"这条 opt-in 语义）
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.perception.goal_backlog import (
    DEFAULT_FOCUS_TOP_N,
    GoalBacklog,
    JOB_ID_FOCUS_RECOMPUTE,
    compute_current_focus,
    ensure_goal_tree_focus_recompute_job,
)
from mini_agent.perception.goal_tree_decomposer import (
    JOB_ID_DECOMPOSE_SCAN,
    DecomposeScanSummary,
    ensure_goal_tree_decompose_scan_job,
    run_decompose_scan_cycle,
)
from mini_agent.storage.paths import AgentPaths


def _make_backlog(tmp) -> GoalBacklog:
    paths = AgentPaths(Path(tmp))
    return GoalBacklog(paths)


def _make_domain(backlog: GoalBacklog, title: str = "事业"):
    """`domain` 节点的父节点只能是 `ultimate`（§4.1.1），测试里凡是只需要
    一个孤立 `domain` 节点（不关心根节点本身）的场景，统一走这个辅助函数
    先补一个根节点。"""
    root = backlog.add_node("ultimate", "根")
    return backlog.add_node("domain", title, parent_id=root.id)


class _FakeLLMHelper:
    """最小 llm_helper 替身：只实现 .ask(prompt) -> str。"""

    def __init__(self, response: str = ""):
        self.response = response
        self.calls: list[str] = []

    def ask(self, prompt: str, **kwargs) -> str:
        self.calls.append(prompt)
        return self.response


class _FakeCronJob:
    def __init__(self, id_: str):
        self.id = id_


class _FakeCronScheduler:
    """最小 CronScheduler 替身：只实现 ensure_job/register_local_handler/
    list_jobs/run（供测试直接触发 handler），与
    tests/test_wiki_quarantine.py 里的替身同构。"""

    def __init__(self):
        self._jobs: dict[str, _FakeCronJob] = {}
        self._handlers: dict[str, "callable"] = {}

    def list_jobs(self):
        return list(self._jobs.values())

    def ensure_job(self, job_id, name, schedule, description="", tags=None, **kwargs):
        if job_id not in self._jobs:
            self._jobs[job_id] = _FakeCronJob(job_id)
        return self._jobs[job_id]

    def register_local_handler(self, job_id, handler):
        self._handlers[job_id] = handler

    def run(self, job_id) -> bool:
        handler = self._handlers[job_id]
        return handler(self._jobs[job_id])


# ── compute_current_focus() ─────────────────────────────────────────────────

class TestComputeCurrentFocus(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.backlog = _make_backlog(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_empty_children_returns_empty(self):
        node = _make_domain(self.backlog)
        self.assertEqual(compute_current_focus(node, [], time.time()), [])

    def test_all_terminal_children_returns_empty(self):
        domain = _make_domain(self.backlog)
        c1 = self.backlog.add_node("goal", "已完成", parent_id=domain.id, status="completed")
        c2 = self.backlog.add_node("goal", "已放弃", parent_id=domain.id, status="abandoned")
        domain = self.backlog.get(domain.id)
        children = [self.backlog.get(c1.id), self.backlog.get(c2.id)]
        self.assertEqual(compute_current_focus(domain, children, time.time()), [])

    def test_picks_top_n_by_priority(self):
        domain = _make_domain(self.backlog)
        low = self.backlog.add_node("goal", "低优先级", parent_id=domain.id, priority=1)
        high = self.backlog.add_node("goal", "高优先级", parent_id=domain.id, priority=9)
        mid = self.backlog.add_node("goal", "中优先级", parent_id=domain.id, priority=5)
        domain = self.backlog.get(domain.id)
        children = [self.backlog.get(n.id) for n in (low, high, mid)]
        result = compute_current_focus(domain, children, time.time(), top_n=2)
        self.assertEqual(result, [high.id, mid.id])

    def test_pinned_included_regardless_of_status(self):
        domain = _make_domain(self.backlog)
        pinned_done = self.backlog.add_node(
            "goal", "已完成但被 pin", parent_id=domain.id, status="completed",
        )
        active = self.backlog.add_node("goal", "活跃", parent_id=domain.id, priority=5)
        domain = self.backlog.get(domain.id)
        domain.focus_pinned_ids = [pinned_done.id]
        children = [self.backlog.get(n.id) for n in (pinned_done, active)]
        result = compute_current_focus(domain, children, time.time(), top_n=2)
        self.assertEqual(result[0], pinned_done.id)
        self.assertIn(active.id, result)

    def test_pinned_not_in_children_is_dropped(self):
        domain = _make_domain(self.backlog)
        active = self.backlog.add_node("goal", "活跃", parent_id=domain.id, priority=5)
        domain = self.backlog.get(domain.id)
        domain.focus_pinned_ids = ["not_a_real_child"]
        children = [self.backlog.get(active.id)]
        result = compute_current_focus(domain, children, time.time(), top_n=2)
        self.assertEqual(result, [active.id])

    def test_default_top_n_matches_constant(self):
        self.assertEqual(DEFAULT_FOCUS_TOP_N, 3)

    def test_aging_boost_can_overtake_priority(self):
        domain = _make_domain(self.backlog)
        fresh_high = self.backlog.add_node(
            "goal", "刚创建的高优先级", parent_id=domain.id, priority=5,
        )
        stale_low = self.backlog.add_node(
            "goal", "停滞很久的低优先级", parent_id=domain.id, priority=4,
        )
        domain = self.backlog.get(domain.id)
        stale_node = self.backlog.get(stale_low.id)
        stale_node.last_touched_at = time.time() - 30 * 86400  # 停滞 30 天
        children = [self.backlog.get(fresh_high.id), stale_node]
        result = compute_current_focus(domain, children, time.time(), top_n=1)
        self.assertEqual(result, [stale_low.id])


# ── GoalBacklog.set_focus_pin() / recompute_current_focus_tree() ───────────

class TestSetFocusPin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.backlog = _make_backlog(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_pin_adds_and_recomputes_immediately(self):
        domain = _make_domain(self.backlog)
        low = self.backlog.add_node("goal", "低优先级", parent_id=domain.id, priority=1)
        high = self.backlog.add_node("goal", "高优先级", parent_id=domain.id, priority=9)
        ok = self.backlog.set_focus_pin(domain.id, low.id, True)
        self.assertTrue(ok)
        domain = self.backlog.get(domain.id)
        self.assertIn(low.id, domain.focus_pinned_ids)
        self.assertIn(low.id, domain.current_focus_ids)
        self.assertIn(high.id, domain.current_focus_ids)

    def test_unpin_removes(self):
        domain = _make_domain(self.backlog)
        child = self.backlog.add_node("goal", "子", parent_id=domain.id)
        self.backlog.set_focus_pin(domain.id, child.id, True)
        ok = self.backlog.set_focus_pin(domain.id, child.id, False)
        self.assertTrue(ok)
        domain = self.backlog.get(domain.id)
        self.assertNotIn(child.id, domain.focus_pinned_ids)

    def test_pin_rejects_unknown_node(self):
        self.assertFalse(self.backlog.set_focus_pin("no_such_node", "x", True))

    def test_pin_rejects_non_child(self):
        domain = _make_domain(self.backlog)
        self.assertFalse(self.backlog.set_focus_pin(domain.id, "not_a_child", True))


class TestRecomputeCurrentFocusTree(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.backlog = _make_backlog(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_no_root_returns_zero(self):
        self.assertEqual(self.backlog.recompute_current_focus_tree(), 0)

    def test_recomputes_ultimate_domain_stage_only(self):
        root = self.backlog.add_node("ultimate", "人生")
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        goal = self.backlog.add_node("goal", "找工作", parent_id=domain.id, priority=5)
        self.backlog.add_node("objective", "写简历", parent_id=goal.id, priority=3)

        updated = self.backlog.recompute_current_focus_tree()
        self.assertGreaterEqual(updated, 2)  # root + domain 至少各更新一次

        root = self.backlog.get(root.id)
        domain = self.backlog.get(domain.id)
        goal = self.backlog.get(goal.id)
        self.assertIn(domain.id, root.current_focus_ids)
        self.assertIn(goal.id, domain.current_focus_ids)
        # goal/objective 两层不参与本字段计算，恒为空
        self.assertEqual(goal.current_focus_ids, [])

    def test_second_call_is_idempotent_when_nothing_changed(self):
        root = self.backlog.add_node("ultimate", "人生")
        self.backlog.add_node("domain", "事业", parent_id=root.id)
        self.backlog.recompute_current_focus_tree()
        updated_again = self.backlog.recompute_current_focus_tree()
        self.assertEqual(updated_again, 0)

    def test_bottom_up_reflects_child_completion(self):
        root = self.backlog.add_node("ultimate", "人生")
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        goal = self.backlog.add_node("goal", "唯一的目标", parent_id=domain.id)
        self.backlog.recompute_current_focus_tree()
        domain_before = self.backlog.get(domain.id)
        self.assertIn(goal.id, domain_before.current_focus_ids)

        self.backlog.set_status(goal.id, "completed")
        self.backlog.recompute_current_focus_tree()
        domain_after = self.backlog.get(domain.id)
        self.assertEqual(domain_after.current_focus_ids, [])


class TestEnsureFocusRecomputeJob(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.backlog = _make_backlog(self._tmpdir.name)
        self.scheduler = _FakeCronScheduler()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_registers_job_and_handler_recomputes(self):
        newly_added = ensure_goal_tree_focus_recompute_job(self.backlog, self.scheduler)
        self.assertTrue(newly_added)
        self.assertIn(JOB_ID_FOCUS_RECOMPUTE, {j.id for j in self.scheduler.list_jobs()})

        root = self.backlog.add_node("ultimate", "人生")
        self.backlog.add_node("domain", "事业", parent_id=root.id)
        ok = self.scheduler.run(JOB_ID_FOCUS_RECOMPUTE)
        self.assertTrue(ok)
        root = self.backlog.get(root.id)
        self.assertTrue(root.current_focus_ids)

    def test_second_call_does_not_re_add(self):
        ensure_goal_tree_focus_recompute_job(self.backlog, self.scheduler)
        newly_added_again = ensure_goal_tree_focus_recompute_job(self.backlog, self.scheduler)
        self.assertFalse(newly_added_again)


# ── run_decompose_scan_cycle() / ensure_goal_tree_decompose_scan_job() ─────

class TestRunDecomposeScanCycle(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_stale_node_triggers_decompose(self):
        domain = _make_domain(self.backlog)
        domain = self.backlog.get(domain.id)
        domain.last_touched_at = time.time() - 20 * 86400  # 停滞 20 天，超过 14 天阈值
        self.backlog.save()

        helper = _FakeLLMHelper("新阶段目标｜描述｜stage")
        result = run_decompose_scan_cycle(self.paths, self.backlog, llm_helper=helper)
        self.assertIsInstance(result, DecomposeScanSummary)
        self.assertTrue(result.ok)
        self.assertEqual(result.stale_node_count, 1)
        self.assertEqual(result.candidate_count, 1)
        domain = self.backlog.get(domain.id)
        self.assertEqual(len(domain.decompose_candidates), 1)

    def test_completion_linked_parent_triggers_decompose(self):
        root = self.backlog.add_node("ultimate", "人生")
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        stage = self.backlog.add_node("stage", "唯一的阶段目标", parent_id=domain.id)
        # domain 自身没有超过停滞阈值（last_touched_at 是刚创建的），不会
        # 被 find_stale_nodes_for_scan() 命中，只能靠完成态联动命中。
        self.backlog.set_status(stage.id, "completed")

        helper = _FakeLLMHelper("下一阶段｜描述｜stage")
        result = run_decompose_scan_cycle(self.paths, self.backlog, llm_helper=helper)
        self.assertEqual(result.completion_linked_count, 1)
        self.assertIn(domain.id, result.node_ids)

    def test_old_completion_outside_lookback_window_is_ignored(self):
        root = self.backlog.add_node("ultimate", "人生")
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        stage = self.backlog.add_node("stage", "唯一的阶段目标", parent_id=domain.id)
        self.backlog.set_status(stage.id, "completed")
        stage_node = self.backlog.get(stage.id)
        stage_node.last_touched_at = time.time() - 40 * 3600  # 40 小时前，超过默认 25 小时回看窗口
        domain_node = self.backlog.get(domain.id)
        domain_node.last_touched_at = time.time()  # domain 本身也不停滞
        self.backlog.save()

        helper = _FakeLLMHelper("候选｜desc｜stage")
        result = run_decompose_scan_cycle(self.paths, self.backlog, llm_helper=helper)
        self.assertEqual(result.completion_linked_count, 0)
        self.assertEqual(result.stale_node_count, 0)
        self.assertEqual(result.scanned_count, 0)

    def test_stale_and_completion_linked_dedup(self):
        """同一个节点既停滞又是完成态联动命中时，只应该被处理一次。"""
        root = self.backlog.add_node("ultimate", "人生")
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        stage = self.backlog.add_node("stage", "唯一的阶段目标", parent_id=domain.id)
        self.backlog.set_status(stage.id, "completed")
        domain_node = self.backlog.get(domain.id)
        domain_node.last_touched_at = time.time() - 20 * 86400  # 同时满足停滞条件
        self.backlog.save()

        helper = _FakeLLMHelper("候选｜desc｜stage")
        result = run_decompose_scan_cycle(self.paths, self.backlog, llm_helper=helper)
        self.assertEqual(result.stale_node_count, 1)
        self.assertEqual(result.completion_linked_count, 0)  # 已被停滞集合去重排除
        self.assertEqual(result.scanned_count, 1)

    def test_no_hits_returns_empty_summary(self):
        _make_domain(self.backlog, "刚创建，不停滞")
        helper = _FakeLLMHelper("候选｜desc｜stage")
        result = run_decompose_scan_cycle(self.paths, self.backlog, llm_helper=helper)
        self.assertEqual(result.scanned_count, 0)
        self.assertEqual(result.candidate_count, 0)
        self.assertEqual(helper.calls, [])

    def test_rhythm_governance_still_applies_inside_decompose(self):
        """decompose() 内部的节奏治理（已有未处理候选时跳过）依然生效，
        本函数不会绕过它。"""
        domain = _make_domain(self.backlog)
        domain_node = self.backlog.get(domain.id)
        domain_node.last_touched_at = time.time() - 20 * 86400
        self.backlog.save()
        self.backlog.append_decompose_candidates(domain.id, [
            {"id": "cand_existing", "title": "已有候选", "description": "", "level": "stage"},
        ])
        helper = _FakeLLMHelper("新候选｜desc｜stage")
        result = run_decompose_scan_cycle(self.paths, self.backlog, llm_helper=helper)
        self.assertEqual(result.stale_node_count, 1)
        self.assertEqual(result.candidate_count, 0)  # 被节奏治理拦下，未新增
        self.assertEqual(helper.calls, [])


class TestEnsureDecomposeScanJob(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)
        self.scheduler = _FakeCronScheduler()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_registers_job(self):
        newly_added = ensure_goal_tree_decompose_scan_job(
            self.paths, self.backlog, self.scheduler,
        )
        self.assertTrue(newly_added)
        self.assertIn(JOB_ID_DECOMPOSE_SCAN, {j.id for j in self.scheduler.list_jobs()})

    def test_skips_silently_without_llm_helper_provider(self):
        ensure_goal_tree_decompose_scan_job(self.paths, self.backlog, self.scheduler)
        ok = self.scheduler.run(JOB_ID_DECOMPOSE_SCAN)
        self.assertTrue(ok)  # 没有 provider，静默跳过，不算失败

    def test_skips_silently_when_provider_returns_none(self):
        ensure_goal_tree_decompose_scan_job(
            self.paths, self.backlog, self.scheduler,
            llm_helper_provider=lambda: None,
        )
        ok = self.scheduler.run(JOB_ID_DECOMPOSE_SCAN)
        self.assertTrue(ok)

    def test_runs_scan_cycle_when_helper_available(self):
        domain = _make_domain(self.backlog)
        domain_node = self.backlog.get(domain.id)
        domain_node.last_touched_at = time.time() - 20 * 86400
        self.backlog.save()
        helper = _FakeLLMHelper("候选｜desc｜stage")

        ensure_goal_tree_decompose_scan_job(
            self.paths, self.backlog, self.scheduler,
            llm_helper_provider=lambda: helper,
        )
        ok = self.scheduler.run(JOB_ID_DECOMPOSE_SCAN)
        self.assertTrue(ok)
        self.assertEqual(len(helper.calls), 1)
        domain_node = self.backlog.get(domain.id)
        self.assertEqual(len(domain_node.decompose_candidates), 1)


if __name__ == "__main__":
    unittest.main()
