"""tests/test_tick_dispatch_phase3_goal_side_calls.py

[next_doc/tick_dispatch_only_execution_model_plan.md 阶段三]
executor 之外三处 tick 线程内同步 LLM 调用的异步化：
  #7 AutonomousLoop._ensure_goal_objectives 的 Goal→Objective 拆解
  #8 goal_cron_bridge._check_pursuit_saturation（reap_finished_cycles 内）
  #6 goal_cycle 触发时的 LLM 进展/稳定性判断（缓存 + 后台刷新，difflib 兜底）

核心验收：LLM 永远挂起时，对应调用点在 tick 线程里秒级返回；未注入派发器时行为与改造前一致。
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mini_agent.evolution import goal_cron_bridge as bridge
from mini_agent.evolution.autonomous_loop import AutonomousLoop
from mini_agent.evolution.tick_dispatcher import TickDispatcher
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


def _wait_until(pred, timeout=5.0, step=0.01):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.paths = AgentPaths(Path(self._tmp.name))
        self.backlog = GoalBacklog(self.paths)
        self.dispatcher = TickDispatcher(max_workers=4, default_timeout_seconds=60, grace_seconds=0)
        self.gates: list[threading.Event] = []

    def tearDown(self):
        bridge.set_async_side_calls(None)
        for g in self.gates:
            g.set()
        _wait_until(lambda: self.dispatcher.stats()["running"] == 0, timeout=3.0)
        time.sleep(0.05)
        self._tmp.cleanup()

    def gate(self) -> threading.Event:
        g = threading.Event()
        self.gates.append(g)
        return g


# ── #7 Goal 拆解 ─────────────────────────────────────────────────────────────

class TestGoalDecomposeAsync(_Base):
    def _loop(self, decompose_fn, *, async_on=True):
        loop = AutonomousLoop(goal_backlog=self.backlog, input_queue=None, paths=self.paths,
                              cfg=None, goal_decompose_fn=decompose_fn)
        if async_on:
            loop.set_tick_dispatcher(self.dispatcher, timeout_seconds=60)
        return loop

    def _objectives(self, goal_id):
        self.backlog.load()
        g = self.backlog.get(goal_id)
        return [self.backlog.get(c).title for c in g.children_ids]

    def test_hanging_llm_does_not_block_tick(self):
        gate = self.gate()
        goal = self.backlog.add_goal(title="G", description="", source="user", priority=50)
        loop = self._loop(lambda g: (gate.wait(30), ["x"])[1])
        t0 = time.time()
        loop._ensure_goal_objectives()
        self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(self._objectives(goal.id), [])  # 还没有 Objective，但没卡住
        self.assertTrue(self.dispatcher.is_running(f"goal_decompose:{goal.id}"))

    def test_repeated_ticks_dedupe_while_running(self):
        gate = self.gate()
        calls = []
        self.backlog.add_goal(title="G", description="", source="user", priority=50)

        def slow(g):
            calls.append(g.id)
            gate.wait(30)
            return ["x"]

        loop = self._loop(slow)
        for _ in range(3):
            loop._ensure_goal_objectives()
        _wait_until(lambda: len(calls) >= 1)
        time.sleep(0.1)
        self.assertEqual(len(calls), 1)

    def test_result_consumed_on_next_tick(self):
        goal = self.backlog.add_goal(title="G", description="", source="user", priority=50)
        loop = self._loop(lambda g: ["step A", "step B"])
        loop._ensure_goal_objectives()
        self.assertTrue(_wait_until(lambda: self.dispatcher.stats()["running"] == 0))
        loop._ensure_goal_objectives()  # 消费结果
        self.assertEqual(self._objectives(goal.id), ["step A", "step B"])
        import json
        lines = (self.paths.workdir_dir / "activity_digest.jsonl").read_text(encoding="utf-8").splitlines()
        digests = [json.loads(l) for l in lines if "objective_auto_created" in l]
        self.assertEqual(len(digests), 2)

    def test_exception_degrades_to_mirror(self):
        goal = self.backlog.add_goal(title="Mirror me", description="", source="user", priority=50)

        def boom(g):
            raise RuntimeError("llm down")

        loop = self._loop(boom)
        loop._ensure_goal_objectives()
        self.assertTrue(_wait_until(lambda: self.dispatcher.stats()["running"] == 0))
        loop._ensure_goal_objectives()
        self.assertEqual(self._objectives(goal.id), ["Mirror me"])

    def test_timeout_degrades_to_mirror(self):
        gate = self.gate()
        goal = self.backlog.add_goal(title="Slow goal", description="", source="user", priority=50)
        loop = self._loop(lambda g: (gate.wait(30), ["x"])[1])
        loop.set_tick_dispatcher(self.dispatcher, timeout_seconds=1)  # grace=0 → 1s 后可回收
        loop._ensure_goal_objectives()
        time.sleep(1.1)
        loop._ensure_goal_objectives()  # reap_stale 触发 on_done(timeout) → 结果为 []
        loop._ensure_goal_objectives()  # 消费 → 降级镜像
        self.assertEqual(self._objectives(goal.id), ["Slow goal"])

    def test_dispatcher_full_retries_next_tick(self):
        d = TickDispatcher(max_workers=1, default_timeout_seconds=60, grace_seconds=0)
        gate = self.gate()
        d.dispatch("occupy", lambda: gate.wait(30))
        goal = self.backlog.add_goal(title="G", description="", source="user", priority=50)
        loop = AutonomousLoop(goal_backlog=self.backlog, input_queue=None, paths=self.paths,
                              cfg=None, goal_decompose_fn=lambda g: ["ok"])
        loop.set_tick_dispatcher(d, timeout_seconds=60)
        loop._ensure_goal_objectives()  # 被拒，不回退同步、不创建 Objective
        self.assertEqual(self._objectives(goal.id), [])
        gate.set()
        self.assertTrue(_wait_until(lambda: d.stats()["running"] == 0))
        loop._ensure_goal_objectives()  # 重新派发
        self.assertTrue(_wait_until(lambda: d.stats()["running"] == 0))
        loop._ensure_goal_objectives()  # 消费
        self.assertEqual(self._objectives(goal.id), ["ok"])

    def test_without_dispatcher_stays_synchronous(self):
        goal = self.backlog.add_goal(title="G", description="", source="user", priority=50)
        loop = self._loop(lambda g: ["sync"], async_on=False)
        loop._ensure_goal_objectives()
        self.assertEqual(self._objectives(goal.id), ["sync"])  # 一次调用就完成

    def test_stale_results_pruned(self):
        loop = self._loop(lambda g: ["x"])
        loop._goal_decompose_results["ghost"] = ["old"]
        self.backlog.add_goal(title="G", description="", source="user", priority=50)
        loop._ensure_goal_objectives()
        self.assertNotIn("ghost", loop._goal_decompose_results)


# ── #8 pursuit 饱和度复核 ────────────────────────────────────────────────────

class TestPursuitCheckAsync(_Base):
    def _goal(self):
        return self.backlog.add_goal(title="P", description="", source="user", priority=50)

    def test_hanging_check_returns_immediately(self):
        gate = self.gate()
        goal = self._goal()
        bridge.set_async_side_calls(self.dispatcher, timeout_seconds=60)
        with patch.object(bridge, "_check_pursuit_saturation_sync",
                          side_effect=lambda *a, **k: gate.wait(30)):
            t0 = time.time()
            bridge._check_pursuit_saturation(self.backlog, goal, child_id="c1")
            self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(bridge.async_side_calls_stats()["pursuit_dispatched"], 1)

    def test_sync_when_not_injected(self):
        goal = self._goal()
        calls = []
        with patch.object(bridge, "_check_pursuit_saturation_sync",
                          side_effect=lambda *a, **k: calls.append(1)):
            bridge._check_pursuit_saturation(self.backlog, goal, child_id="c1")
        self.assertEqual(calls, [1])  # 同步执行完才返回

    def test_busy_dispatcher_defers_then_retries(self):
        d = TickDispatcher(max_workers=1, default_timeout_seconds=60, grace_seconds=0)
        gate = self.gate()
        d.dispatch("occupy", lambda: gate.wait(30))
        goal = self._goal()
        bridge.set_async_side_calls(d, timeout_seconds=60)
        ran = []
        with patch.object(bridge, "_check_pursuit_saturation_sync",
                          side_effect=lambda *a, **k: ran.append(1)):
            bridge._check_pursuit_saturation(self.backlog, goal, child_id="c1")
            st = bridge.async_side_calls_stats()
            self.assertEqual((st["pursuit_deferred"], st["pending_pursuit_checks"]), (1, 1))
            self.assertEqual(ran, [])
            gate.set()
            self.assertTrue(_wait_until(lambda: d.stats()["running"] == 0))
            bridge._retry_pending_pursuit_checks(self.backlog)
            self.assertTrue(_wait_until(lambda: ran == [1]))
        self.assertEqual(bridge.async_side_calls_stats()["pending_pursuit_checks"], 0)

    def test_reap_finished_cycles_does_not_block(self):
        """端到端：reap 一个已完成的周期子节点，pursuit 复核挂死时 reap 仍秒级返回且计数正常。"""
        gate = self.gate()
        goal = self.backlog.add_goal(title="Cyc", description="", source="user", priority=50)
        self.backlog.set_recurrence(goal.id, recurring=True, cron_job_id="job1")
        child = self.backlog.add_objectives_for_goal(goal.id, ["round 1"])[0]
        self.backlog.update_status(child.id, "completed") if hasattr(self.backlog, "update_status") else None
        self.backlog.load()
        node = self.backlog.get(child.id)
        node.status = "completed"
        self.backlog.save()
        bridge.set_async_side_calls(self.dispatcher, timeout_seconds=60)
        with patch.object(bridge, "_check_pursuit_saturation_sync",
                          side_effect=lambda *a, **k: gate.wait(30)):
            t0 = time.time()
            n = bridge.reap_finished_cycles(self.backlog)
            self.assertLess(time.time() - t0, 2.0)
        self.assertEqual(n, 1)


# ── #6 goal_cycle LLM 进展判断 ───────────────────────────────────────────────

class TestSignalLlmAsync(_Base):
    def test_no_cache_returns_none_immediately_and_refreshes(self):
        gate = self.gate()

        class H:
            def ask(self, prompt):
                gate.wait(30)
                return "STUCK"

        bridge.set_async_side_calls(self.dispatcher, timeout_seconds=60)
        fn = bridge._make_async_signal_llm("g1", "progress_trend", H())
        t0 = time.time()
        self.assertIsNone(fn("prompt"))  # 无缓存 → None → 调用方退回 difflib
        self.assertLess(time.time() - t0, 1.0)
        self.assertTrue(self.dispatcher.is_running("goal_signal:g1:progress_trend"))

    def test_cache_used_next_time(self):
        class H:
            def ask(self, prompt):
                return "STUCK"

        bridge.set_async_side_calls(self.dispatcher, timeout_seconds=60)
        fn = bridge._make_async_signal_llm("g1", "progress_trend", H())
        self.assertIsNone(fn("p"))
        self.assertTrue(_wait_until(lambda: self.dispatcher.stats()["running"] == 0))
        fn2 = bridge._make_async_signal_llm("g1", "progress_trend", H())
        self.assertEqual(fn2("p"), "STUCK")
        self.assertGreaterEqual(bridge.async_side_calls_stats()["signal_cache_hits"], 1)

    def test_failure_keeps_old_cache(self):
        class Ok:
            def ask(self, prompt):
                return "PROGRESSING"

        class Bad:
            def ask(self, prompt):
                raise RuntimeError("down")

        bridge.set_async_side_calls(self.dispatcher, timeout_seconds=60)
        bridge._make_async_signal_llm("g1", "k", Ok())("p")
        self.assertTrue(_wait_until(lambda: self.dispatcher.stats()["running"] == 0))
        bridge._make_async_signal_llm("g1", "k", Bad())("p")
        self.assertTrue(_wait_until(lambda: self.dispatcher.stats()["running"] == 0))
        self.assertEqual(bridge._make_async_signal_llm("g1", "k", Bad())("p"), "PROGRESSING")

    def test_works_with_compute_progress_trend_signal(self):
        """包装后的 callable 配合真实 compute_progress_trend_signal：无缓存 → difflib 结果。"""
        from mini_agent.perception import execution_phase as ep

        class _Child:
            def __init__(self, note):
                self.progress_notes = note

        class _Goal:
            reaped_cycle_child_ids = ["c1", "c2", "c3"]

        class _Backlog:
            def get(self, i):
                return _Goal() if i == "g1" else _Child("same note about progress")

        class H:
            def ask(self, prompt):
                return "PROGRESSING"

        bridge.set_async_side_calls(self.dispatcher, timeout_seconds=60)
        fn = bridge._make_async_signal_llm("g1", "progress_trend", H())
        self.assertIs(ep.compute_progress_trend_signal(_Backlog(), "g1", llm_helper=fn), True)  # difflib
        self.assertTrue(_wait_until(lambda: self.dispatcher.stats()["running"] == 0))
        fn2 = bridge._make_async_signal_llm("g1", "progress_trend", H())
        self.assertIs(ep.compute_progress_trend_signal(_Backlog(), "g1", llm_helper=fn2), False)  # LLM 缓存

    def test_resolve_execution_phase_uses_async_wrapper(self):
        """_resolve_execution_phase 在注入派发器且开启 LLM 开关时，不在调用线程里等 LLM。"""
        gate = self.gate()

        class H:
            def ask(self, prompt):
                gate.wait(30)
                return "STUCK"

        class _Goal:
            id = "goal_1"
            execution_spec_confirmed = False

        bridge.set_async_side_calls(self.dispatcher, timeout_seconds=60)
        cfg = MagicMock()
        cfg.execution_phase.progress_trend_llm_enabled = True
        with patch("mini_agent.config.load_config", return_value=cfg):
            t0 = time.time()
            bridge._resolve_execution_phase(self.paths, _Goal(), 1, goal_backlog=self.backlog,
                                            llm_helper_provider=lambda: H())
            self.assertLess(time.time() - t0, 2.0)


class TestSetAsyncSideCalls(_Base):
    def test_clear_resets_state(self):
        bridge.set_async_side_calls(self.dispatcher)
        bridge._signal_verdicts[("g", "k")] = ("STUCK", time.time())
        bridge.set_async_side_calls(None)
        st = bridge.async_side_calls_stats()
        self.assertFalse(st["enabled"])
        self.assertEqual(st["cached_signal_verdicts"], 0)


if __name__ == "__main__":
    unittest.main()
