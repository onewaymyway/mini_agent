"""
tests/test_hybrid_exec.py — hybrid_exec P1 单元测试

对应 next_doc/hybrid_exec_design_plan.md §8 P1 验收标准：
  1. ScriptRepository：版本存取、成功/失败统计、连续失败自动 retire
  2. ScriptRunner._parse_result：结果包协议解析（成功/失败/JSON输出/非法输出）
  3. HybridExecutor 主流程三条主路径：
     a. 已有脚本直接成功
     b. 已有脚本失败 → 修复成功
     c. 已有脚本失败 → 修复也失败 → 降级 Fallback(LLM) 成功
     d. 无脚本 → 探索成功（转正入库）
     e. 无脚本 → 探索失败（LLM 探索不过，Agent 探索未实现）→ Fallback 兜底
  4. NotImplementedError（P1 阶段 Agent 相关能力未实现）不会打断整体流程
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_agent.hybrid_exec.executor import HybridExecutor
from mini_agent.hybrid_exec.explorer import Explorer
from mini_agent.hybrid_exec.fallback import FallbackExecutor
from mini_agent.hybrid_exec.repairer import Repairer
from mini_agent.hybrid_exec.repository import ScriptRepository
from mini_agent.hybrid_exec.runner import ScriptRunner
from mini_agent.hybrid_exec.spec import ExecutionTier, ScriptOutcome, TaskSpec


# ---------------------------------------------------------------------------
# ScriptRepository
# ---------------------------------------------------------------------------


class TestScriptRepository(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = ScriptRepository(Path(self._tmp.name), retire_after_consecutive_fail=3)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_active_script_initially(self):
        self.assertIsNone(self.repo.get_active_script("t1"))

    def test_save_and_get_active(self):
        rec = self.repo.save_new_version("t1", "def run(ctx):\n    return 'ok'\n", "llm_explorer")
        self.assertEqual(rec.version, 1)
        active = self.repo.get_active_script("t1")
        self.assertIsNotNone(active)
        self.assertEqual(active.version, 1)
        self.assertEqual(active.status, "active")
        self.assertEqual(self.repo.load_code("t1", 1), "def run(ctx):\n    return 'ok'\n")

    def test_new_version_supersedes_old(self):
        self.repo.save_new_version("t1", "code v1", "llm_explorer")
        rec2 = self.repo.save_new_version("t1", "code v2", "llm_repairer")
        self.assertEqual(rec2.version, 2)
        active = self.repo.get_active_script("t1")
        self.assertEqual(active.version, 2)
        versions = self.repo.list_versions("t1")
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0].status, "superseded")
        self.assertEqual(versions[1].status, "active")

    def test_record_success_resets_consecutive_fail(self):
        self.repo.save_new_version("t1", "code", "llm_explorer")
        self.repo.record_failure("t1", 1, "boom")
        self.repo.record_success("t1", 1)
        rec = self.repo.get_active_script("t1")
        self.assertEqual(rec.consecutive_fail, 0)
        self.assertEqual(rec.success_count, 1)
        self.assertEqual(rec.fail_count, 1)

    def test_auto_retire_after_consecutive_failures(self):
        self.repo.save_new_version("t1", "code", "llm_explorer")
        self.repo.record_failure("t1", 1, "err1")
        self.repo.record_failure("t1", 1, "err2")
        self.repo.record_failure("t1", 1, "err3")  # 达到阈值 3
        self.assertIsNone(self.repo.get_active_script("t1"))
        versions = self.repo.list_versions("t1")
        self.assertEqual(versions[0].status, "retired")

    def test_manual_retire(self):
        self.repo.save_new_version("t1", "code", "llm_explorer")
        self.repo.retire("t1", 1, "手动退役")
        self.assertIsNone(self.repo.get_active_script("t1"))


# ---------------------------------------------------------------------------
# ScriptRunner._parse_result
# ---------------------------------------------------------------------------


class TestScriptRunnerParseResult(unittest.TestCase):
    def test_parse_success_text_output(self):
        stdout = 'some log line\n{"ok": true, "output": "hello", "output_is_json": false}\n'
        outcome = ScriptRunner._parse_result(stdout, "", 0, 1.0)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.output, "hello")

    def test_parse_success_json_output(self):
        stdout = json.dumps({"ok": True, "output": json.dumps({"a": 1}), "output_is_json": True})
        outcome = ScriptRunner._parse_result(stdout, "", 0, 1.0)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.output, {"a": 1})

    def test_parse_failure_packet(self):
        stdout = json.dumps({
            "ok": False, "error": "boom", "error_type": "ValueError", "traceback": "tb...",
        })
        outcome = ScriptRunner._parse_result(stdout, "stderr text", 1, 0.5)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error, "boom")
        self.assertEqual(outcome.error_type, "ValueError")

    def test_parse_empty_output(self):
        outcome = ScriptRunner._parse_result("", "crashed", 1, 0.1)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error_type, "EmptyOutput")

    def test_parse_non_json_last_line(self):
        outcome = ScriptRunner._parse_result("not json at all", "", 1, 0.1)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error_type, "ResultPacketParseError")


# ---------------------------------------------------------------------------
# HybridExecutor 主流程（用 fake 组件，不触发真实子进程/LLM 调用）
# ---------------------------------------------------------------------------


class _FakeScriptRunner:
    """按脚本文件名里的约定关键字返回预设结果，模拟"这一版脚本是否能跑通"。"""

    def __init__(self, script_behaviors: dict):
        # script_behaviors: {"v1.py" 内容标记 -> ScriptOutcome}，按脚本源码内容匹配
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
    def __init__(self, code=None, raise_not_implemented=False):
        self.code = code
        self.raise_not_implemented = raise_not_implemented

    def explore(self, task):
        if self.raise_not_implemented:
            raise NotImplementedError("agent explorer not implemented in P1")
        return self.code


class _StubRepairer(Repairer):
    def __init__(self, code=None, raise_not_implemented=False):
        self.code = code
        self.raise_not_implemented = raise_not_implemented

    def repair(self, task, broken_code, outcome):
        if self.raise_not_implemented:
            raise NotImplementedError("agent repairer not implemented in P1")
        return self.code


class _StubFallback(FallbackExecutor):  # noqa: D101 — 测试用，不调用 super().__init__
    def __init__(self, llm_output=None, llm_raises=False):
        self.llm_output = llm_output
        self.llm_raises = llm_raises
        self.agent_calls = 0

    def llm_direct(self, task):
        if self.llm_raises:
            raise RuntimeError("llm provider down")
        return self.llm_output

    def agent_direct(self, task):
        self.agent_calls += 1
        raise NotImplementedError("agent fallback not implemented in P1")


def _make_executor(repo: ScriptRepository, script_runner, *, explorer_code=None,
                    repair_code=None, fallback_llm_output="fallback answer"):
    return HybridExecutor(
        repo=repo,
        script_runner=script_runner,
        llm_explorer=_StubExplorer(code=explorer_code),
        agent_explorer=_StubExplorer(raise_not_implemented=True),
        llm_repairer=_StubRepairer(code=repair_code),
        agent_repairer=_StubRepairer(raise_not_implemented=True),
        fallback=_StubFallback(llm_output=fallback_llm_output),
    )


class TestHybridExecutorFlows(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = ScriptRepository(Path(self._tmp.name), retire_after_consecutive_fail=3)

    def tearDown(self):
        self._tmp.cleanup()

    def test_existing_script_succeeds_directly(self):
        self.repo.save_new_version("t1", "# GOOD_SCRIPT", "manual")
        runner = _FakeScriptRunner({"GOOD_SCRIPT": ScriptOutcome(ok=True, output="42")})
        executor = _make_executor(self.repo, runner)

        result = executor.run(TaskSpec(task_id="t1", description="demo", input_data={}))

        self.assertTrue(result.ok)
        self.assertEqual(result.output, "42")
        self.assertEqual(result.tier_used, ExecutionTier.SCRIPT)
        self.assertEqual(result.script_version, 1)
        # 成功路径不应该调用 explorer/repairer/fallback
        self.assertEqual(len(runner.calls), 1)

    def test_existing_script_fails_then_repair_succeeds(self):
        self.repo.save_new_version("t1", "# BAD_SCRIPT", "manual")
        runner = _FakeScriptRunner({
            "BAD_SCRIPT": ScriptOutcome(ok=False, error="boom", error_type="ValueError"),
            "FIXED_SCRIPT": ScriptOutcome(ok=True, output="fixed-result"),
        })
        executor = _make_executor(self.repo, runner, repair_code="# FIXED_SCRIPT")

        result = executor.run(TaskSpec(task_id="t1", description="demo", input_data={}, max_script_repair_attempts=2))

        self.assertTrue(result.ok)
        self.assertEqual(result.output, "fixed-result")
        self.assertEqual(result.tier_used, ExecutionTier.SCRIPT)
        self.assertEqual(result.script_version, 2)  # 修复后存为新版本
        active = self.repo.get_active_script("t1")
        self.assertEqual(active.version, 2)

    def test_existing_script_fails_repair_fails_falls_back_to_llm(self):
        self.repo.save_new_version("t1", "# BAD_SCRIPT", "manual")
        runner = _FakeScriptRunner({
            "BAD_SCRIPT": ScriptOutcome(ok=False, error="boom"),
            "STILL_BAD": ScriptOutcome(ok=False, error="still boom"),
        })
        executor = _make_executor(self.repo, runner, repair_code="# STILL_BAD", fallback_llm_output="llm saved the day")

        result = executor.run(TaskSpec(task_id="t1", description="demo", input_data={}, max_script_repair_attempts=1))

        self.assertTrue(result.ok)
        self.assertEqual(result.output, "llm saved the day")
        self.assertEqual(result.tier_used, ExecutionTier.LLM)
        self.assertIsNone(result.script_version)
        # 修复失败的那次也应该被记为一次 record_failure（fail_count 增加）
        versions = self.repo.list_versions("t1")
        self.assertGreaterEqual(versions[0].fail_count, 1)

    def test_no_script_explores_and_stores_new_version(self):
        runner = _FakeScriptRunner({"NEW_SCRIPT": ScriptOutcome(ok=True, output="explored-result")})
        executor = _make_executor(self.repo, runner, explorer_code="# NEW_SCRIPT")

        result = executor.run(TaskSpec(task_id="t2", description="demo", input_data={"x": 1}))

        self.assertTrue(result.ok)
        self.assertEqual(result.output, "explored-result")
        self.assertEqual(result.tier_used, ExecutionTier.SCRIPT)
        self.assertEqual(result.script_version, 1)
        active = self.repo.get_active_script("t2")
        self.assertIsNotNone(active)
        self.assertEqual(active.created_by, "llm_explorer")

    def test_no_script_explore_fails_falls_back_to_llm(self):
        runner = _FakeScriptRunner({"NEW_SCRIPT": ScriptOutcome(ok=False, error="dry-run failed")})
        executor = _make_executor(self.repo, runner, explorer_code="# NEW_SCRIPT", fallback_llm_output="direct answer")

        result = executor.run(TaskSpec(task_id="t3", description="demo", input_data={}))

        self.assertTrue(result.ok)
        self.assertEqual(result.output, "direct answer")
        self.assertEqual(result.tier_used, ExecutionTier.LLM)
        self.assertIsNone(self.repo.get_active_script("t3"))  # dry-run 没过，不应该入库

    def test_output_validator_rejects_script_result_triggers_repair(self):
        self.repo.save_new_version("t1", "# GOOD_SCRIPT", "manual")
        runner = _FakeScriptRunner({
            "GOOD_SCRIPT": ScriptOutcome(ok=True, output="not-a-number"),
            "FIXED_SCRIPT": ScriptOutcome(ok=True, output="42"),
        })

        def validator(output):
            return (isinstance(output, str) and output.isdigit()), "必须是纯数字字符串"

        executor = _make_executor(self.repo, runner, repair_code="# FIXED_SCRIPT")
        task = TaskSpec(
            task_id="t1", description="demo", input_data={}, output_validator=validator,
            max_script_repair_attempts=1,
        )
        result = executor.run(task)

        self.assertTrue(result.ok)
        self.assertEqual(result.output, "42")
        self.assertEqual(result.script_version, 2)

    def test_agent_tier_not_implemented_does_not_crash(self):
        """P1 阶段：allow_tiers 包含 AGENT，但 AgentExplorer/AgentRepairer/
        agent_direct 都抛 NotImplementedError，整个流程应平滑降级而不是崩溃。"""
        runner = _FakeScriptRunner({})  # 任何脚本都跑不通
        executor = _make_executor(self.repo, runner, explorer_code=None, fallback_llm_output=None)
        # explorer_code=None 时 _StubExplorer.explore 返回 None，dry_run 会把
        # None 当脚本内容写入文件，FakeScriptRunner 找不到匹配 behavior 会失败，
        # 从而触发 AgentExplorer 分支（同样是 NotImplementedError）。

        result = executor.run(TaskSpec(task_id="t4", description="demo", input_data={}))

        self.assertFalse(result.ok)
        self.assertIsNone(result.output)
        # 决策轨迹里应能看到 agent 相关尝试被记录为失败，而不是抛异常中断
        agent_stages = [a for a in result.attempts if a.tier == ExecutionTier.AGENT]
        self.assertTrue(len(agent_stages) >= 1)
        self.assertTrue(all(not a.ok for a in agent_stages))


if __name__ == "__main__":
    unittest.main()
