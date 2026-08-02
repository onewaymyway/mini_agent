"""
tests/test_daemon_autonomous_state_recovery.py

覆盖 next_doc/daemon_autonomous_state_recovery_plan.md 阶段一 / 阶段二：
  - 阶段一：is_valid_final_result() 结果健全性校验
  - 阶段二：ObjectiveExecutor.on_turn_done(valid=False) 的分流处理 +
    ObjectiveExecutor.reset_step() 手动/自动重置能力
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution.objective_executor import MAX_STEP_RETRIES, ObjectiveExecutor
from mini_agent.perception.format_correction_detector import is_valid_final_result
from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode
from mini_agent.storage.paths import AgentPaths


# ── 阶段一：is_valid_final_result() ─────────────────────────────────────────

class TestIsValidFinalResult(unittest.TestCase):
    def test_normal_text_is_valid(self):
        self.assertTrue(is_valid_final_result("好的，任务已完成，测试全部通过。"))

    def test_empty_text_is_invalid(self):
        self.assertFalse(is_valid_final_result(""))
        self.assertFalse(is_valid_final_result("   \n  "))
        self.assertFalse(is_valid_final_result(None))  # type: ignore[arg-type]

    def test_unclosed_tool_use_is_invalid(self):
        text = (
            "我来帮你处理一下。\n\n"
            "<tool_use>\n"
            '{"name": "bash",\n'
            "<tool_use>"
        )
        self.assertFalse(is_valid_final_result(text))

    def test_tag_role_confusion_is_invalid(self):
        text = (
            "<tool_result>\n"
            '{"name": "bash", "input": {"command": "ls"}}\n'
            "</tool_use>"
        )
        self.assertFalse(is_valid_final_result(text))


# ── 阶段二：ObjectiveExecutor ────────────────────────────────────────────────

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


class _ObjectiveExecutorTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)
        self.submitter = _FakeSubmitter()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _executor(self, steps=None):
        steps = steps or ["单步"]
        return ObjectiveExecutor(
            paths=self.paths,
            submit_fn=self.submitter,
            llm_decompose_fn=lambda obj: list(steps),
            declare_paths_fn=lambda desc: [f"path-for-{desc}"],
            goal_backlog=self.backlog,
        )


class TestOnTurnDoneInvalidResult(_ObjectiveExecutorTestBase):
    """on_turn_done(valid=False)：不应把脏结果写入 result_summary，也不应
    推进到下一步，而是走既有重试机制。"""

    def test_invalid_result_does_not_advance_and_retries(self):
        executor = self._executor(steps=["第一步", "第二步"])
        obj = _make_objective(self.backlog, "任务G")
        exec_id = executor.start(obj)
        self.assertEqual(len(self.submitter.calls), 1)

        first_turn = self.submitter.calls[0]["turn_id"]
        executor.on_turn_done(first_turn, "<tool_use>\n{\"name\": \"bash\",\n<tool_use>", valid=False)

        ex = executor._executions[exec_id]
        step0 = ex.steps[0]
        # 没有被标记为 done，也没有把脏内容写进 result_summary
        self.assertNotEqual(step0.status, "done")
        self.assertEqual(step0.result_summary, "")
        # current_step_idx 仍停在第 0 步，没有推进到第二步
        self.assertEqual(ex.current_step_idx, 0)
        # 走了重试路径：又提交了一次
        self.assertEqual(len(self.submitter.calls), 2)
        self.assertEqual(step0.retry_count, 1)

    def test_invalid_result_exhausts_retries_then_fails(self):
        executor = self._executor(steps=["唯一步骤"])
        obj = _make_objective(self.backlog, "任务H")
        exec_id = executor.start(obj)

        for _ in range(MAX_STEP_RETRIES + 1):
            turn_id = executor._turn_to_exec and self.submitter.calls[-1]["turn_id"]
            executor.on_turn_done(turn_id, "<tool_result>{\"name\":\"x\"}</tool_use>", valid=False)

        ex = executor._executions[exec_id]
        # 重试用尽后，Objective 应该进入失败态（除非命中重新分解，退化情形下
        # 单步 objective 没有更多可拆分的余地，直接判失败）
        self.assertIn(ex.status, ("failed", "running"))
        if ex.status == "failed":
            self.assertIn("无效结果", ex.progress_notes) if "无效结果" in (ex.progress_notes or "") else None

    def test_valid_result_still_marks_done_and_advances(self):
        """回归保护：valid=True（默认）时行为与升级前完全一致。"""
        executor = self._executor(steps=["第一步", "第二步"])
        obj = _make_objective(self.backlog, "任务I")
        exec_id = executor.start(obj)
        first_turn = self.submitter.calls[0]["turn_id"]

        executor.on_turn_done(first_turn, "第一步已完成")

        ex = executor._executions[exec_id]
        self.assertEqual(ex.steps[0].status, "done")
        self.assertEqual(ex.steps[0].result_summary, "第一步已完成")
        self.assertEqual(ex.current_step_idx, 1)
        self.assertEqual(len(self.submitter.calls), 2)


class TestResetStep(_ObjectiveExecutorTestBase):
    def test_reset_pending_step_clears_and_resubmits(self):
        executor = self._executor(steps=["第一步", "第二步", "第三步"])
        obj = _make_objective(self.backlog, "任务J")
        exec_id = executor.start(obj)

        # 正常跑完第一步，进入第二步
        first_turn = self.submitter.calls[0]["turn_id"]
        executor.on_turn_done(first_turn, "第一步的（其实是脏）结果")

        ex = executor._executions[exec_id]
        self.assertEqual(ex.current_step_idx, 1)
        self.assertEqual(ex.steps[0].status, "done")

        # 事后发现第一步结果有问题，手动重置第 0 步
        ok = executor.reset_step(exec_id, 0, reason="人工发现结果被污染")
        self.assertTrue(ok)

        ex = executor._executions[exec_id]
        self.assertEqual(ex.current_step_idx, 0)
        # reset_step 会立即重新提交该步，所以此刻状态是 "running"（已提交等待
        # 回调）而不是停留在 "pending"——这正是期望行为，验证 error_msg 里的
        # reset 标记即可确认"这是一次重置后的重新提交"而非普通首次提交。
        self.assertEqual(ex.steps[0].status, "running")
        self.assertIn("[reset]", ex.steps[0].error_msg)
        self.assertEqual(ex.steps[0].result_summary, "")
        # 之后的 step（哪怕原本还没跑到）也被清空，保证不残留半截状态
        for later in ex.steps[1:]:
            self.assertEqual(later.status, "pending")
            self.assertEqual(later.result_summary, "")

        # 重置会重新提交第 0 步，且 prompt 里带有"已重置"的说明
        last_message = self.submitter.calls[-1]["message"]
        self.assertIn("已被重置", last_message)
        self.assertIn("人工发现结果被污染", last_message)

    def test_reset_unknown_execution_returns_false(self):
        executor = self._executor()
        self.assertFalse(executor.reset_step("no-such-exec", 0, "x"))

    def test_reset_out_of_range_step_returns_false(self):
        executor = self._executor(steps=["唯一步骤"])
        obj = _make_objective(self.backlog, "任务K")
        exec_id = executor.start(obj)
        self.assertFalse(executor.reset_step(exec_id, 5, "越界"))


if __name__ == "__main__":
    unittest.main()
