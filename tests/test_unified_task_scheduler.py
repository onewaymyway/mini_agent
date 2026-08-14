"""
tests/test_unified_task_scheduler.py

覆盖 next_doc/goal_cron_unified_scheduler_improvement_plan.md P5 第 1-2 步
新增的 `mini_agent.evolution.unified_task_scheduler` 模块：

- `SchedulableTask`/`TaskChannel` 接口定义本身不产生行为，此处只做基本
  构造/字段校验。
- `ObjectiveChannelAdapter`/`CronChannelAdapter`/`GoalCycleChannelAdapter`
  三个只读适配器：`poll_due()` 正确复用既有通道数据；`execute()` 中
  `CronChannelAdapter`/`GoalCycleChannelAdapter` 已委托 `trigger_job_now()`
  真正派发（覆盖测试见 test_unified_dispatch_p5_step4.py），
  `ObjectiveChannelAdapter.execute()` 仍按设计 raise NotImplementedError。
- `UnifiedTaskScheduler.poll_all()`/`suggest_order()`：聚合、降级、排序
  行为符合预期，且确认调用全程不修改任何底层通道状态（只读）。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_unified_task_scheduler.py -q
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.evolution.unified_task_scheduler import (
    CronChannelAdapter,
    GoalCycleChannelAdapter,
    ObjectiveChannelAdapter,
    allocate_weighted_slots,
    SchedulableTask,
    UnifiedTaskScheduler,
    build_default_scheduler,
)
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


class TestSchedulableTaskBasics(unittest.TestCase):
    def test_default_fields(self):
        t = SchedulableTask(source="goal", task_id="obj_1")
        self.assertEqual(t.title, "")
        self.assertEqual(t.priority, 0.0)
        self.assertIsNone(t.due_at)
        self.assertEqual(t.resource_estimate, 1.0)
        self.assertEqual(t.extra, {})


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.paths = AgentPaths(self.root)
        self.paths.workdir_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmpdir.cleanup()


class TestObjectiveChannelAdapter(_Base):
    def test_none_backlog_returns_empty(self):
        adapter = ObjectiveChannelAdapter(None)
        self.assertEqual(adapter.poll_due(), [])

    def test_poll_due_reflects_active_objectives(self):
        backlog = GoalBacklog(self.paths)
        goal = backlog.add_goal(title="parent goal", priority=5)
        obj = backlog.add_objective(title="child objective", parent_id=goal.id, priority=5)
        backlog.save()

        adapter = ObjectiveChannelAdapter(backlog)
        tasks = adapter.poll_due()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].source, "goal")
        self.assertEqual(tasks[0].task_id, obj.id)
        self.assertEqual(tasks[0].title, "child objective")
        self.assertIsNone(tasks[0].due_at)

    def test_execute_without_autonomous_loop_returns_false(self):
        """[Track 1] autonomous_loop 未注入时 execute() 应安全返回 False，
        不再抛 NotImplementedError（Track 1 之前的旧行为）。"""
        adapter = ObjectiveChannelAdapter(None)
        result = adapter.execute(SchedulableTask(source="goal", task_id="x"))
        self.assertFalse(result)

    def test_execute_delegates_to_autonomous_loop_trigger(self):
        """[Track 1] execute() 应委托给 autonomous_loop.trigger_objective_
        now(task_id)，并把其返回值原样透传。"""
        calls = []

        class _FakeLoop:
            def trigger_objective_now(self, objective_id):
                calls.append(objective_id)
                return True

        adapter = ObjectiveChannelAdapter(None, autonomous_loop=_FakeLoop())
        result = adapter.execute(SchedulableTask(source="goal", task_id="obj-42"))
        self.assertTrue(result)
        self.assertEqual(calls, ["obj-42"])

    def test_execute_swallows_autonomous_loop_exceptions(self):
        """[Track 1] autonomous_loop.trigger_objective_now() 抛异常时，
        execute() 应捕获并返回 False，不向上传播（与 CronChannelAdapter.
        execute() 的既有异常处理风格一致）。"""

        class _BrokenLoop:
            def trigger_objective_now(self, objective_id):
                raise RuntimeError("boom")

        adapter = ObjectiveChannelAdapter(None, autonomous_loop=_BrokenLoop())
        result = adapter.execute(SchedulableTask(source="goal", task_id="x"))
        self.assertFalse(result)

    def test_poll_due_is_read_only(self):
        """调用 poll_due() 不应该修改任何 GoalNode 字段（比如
        last_scheduled_at），确认这是纯只读观察，不产生调度副作用。"""
        backlog = GoalBacklog(self.paths)
        goal = backlog.add_goal(title="parent", priority=1)
        backlog.add_objective(title="child", parent_id=goal.id, priority=1)
        backlog.save()

        before = backlog.get(goal.id).last_scheduled_at
        adapter = ObjectiveChannelAdapter(backlog)
        adapter.poll_due()
        adapter.poll_due()
        after = backlog.get(goal.id).last_scheduled_at
        self.assertEqual(before, after)

    def test_resource_estimate_defaults_to_1_without_phase_history(self):
        """没有任何 execution phase 历史（Goal 刚创建，或阶段机制从未
        被触发过）时，`last_known_effective_mode()` 保守返回 "explore"，
        对应倍率 1.3——不是恒为 1.0 的旧行为，但仍是"读得到状态但没有
        历史"这条路径下的确定性结果。"""
        backlog = GoalBacklog(self.paths)
        goal = backlog.add_goal(title="parent", priority=1)
        obj = backlog.add_objective(title="child", parent_id=goal.id, priority=1)
        backlog.save()

        adapter = ObjectiveChannelAdapter(backlog, paths=self.paths)
        tasks = adapter.poll_due()
        self.assertEqual(len(tasks), 1)
        self.assertAlmostEqual(tasks[0].resource_estimate, 1.3)
        self.assertEqual(tasks[0].extra.get("phase_mode"), "explore")

    def test_resource_estimate_reflects_stable_phase(self):
        """能读到该 Goal 的 ExecutionPhaseState 且已知有效阶段为
        stable 时，resource_estimate 应换算成 1.0（stable 的默认倍率），
        且 extra["phase_mode"] 同步反映阶段名。"""
        from mini_agent.perception import execution_phase as ep

        backlog = GoalBacklog(self.paths)
        goal = backlog.add_goal(title="parent", priority=1)
        backlog.add_objective(title="child", parent_id=goal.id, priority=1)
        backlog.save()

        ep.set_mode(self.paths, goal.id, "stable")

        adapter = ObjectiveChannelAdapter(backlog, paths=self.paths)
        tasks = adapter.poll_due()
        self.assertEqual(len(tasks), 1)
        self.assertAlmostEqual(tasks[0].resource_estimate, 1.0)
        self.assertEqual(tasks[0].extra.get("phase_mode"), "stable")

    def test_resource_estimate_falls_back_to_1_without_paths(self):
        """未注入 paths、且 goal_backlog 本身也拿不到 _paths 时，阶段感知
        的资源估算整体降级为 1.0，与引入本机制之前的行为一致。"""
        backlog = GoalBacklog(self.paths)
        goal = backlog.add_goal(title="parent", priority=1)
        backlog.add_objective(title="child", parent_id=goal.id, priority=1)
        backlog.save()

        adapter = ObjectiveChannelAdapter(backlog, paths=None)
        adapter._paths = None  # 显式模拟拿不到 paths 的极端情况
        tasks = adapter.poll_due()
        self.assertEqual(len(tasks), 1)
        self.assertAlmostEqual(tasks[0].resource_estimate, 1.0)
        self.assertEqual(tasks[0].extra.get("phase_mode"), "")


