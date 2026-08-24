"""
tests/test_hybrid_exec_skill_tier.py — SKILL 档接入 HybridExecutor 主循环的测试

对应 next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md 第3节
"三档 member 执行机制"里 3.3b 阶段（HybridExecutor._run() 接入 SKILL 档决策
分支）。验证点：

1. 向后兼容：不传 playbook_repo/playbook_runner，或 allow_tiers 不含 SKILL
   时，行为与接入前完全一致（不额外调用 playbook_runner，不产生 SKILL 相关
   attempts）。
2. 有 active 脚本但修复彻底失败、且存在 active playbook 时，SKILL 档顶上，
   成功则 tier_used=SKILL，不再往下走 Fallback。
3. 无脚本、有 active playbook 时，同样优先于 explore 尝试 SKILL。
4. SKILL 档输出未通过 output_validator 时按失败处理，继续降级到
   explore/fallback，且 PlaybookRepository 记一次失败。
5. PlaybookRunner 抛出 PlaybookInvalidError 时，playbook 被直接 retire
   （而不是走 consecutive_fail 计数），随后继续降级。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.hybrid_exec.executor import HybridExecutor
from mini_agent.hybrid_exec.explorer import Explorer
from mini_agent.hybrid_exec.fallback import FallbackExecutor
from mini_agent.hybrid_exec.playbook_repository import PlaybookRepository
from mini_agent.hybrid_exec.playbook_runner import PlaybookInvalidError
from mini_agent.hybrid_exec.repairer import Repairer
from mini_agent.hybrid_exec.repository import ScriptRepository
from mini_agent.hybrid_exec.spec import ExecutionTier, ScriptOutcome, TaskSpec


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
    def __init__(self, code=None, raise_not_implemented=False):
        self.code = code
        self.raise_not_implemented = raise_not_implemented

    def explore(self, task):
        if self.raise_not_implemented:
            raise NotImplementedError("agent explorer not implemented")
        return self.code


class _StubRepairer(Repairer):
    def __init__(self, code=None, raise_not_implemented=False):
        self.code = code
        self.raise_not_implemented = raise_not_implemented

    def repair(self, task, broken_code, outcome):
        if self.raise_not_implemented:
            raise NotImplementedError("agent repairer not implemented")
        return self.code


class _StubFallback(FallbackExecutor):  # noqa: D101 — 测试用，不调用 super().__init__
    def __init__(self, llm_output=None):
        self.llm_output = llm_output

    def llm_direct(self, task):
        return self.llm_output

    def agent_direct(self, task):
        raise NotImplementedError("agent fallback not implemented")


class _StubPlaybookRunner:
    """按 task_id 返回预设输出/异常，模拟 PlaybookRunner.run()。"""

    def __init__(self, output=None, raises: Exception = None):
        self.output = output
        self.raises = raises
        self.calls = 0

    def run(self, task, playbook_content):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.output


def _make_executor(repo, script_runner, *, explorer_code=None, repair_code=None,
                    fallback_llm_output="fallback answer", playbook_repo=None,
                    playbook_runner=None):
    return HybridExecutor(
        repo=repo,
        script_runner=script_runner,
        llm_explorer=_StubExplorer(code=explorer_code),
        agent_explorer=_StubExplorer(raise_not_implemented=True),
        llm_repairer=_StubRepairer(code=repair_code),
        agent_repairer=_StubRepairer(raise_not_implemented=True),
        fallback=_StubFallback(llm_output=fallback_llm_output),
        playbook_repo=playbook_repo,
        playbook_runner=playbook_runner,
    )


class TestSkillTierWiring(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.repo = ScriptRepository(root / "scripts", retire_after_consecutive_fail=3)
        self.pb_repo = PlaybookRepository(root / "playbooks", retire_after_consecutive_fail=3)

    def tearDown(self):
        self._tmp.cleanup()

    def test_backward_compat_no_playbook_wiring(self):
        """不传 playbook_repo/playbook_runner 时，行为与接入前完全一致。"""
        self.repo.save_new_version("t1", "# BAD_SCRIPT", "manual")
        runner = _FakeScriptRunner({
            "BAD_SCRIPT": ScriptOutcome(ok=False, error="boom"),
            "STILL_BAD": ScriptOutcome(ok=False, error="still boom"),
        })
        executor = _make_executor(self.repo, runner, repair_code="# STILL_BAD",
                                   fallback_llm_output="llm saved the day")

        result = executor.run(TaskSpec(task_id="t1", description="demo", input_data={},
                                        max_script_repair_attempts=1))

        self.assertTrue(result.ok)
        self.assertEqual(result.tier_used, ExecutionTier.LLM)
        self.assertFalse(any(a.tier == ExecutionTier.SKILL for a in result.attempts))

    def test_allow_tiers_without_skill_skips_playbook_even_if_present(self):
        """即使配置了 playbook_repo/playbook_runner 且有 active playbook，
        allow_tiers 不含 SKILL 时也不应该被使用。"""
        self.repo.save_new_version("t1", "# BAD_SCRIPT", "manual")
        self.pb_repo.save_new_version("t1", "# 步骤说明", "manual")
        pb_runner = _StubPlaybookRunner(output="不该被用到")
        runner = _FakeScriptRunner({
            "BAD_SCRIPT": ScriptOutcome(ok=False, error="boom"),
            "STILL_BAD": ScriptOutcome(ok=False, error="still boom"),
        })
        executor = _make_executor(self.repo, runner, repair_code="# STILL_BAD",
                                   fallback_llm_output="llm saved the day",
                                   playbook_repo=self.pb_repo, playbook_runner=pb_runner)

        result = executor.run(TaskSpec(task_id="t1", description="demo", input_data={},
                                        max_script_repair_attempts=1))

        self.assertEqual(result.tier_used, ExecutionTier.LLM)
        self.assertEqual(pb_runner.calls, 0)

    def test_script_repair_fails_skill_succeeds(self):
        """脚本修复彻底失败，SKILL 档有可用 playbook 时顶上并成功。"""
        self.repo.save_new_version("t1", "# BAD_SCRIPT", "manual")
        self.pb_repo.save_new_version("t1", "# 步骤说明 v1", "manual")
        pb_runner = _StubPlaybookRunner(output="skill-result")
        runner = _FakeScriptRunner({
            "BAD_SCRIPT": ScriptOutcome(ok=False, error="boom"),
            "STILL_BAD": ScriptOutcome(ok=False, error="still boom"),
        })
        executor = _make_executor(self.repo, runner, repair_code="# STILL_BAD",
                                   fallback_llm_output="不该走到这里",
                                   playbook_repo=self.pb_repo, playbook_runner=pb_runner)

        task = TaskSpec(
            task_id="t1", description="demo", input_data={},
            max_script_repair_attempts=1,
            allow_tiers=(ExecutionTier.SCRIPT, ExecutionTier.LLM, ExecutionTier.SKILL, ExecutionTier.AGENT),
        )
        result = executor.run(task)

        self.assertTrue(result.ok)
        self.assertEqual(result.output, "skill-result")
        self.assertEqual(result.tier_used, ExecutionTier.SKILL)
        self.assertIsNone(result.script_version)
        self.assertEqual(pb_runner.calls, 1)
        pb_active = self.pb_repo.get_active_playbook("t1")
        self.assertEqual(pb_active.success_count, 1)

    def test_no_script_skill_succeeds_before_explore(self):
        """完全没有脚本时，有 active playbook 应优先于 explore 被尝试。"""
        self.pb_repo.save_new_version("t1", "# 步骤说明 v1", "manual")
        pb_runner = _StubPlaybookRunner(output="skill-result-2")
        runner = _FakeScriptRunner({"NEW_SCRIPT": ScriptOutcome(ok=True, output="explored-result")})
        executor = _make_executor(self.repo, runner, explorer_code="# NEW_SCRIPT",
                                   playbook_repo=self.pb_repo, playbook_runner=pb_runner)

        task = TaskSpec(
            task_id="t1", description="demo", input_data={},
            allow_tiers=(ExecutionTier.SCRIPT, ExecutionTier.LLM, ExecutionTier.SKILL, ExecutionTier.AGENT),
        )
        result = executor.run(task)

        self.assertEqual(result.tier_used, ExecutionTier.SKILL)
        self.assertEqual(result.output, "skill-result-2")
        # explore 不应该被调用（runner 只应该被 dry-run 调用 0 次）
        self.assertEqual(len(runner.calls), 0)

    def test_skill_output_fails_validator_falls_through_to_explore(self):
        """SKILL 档输出未通过 output_validator：记一次失败，继续降级。"""
        self.pb_repo.save_new_version("t1", "# 步骤说明", "manual")
        pb_runner = _StubPlaybookRunner(output="bad-output")
        runner = _FakeScriptRunner({"NEW_SCRIPT": ScriptOutcome(ok=True, output="explored-result")})
        executor = _make_executor(self.repo, runner, explorer_code="# NEW_SCRIPT",
                                   playbook_repo=self.pb_repo, playbook_runner=pb_runner)

        def validator(output):
            return (output == "explored-result"), "must equal explored-result"

        task = TaskSpec(
            task_id="t1", description="demo", input_data={},
            output_validator=validator,
            allow_tiers=(ExecutionTier.SCRIPT, ExecutionTier.LLM, ExecutionTier.SKILL, ExecutionTier.AGENT),
        )
        result = executor.run(task)

        self.assertEqual(result.tier_used, ExecutionTier.SCRIPT)
        self.assertEqual(result.output, "explored-result")
        pb_active = self.pb_repo.get_active_playbook("t1")
        self.assertEqual(pb_active.fail_count, 1)

    def test_playbook_invalid_error_retires_immediately(self):
        """PlaybookInvalidError 直接 retire，不走 consecutive_fail 计数。"""
        self.pb_repo.save_new_version("t1", "# 步骤说明", "manual")
        pb_runner = _StubPlaybookRunner(raises=PlaybookInvalidError("页面结构完全变了"))
        runner = _FakeScriptRunner({"NEW_SCRIPT": ScriptOutcome(ok=True, output="explored-result")})
        executor = _make_executor(self.repo, runner, explorer_code="# NEW_SCRIPT",
                                   playbook_repo=self.pb_repo, playbook_runner=pb_runner)

        task = TaskSpec(
            task_id="t1", description="demo", input_data={},
            allow_tiers=(ExecutionTier.SCRIPT, ExecutionTier.LLM, ExecutionTier.SKILL, ExecutionTier.AGENT),
        )
        result = executor.run(task)

        self.assertEqual(result.tier_used, ExecutionTier.SCRIPT)
        self.assertIsNone(self.pb_repo.get_active_playbook("t1"))
        versions = self.pb_repo.list_versions("t1")
        self.assertEqual(versions[0].status, "retired")

    def test_no_active_playbook_skips_skill_silently(self):
        """没有 active playbook（从未探索出过）时，SKILL 档静默跳过，不产生
        SKILL 相关 attempts，也不报错。"""
        pb_runner = _StubPlaybookRunner(output="不该被调用")
        runner = _FakeScriptRunner({"NEW_SCRIPT": ScriptOutcome(ok=True, output="explored-result")})
        executor = _make_executor(self.repo, runner, explorer_code="# NEW_SCRIPT",
                                   playbook_repo=self.pb_repo, playbook_runner=pb_runner)

        task = TaskSpec(
            task_id="t1", description="demo", input_data={},
            allow_tiers=(ExecutionTier.SCRIPT, ExecutionTier.LLM, ExecutionTier.SKILL, ExecutionTier.AGENT),
        )
        result = executor.run(task)

        self.assertEqual(result.tier_used, ExecutionTier.SCRIPT)
        self.assertEqual(pb_runner.calls, 0)
        self.assertFalse(any(a.tier == ExecutionTier.SKILL for a in result.attempts))


if __name__ == "__main__":
    unittest.main()
