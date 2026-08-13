"""tests/test_goal_cron_bridge.py

覆盖 next_doc/goal_cron_binding_plan.md 的核心行为：
  1. make_goal_recurring / stop_goal_recurrence：绑定/解绑写回 GoalNode 和 CronJob
  2. _fire_goal_cycle 的三条门禁：Goal 非 active 时跳过、上一轮未完成时跳过、
     正常情况下派生并启动新一轮子 Objective
  3. reap_finished_cycles：终态子节点计入 cycle_count + progress_notes，且不重复计数

ObjectiveExecutor 用一个轻量 Fake 代替（只实现 is_running/start 两个被
goal_cron_bridge 用到的方法），不拉起真实执行引擎——goal_cron_bridge 本身
是纯粹的"读写 GoalBacklog + 调用 ObjectiveExecutor 两个方法"的胶水层，
不需要验证 ObjectiveExecutor 内部行为。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.evolution import goal_cron_bridge as bridge
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


class FakeObjectiveExecutor:
    """duck-typed 替身，只实现 goal_cron_bridge 依赖的两个方法。"""

    def __init__(self):
        self._running_ids: set[str] = set()
        self.start_calls: list[str] = []
        self.start_should_fail = False

    def is_running(self, objective_id: str) -> bool:
        return objective_id in self._running_ids

    def start(self, objective):
        self.start_calls.append(objective.id)
        if self.start_should_fail:
            return None
        self._running_ids.add(objective.id)
        return f"exec_{objective.id}"

    def finish(self, objective_id: str) -> None:
        """测试辅助：模拟这一轮执行结束（不再 is_running）。"""
        self._running_ids.discard(objective_id)


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(Path(tmp))


def _set_autonomy_maintenance(paths) -> None:
    """goal_cron_bridge._fire_goal_cycle 在 autonomy_level="passive" 时会
    直接跳过（见 Track D 的档位边界说明），测试默认场景需要先写一份
    self_profile.json 把档位调到 maintenance。"""
    from mini_agent.perception.global_knowledge import load_self_profile, save_self_profile
    profile = load_self_profile(paths)
    if profile is None:
        from mini_agent.perception.global_knowledge import SelfProfile
        profile = SelfProfile()
    profile.operating_state.autonomy_level = "maintenance"
    save_self_profile(paths, profile)


class TestMakeAndStopRecurring(unittest.TestCase):
    def test_make_goal_recurring_binds_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="持续关注 AI 技术")
            cs = CronScheduler(paths, submit_fn=None)
            cs.load()

            job = bridge.make_goal_recurring(gb, cs, goal.id, "interval:86400")

            self.assertEqual(job.goal_id, goal.id)
            self.assertEqual(job.run_mode, "goal_cycle")

            updated = gb.get(goal.id)
            self.assertTrue(updated.recurring)
            self.assertEqual(updated.recurrence_cron_job_id, job.id)

    def test_make_goal_recurring_missing_goal_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            gb = GoalBacklog(paths)
            cs = CronScheduler(paths, submit_fn=None)
            cs.load()
            with self.assertRaises(ValueError):
                bridge.make_goal_recurring(gb, cs, "goal_does_not_exist", "interval:3600")

    def test_make_goal_recurring_reuses_existing_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="G")
            cs = CronScheduler(paths, submit_fn=None)
            cs.load()

            job1 = bridge.make_goal_recurring(gb, cs, goal.id, "interval:3600")
            job2 = bridge.make_goal_recurring(gb, cs, goal.id, "interval:7200")

            self.assertEqual(job1.id, job2.id)
            self.assertEqual(cs.get(job1.id).schedule, "interval:7200")
            # 不应该产生第二个绑定同一个 Goal 的 job
            goal_cycle_jobs = [j for j in cs.list_jobs(enabled_only=False) if j.goal_id == goal.id]
            self.assertEqual(len(goal_cycle_jobs), 1)

    def test_stop_goal_recurrence_disables_job_and_unsets_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="G")
            cs = CronScheduler(paths, submit_fn=None)
            cs.load()
            job = bridge.make_goal_recurring(gb, cs, goal.id, "interval:3600")

            ok = bridge.stop_goal_recurrence(gb, cs, goal.id)

            self.assertTrue(ok)
            self.assertFalse(gb.get(goal.id).recurring)
            self.assertIsNone(gb.get(goal.id).recurrence_cron_job_id)
            self.assertFalse(cs.get(job.id).enabled)


class TestFireGoalCycle(unittest.TestCase):
    def test_skips_when_goal_not_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _set_autonomy_maintenance(paths)
            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="G")
            gb.set_status(goal.id, "paused")
            cs = CronScheduler(paths, submit_fn=None)
            cs.load()
            job = bridge.make_goal_recurring(gb, cs, goal.id, "interval:3600")
            oe = FakeObjectiveExecutor()

            fired = bridge._fire_goal_cycle(cs.get(job.id), gb, oe)

            self.assertFalse(fired)
            self.assertEqual(oe.start_calls, [])

    def test_skips_when_passive_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            # 不调用 _set_autonomy_maintenance：默认/读取失败时是 passive
            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="G")
            cs = CronScheduler(paths, submit_fn=None)
            cs.load()
            job = bridge.make_goal_recurring(gb, cs, goal.id, "interval:3600")
            oe = FakeObjectiveExecutor()

            fired = bridge._fire_goal_cycle(cs.get(job.id), gb, oe)

            self.assertFalse(fired)
            self.assertEqual(oe.start_calls, [])

    def test_starts_first_cycle_and_skips_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _set_autonomy_maintenance(paths)
            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="持续关注 AI 技术")
            cs = CronScheduler(paths, submit_fn=None)
            cs.load()
            job = bridge.make_goal_recurring(
                gb, cs, goal.id, "interval:3600", task_template="搜索最新 AI 技术进展"
            )
            oe = FakeObjectiveExecutor()

            fired1 = bridge._fire_goal_cycle(cs.get(job.id), gb, oe)
            self.assertTrue(fired1)
            self.assertEqual(len(oe.start_calls), 1)

            children = gb.get(goal.id).children_ids
            self.assertEqual(len(children), 1)
            child = gb.get(children[0])
            # [goal_cron_output_directory_convention_plan.md §3] _fire_goal_cycle()
            # 现在会在描述末尾追加"本轮产出请写入：<cycle_dir>"，原始
            # task_template 仍完整保留在前面。
            self.assertTrue(child.description.startswith("搜索最新 AI 技术进展"))
            self.assertIn("本轮产出请写入：", child.description)
            self.assertIn("cycle_0001", child.description)
            self.assertIn("第 1 轮", child.title)

            # 第一轮仍在跑（is_running=True）：第二次触发应该被幂等检查拦住
            fired2 = bridge._fire_goal_cycle(cs.get(job.id), gb, oe)
            self.assertFalse(fired2)
            self.assertEqual(len(oe.start_calls), 1)

            # 第一轮结束后，下一次触发应该能正常开始第二轮
            oe.finish(child.id)
            gb.set_status(child.id, "completed")
            fired3 = bridge._fire_goal_cycle(cs.get(job.id), gb, oe)
            self.assertTrue(fired3)
            self.assertEqual(len(oe.start_calls), 2)
            self.assertEqual(len(gb.get(goal.id).children_ids), 2)

    def test_start_failure_marks_child_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _set_autonomy_maintenance(paths)
            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="G")
            cs = CronScheduler(paths, submit_fn=None)
            cs.load()
            job = bridge.make_goal_recurring(gb, cs, goal.id, "interval:3600")
            oe = FakeObjectiveExecutor()
            oe.start_should_fail = True

            fired = bridge._fire_goal_cycle(cs.get(job.id), gb, oe)

            self.assertFalse(fired)
            children = gb.get(goal.id).children_ids
            self.assertEqual(len(children), 1)
            self.assertEqual(gb.get(children[0]).status, "failed")


class TestReapFinishedCycles(unittest.TestCase):
    def test_reaps_terminal_children_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="G")
            gb.set_recurrence(goal.id, recurring=True, cron_job_id="user:fake")
            obj = gb.add_objective(title="第 1 轮", parent_id=goal.id, source="cron")
            gb.set_status(obj.id, "completed")
            gb.update_fields(obj.id, progress_notes="完成了第一轮")

            reaped = bridge.reap_finished_cycles(gb)

            self.assertEqual(reaped, 1)
            updated = gb.get(goal.id)
            self.assertEqual(updated.cycle_count, 1)
            self.assertIn("完成了第一轮", updated.progress_notes)
            self.assertIn(obj.id, updated.reaped_cycle_child_ids)

            # 再跑一次不应该重复计数
            reaped_again = bridge.reap_finished_cycles(gb)
            self.assertEqual(reaped_again, 0)
            self.assertEqual(gb.get(goal.id).cycle_count, 1)

    def test_ignores_non_recurring_goals(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="G")  # recurring=False（默认）
            obj = gb.add_objective(title="obj", parent_id=goal.id, source="user")
            gb.set_status(obj.id, "completed")

            reaped = bridge.reap_finished_cycles(gb)

            self.assertEqual(reaped, 0)
            self.assertEqual(gb.get(goal.id).cycle_count, 0)

    def test_ignores_active_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="G")
            gb.set_recurrence(goal.id, recurring=True, cron_job_id="user:fake")
            gb.add_objective(title="第 1 轮", parent_id=goal.id, source="cron")  # 仍是 active

            reaped = bridge.reap_finished_cycles(gb)

            self.assertEqual(reaped, 0)
            self.assertEqual(gb.get(goal.id).cycle_count, 0)


class TestSkipNextCycle(unittest.TestCase):
    """goal_cron_visibility_and_intervention_improvement_plan.md Track B"""

    def test_fire_skips_once_and_clears_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _set_autonomy_maintenance(paths)
            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="G")
            gb.set_recurrence(goal.id, recurring=True, cron_job_id="user:fake")
            gb.update_fields(goal.id, skip_next_cycle=True)
            oe = FakeObjectiveExecutor()
            cs = CronScheduler(paths, submit_fn=None)
            cs.load()
            job = cs.add_job(name="j", schedule="interval:60", task_template="t",
                              goal_id=goal.id, run_mode="goal_cycle")

            fired = bridge._fire_goal_cycle(job, gb, oe)

            self.assertFalse(fired)
            self.assertEqual(oe.start_calls, [])
            updated = gb.get(goal.id)
            self.assertFalse(updated.skip_next_cycle)
            self.assertIn("跳过", updated.progress_notes)

            # 跳过只生效一次，下一次触发应正常派生新一轮
            fired_again = bridge._fire_goal_cycle(job, gb, oe)
            self.assertTrue(fired_again)
            self.assertEqual(len(oe.start_calls), 1)


class TestLightweightNextCycle(unittest.TestCase):
    """goal_cron_task_optimization_holistic_plan.md 方向 C"""

    def test_fire_appends_lightweight_hint_and_clears_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _set_autonomy_maintenance(paths)
            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="G")
            gb.set_recurrence(goal.id, recurring=True, cron_job_id="user:fake")
            gb.update_fields(goal.id, next_cycle_lightweight=True)
            oe = FakeObjectiveExecutor()
            cs = CronScheduler(paths, submit_fn=None)
            cs.load()
            job = cs.add_job(name="j", schedule="interval:60", task_template="t",
                              goal_id=goal.id, run_mode="goal_cycle")

            fired = bridge._fire_goal_cycle(job, gb, oe)

            self.assertTrue(fired)
            self.assertEqual(len(oe.start_calls), 1)
            updated_goal = gb.get(goal.id)
            self.assertFalse(updated_goal.next_cycle_lightweight)
            self.assertIn("从简", updated_goal.progress_notes)
            child = gb.get(oe.start_calls[0])
            self.assertIn("降级执行", child.description)

            # 只影响这一次触发，下一轮不应再自动带上降级提示。
            oe.finish(oe.start_calls[0])
            gb.set_status(oe.start_calls[0], "completed")
            fired_again = bridge._fire_goal_cycle(job, gb, oe)
            self.assertTrue(fired_again)
            child2 = gb.get(oe.start_calls[1])
            self.assertNotIn("降级执行", child2.description)


class TestReapFailureNotification(unittest.TestCase):
    """goal_cron_visibility_and_intervention_improvement_plan.md Track C"""

    def test_dispatches_notification_on_failed_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="G")
            gb.set_recurrence(goal.id, recurring=True, cron_job_id="user:fake")
            obj = gb.add_objective(title="第 1 轮", parent_id=goal.id, source="cron")
            gb.set_status(obj.id, "failed")
            gb.update_fields(obj.id, progress_notes="出错了")

            calls = []

            class _FakeDispatcher:
                def __init__(self, _paths):
                    pass

                def dispatch(self, message, channels=None):
                    calls.append(message)
                    return {"kanban": True}

            import mini_agent.notification.dispatcher as dispatcher_mod
            original = dispatcher_mod.NotificationDispatcher
            dispatcher_mod.NotificationDispatcher = _FakeDispatcher
            try:
                reaped = bridge.reap_finished_cycles(gb)
            finally:
                dispatcher_mod.NotificationDispatcher = original

            self.assertEqual(reaped, 1)
            self.assertEqual(len(calls), 1)
            self.assertIn("失败", calls[0].title)

    def test_no_notification_on_completed_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="G")
            gb.set_recurrence(goal.id, recurring=True, cron_job_id="user:fake")
            obj = gb.add_objective(title="第 1 轮", parent_id=goal.id, source="cron")
            gb.set_status(obj.id, "completed")

            calls = []

            class _FakeDispatcher:
                def __init__(self, _paths):
                    pass

                def dispatch(self, message, channels=None):
                    calls.append(message)
                    return {}

            import mini_agent.notification.dispatcher as dispatcher_mod
            original = dispatcher_mod.NotificationDispatcher
            dispatcher_mod.NotificationDispatcher = _FakeDispatcher
            try:
                bridge.reap_finished_cycles(gb)
            finally:
                dispatcher_mod.NotificationDispatcher = original

            self.assertEqual(calls, [])


class TestArchiveFinishedCycleChildren(unittest.TestCase):
    """goal_cron_visibility_and_intervention_improvement_plan.md Track D"""

    def test_archives_older_children_beyond_keep_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="G")
            gb.set_recurrence(goal.id, recurring=True, cron_job_id="user:fake")
            for i in range(5):
                obj = gb.add_objective(title=f"第 {i+1} 轮", parent_id=goal.id, source="cron")
                gb.set_status(obj.id, "completed")
                bridge.reap_finished_cycles(gb)  # 逐轮 reap，会带着 archive 一起跑

            archived = gb.archive_finished_cycle_children(goal.id, keep_recent=2)

            self.assertEqual(archived, 3)
            updated = gb.get(goal.id)
            self.assertEqual(len(updated.children_ids), 2)
            self.assertEqual(len(updated.reaped_cycle_child_ids), 2)
            self.assertEqual(updated.cycle_count, 5)  # 归档不影响已完成轮数计数

            archive_path = paths.workdir_dir / "goal_cycle_archive.jsonl"
            self.assertTrue(archive_path.exists())
            lines = archive_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 3)

    def test_no_archive_below_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="G")
            gb.set_recurrence(goal.id, recurring=True, cron_job_id="user:fake")
            obj = gb.add_objective(title="第 1 轮", parent_id=goal.id, source="cron")
            gb.set_status(obj.id, "completed")
            bridge.reap_finished_cycles(gb)

            archived = gb.archive_finished_cycle_children(goal.id, keep_recent=20)

            self.assertEqual(archived, 0)
            self.assertEqual(len(gb.get(goal.id).children_ids), 1)

    def test_reap_skips_archive_while_in_explore_phase(self):
        """goal_cron_task_optimization_holistic_plan.md 方向 A —— explore/
        converge 阶段暂缓归档，避免早期尝试细节被过早清掉。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            from mini_agent.perception import execution_phase as ep

            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="G")
            gb.set_recurrence(goal.id, recurring=True, cron_job_id="user:fake")
            ep.set_mode(paths, goal.id, "explore")  # 用户手动锁定在 explore

            for i in range(25):
                obj = gb.add_objective(title=f"第 {i + 1} 轮", parent_id=goal.id, source="cron")
                gb.set_status(obj.id, "completed")
                bridge.reap_finished_cycles(gb)

            # 默认 keep_recent=20，25 轮理应触发归档，但阶段锁定在 explore，
            # reap 内部的归档调用应被跳过。
            updated = gb.get(goal.id)
            self.assertEqual(len(updated.children_ids), 25)
            archive_path = paths.workdir_dir / "goal_cycle_archive.jsonl"
            self.assertFalse(archive_path.exists())

    def test_reap_archives_once_phase_reaches_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            from mini_agent.perception import execution_phase as ep

            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="G")
            gb.set_recurrence(goal.id, recurring=True, cron_job_id="user:fake")
            ep.set_mode(paths, goal.id, "stable")

            for i in range(25):
                obj = gb.add_objective(title=f"第 {i + 1} 轮", parent_id=goal.id, source="cron")
                gb.set_status(obj.id, "completed")
                bridge.reap_finished_cycles(gb)

            updated = gb.get(goal.id)
            self.assertEqual(len(updated.children_ids), 20)
            archive_path = paths.workdir_dir / "goal_cycle_archive.jsonl"
            self.assertTrue(archive_path.exists())