class TestCronChannelAdapters(_Base):
    def _make_scheduler(self) -> CronScheduler:
        return CronScheduler(self.paths, submit_fn=lambda *a, **k: True)

    def test_normal_cron_only_returns_due_message_jobs(self):
        cs = self._make_scheduler()
        due_job = cs.add_job(name="due_job", schedule="interval:3600", task_template="do x")
        due_job.next_run_at = time.time() - 10
        not_due_job = cs.add_job(name="not_due_job", schedule="interval:3600", task_template="do y")
        not_due_job.next_run_at = time.time() + 3600
        cycle_job = cs.add_job(name="cycle_job", schedule="interval:3600",
                                task_template="advance", run_mode="goal_cycle", goal_id="g1")
        cycle_job.next_run_at = time.time() - 10
        cs.save()

        adapter = CronChannelAdapter(cs)
        tasks = adapter.poll_due()
        ids = {t.task_id for t in tasks}
        self.assertIn(due_job.id, ids)
        self.assertNotIn(not_due_job.id, ids)
        self.assertNotIn(cycle_job.id, ids)  # goal_cycle 不出现在普通 cron 通道
        self.assertEqual(tasks[0].source, "cron")
        self.assertEqual(tasks[0].due_at, due_job.next_run_at)

    def test_disabled_job_excluded(self):
        cs = self._make_scheduler()
        job = cs.add_job(name="disabled_job", schedule="interval:3600", task_template="x")
        job.next_run_at = time.time() - 10
        job.enabled = False
        cs.save()

        adapter = CronChannelAdapter(cs)
        self.assertEqual(adapter.poll_due(), [])

    def test_goal_cycle_adapter_only_returns_goal_cycle_jobs(self):
        cs = self._make_scheduler()
        normal_job = cs.add_job(name="normal", schedule="interval:3600", task_template="x")
        normal_job.next_run_at = time.time() - 10
        cycle_job = cs.add_job(name="cycle", schedule="interval:3600",
                                task_template="advance", run_mode="goal_cycle", goal_id="g1")
        cycle_job.next_run_at = time.time() - 10
        cs.save()

        adapter = GoalCycleChannelAdapter(cs)
        tasks = adapter.poll_due()
        ids = {t.task_id for t in tasks}
        self.assertIn(cycle_job.id, ids)
        self.assertNotIn(normal_job.id, ids)
        self.assertEqual(tasks[0].source, "goal_cycle")

    def test_none_scheduler_returns_empty_for_both_adapters(self):
        self.assertEqual(CronChannelAdapter(None).poll_due(), [])
        self.assertEqual(GoalCycleChannelAdapter(None).poll_due(), [])

    def test_execute_with_none_scheduler_returns_false_not_raises(self):
        # [P5 第 4 步] execute() 已实现真正委托派发，None scheduler 时
        # 静默返回 False，不再 raise NotImplementedError（那是 P5 第 1-2
        # 步的旧行为，见 test_unified_dispatch_p5_step4.py 的完整覆盖）。
        self.assertFalse(CronChannelAdapter(None).execute(SchedulableTask(source="cron", task_id="x")))
        self.assertFalse(GoalCycleChannelAdapter(None).execute(SchedulableTask(source="goal_cycle", task_id="x")))


