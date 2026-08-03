"""
tests/test_objective_persistent_runner.py

覆盖 next_doc/daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md
阶段一：ObjectivePersistentRunner ——

  - 同一 execution_id 的多个 step 复用同一个 Agent 实例、跑在同一条线程上
  - 不同 execution_id 之间真正并行（互不等待）
  - release() 立即释放专属线程 + Agent 实例
  - build_objective_agent(persistent=True) 只影响注入文案，不影响其它字段

测试里全部用 fake Agent（不构造真实 Agent/LLM client），避免依赖网络/API key。
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from mini_agent.evolution import objective_agent_bridge as bridge_mod
from mini_agent.evolution.objective_agent_bridge import ObjectivePersistentRunner


class _FakeAgent:
    """记录被调用的 run_turn 次数/线程，不做真实 LLM 调用。"""

    _instances_built = 0

    def __init__(self, sleep_seconds: float = 0.0):
        _FakeAgent._instances_built += 1
        self.instance_id = _FakeAgent._instances_built
        self.run_turn_calls: list[str] = []
        self.run_turn_threads: list[int] = []
        self._sleep_seconds = sleep_seconds
        self._last_turn_result_invalid = False

    def run_turn(self, message: str) -> str:
        self.run_turn_calls.append(message)
        self.run_turn_threads.append(threading.get_ident())
        if self._sleep_seconds:
            time.sleep(self._sleep_seconds)
        return f"done: {message[:20]}"


class _FakeAppConfig:
    """占位对象，ObjectivePersistentRunner 只把它原样透传给
    build_objective_agent（此处被 monkeypatch，不会真正用到其内部字段）。"""
    pass


def _make_runner(build_fn, idle_ttl_seconds: float = 1800.0):
    done_calls: list[tuple] = []
    failed_calls: list[tuple] = []

    def on_done(turn_id, summary, valid=True):
        done_calls.append((turn_id, summary, valid))

    def on_failed(turn_id, error):
        failed_calls.append((turn_id, error))

    runner = ObjectivePersistentRunner(
        base_cfg=_FakeAppConfig(),
        on_done=on_done,
        on_failed=on_failed,
        idle_ttl_seconds=idle_ttl_seconds,
    )
    return runner, done_calls, failed_calls


class TestObjectivePersistentRunnerReuse(unittest.TestCase):
    def setUp(self):
        _FakeAgent._instances_built = 0

    def test_same_execution_reuses_agent_and_thread(self):
        """同一 execution_id 的多个 step 应该复用同一个 Agent 实例、
        跑在同一条专属线程上。"""
        with patch.object(bridge_mod, "build_objective_agent", side_effect=lambda *a, **kw: _FakeAgent()):
            runner, done_calls, failed_calls = _make_runner(None)
            try:
                meta = {"execution_id": "exec_1", "objective_id": "obj_1"}
                for i in range(3):
                    turn_id = runner.submit(f"step {i}", "autonomous", meta)
                    self.assertIsNotNone(turn_id)
                    # 等待后台线程处理完这一步（同步等待，避免竞态）
                    deadline = time.time() + 5
                    while len(done_calls) <= i and time.time() < deadline:
                        time.sleep(0.01)

                self.assertEqual(len(done_calls), 3)
                self.assertEqual(len(failed_calls), 0)
                # 只应该构建了 1 个 Agent 实例（跨 3 个 step 复用）
                self.assertEqual(_FakeAgent._instances_built, 1)
            finally:
                runner.shutdown(wait=True)

    def test_different_executions_run_in_parallel(self):
        """两个不同的 execution_id 应该各自独立、可以同时处于"运行中"，
        不需要互相等待——用一个可控的 sleep 验证总耗时接近单次耗时而不是
        两次耗时之和。"""
        sleep_seconds = 0.3

        def fake_build(*args, **kwargs):
            return _FakeAgent(sleep_seconds=sleep_seconds)

        with patch.object(bridge_mod, "build_objective_agent", side_effect=fake_build):
            runner, done_calls, failed_calls = _make_runner(None)
            try:
                t0 = time.time()
                runner.submit("step a", "autonomous", {"execution_id": "exec_a", "objective_id": "a"})
                runner.submit("step b", "autonomous", {"execution_id": "exec_b", "objective_id": "b"})

                deadline = time.time() + 5
                while len(done_calls) < 2 and time.time() < deadline:
                    time.sleep(0.01)
                elapsed = time.time() - t0

                self.assertEqual(len(done_calls), 2)
                # 真并行：总耗时应明显小于两次串行耗时（2 * sleep_seconds），
                # 留足够宽松的阈值避免测试环境抖动导致误判。
                self.assertLess(elapsed, sleep_seconds * 1.8)
            finally:
                runner.shutdown(wait=True)

    def test_release_frees_thread_and_agent(self):
        """release() 应立即从内部映射里移除该 execution，之后再收到同一
        execution_id 的 step 会重新构建一个新的 Agent 实例。"""
        with patch.object(bridge_mod, "build_objective_agent", side_effect=lambda *a, **kw: _FakeAgent()):
            runner, done_calls, failed_calls = _make_runner(None)
            try:
                meta = {"execution_id": "exec_release", "objective_id": "obj"}
                runner.submit("step 1", "autonomous", meta)
                deadline = time.time() + 5
                while len(done_calls) < 1 and time.time() < deadline:
                    time.sleep(0.01)
                self.assertIn("exec_release", runner.active_execution_ids())

                runner.release("exec_release")
                self.assertNotIn("exec_release", runner.active_execution_ids())

                # 重新提交同一个 execution_id：应重新构建一个新 Agent 实例
                runner.submit("step 2 after release", "autonomous", meta)
                deadline = time.time() + 5
                while len(done_calls) < 2 and time.time() < deadline:
                    time.sleep(0.01)
                self.assertEqual(_FakeAgent._instances_built, 2)
            finally:
                runner.shutdown(wait=True)

    def test_stopped_runner_rejects_new_submits(self):
        with patch.object(bridge_mod, "build_objective_agent", side_effect=lambda *a, **kw: _FakeAgent()):
            runner, done_calls, failed_calls = _make_runner(None)
            runner.shutdown(wait=True)
            turn_id = runner.submit("late step", "autonomous", {"execution_id": "exec_x", "objective_id": "x"})
            self.assertIsNone(turn_id)

    def test_build_failure_reports_on_failed(self):
        def failing_build(*args, **kwargs):
            raise RuntimeError("boom")

        with patch.object(bridge_mod, "build_objective_agent", side_effect=failing_build):
            runner, done_calls, failed_calls = _make_runner(None)
            try:
                runner.submit("step", "autonomous", {"execution_id": "exec_fail", "objective_id": "x"})
                deadline = time.time() + 5
                while len(failed_calls) < 1 and time.time() < deadline:
                    time.sleep(0.01)
                self.assertEqual(len(failed_calls), 1)
                self.assertEqual(len(done_calls), 0)
            finally:
                runner.shutdown(wait=True)


class TestBuildObjectiveAgentPersistentFlag(unittest.TestCase):
    def test_persistent_flag_changes_system_extra_wording_only(self):
        import mini_agent.evolution.objective_agent_bridge as mod

        captured_cfgs = []

        class _FakeGuard:
            def __init__(self, **kwargs):
                pass

        class _FakeLLMConfig:
            @staticmethod
            def from_app_config(cfg):
                return object()

        class _FakeAppConfigReal:
            project_root = "."
            sandbox = None
            model = "m"
            llm_provider = "anthropic"
            llm_base_url = None
            use_system_tool_call = False
            debug_llm = False
            tool_cache_enabled = False
            api_key = "x"
            system_extra = ""
            autonomy = None

        def fake_load_config(**kwargs):
            class _CompressCfg:
                enabled = False
                threshold = 0.7

            class _Cfg:
                api_key = "x"
                system_extra = ""
                compress = _CompressCfg()
            c = _Cfg()
            captured_cfgs.append(c)
            return c

        with patch.object(mod, "load_config", side_effect=fake_load_config), \
             patch.object(mod, "LLMConfig", _FakeLLMConfig), \
             patch.object(mod, "PermissionGuard", _FakeGuard), \
             patch.object(mod, "create_client", lambda *a, **kw: object()), \
             patch.object(mod, "Agent", lambda **kwargs: kwargs):

            result_persistent = mod.build_objective_agent(
                _FakeAppConfigReal(), "my objective", "exec_1", persistent=True,
            )
            result_isolated = mod.build_objective_agent(
                _FakeAppConfigReal(), "my objective", "exec_2", persistent=False,
            )

        self.assertIn("持久化 Worker", captured_cfgs[0].system_extra)
        self.assertIn("独立上下文", captured_cfgs[1].system_extra)
        # Agent(**kwargs) 被替身成透传 dict，registry/skill_loader/tool_cache
        # 三个字段应保持 None（全量继承默认工具集），两种模式行为一致。
        for result in (result_persistent, result_isolated):
            self.assertIsNone(result["registry"])
            self.assertIsNone(result["skill_loader"])
            self.assertIsNone(result["tool_cache"])
            self.assertTrue(result["is_subagent"])


if __name__ == "__main__":
    unittest.main()
