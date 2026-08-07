"""
tests/test_goal_cron_feedback_and_output_policy.py

覆盖 next_doc/goal_cron_feedback_and_output_policy_plan.md 的核心行为：
  - Track A/B: GoalNode/CronJob user_feedback 追加 + description/task_template 合并
  - 双向联动（Goal <-> CronJob）与防重复循环
  - Track F: add_objectives_for_goal() 描述继承 + effective_context() 兜底
  - Track H: output_path_policy 幂等创建/读取
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.perception.goal_backlog import GoalBacklog, compose_context
from mini_agent.evolution.cron_scheduler import CronScheduler, CronJob
from mini_agent.evolution.output_path_policy import ensure_policy_file, load_policy, DEFAULT_POLICY
from mini_agent.storage.paths import AgentPaths


class TestGoalUserFeedback(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_add_user_feedback_appends_history_and_description(self):
        backlog = GoalBacklog(self.paths)
        goal = backlog.add_goal(title="目标A", description="原始描述")
        ok = backlog.add_user_feedback(goal.id, "记得加上日志")
        self.assertTrue(ok)
        updated = backlog.get(goal.id)
        self.assertEqual(len(updated.user_feedback), 1)
        self.assertEqual(updated.user_feedback[0]["text"], "记得加上日志")
        self.assertIn("原始描述", updated.description)
        self.assertIn("记得加上日志", updated.description)

    def test_add_user_feedback_missing_node_returns_false(self):
        backlog = GoalBacklog(self.paths)
        self.assertFalse(backlog.add_user_feedback("goal_nonexistent", "text"))

    def test_add_user_feedback_empty_text_returns_false(self):
        backlog = GoalBacklog(self.paths)
        goal = backlog.add_goal(title="目标B")
        self.assertFalse(backlog.add_user_feedback(goal.id, "   "))


class TestGoalCronFeedbackSync(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_goal_feedback_syncs_to_bound_cron_job(self):
        backlog = GoalBacklog(self.paths)
        goal = backlog.add_goal(title="周期目标")
        scheduler = CronScheduler(self.paths)
        job = CronJob(
            id="user:test1", name="job1", schedule="interval:3600",
            task_template="原任务", run_mode="goal_cycle", goal_id=goal.id,
        )
        scheduler._jobs[job.id] = job
        scheduler.save()
        backlog.update_fields(goal.id, recurring=True, recurrence_cron_job_id=job.id)

        ok = backlog.add_user_feedback(goal.id, "同步测试意见")
        self.assertTrue(ok)

        scheduler2 = CronScheduler(self.paths)
        scheduler2.load()
        synced_job = scheduler2.get(job.id)
        self.assertIsNotNone(synced_job)
        self.assertIn("同步测试意见", synced_job.task_template)
        self.assertEqual(len(synced_job.user_feedback), 1)

    def test_cron_feedback_syncs_to_bound_goal_and_no_infinite_loop(self):
        backlog = GoalBacklog(self.paths)
        goal = backlog.add_goal(title="周期目标2")
        backlog.update_fields(goal.id, recurring=True, recurrence_cron_job_id="user:test2")

        scheduler = CronScheduler(self.paths)
        job = CronJob(
            id="user:test2", name="job2", schedule="interval:3600",
            task_template="原任务2", run_mode="goal_cycle", goal_id=goal.id,
        )
        scheduler._jobs[job.id] = job
        scheduler.save()

        ok = scheduler.add_user_feedback(job.id, "来自cron的意见")
        self.assertTrue(ok)
        # 未抛异常/未死循环即视为通过；再确认双向都写入了恰好一条记录
        job_after = scheduler.get(job.id)
        self.assertEqual(len(job_after.user_feedback), 1)

        backlog2 = GoalBacklog(self.paths)
        backlog2.load()
        goal_after = backlog2.get(goal.id)
        self.assertEqual(len(goal_after.user_feedback), 1)
        self.assertIn("来自cron的意见", goal_after.description)


class TestDescriptionInheritance(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_add_objectives_for_goal_inherits_description(self):
        # [goal_cron_output_directory_convention_plan.md §5 开放问题 3]
        # 一次性 Goal 的子 Objective 现在会在父级 description 之后追加
        # "本轮产出请写入：<目录>"，不再是与父级 description 精确相等——
        # 断言改为"以父级 description 开头 + 包含产出目录提示"，这正是
        # 本方案的预期行为变化（与 test_goal_cron_bridge.py 里 recurring
        # Goal 一侧的同类断言调整保持一致）。
        backlog = GoalBacklog(self.paths)
        goal = backlog.add_goal(title="父目标", description="父级约束条件")
        created = backlog.add_objectives_for_goal(goal.id, ["子任务1", "子任务2"])
        self.assertEqual(len(created), 2)
        for node in created:
            self.assertTrue(node.description.startswith("父级约束条件"))
            self.assertIn("本轮产出请写入：", node.description)

    def test_compose_context_joins_both_when_present(self):
        self.assertEqual(
            compose_context("父级说明", "本轮任务"),
            "父级说明\n\n本轮任务",
        )
        self.assertEqual(compose_context("", "本轮任务"), "本轮任务")
        self.assertEqual(compose_context("父级说明", ""), "父级说明")

    def test_effective_context_walks_parent_chain(self):
        backlog = GoalBacklog(self.paths)
        goal = backlog.add_goal(title="父目标", description="根说明")
        obj = backlog.add_objective(title="子目标", parent_id=goal.id, description="子说明")
        ctx = backlog.effective_context(obj.id)
        self.assertIn("根说明", ctx)
        self.assertIn("子说明", ctx)
        self.assertLess(ctx.index("根说明"), ctx.index("子说明"))


class TestOutputPathPolicy(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_ensure_policy_file_idempotent(self):
        path = ensure_policy_file(self.paths)
        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), DEFAULT_POLICY)
        # 修改后再次 ensure 不覆盖
        path.write_text("自定义规则", encoding="utf-8")
        ensure_policy_file(self.paths)
        self.assertEqual(path.read_text(encoding="utf-8"), "自定义规则")

    def test_load_policy_reflects_user_edits(self):
        ensure_policy_file(self.paths)
        path = ensure_policy_file(self.paths)
        path.write_text("用户自定义规范", encoding="utf-8")
        self.assertEqual(load_policy(self.paths), "用户自定义规范")


if __name__ == "__main__":
    unittest.main()
