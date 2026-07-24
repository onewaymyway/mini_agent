"""
tests/test_objective_executor_kanban_tracks.py

覆盖 next_doc/kanban_and_autonomy_improvement_plan.md 中已落地的部分：
  - Track C：并行 Objective 路径互斥检测（退化版：保守串行化）
  - Track B：GoalNode 与 ObjectiveExecution 状态单向同步
  - Track D：看板可操作能力（cancel / retry_current_step / inject_guidance）
  - Track F：Step 失败重试策略升级（重试 prompt 携带失败原因）
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from mini_agent.evolution.objective_executor import ObjectiveExecutor
from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode
from mini_agent.storage.paths import AgentPaths


def _make_objective(backlog: GoalBacklog, title: str) -> GoalNode:
    goal = backlog.add_goal(title=f"{title}-goal", description="", source="user", priority=50)
    objs = backlog.add_objectives_for_goal(goal.id, [title])
    return objs[0]


class _FakeSubmitter:
    """记录每次提交的 message，返回递增的 turn_id，不做真实调度。"""

    def __init__(self):
        self.calls: list[dict] = []
        self._n = 0

    def __call__(self, message: str, initiator: str, meta: dict):
        self._n += 1
        turn_id = f"turn_{self._n}"
        self.calls.append({"turn_id": turn_id, "message": message, "initiator": initiator, "meta": meta})
        return turn_id


class TestPathMutex(unittest.TestCase):
    """Track C：两个都会碰同一路径的 Objective，第二个应该被 blocked 而不是并行执行；
    互不相关路径的 Objective 依然能正常并行。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)
        self.submitter = _FakeSubmitter()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _executor(self, declare_paths_fn):
        return ObjectiveExecutor(
            paths=self.paths,
            submit_fn=self.submitter,
            llm_decompose_fn=lambda obj: [f"{obj.title} - 单步"],
            declare_paths_fn=declare_paths_fn,
            goal_backlog=self.backlog,
        )

    def test_conflicting_paths_block_second_objective(self):
        obj_a = _make_objective(self.backlog, "写 README A")
        obj_b = _make_objective(self.backlog, "写 README B")

        def declare(desc: str):
            return ["README.md"]  # 两个 Objective 的 step 都声明碰同一个文件

        executor = self._executor(declare)
        exec_a = executor.start(obj_a)
        exec_b = executor.start(obj_b)

        self.assertIsNotNone(exec_a)
        # b 应该没能真正提交（返回 None），且它的当前 step 处于 blocked
        self.assertIsNone(exec_b)
        # start() 内部创建的 execution 仍然保留在 _executions 里（标记为 failed
        # 只在“非路径冲突”情形下发生），这里改为直接看 executor 内部状态
        blocked_execs = [
            ex for ex in executor._executions.values()
            if ex.objective_id == obj_b.id
        ]
        self.assertEqual(len(blocked_execs), 1)
        self.assertEqual(blocked_execs[0].current_step.status, "blocked")

        # 只有 a 的 step 真正提交到了 submitter
        self.assertEqual(len(self.submitter.calls), 1)

        # a 完成后释放路径，retry_blocked_steps() 应该能把 b 重新提交上去
        executor.on_turn_done(self.submitter.calls[0]["turn_id"], "A 完成")
        retried = executor.retry_blocked_steps()
        self.assertIn(blocked_execs[0].execution_id, retried)
        self.assertEqual(len(self.submitter.calls), 2)

    def test_non_conflicting_paths_run_in_parallel(self):
        obj_a = _make_objective(self.backlog, "写 a.md")
        obj_b = _make_objective(self.backlog, "写 b.md")

        def declare(desc: str):
            return ["a.md"] if "a.md" in desc else ["b.md"]

        executor = self._executor(declare)
        exec_a = executor.start(obj_a)
        exec_b = executor.start(obj_b)

        self.assertIsNotNone(exec_a)
        self.assertIsNotNone(exec_b)
        self.assertEqual(len(self.submitter.calls), 2)

    def test_unknown_paths_degrade_to_serial(self):
        """声明不出路径时退化为哨兵路径，两个 Objective 也不能并行。"""
        obj_a = _make_objective(self.backlog, "做点什么 A")
        obj_b = _make_objective(self.backlog, "做点什么 B")

        executor = self._executor(declare_paths_fn=lambda desc: [])
        exec_a = executor.start(obj_a)
        exec_b = executor.start(obj_b)

        self.assertIsNotNone(exec_a)
        self.assertIsNone(exec_b)
        self.assertEqual(len(self.submitter.calls), 1)