class TestExecutionSpecIntegration(unittest.TestCase):
    """[goal_execution_spec_generation_plan.md §5] goal_cron_bridge 对已确认
    执行规范的消费：未确认不生效、确认后拼进 description、§5.1 软核对提示。
    """

    def test_unconfirmed_spec_does_not_affect_description(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _set_autonomy_maintenance(paths)
            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="周报 Goal")
            cs = CronScheduler(paths, submit_fn=None)
            cs.load()
            job = bridge.make_goal_recurring(gb, cs, goal.id, "interval:3600", task_template="写周报")
            oe = FakeObjectiveExecutor()

            from mini_agent.perception import goal_execution_spec as ges
            spec = ges.GoalExecutionSpec(goal_id=goal.id)
            spec.deliverables.append(ges.Deliverable(name="report.md", naming_pattern="report.md"))
            ges.save_spec(paths, goal.id, spec)  # 保存但不确认

            fired = bridge._fire_goal_cycle(cs.get(job.id), gb, oe)
            self.assertTrue(fired)
            child = gb.get(gb.get(goal.id).children_ids[0])
            self.assertNotIn("执行规范", child.description)

    def test_confirmed_spec_is_injected_into_description(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _set_autonomy_maintenance(paths)
            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="周报 Goal")
            cs = CronScheduler(paths, submit_fn=None)
            cs.load()
            job = bridge.make_goal_recurring(gb, cs, goal.id, "interval:3600", task_template="写周报")
            oe = FakeObjectiveExecutor()

            from mini_agent.perception import goal_execution_spec as ges
            spec = ges.GoalExecutionSpec(goal_id=goal.id)
            spec.deliverables.append(ges.Deliverable(name="report.md", description="周报文件", naming_pattern="report.md"))
            spec.special_constraints.append("不要包含真实姓名")
            ges.GoalExecutionSpecBuilder.confirm(spec)
            ges.save_spec(paths, goal.id, spec)
            gb.update_fields(goal.id, execution_spec_confirmed=True)

            fired = bridge._fire_goal_cycle(cs.get(job.id), gb, oe)
            self.assertTrue(fired)
            child = gb.get(gb.get(goal.id).children_ids[0])
            self.assertIn("执行规范", child.description)
            self.assertIn("report.md", child.description)
            self.assertIn("不要包含真实姓名", child.description)

    def test_soft_check_alerts_after_consecutive_misses(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _set_autonomy_maintenance(paths)
            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="周报 Goal")
            cs = CronScheduler(paths, submit_fn=None)
            cs.load()
            job = bridge.make_goal_recurring(gb, cs, goal.id, "interval:3600", task_template="写周报")
            oe = FakeObjectiveExecutor()

            from mini_agent.perception import goal_execution_spec as ges
            from mini_agent.evolution import output_workspace

            spec = ges.GoalExecutionSpec(goal_id=goal.id)
            spec.deliverables.append(ges.Deliverable(name="report.md", naming_pattern="report.md"))
            ges.GoalExecutionSpecBuilder.confirm(spec)
            ges.save_spec(paths, goal.id, spec)
            gb.update_fields(goal.id, execution_spec_confirmed=True)

            base_dir = output_workspace.goal_output_base_dir(paths, goal.id)

            # 跑 4 轮：每轮触发时会核对"上一轮"的 manifest，所以第 1 轮触发时
            # 还没有可核对的历史，从第 2 轮起才开始计数未命中，需要 4 轮
            # 触发才能让"上一轮未命中"累计满 3 次，触发默认
            # soft_check_alert_after_cycles=3 的提示。
            for i in range(4):
                fired = bridge._fire_goal_cycle(cs.get(job.id), gb, oe)
                self.assertTrue(fired)
                child_id = gb.get(goal.id).children_ids[-1]
                cycle_dir = output_workspace.allocate_cycle_dir(paths, goal.id, i + 1)
                output_workspace.write_manifest(base_dir, cycle_dir, artifacts=[])
                oe.finish(child_id)
                gb.set_status(child_id, "completed")

            updated_goal = gb.get(goal.id)
            self.assertIn("建议复查执行规范", updated_goal.progress_notes)


