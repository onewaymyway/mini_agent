"""
tests/test_hybrid_exec_p4.py — hybrid_exec P4 单元测试

对应 next_doc/hybrid_exec_design_plan.md §8 P4：
  1. ReexplorePolicy：样本不足/达标/不达标三种判定
  2. HybridExecutor 接入 reexplore_policy 后的两条集成路径：
     a. 主动探索成功 → 直接用新版本，不再执行旧脚本
     b. 主动探索失败 → 不影响，继续正常执行旧脚本
  3. kanban_summary.build_kanban_summary：跨 task 聚合脚本仓库 + run 统计
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.hybrid_exec.executor import HybridExecutor
from mini_agent.hybrid_exec.explorer import Explorer
from mini_agent.hybrid_exec.fallback import FallbackExecutor
from mini_agent.hybrid_exec.kanban_summary import build_kanban_summary
from mini_agent.hybrid_exec.policy import ReexplorePolicy
from mini_agent.hybrid_exec.recorder import RunRecorder
from mini_agent.hybrid_exec.repairer import Repairer
from mini_agent.hybrid_exec.repository import ScriptRecord, ScriptRepository
from mini_agent.hybrid_exec.spec import ExecutionTier, ScriptOutcome, TaskSpec


# ---------------------------------------------------------------------------
# ReexplorePolicy
# ---------------------------------------------------------------------------


class TestReexplorePolicy(unittest.TestCase):
    def _record(self, success=0, fail=0) -> ScriptRecord:
        return ScriptRecord(version=1, created_at="", created_by="manual", success_count=success, fail_count=fail)

    def test_disabled_never_triggers(self):
        policy = ReexplorePolicy(enabled=False, min_samples=1, success_rate_threshold=0.9)
        should, reason = policy.should_reexplore(self._record(success=0, fail=10))
        self.assertFalse(should)
        self.assertIn("未启用", reason)

    def test_insufficient_samples_does_not_trigger(self):
        policy = ReexplorePolicy(enabled=True, min_samples=5, success_rate_threshold=0.9)
        should, reason = policy.should_reexplore(self._record(success=0, fail=2))
        self.assertFalse(should)
        self.assertIn("样本数不足", reason)

    def test_low_success_rate_triggers(self):
        policy = ReexplorePolicy(enabled=True, min_samples=5, success_rate_threshold=0.6)
        should, reason = policy.should_reexplore(self._record(success=2, fail=8))  # 20%
        self.assertTrue(should)
        self.assertIn("20%", reason)

    def test_healthy_success_rate_does_not_trigger(self):
        policy = ReexplorePolicy(enabled=True, min_samples=5, success_rate_threshold=0.6)
        should, reason = policy.should_reexplore(self._record(success=9, fail=1))  # 90%
        self.assertFalse(should)
        self.assertIn("达标", reason)


# ---------------------------------------------------------------------------
# HybridExecutor + reexplore_policy 集成
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
    def repair(self, task, broken_code, outcome):
        return None


class _StubFallback(FallbackExecutor):  # noqa: D101 — 测试用，不调用 super().__init__
    def __init__(self, llm_output="fallback"):
        self.llm_output = llm_output

    def llm_direct(self, task):
        return self.llm_output

    def agent_direct(self, task):
        raise NotImplementedError


class TestHybridExecutorWithReexplorePolicy(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.repo = ScriptRepository(base / "scripts", retire_after_consecutive_fail=100)  # 本组测试不关心 retire
        self.recorder = RunRecorder(base / "runs")

    def tearDown(self):
        self._tmp.cleanup()

    def _seed_flaky_script(self):
        """存一版脚本，并手工堆出一段"能跑但成功率很差"的历史（success_count/
        fail_count 未到 retire 阈值，但明显不健康）。"""
        rec = self.repo.save_new_version("t1", "# OLD_SCRIPT", "manual")
        for _ in range(2):
            self.repo.record_success("t1", rec.version)
        for _ in range(8):
            self.repo.record_failure("t1", rec.version, "flaky")
        return rec

    def test_proactive_reexplore_success_replaces_active_without_running_old_script(self):
        self._seed_flaky_script()
        runner = _FakeScriptRunner({"NEW_GOOD_SCRIPT": ScriptOutcome(ok=True, output="better")})
        executor = HybridExecutor(
            repo=self.repo,
            script_runner=runner,
            llm_explorer=_StubExplorer(code="# NEW_GOOD_SCRIPT"),
            agent_explorer=_StubExplorer(code=None),
            llm_repairer=_StubRepairer(),
            agent_repairer=_StubRepairer(),
            fallback=_StubFallback(),
            run_recorder=self.recorder,
            reexplore_policy=ReexplorePolicy(enabled=True, min_samples=5, success_rate_threshold=0.6),
        )

        result = executor.run(TaskSpec(task_id="t1", description="demo", input_data={}))

        self.assertTrue(result.ok)
        self.assertEqual(result.output, "better")
        self.assertEqual(result.tier_used, ExecutionTier.SCRIPT)
        # 旧脚本（OLD_SCRIPT）不应该被执行过——主动探索成功后直接采用新版本
        self.assertTrue(all("OLD_SCRIPT" not in c for c in runner.calls))
        active = self.repo.get_active_script("t1")
        self.assertEqual(active.created_by, "llm_explorer")

    def test_proactive_reexplore_failure_falls_back_to_still_using_old_script(self):
        old_rec = self._seed_flaky_script()
        runner = _FakeScriptRunner({"OLD_SCRIPT": ScriptOutcome(ok=True, output="still works")})
        # llm_explorer 探索出的候选脚本 dry-run 会失败（不在 behaviors 里配置）
        executor = HybridExecutor(
            repo=self.repo,
            script_runner=runner,
            llm_explorer=_StubExplorer(code="# CANDIDATE_THAT_FAILS_DRYRUN"),
            agent_explorer=_StubExplorer(code=None),
            llm_repairer=_StubRepairer(),
            agent_repairer=_StubRepairer(),
            fallback=_StubFallback(),
            run_recorder=self.recorder,
            reexplore_policy=ReexplorePolicy(enabled=True, min_samples=5, success_rate_threshold=0.6),
        )

        result = executor.run(TaskSpec(task_id="t1", description="demo", input_data={}))

        self.assertTrue(result.ok)
        self.assertEqual(result.output, "still works")
        self.assertEqual(result.script_version, old_rec.version)  # 仍是旧版本
        active = self.repo.get_active_script("t1")
        self.assertEqual(active.version, old_rec.version)  # 没有被主动探索的失败候选替换掉

    def test_policy_disabled_by_default_does_not_interfere(self):
        self._seed_flaky_script()
        runner = _FakeScriptRunner({"OLD_SCRIPT": ScriptOutcome(ok=True, output="as usual")})
        executor = HybridExecutor(
            repo=self.repo,
            script_runner=runner,
            llm_explorer=_StubExplorer(code="# SHOULD_NOT_BE_CALLED"),
            agent_explorer=_StubExplorer(code=None),
            llm_repairer=_StubRepairer(),
            agent_repairer=_StubRepairer(),
            fallback=_StubFallback(),
            # reexplore_policy 不传，默认 None，不启用
        )
        result = executor.run(TaskSpec(task_id="t1", description="demo", input_data={}))
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "as usual")
        self.assertTrue(all("SHOULD_NOT_BE_CALLED" not in c for c in runner.calls))


# ---------------------------------------------------------------------------
# kanban_summary.build_kanban_summary
# ---------------------------------------------------------------------------


class TestBuildKanbanSummary(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_project_returns_empty_list(self):
        summary = build_kanban_summary(self.project_root)
        self.assertEqual(summary, {"tasks": []})

    def test_aggregates_script_and_run_stats(self):
        from mini_agent.hybrid_exec.spec import ExecutionResult

        scripts_dir = self.project_root / ".agent" / "hybrid_exec" / "scripts"
        runs_dir = self.project_root / ".agent" / "hybrid_exec" / "runs"

        repo = ScriptRepository(scripts_dir)
        repo.save_new_version("t1", "code v1", "llm_explorer")
        repo.record_success("t1", 1)
        repo.record_failure("t1", 1, "err")

        recorder = RunRecorder(runs_dir)
        recorder.record("t1", ExecutionResult(ok=True, output="a", tier_used=ExecutionTier.SCRIPT, script_version=1))
        recorder.record("t2", ExecutionResult(ok=False, output=None, tier_used=ExecutionTier.LLM, script_version=None))

        summary = build_kanban_summary(self.project_root)
        by_id = {t["task_id"]: t for t in summary["tasks"]}

        self.assertEqual(set(by_id.keys()), {"t1", "t2"})

        t1 = by_id["t1"]
        self.assertEqual(t1["active_version"], 1)
        self.assertEqual(t1["active_status"], "active")
        self.assertEqual(t1["active_success_count"], 1)
        self.assertEqual(t1["active_fail_count"], 1)
        self.assertEqual(t1["run_summary"]["total_runs"], 1)

        t2 = by_id["t2"]
        self.assertIsNone(t2["active_version"])
        self.assertEqual(t2["active_status"], "none")
        self.assertEqual(t2["run_summary"]["total_runs"], 1)

    def test_stable_sort_order(self):
        scripts_dir = self.project_root / ".agent" / "hybrid_exec" / "scripts"
        repo = ScriptRepository(scripts_dir)
        for tid in ["zeta", "alpha", "mid"]:
            repo.save_new_version(tid, "code", "manual")
        summary = build_kanban_summary(self.project_root)
        ids = [t["task_id"] for t in summary["tasks"]]
        self.assertEqual(ids, sorted(ids))


if __name__ == "__main__":
    unittest.main()