class TestGoalStatusSync(unittest.TestCase):
    """Track B：Objective 完成/失败/取消后单向同步回写 GoalNode.status。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)
        self.submitter = _FakeSubmitter()
        self.executor = ObjectiveExecutor(
            paths=self.paths,
            submit_fn=self.submitter,
            llm_decompose_fn=lambda obj: [f"{obj.title} - 单步"],
            declare_paths_fn=lambda desc: [f"path-for-{desc}"],
            goal_backlog=self.backlog,
        )

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_completed_syncs_goal_status(self):
        obj = _make_objective(self.backlog, "任务A")
        exec_id = self.executor.start(obj)
        self.assertIsNotNone(exec_id)
        turn_id = self.submitter.calls[0]["turn_id"]
        self.executor.on_turn_done(turn_id, "完成了")

        refreshed = self.backlog.get(obj.id)
        self.assertEqual(refreshed.status, "completed")

    def test_failed_syncs_goal_status(self):
        obj = _make_objective(self.backlog, "任务B")
        exec_id = self.executor.start(obj)
        turn_id = self.submitter.calls[0]["turn_id"]
        # 连续失败超过重试次数
        for _ in range(10):
            if self.submitter.calls:
                self.executor.on_turn_failed(self.submitter.calls[-1]["turn_id"], "出错了")
            else:
                break

        refreshed = self.backlog.get(obj.id)
        self.assertEqual(refreshed.status, "failed")

    def test_cancel_syncs_goal_status_and_releases_slot(self):
        obj = _make_objective(self.backlog, "任务C")
        exec_id = self.executor.start(obj)
        self.assertEqual(self.executor.running_count(), 1)

        ok = self.executor.cancel(exec_id)
        self.assertTrue(ok)
        self.assertEqual(self.executor.running_count(), 0)

        refreshed = self.backlog.get(obj.id)
        self.assertEqual(refreshed.status, "cancelled")

        # 已终止的 execution 不能重复 cancel
        self.assertFalse(self.executor.cancel(exec_id))


class TestKanbanActionableApis(unittest.TestCase):
    """Track D：retry_current_step / inject_guidance。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)
        self.submitter = _FakeSubmitter()
        self.executor = ObjectiveExecutor(
            paths=self.paths,
            submit_fn=self.submitter,
            llm_decompose_fn=lambda obj: [f"{obj.title} - 单步"],
            declare_paths_fn=lambda desc: [f"path-for-{desc}"],
            goal_backlog=self.backlog,
        )

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_retry_current_step_resubmits(self):
        obj = _make_objective(self.backlog, "任务D")
        exec_id = self.executor.start(obj)
        self.assertEqual(len(self.submitter.calls), 1)

        ok = self.executor.retry_current_step(exec_id)
        self.assertTrue(ok)
        self.assertEqual(len(self.submitter.calls), 2)

    def test_inject_guidance_appears_in_next_prompt(self):
        obj = _make_objective(self.backlog, "任务E")
        exec_id = self.executor.start(obj)

        ok = self.executor.inject_guidance(exec_id, "换个思路试试")
        self.assertTrue(ok)

        self.executor.retry_current_step(exec_id)
        last_message = self.submitter.calls[-1]["message"]
        self.assertIn("换个思路试试", last_message)
        self.assertIn("[用户补充说明]", last_message)

    def test_inject_guidance_unknown_execution_returns_false(self):
        self.assertFalse(self.executor.inject_guidance("no-such-exec", "hi"))


class TestRetryPromptCarriesFailureReason(unittest.TestCase):
    """Track F：重试时把上一次失败原因注入下一次 prompt，而不是原样重发。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)
        self.submitter = _FakeSubmitter()
        self.executor = ObjectiveExecutor(
            paths=self.paths,
            submit_fn=self.submitter,
            llm_decompose_fn=lambda obj: [f"{obj.title} - 单步"],
            declare_paths_fn=lambda desc: [f"path-for-{desc}"],
            goal_backlog=self.backlog,
        )

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_retry_prompt_includes_previous_error(self):
        obj = _make_objective(self.backlog, "任务F")
        self.executor.start(obj)
        first_turn = self.submitter.calls[0]["turn_id"]

        self.executor.on_turn_failed(first_turn, "网络连接超时，工具调用失败")

        self.assertEqual(len(self.submitter.calls), 2)
        retry_message = self.submitter.calls[1]["message"]
        self.assertIn("网络连接超时，工具调用失败", retry_message)
        self.assertIn("请根据失败原因调整方法后重试", retry_message)


if __name__ == "__main__":
    unittest.main()