class TestPhaseHealthNotification(unittest.TestCase):
    """goal_cron_task_optimization_holistic_plan.md 方向 B"""

    def test_dispatches_notification_when_stuck_in_explore(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            from mini_agent.perception import execution_phase as ep

            gb = GoalBacklog(paths)
            goal = gb.add_goal(title="G")

            # 预置一个已经"卡在 explore 很久"的阶段状态。
            state = ep.ExecutionPhaseState(goal_id=goal.id, mode="auto", locked=False)
            state.cycles_in_mode = ep.DEFAULT_STUCK_EXPLORE_CYCLES
            ep.save_phase(paths, state)

            calls = []

            class _FakeDispatcher:
                def __init__(self, _paths):
                    pass

                def dispatch(self, message, channels=None):
                    calls.append(message)
                    return {"kanban": True}

            import mini_agent.notification.dispatcher as dispatcher_mod
            original = dispatcher_mod.NotificationDispatcher
            dispatcher_mod.NotificationDispatcher = _FakeDispatcher
            try:
                bridge._append_execution_phase_context(paths, goal, 1, "base description")
            finally:
                dispatcher_mod.NotificationDispatcher = original

            self.assertEqual(len(calls), 1)
            self.assertIn("关注", calls[0].title)

            # 冷却期内再触发一次不应重复通知。
            calls2 = []

            class _FakeDispatcher2:
                def __init__(self, _paths):
                    pass

                def dispatch(self, message, channels=None):
                    calls2.append(message)
                    return {"kanban": True}

            dispatcher_mod.NotificationDispatcher = _FakeDispatcher2
            try:
                bridge._append_execution_phase_context(paths, goal, 2, "base description")
            finally:
                dispatcher_mod.NotificationDispatcher = original

            self.assertEqual(len(calls2), 0)


if __name__ == "__main__":
    unittest.main()
