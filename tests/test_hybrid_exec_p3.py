"""
tests/test_hybrid_exec_p3.py — hybrid_exec P3 单元测试

对应 next_doc/hybrid_exec_design_plan.md §8 P3：
  1. RunRecorder：单条 run 记录落盘 + summary.json 滚动聚合
  2. HybridExecutor 接入 run_recorder 后自动记录每次 run()
  3. 退役策略联调：脚本反复失败触发 ScriptRepository 自动退役后，
     下一次 run() 调用能透明地重新走探索流程，不需要人工介入；
     以及"修复成功会重置连续失败计数，不会被误退役"
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.hybrid_exec.executor import HybridExecutor
from mini_agent.hybrid_exec.explorer import Explorer
from mini_agent.hybrid_exec.fallback import FallbackExecutor
from mini_agent.hybrid_exec.recorder import RunRecorder
from mini_agent.hybrid_exec.repairer import Repairer
from mini_agent.hybrid_exec.repository import ScriptRepository
from mini_agent.hybrid_exec.spec import ExecutionResult, ExecutionTier, ScriptOutcome, TaskSpec


# ---------------------------------------------------------------------------
# RunRecorder
# ---------------------------------------------------------------------------


class TestRunRecorder(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.recorder = RunRecorder(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_summary_initially(self):
        self.assertIsNone(self.recorder.get_summary("t1"))
        self.assertEqual(self.recorder.list_run_ids("t1"), [])

    def test_record_writes_run_file_and_summary(self):
        result = ExecutionResult(ok=True, output="42", tier_used=ExecutionTier.SCRIPT, script_version=1)
        run_path = self.recorder.record("t1", result)
        self.assertTrue(run_path.exists())

        summary = self.recorder.get_summary("t1")
        self.assertEqual(summary["total_runs"], 1)
        self.assertEqual(summary["success_runs"], 1)
        self.assertEqual(summary.get("fail_runs", 0), 0)
        self.assertEqual(summary["tier_counts"], {"script": 1})
        self.assertTrue(summary["last_run_ok"])

        run_ids = self.recorder.list_run_ids("t1")
        self.assertEqual(len(run_ids), 1)
        loaded = self.recorder.load_run("t1", run_ids[0])
        self.assertEqual(loaded["output"], "42")

    def test_summary_aggregates_across_multiple_records(self):
        self.recorder.record("t1", ExecutionResult(ok=True, output="a", tier_used=ExecutionTier.SCRIPT, script_version=1))
        self.recorder.record("t1", ExecutionResult(ok=False, output=None, tier_used=ExecutionTier.LLM, script_version=None))
        self.recorder.record("t1", ExecutionResult(ok=True, output="b", tier_used=ExecutionTier.SCRIPT, script_version=1))

        summary = self.recorder.get_summary("t1")
        self.assertEqual(summary["total_runs"], 3)
        self.assertEqual(summary["success_runs"], 2)
        self.assertEqual(summary["fail_runs"], 1)
        self.assertEqual(summary["tier_counts"], {"script": 2, "llm": 1})
        self.assertEqual(len(self.recorder.list_run_ids("t1")), 3)

    def test_different_tasks_do_not_interfere(self):
        self.recorder.record("t1", ExecutionResult(ok=True, output="a", tier_used=ExecutionTier.SCRIPT, script_version=1))
        self.recorder.record("t2", ExecutionResult(ok=True, output="b", tier_used=ExecutionTier.LLM, script_version=None))
        self.assertEqual(self.recorder.get_summary("t1")["tier_counts"], {"script": 1})
        self.assertEqual(self.recorder.get_summary("t2")["tier_counts"], {"llm": 1})


# ---------------------------------------------------------------------------
# HybridExecutor + run_recorder 集成
# ---------------------------------------------------------------------------


class _FakeScriptRunner:
    def __init__(self, script_behaviors: dict):
        self.behaviors = script_behaviors
        self.calls = []

    def run(self, script_path, task, **kwargs):
        code = Path(script_path).read_text(encoding="utf-8")
        self.calls.append(code)
        for marker, outcome in self.behaviors.items():
            if marker in code:
                return outcome
        return ScriptOutcome(ok=False, error=f"no behavior configured for code={code!r}")


class _StubExplorer(Explorer):
    def __init__(self, code=None):
        self.code = code

    def explore(self, task):
        return self.code


class _StubRepairer(Repairer):
    def __init__(self, code=None):
        self.code = code

    def repair(self, task, broken_code, outcome):
        return self.code


class _StubFallback(FallbackExecutor):  # noqa: D101 — 测试用，不调用 super().__init__
    def __init__(self, llm_output=None):
        self.llm_output = llm_output

    def llm_direct(self, task):
        return self.llm_output

    def agent_direct(self, task):
        raise NotImplementedError


def _make_executor(repo, script_runner, recorder, *, explorer_code=None, repair_code=None, fallback_llm_output="fallback"):
    return HybridExecutor(
        repo=repo,
        script_runner=script_runner,
        llm_explorer=_StubExplorer(code=explorer_code),
        agent_explorer=_StubExplorer(code=None),
        llm_repairer=_StubRepairer(code=repair_code),
        agent_repairer=_StubRepairer(code=None),
        fallback=_StubFallback(llm_output=fallback_llm_output),
        run_recorder=recorder,
    )


class TestHybridExecutorWithRunRecorder(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.repo = ScriptRepository(base / "scripts", retire_after_consecutive_fail=3)
        self.recorder = RunRecorder(base / "runs")

    def tearDown(self):
        self._tmp.cleanup()

    def test_successful_run_is_recorded(self):
        self.repo.save_new_version("t1", "# GOOD_SCRIPT", "manual")
        runner = _FakeScriptRunner({"GOOD_SCRIPT": ScriptOutcome(ok=True, output="42")})
        executor = _make_executor(self.repo, runner, self.recorder)

        result = executor.run(TaskSpec(task_id="t1", description="demo", input_data={}))

        self.assertTrue(result.ok)
        summary = self.recorder.get_summary("t1")
        self.assertIsNotNone(summary)
        self.assertEqual(summary["total_runs"], 1)
        self.assertEqual(summary["success_runs"], 1)

    def test_recorder_failure_does_not_break_run(self):
        """run_recorder.record 抛异常时，run() 仍应正常返回结果（防御性设计）。"""
        self.repo.save_new_version("t1", "# GOOD_SCRIPT", "manual")
        runner = _FakeScriptRunner({"GOOD_SCRIPT": ScriptOutcome(ok=True, output="42")})

        class _BrokenRecorder(RunRecorder):
            def record(self, task_id, result):
                raise OSError("disk full")

        executor = _make_executor(self.repo, runner, _BrokenRecorder(Path("/nonexistent")))
        result = executor.run(TaskSpec(task_id="t1", description="demo", input_data={}))
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "42")


# ---------------------------------------------------------------------------
# 退役策略联调：脚本反复失败 → 自动退役 → 下次 run() 透明重新探索
# ---------------------------------------------------------------------------


class TestRetirementIntegration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        # 阈值设为 2：脚本执行失败 + 修复失败各记一次，一次 run() 调用内
        # 就会触发退役，便于用最少的 run() 调用次数验证联调链路。
        self.repo = ScriptRepository(base / "scripts", retire_after_consecutive_fail=2)
        self.recorder = RunRecorder(base / "runs")

    def tearDown(self):
        self._tmp.cleanup()

    def test_persistent_failure_triggers_retire_then_next_run_reexplores(self):
        self.repo.save_new_version("t1", "# BAD_SCRIPT", "manual")
        runner = _FakeScriptRunner({
            "BAD_SCRIPT": ScriptOutcome(ok=False, error="boom"),
            "STILL_BAD": ScriptOutcome(ok=False, error="still boom"),
        })
        executor = _make_executor(
            self.repo, runner, self.recorder,
            repair_code="# STILL_BAD", fallback_llm_output="fallback answer 1",
        )
        task = TaskSpec(task_id="t1", description="demo", input_data={}, max_script_repair_attempts=1)

        # 第一次 run()：脚本失败 + 修复失败，累计 2 次失败，达到阈值自动退役。
        result1 = executor.run(task)
        self.assertTrue(result1.ok)  # 降级到 LLM fallback 仍拿到结果
        self.assertEqual(result1.tier_used, ExecutionTier.LLM)
        self.assertIsNone(self.repo.get_active_script("t1"))  # 已自动退役

        # 第二次 run()（同一个 executor/repo，不需要人工干预）：
        # 因为没有 active 脚本，应自动走探索分支，用新脚本重新入库成功。
        runner.behaviors["NEW_GOOD_SCRIPT"] = ScriptOutcome(ok=True, output="explored-again")
        executor2 = _make_executor(
            self.repo, runner, self.recorder,
            explorer_code="# NEW_GOOD_SCRIPT", fallback_llm_output="fallback answer 2",
        )
        result2 = executor2.run(task)

        self.assertTrue(result2.ok)
        self.assertEqual(result2.output, "explored-again")
        self.assertEqual(result2.tier_used, ExecutionTier.SCRIPT)
        active = self.repo.get_active_script("t1")
        self.assertIsNotNone(active)
        self.assertEqual(active.created_by, "llm_explorer")

        # RunRecorder 也应该记录了两次 run（一次降级 LLM、一次脚本探索成功）。
        summary = self.recorder.get_summary("t1")
        self.assertEqual(summary["total_runs"], 2)
        self.assertEqual(summary["tier_counts"], {"llm": 1, "script": 1})

    def test_repair_success_resets_consecutive_fail_avoids_premature_retire(self):
        """脚本这次失败但修复成功时，consecutive_fail 应被 record_success 重置，
        不会因为"先失败一次"就被误判为该退役了。"""
        self.repo.save_new_version("t1", "# BAD_SCRIPT", "manual")
        runner = _FakeScriptRunner({
            "BAD_SCRIPT": ScriptOutcome(ok=False, error="boom"),
            "FIXED_SCRIPT": ScriptOutcome(ok=True, output="fixed"),
        })
        executor = _make_executor(self.repo, runner, self.recorder, repair_code="# FIXED_SCRIPT")
        task = TaskSpec(task_id="t1", description="demo", input_data={}, max_script_repair_attempts=1)

        result = executor.run(task)

        self.assertTrue(result.ok)
        self.assertEqual(result.output, "fixed")
        active = self.repo.get_active_script("t1")
        self.assertIsNotNone(active)  # 没有被退役
        self.assertEqual(active.status, "active")
        self.assertEqual(active.consecutive_fail, 0)  # 修复成功后计数被重置


if __name__ == "__main__":
    unittest.main()