class TestUnifiedTaskScheduler(_Base):
    def test_poll_all_groups_by_channel_name(self):
        scheduler = UnifiedTaskScheduler()

        class _Fake:
            def __init__(self, tasks):
                self._tasks = tasks

            def poll_due(self):
                return self._tasks

            def execute(self, task):
                raise NotImplementedError

        scheduler.register_channel("a", _Fake([SchedulableTask(source="a", task_id="1")]))
        scheduler.register_channel("b", _Fake([]))
        result = scheduler.poll_all()
        self.assertEqual(len(result["a"]), 1)
        self.assertEqual(result["b"], [])

    def test_poll_all_channel_exception_degrades_to_empty(self):
        scheduler = UnifiedTaskScheduler()

        class _Broken:
            def poll_due(self):
                raise RuntimeError("boom")

            def execute(self, task):
                raise NotImplementedError

        class _Ok:
            def poll_due(self):
                return [SchedulableTask(source="ok", task_id="1")]

            def execute(self, task):
                raise NotImplementedError

        scheduler.register_channel("broken", _Broken())
        scheduler.register_channel("ok", _Ok())
        result = scheduler.poll_all()
        self.assertEqual(result["broken"], [])
        self.assertEqual(len(result["ok"]), 1)

    def test_suggest_order_sorts_by_weighted_priority_then_due_at(self):
        scheduler = UnifiedTaskScheduler()

        class _Fake:
            def __init__(self, tasks):
                self._tasks = tasks

            def poll_due(self):
                return self._tasks

            def execute(self, task):
                raise NotImplementedError

        low = SchedulableTask(source="a", task_id="low", priority=1.0)
        high = SchedulableTask(source="a", task_id="high", priority=10.0)
        due_soon = SchedulableTask(source="b", task_id="due_soon", priority=1.0, due_at=100.0)
        due_late = SchedulableTask(source="b", task_id="due_late", priority=1.0, due_at=200.0)

        scheduler.register_channel("a", _Fake([low, high]))
        scheduler.register_channel("b", _Fake([due_late, due_soon]))

        order = scheduler.suggest_order()
        # 最高优先级排最前
        self.assertEqual(order[0].task_id, "high")
        # 同优先级按 due_at 更早的排前面
        due_only = [t for t in order if t.source == "b"]
        self.assertEqual([t.task_id for t in due_only], ["due_soon", "due_late"])

    def test_suggest_order_respects_channel_weights(self):
        scheduler = UnifiedTaskScheduler()

        class _Fake:
            def __init__(self, tasks):
                self._tasks = tasks

            def poll_due(self):
                return self._tasks

            def execute(self, task):
                raise NotImplementedError

        goal_task = SchedulableTask(source="goal", task_id="g", priority=5.0)
        cron_task = SchedulableTask(source="cron", task_id="c", priority=5.0)
        scheduler.register_channel("goal", _Fake([goal_task]))
        scheduler.register_channel("cron", _Fake([cron_task]))

        # 默认权重相同，谁在前取决于合并顺序里的 tie-break（本用例不强依赖）
        order_equal = scheduler.suggest_order()
        self.assertEqual({t.task_id for t in order_equal}, {"g", "c"})

        # cron 权重更高时应该排到 goal 前面
        order_weighted = scheduler.suggest_order(channel_weights={"cron": 2.0, "goal": 1.0})
        self.assertEqual(order_weighted[0].task_id, "c")

    def test_build_default_scheduler_registers_three_channels(self):
        scheduler = build_default_scheduler(goal_backlog=None, cron_scheduler=None)
        result = scheduler.poll_all()
        self.assertEqual(set(result.keys()), {"goal", "cron", "goal_cycle"})
        # 依赖全为 None 时三条通道都降级为空列表，不报错
        for tasks in result.values():
            self.assertEqual(tasks, [])


