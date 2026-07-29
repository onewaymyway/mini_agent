"""tests/test_external_context_prompt_injection.py — P6 精确 prompt 注入测试

覆盖 watchlist_notification_goal_design.md §4.5：
  1. `_default_llm_decompose` 在 objective.external_context 非空时，把
     最近 N 条外部信息追加进 prompt；为空时 prompt 与升级前完全一致
     （不额外插入空标题）。
  2. `_default_llm_redecompose` 通过新增的 external_context 关键字参数
     接收外部信息并注入 prompt；未传参数（旧调用方）时行为不变。
  3. `ObjectiveExecutor._attempt_redecompose` 会从 goal_backlog 里读取
     *这一个* objective 自己的 external_context 并透传，找不到
     goal_backlog/节点时优雅退化为空列表，不影响原有行为。
  4. 精确注入：不会把其它 Goal/Objective 的 external_context 混进来。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution.objective_executor import (
    ObjectiveExecutor,
    ObjectiveExecution,
    ExecutionStep,
    _default_llm_decompose,
    _default_llm_redecompose,
    _format_external_context,
)
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


class _FakeObjective:
    def __init__(self, title, progress_notes="", external_context=None):
        self.title = title
        self.progress_notes = progress_notes
        self.external_context = external_context or []


class _FakeLLMHelper:
    def __init__(self):
        self.last_prompt = None

    def ask(self, prompt):
        self.last_prompt = prompt
        return "1. 步骤一\n2. 步骤二"


class TestFormatExternalContext(unittest.TestCase):
    def test_empty_returns_empty_string(self):
        obj = _FakeObjective("t")
        self.assertEqual(_format_external_context(obj), "")

    def test_non_empty_includes_title_and_snippet(self):
        obj = _FakeObjective("t", external_context=[
            {"title": "竞品发布", "snippet": "详情xxx", "occurred_at": 0},
        ])
        text = _format_external_context(obj)
        self.assertIn("竞品发布", text)
        self.assertIn("详情xxx", text)

    def test_only_keeps_most_recent_n(self):
        items = [{"title": f"t{i}", "snippet": ""} for i in range(10)]
        obj = _FakeObjective("t", external_context=items)
        text = _format_external_context(obj, max_items=3)
        self.assertNotIn("t0", text)
        self.assertIn("t9", text)


class TestDecomposePromptInjection(unittest.TestCase):
    def test_prompt_unchanged_when_no_external_context(self):
        helper = _FakeLLMHelper()
        obj = _FakeObjective("目标A", progress_notes="做了一半")
        _default_llm_decompose(helper, obj)
        self.assertNotIn("相关外部信息", helper.last_prompt)

    def test_prompt_includes_external_context_when_present(self):
        helper = _FakeLLMHelper()
        obj = _FakeObjective("目标A", progress_notes="做了一半", external_context=[
            {"title": "外部事件X", "snippet": "摘要Y", "occurred_at": 0},
        ])
        _default_llm_decompose(helper, obj)
        self.assertIn("相关外部信息", helper.last_prompt)
        self.assertIn("外部事件X", helper.last_prompt)
        self.assertIn("摘要Y", helper.last_prompt)


class TestRedecomposePromptInjection(unittest.TestCase):
    def test_backward_compatible_without_external_context_kwarg(self):
        helper = _FakeLLMHelper()
        result = _default_llm_redecompose(helper, "目标A", [], ["步骤1"], "失败原因")
        self.assertIsInstance(result, list)
        self.assertNotIn("相关外部信息", helper.last_prompt)

    def test_includes_external_context_when_passed(self):
        helper = _FakeLLMHelper()
        _default_llm_redecompose(
            helper, "目标A", [], ["步骤1"], "失败原因",
            external_context=[{"title": "外部事件Z", "snippet": "摘要W"}],
        )
        self.assertIn("相关外部信息", helper.last_prompt)
        self.assertIn("外部事件Z", helper.last_prompt)


class TestAttemptRedecomposePassesOwnExternalContextOnly(unittest.TestCase):
    def test_passes_only_this_objective_external_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            backlog = GoalBacklog(paths)
            goal = backlog.add_goal("父目标")
            obj_a = backlog.add_objective("子目标A", parent_id=goal.id)
            obj_b = backlog.add_objective("子目标B", parent_id=goal.id)
            backlog.attach_external_context(obj_a.id, {"title": "只属于A的信息", "snippet": ""})
            backlog.attach_external_context(obj_b.id, {"title": "只属于B的信息", "snippet": ""})

            captured = {}

            def _redecompose_fn(objective_title, completed_summaries, remaining_descs, failure_reason, external_context=None):
                captured["external_context"] = external_context
                return ["新步骤1", "新步骤2"]

            executor = ObjectiveExecutor(
                paths, submit_fn=lambda *a, **k: "turn_1",
                llm_redecompose_fn=_redecompose_fn, goal_backlog=backlog,
            )
            ex = ObjectiveExecution(
                execution_id="exec_1", objective_id=obj_a.id, objective_title="子目标A",
                steps=[ExecutionStep(step_id="s0", step_index=0, description="step0", result_summary="done")],
            )
            executor._executions["exec_1"] = ex

            executor._attempt_redecompose(ex, 0, "失败原因")

            self.assertEqual(len(captured["external_context"]), 1)
            self.assertEqual(captured["external_context"][0]["title"], "只属于A的信息")

    def test_missing_goal_backlog_degrades_to_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            captured = {}

            def _redecompose_fn(objective_title, completed_summaries, remaining_descs, failure_reason, external_context=None):
                captured["external_context"] = external_context
                return ["新步骤1"]

            executor = ObjectiveExecutor(
                paths, submit_fn=lambda *a, **k: "turn_1",
                llm_redecompose_fn=_redecompose_fn, goal_backlog=None,
            )
            ex = ObjectiveExecution(
                execution_id="exec_2", objective_id="obj_missing", objective_title="X",
                steps=[ExecutionStep(step_id="s0", step_index=0, description="step0", result_summary="done")],
            )
            executor._executions["exec_2"] = ex
            executor._attempt_redecompose(ex, 0, "失败原因")
            self.assertEqual(captured["external_context"], [])


if __name__ == "__main__":
    unittest.main()
