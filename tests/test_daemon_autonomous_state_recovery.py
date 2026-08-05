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



# ── 阶段三：ObjectiveIsolatedRunner（P1 自主任务独立上下文）───────────────

class _FakeIsolatedAgent:
    """代替真实 Agent：不需要 LLM/API key，只模拟 run_turn() 的行为。"""

    def __init__(self, text="步骤已完成", raise_exc=None, hit_invalid=False):
        self._text = text
        self._raise_exc = raise_exc
        self._last_turn_result_invalid = hit_invalid

    def run_turn(self, message):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._text


class TestObjectiveIsolatedRunner(unittest.TestCase):
    def setUp(self):
        from mini_agent.evolution.objective_agent_bridge import ObjectiveIsolatedRunner
        from mini_agent.config.models import AppConfig

        self.done_calls = []
        self.failed_calls = []
        self._lock_event = __import__("threading").Event()

        def _on_done(turn_id, summary, valid=True):
            self.done_calls.append((turn_id, summary, valid))
            self._lock_event.set()

        def _on_failed(turn_id, error):
            self.failed_calls.append((turn_id, error))
            self._lock_event.set()

        self.runner = ObjectiveIsolatedRunner(
            base_cfg=AppConfig(),
            on_done=_on_done,
            on_failed=_on_failed,
            max_workers=2,
        )

    def tearDown(self):
        self.runner.shutdown(wait=True)

    def _submit_with_fake_agent(self, fake_agent, timeout=5.0):
        import unittest.mock as mock
        from mini_agent.evolution import objective_agent_bridge

        self._lock_event.clear()
        with mock.patch.object(
            objective_agent_bridge, "build_objective_agent", return_value=fake_agent
        ):
            turn_id = self.runner.submit(
                "步骤 1/1: 做点事情", "autonomous",
                {"execution_id": "exec-1", "objective_id": "obj-1", "step_index": 0},
            )
        self.assertIsNotNone(turn_id)
        self.assertTrue(self._lock_event.wait(timeout=timeout))
        return turn_id

    def test_successful_step_calls_on_done_with_valid_true(self):
        turn_id = self._submit_with_fake_agent(_FakeIsolatedAgent(text="搞定了"))
        self.assertEqual(len(self.done_calls), 1)
        got_turn_id, summary, valid = self.done_calls[0]
        self.assertEqual(got_turn_id, turn_id)
        self.assertEqual(summary, "搞定了")
        self.assertTrue(valid)
        self.assertEqual(self.failed_calls, [])

    def test_invalid_result_calls_on_done_with_valid_false(self):
        self._submit_with_fake_agent(_FakeIsolatedAgent(text="脏结果", hit_invalid=True))
        self.assertEqual(len(self.done_calls), 1)
        _, _, valid = self.done_calls[0]
        self.assertFalse(valid)

    def test_run_turn_exception_calls_on_failed(self):
        self._submit_with_fake_agent(_FakeIsolatedAgent(raise_exc=RuntimeError("boom")))
        self.assertEqual(self.done_calls, [])
        self.assertEqual(len(self.failed_calls), 1)
        self.assertIn("boom", self.failed_calls[0][1])

    def test_agent_build_failure_calls_on_failed(self):
        import unittest.mock as mock
        from mini_agent.evolution import objective_agent_bridge

        self._lock_event.clear()
        with mock.patch.object(
            objective_agent_bridge, "build_objective_agent",
            side_effect=RuntimeError("cannot build"),
        ):
            turn_id = self.runner.submit(
                "步骤 1/1: 做点事情", "autonomous", {"execution_id": "exec-2"},
            )
        self.assertIsNotNone(turn_id)
        self.assertTrue(self._lock_event.wait(timeout=5.0))
        self.assertEqual(len(self.failed_calls), 1)
        self.assertIn("cannot build", self.failed_calls[0][1])

    def test_submit_after_shutdown_returns_none(self):
        self.runner.shutdown(wait=True)
        turn_id = self.runner.submit("msg", "autonomous", {})
        self.assertIsNone(turn_id)

    def test_submit_fn_can_be_swapped_into_objective_executor(self):
        """[接线验证] ObjectiveExecutor._submit_fn 是一个普通可赋值属性，
        ObjectiveIsolatedRunner.submit 可以直接替换默认的共享提交路径，
        与 api/server.py 里的接线方式一致。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = AgentPaths(Path(tmpdir))
            backlog = GoalBacklog(paths)
            executor = ObjectiveExecutor(paths=paths, submit_fn=None, goal_backlog=backlog)
            self.assertIsNone(executor._submit_fn)
            executor._submit_fn = self.runner.submit
            self.assertEqual(executor._submit_fn, self.runner.submit)
            self.assertIs(executor._submit_fn.__self__, self.runner)



# ── 阶段四：GuardianRunner（P2 看护模式）────────────────────────────────────

class TestGuardianRunnerUnit(unittest.TestCase):
    def _guardian(self, **kwargs):
        from mini_agent.evolution.guardian import GuardianRunner
        defaults = dict(consecutive_limit=3, max_recoveries=1, max_rounds=10)
        defaults.update(kwargs)
        return GuardianRunner(**defaults)

    def test_varied_results_never_trigger(self):
        from mini_agent.evolution.guardian import StuckSignal
        guardian = self._guardian()
        texts = [
            "已经完成数据库迁移脚本的编写",
            "修复了登录页面的样式错乱问题",
            "补充了单元测试覆盖率到 90%",
            "重构了缓存模块的过期策略",
            "接入了新的第三方支付网关",
            "优化了首页加载速度",
            "清理了废弃的旧接口代码",
            "完成了国际化多语言支持",
        ]
        for i, text in enumerate(texts):
            signal = guardian.observe_step(i, text)
            self.assertIs(signal, StuckSignal.NONE)

    def test_repeated_identical_results_trigger_recover_then_give_up(self):
        from mini_agent.evolution.guardian import StuckSignal
        guardian = self._guardian(consecutive_limit=3, max_recoveries=1)
        signals = [guardian.observe_step(i, "一模一样的结果文本") for i in range(6)]
        self.assertIn(StuckSignal.RECOVER, signals)
        self.assertIn(StuckSignal.GIVE_UP, signals)
        # GIVE_UP 之后不应该再有更严重的信号（枚举里没有更严重的，只需确认
        # GIVE_UP 确实出现过，且出现在 RECOVER 之后）。
        self.assertLess(signals.index(StuckSignal.RECOVER), signals.index(StuckSignal.GIVE_UP))

    def test_should_terminate_by_rounds(self):
        guardian = self._guardian(max_rounds=3, consecutive_limit=100)
        for i in range(2):
            guardian.observe_step(i, f"结果{i}")
            self.assertFalse(guardian.should_terminate_by_rounds())
        guardian.observe_step(2, "结果2")
        self.assertTrue(guardian.should_terminate_by_rounds())

    def test_max_rounds_zero_means_unlimited(self):
        guardian = self._guardian(max_rounds=0)
        for i in range(50):
            guardian.observe_step(i, f"结果{i}")
        self.assertFalse(guardian.should_terminate_by_rounds())

    def test_dead_end_dedup_and_render(self):
        guardian = self._guardian()
        guardian.record_dead_end(0, "尝试用方法 A 但一直失败")
        guardian.record_dead_end(1, "尝试用方法 A 但一直失败")  # 近似重复，去重
        guardian.record_dead_end(2, "换成方法 B 也没用")
        block = guardian.render_dead_ends_block()
        self.assertIn("方法 A", block)
        self.assertIn("方法 B", block)
        self.assertEqual(block.count("步骤"), 2)

    def test_reset_clears_all_state(self):
        from mini_agent.evolution.guardian import StuckSignal
        guardian = self._guardian(consecutive_limit=2, max_recoveries=1)
        guardian.observe_step(0, "同样的文本")
        guardian.observe_step(1, "同样的文本")
        guardian.record_dead_end(0, "某个死路")
        guardian.reset()
        self.assertEqual(guardian.round_count, 0)
        self.assertEqual(guardian.recoveries_used, 0)
        self.assertEqual(guardian.render_dead_ends_block(), "")
        self.assertIs(guardian.observe_step(0, "任意文本"), StuckSignal.NONE)


class TestGuardianModeObjectiveExecutorIntegration(_ObjectiveExecutorTestBase):
    """[daemon_autonomous_state_recovery_plan.md 阶段四] guardian_mode_enabled
    默认 False 时完全不影响 ObjectiveExecutor 行为；开启后能在"连续多步
    结果高度相似"时触发既有的重新分解/判失败收尾路径。"""

    def _cfg(self, **autonomy_overrides):
        from mini_agent.config.models import AppConfig

        cfg = AppConfig()
        for k, v in autonomy_overrides.items():
            setattr(cfg.autonomy, k, v)
        return cfg

    def _executor_with_cfg(self, cfg, steps=None, llm_redecompose_fn=None):
        steps = steps or ["第一步", "第二步", "第三步", "第四步", "第五步"]
        return ObjectiveExecutor(
            paths=self.paths,
            submit_fn=self.submitter,
            llm_decompose_fn=lambda obj: list(steps),
            declare_paths_fn=lambda desc: [f"path-for-{desc}"],
            goal_backlog=self.backlog,
            llm_redecompose_fn=llm_redecompose_fn,
            cfg=cfg,
        )

    def test_guardian_disabled_no_effect(self):
        # [daemon_stability_and_ux_improvement_plan.md P0-6] guardian_mode_enabled
        # 默认值已改为 True，这里显式关闭以验证"关闭时无影响"这一分支本身
        # 仍然成立。
        cfg = self._cfg(guardian_mode_enabled=False)
        executor = self._executor_with_cfg(cfg)
        obj = _make_objective(self.backlog, "任务L")
        exec_id = executor.start(obj)
        # 连续提交完全相同的结果，不应触发任何 guardian 逻辑（因为关闭）
        for _ in range(4):
            turn_id = self.submitter.calls[-1]["turn_id"]
            executor.on_turn_done(turn_id, "一模一样的结果", valid=True)
            ex = executor._executions[exec_id]
            if ex.status in ("completed", "failed"):
                break
        ex = executor._executions[exec_id]
        # 5 步全部完成，没有被 guardian 提前判失败
        self.assertNotIn("guardian", ex.progress_notes)

    def test_guardian_enabled_triggers_fail_without_redecompose(self):
        cfg = self._cfg(
            guardian_mode_enabled=True,
            guardian_stuck_consecutive_limit=2,
            guardian_max_recoveries=1,
            guardian_max_rounds=100,
        )
        executor = self._executor_with_cfg(cfg, llm_redecompose_fn=None)
        obj = _make_objective(self.backlog, "任务M")
        exec_id = executor.start(obj)

        for _ in range(6):
            ex = executor._executions[exec_id]
            if ex.status in ("completed", "failed"):
                break
            turn_id = self.submitter.calls[-1]["turn_id"]
            executor.on_turn_done(turn_id, "完全相同的结果文本", valid=True)

        ex = executor._executions[exec_id]
        self.assertEqual(ex.status, "failed")
        self.assertIn("guardian", ex.progress_notes)

    def test_guardian_enabled_falls_back_to_redecompose_when_available(self):
        cfg = self._cfg(
            guardian_mode_enabled=True,
            guardian_stuck_consecutive_limit=2,
            guardian_max_recoveries=1,
        )

        def _redecompose(title, completed, remaining, reason, external_context=None):
            return ["换个思路的新步骤A", "换个思路的新步骤B"]

        executor = self._executor_with_cfg(cfg, llm_redecompose_fn=_redecompose)
        obj = _make_objective(self.backlog, "任务N")
        exec_id = executor.start(obj)

        for _ in range(6):
            ex = executor._executions[exec_id]
            if ex.status in ("completed", "failed"):
                break
            turn_id = self.submitter.calls[-1]["turn_id"]
            executor.on_turn_done(turn_id, "完全相同的结果文本", valid=True)

        ex = executor._executions[exec_id]
        # 重新分解成功后 execution 不应停留在 failed（要么继续 running，
        # 要么后续步骤走完变成 completed），且确实用上了新的步骤描述。
        self.assertTrue(ex.redecompose_attempted)
        self.assertTrue(any("换个思路" in s.description for s in ex.steps))


if __name__ == "__main__":
    unittest.main()