class TestAllocateWeightedSlots(unittest.TestCase):
    """[P5 第 3 步] `allocate_weighted_slots()` 纯函数的分配行为。"""

    def test_equal_weights_default_matches_pre_p5_behavior(self):
        # degraded_total_slots=2、权重 1:1、cron 保底 1 —— 应该正好复现
        # 改造前 goal=1/cron=1 的默认行为（SchedulerConfig 默认值就是照这个
        # 场景选的）。
        allocation = allocate_weighted_slots(
            2, {"goal": 1.0, "cron": 1.0}, reserved_min={"cron": 1},
        )
        self.assertEqual(allocation, {"goal": 1, "cron": 1})

    def test_reserved_min_guarantees_cron_floor_even_with_low_weight(self):
        allocation = allocate_weighted_slots(
            4, {"goal": 9.0, "cron": 1.0}, reserved_min={"cron": 1},
        )
        self.assertGreaterEqual(allocation["cron"], 1)
        self.assertEqual(sum(allocation.values()), 4)

    def test_reserved_min_exceeding_total_slots_degrades_gracefully(self):
        allocation = allocate_weighted_slots(
            1, {"goal": 1.0, "cron": 1.0}, reserved_min={"cron": 1, "goal": 1},
        )
        # 总槽位不够两边都保底时，按声明顺序依次满足，总和不超过 total_slots
        self.assertEqual(sum(allocation.values()), 1)

    def test_zero_or_negative_total_slots_allocates_nothing(self):
        allocation = allocate_weighted_slots(0, {"goal": 1.0, "cron": 1.0})
        self.assertEqual(allocation, {"goal": 0, "cron": 0})
        allocation_neg = allocate_weighted_slots(-3, {"goal": 1.0, "cron": 1.0})
        self.assertEqual(allocation_neg, {"goal": 0, "cron": 0})

    def test_weighted_split_sums_to_total_slots_largest_remainder(self):
        allocation = allocate_weighted_slots(5, {"goal": 2.0, "cron": 1.0})
        self.assertEqual(sum(allocation.values()), 5)
        # goal 权重是 cron 两倍，理应分到更多（或至少不少于）槽位
        self.assertGreaterEqual(allocation["goal"], allocation["cron"])

    def test_missing_weight_defaults_to_zero_but_reserved_min_still_applies(self):
        allocation = allocate_weighted_slots(
            3, {"goal": 1.0}, reserved_min={"cron": 1},
        )
        self.assertEqual(allocation["cron"], 1)
        self.assertEqual(sum(allocation.values()), 3)

    def test_empty_weights_and_reserved_min_allocates_nothing(self):
        self.assertEqual(allocate_weighted_slots(5, {}), {})


if __name__ == "__main__":
    unittest.main()
