"""tests/test_goal_overall_completion.py

覆盖 next_doc/goal_execution_spec_generation_plan.md §5 第二段 /
implementation_record.md 未实施清单第 5 项：`overall_completion_criteria`
驱动的一次性 Goal 整体关闭判断。

  1. GoalExecutionSpecBuilder.evaluate_overall_completion()：正常解析
     close/continue 两种判定、解析失败时保守 continue。
  2. GoalBacklog.maybe_close_goal_by_overall_criteria()：
     - 前置条件不满足时（recurring Goal / 还有子节点未终态 / 未确认规范 /
       overall_completion_criteria 为空）不触发任何 LLM 调用，返回 None。
     - 条件满足、LLM 判定 close 时：goal.status 变为 completed，追加
       progress_notes。
     - 条件满足、LLM 判定 continue 时：goal.status 保持 active，追加
       说明性 progress_notes。
  3. ObjectiveExecutor._on_objective_completed() 收尾时会调用
     _maybe_close_parent_goal()，最终把满足条件的父 Goal 关闭。
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

from mini_agent.evolution import output_workspace
from mini_agent.evolution.objective_executor import ObjectiveExecutor
from mini_agent.perception import goal_execution_spec as ges
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


def _paths(tmp) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class _FakeHelper:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[str] = []

    def ask(self, prompt, *, system="", max_retries=3, retry_policy=None,
             override_model=None, override_provider=None, override_temperature=None):
        self.calls.append(prompt)
        return self.response


def _base_cfg(tmp_path):
    from mini_agent.config.loader import load_config
    cfg = load_config(
        project_root=tmp_path, verbose=False, sandbox=True,
        auto_approve=True, model="claude-sonnet-4-6",
    )
    cfg.api_key = "sk-fake"
    return cfg


def _confirmed_spec_with_overall_criteria(goal_id: str) -> ges.GoalExecutionSpec:
    spec = ges.GoalExecutionSpec(goal_id=goal_id)
    spec.overall_completion_criteria.append(
        ges.Criterion(text="全部子任务均已产出报告", verification_method="manual_review")
    )
    ges.GoalExecutionSpecBuilder.confirm(spec)
    return spec


# ── GoalExecutionSpecBuilder.evaluate_overall_completion() ────────────────────

class TestEvaluateOverallCompletion(unittest.TestCase):
    def test_parses_close_decision(self):
        helper = _FakeHelper(json.dumps({"decision": "close", "reasoning": "标准均已满足"}))
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _base_cfg(Path(tmp))
            builder = ges.GoalExecutionSpecBuilder(cfg, llm_helper=helper)
            spec = _confirmed_spec_with_overall_criteria("goal_1")

            result = builder.evaluate_overall_completion(
                "调研 Goal", "调研某个技术方案", spec,
                children=[("第一步", "completed"), ("第二步", "completed")],
                manifests=[],
            )
        self.assertEqual(result["decision"], "close")
        self.assertIn("满足", result["reasoning"])
        self.assertEqual(len(helper.calls), 1)

    def test_parses_continue_decision(self):
        helper = _FakeHelper(json.dumps({"decision": "continue", "reasoning": "标准2未见证据"}))
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _base_cfg(Path(tmp))
            builder = ges.GoalExecutionSpecBuilder(cfg, llm_helper=helper)
            spec = _confirmed_spec_with_overall_criteria("goal_2")

            result = builder.evaluate_overall_completion(
                "调研 Goal", "", spec,
                children=[("第一步", "completed"), ("第二步", "failed")],
                manifests=[],
            )
        self.assertEqual(result["decision"], "continue")

    def test_unparseable_response_falls_back_to_continue(self):
        helper = _FakeHelper("not json at all")
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _base_cfg(Path(tmp))
            builder = ges.GoalExecutionSpecBuilder(cfg, llm_helper=helper)
            spec = _confirmed_spec_with_overall_criteria("goal_3")

            result = builder.evaluate_overall_completion(
                "Goal", "", spec, children=[("步骤", "completed")], manifests=[],
            )
        self.assertEqual(result["decision"], "continue")
        self.assertIn("判定失败", result["reasoning"])

    def test_use_agent_disabled_by_default_never_spawns_agent(self):
        """[goal_execution_spec_generation_implementation_record.md §10
        后续建议顺序第 2 条 / 未实施清单第 8 项] 默认
        `overall_completion_use_agent=False`，即便传了 `output_base_dir`
        也应该走裸 LLM 单轮路径，不构造受限 Agent——这是引入本能力前的
        既有行为，Stage 9 默认关闭，不应该悄悄改变任何既有 Goal 的实际
        判定路径。"""
        helper = _FakeHelper(json.dumps({"decision": "close", "reasoning": "标准均已满足"}))

        def _fake_spawn(**kwargs):
            raise AssertionError("默认关闭时不应该构造受限 Agent")

        with tempfile.TemporaryDirectory() as tmp:
            cfg = _base_cfg(Path(tmp))
            builder = ges.GoalExecutionSpecBuilder(cfg, llm_helper=helper)
            spec = _confirmed_spec_with_overall_criteria("goal_default_no_agent")
            import unittest.mock as mock
            with mock.patch("mini_agent.role_agents.judge_factory.spawn_judge_agent", _fake_spawn):
                result = builder.evaluate_overall_completion(
                    "Goal", "", spec, children=[("步骤", "completed")], manifests=[],
                    output_base_dir="/tmp/goal_out",
                )
        self.assertEqual(result["decision"], "close")
        self.assertEqual(len(helper.calls), 1)

    def test_use_agent_enabled_spawns_readonly_judge_and_includes_output_dir(self):
        """开启 `overall_completion_use_agent` 后走受限 Agent 路径：工具
        白名单不含 skill_list/list_workflows（只需要看该 Goal 自己的产出
        目录），prompt 里带上 `output_base_dir`。"""
        import unittest.mock as mock

        class _FakeJudgeResult:
            def __init__(self, ok=True, raw_output="", error=None):
                self.ok = ok
                self.raw_output = raw_output
                self.error = error

        captured = {}

        def _fake_spawn(**kwargs):
            captured["kwargs"] = kwargs
            return object()

        def _fake_run_turn(agent, prompt, *, failure_role_label):
            captured["prompt"] = prompt
            return _FakeJudgeResult(ok=True, raw_output=json.dumps(
                {"decision": "close", "reasoning": "已打开报告文件确认表格存在"}
            ))

        with tempfile.TemporaryDirectory() as tmp:
            cfg = _base_cfg(Path(tmp))
            cfg.goal_execution_spec.overall_completion_use_agent = True
            builder = ges.GoalExecutionSpecBuilder(cfg)
            spec = _confirmed_spec_with_overall_criteria("goal_agent_judge")

            with mock.patch("mini_agent.role_agents.judge_factory.spawn_judge_agent", _fake_spawn), \
                 mock.patch("mini_agent.role_agents.judge_factory.run_judge_turn", _fake_run_turn):
                result = builder.evaluate_overall_completion(
                    "周报 Goal", "", spec, children=[("步骤", "completed")], manifests=[],
                    output_base_dir="/tmp/goal_out_dir",
                )

        self.assertEqual(result["decision"], "close")
        self.assertIn("表格", result["reasoning"])
        kwargs = captured["kwargs"]
        self.assertTrue(kwargs["tools_enabled"])
        self.assertIn("read_file", kwargs["allowed_tools"])
        self.assertNotIn("skill_list", kwargs["allowed_tools"])
        self.assertIn("/tmp/goal_out_dir", captured["prompt"])

    def test_use_agent_enabled_without_output_dir_omits_dir_hint(self):
        """开启 agent 路径但调用方没传 `output_base_dir`（旧调用方兼容）
        时，prompt 里不应该出现空路径提示文字。"""
        import unittest.mock as mock

        class _FakeJudgeResult:
            def __init__(self, ok=True, raw_output="", error=None):
                self.ok = ok
                self.raw_output = raw_output
                self.error = error

        captured = {}

        def _fake_spawn(**kwargs):
            return object()

        def _fake_run_turn(agent, prompt, *, failure_role_label):
            captured["prompt"] = prompt
            return _FakeJudgeResult(ok=True, raw_output=json.dumps(
                {"decision": "continue", "reasoning": "还差一条标准"}
            ))

        with tempfile.TemporaryDirectory() as tmp:
            cfg = _base_cfg(Path(tmp))
            cfg.goal_execution_spec.overall_completion_use_agent = True
            builder = ges.GoalExecutionSpecBuilder(cfg)
            spec = _confirmed_spec_with_overall_criteria("goal_agent_judge_no_dir")

            with mock.patch("mini_agent.role_agents.judge_factory.spawn_judge_agent", _fake_spawn), \
                 mock.patch("mini_agent.role_agents.judge_factory.run_judge_turn", _fake_run_turn):
                builder.evaluate_overall_completion(
                    "Goal", "", spec, children=[("步骤", "completed")], manifests=[],
                )

        self.assertNotIn("该 Goal 的产出目录", captured["prompt"])

    def test_use_agent_spawn_failure_falls_back_to_continue(self):
        """受限 Agent 构造失败时，与 build_draft 侧一致：`last_error` 记录
        原因，`evaluate_overall_completion` 保守返回 continue。"""
        import unittest.mock as mock

        def _fake_spawn(**kwargs):
            raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as tmp:
            cfg = _base_cfg(Path(tmp))
            cfg.goal_execution_spec.overall_completion_use_agent = True
            builder = ges.GoalExecutionSpecBuilder(cfg)
            spec = _confirmed_spec_with_overall_criteria("goal_agent_judge_fail")

            with mock.patch("mini_agent.role_agents.judge_factory.spawn_judge_agent", _fake_spawn):
                result = builder.evaluate_overall_completion(
                    "Goal", "", spec, children=[("步骤", "completed")], manifests=[],
                    output_base_dir="/tmp/x",
                )

        self.assertEqual(result["decision"], "continue")
        self.assertIn("构造受限 Agent 失败", builder.last_error)


# ── GoalBacklog.maybe_close_goal_by_overall_criteria() ─────────────────────────

class TestMaybeCloseGoalByOverallCriteria(unittest.TestCase):
    def test_returns_none_when_not_recurring_flag_but_recurring_goal(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            backlog = GoalBacklog(paths)
            goal = backlog.add_goal(title="周期性目标", description="")
            backlog.set_recurrence(goal.id, True, "job_1")
            backlog.add_objectives_for_goal(goal.id, ["第一步"])

            outcome = backlog.maybe_close_goal_by_overall_criteria(goal.id)
        self.assertIsNone(outcome)

    def test_returns_none_when_children_not_all_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            backlog = GoalBacklog(paths)
            goal = backlog.add_goal(title="一次性目标", description="做点事")
            ges.save_spec(paths, goal.id, _confirmed_spec_with_overall_criteria(goal.id))
            backlog.update_fields(goal.id, execution_spec_confirmed=True)
            backlog.add_objectives_for_goal(goal.id, ["第一步", "第二步"])

            outcome = backlog.maybe_close_goal_by_overall_criteria(goal.id)
        self.assertIsNone(outcome)

    def test_returns_none_when_spec_not_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            backlog = GoalBacklog(paths)
            goal = backlog.add_goal(title="一次性目标", description="做点事")
            [obj] = backlog.add_objectives_for_goal(goal.id, ["第一步"])
            backlog.set_status(obj.id, "completed")

            outcome = backlog.maybe_close_goal_by_overall_criteria(goal.id)
        self.assertIsNone(outcome)

    def test_returns_none_when_overall_criteria_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            backlog = GoalBacklog(paths)
            goal = backlog.add_goal(title="一次性目标", description="做点事")
            spec = ges.GoalExecutionSpec(goal_id=goal.id)
            spec.deliverables.append(ges.Deliverable(name="notes.md"))
            ges.GoalExecutionSpecBuilder.confirm(spec)
            ges.save_spec(paths, goal.id, spec)
            backlog.update_fields(goal.id, execution_spec_confirmed=True)
            [obj] = backlog.add_objectives_for_goal(goal.id, ["第一步"])
            backlog.set_status(obj.id, "completed")

            outcome = backlog.maybe_close_goal_by_overall_criteria(goal.id)
        self.assertIsNone(outcome)

    def test_closes_goal_when_llm_decides_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            backlog = GoalBacklog(paths)
            goal = backlog.add_goal(title="一次性目标", description="做点事")
            ges.save_spec(paths, goal.id, _confirmed_spec_with_overall_criteria(goal.id))
            backlog.update_fields(goal.id, execution_spec_confirmed=True)
            [obj] = backlog.add_objectives_for_goal(goal.id, ["第一步"])
            backlog.set_status(obj.id, "completed")

            helper = _FakeHelper(json.dumps({"decision": "close", "reasoning": "全部标准已满足"}))
            cfg = _base_cfg(Path(tmp))

            import mini_agent.perception.goal_execution_spec as ges_mod
            orig_builder_cls = ges_mod.GoalExecutionSpecBuilder

            class _PatchedBuilder(orig_builder_cls):
                def __init__(self, cfg, llm_helper=None, mode=None):
                    super().__init__(cfg, llm_helper=helper, mode=mode)

            ges_mod.GoalExecutionSpecBuilder = _PatchedBuilder
            try:
                outcome = backlog.maybe_close_goal_by_overall_criteria(goal.id, cfg)
            finally:
                ges_mod.GoalExecutionSpecBuilder = orig_builder_cls

            self.assertEqual(outcome, "closed")
            reloaded = backlog.get(goal.id)
            self.assertEqual(reloaded.status, "completed")
            self.assertIn("整体完成判定", reloaded.progress_notes)

    def test_keeps_goal_open_when_llm_decides_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            backlog = GoalBacklog(paths)
            goal = backlog.add_goal(title="一次性目标", description="做点事")
            ges.save_spec(paths, goal.id, _confirmed_spec_with_overall_criteria(goal.id))
            backlog.update_fields(goal.id, execution_spec_confirmed=True)
            [obj] = backlog.add_objectives_for_goal(goal.id, ["第一步"])
            backlog.set_status(obj.id, "completed")

            helper = _FakeHelper(json.dumps({"decision": "continue", "reasoning": "证据不足"}))
            cfg = _base_cfg(Path(tmp))

            import mini_agent.perception.goal_execution_spec as ges_mod
            orig_builder_cls = ges_mod.GoalExecutionSpecBuilder

            class _PatchedBuilder(orig_builder_cls):
                def __init__(self, cfg, llm_helper=None, mode=None):
                    super().__init__(cfg, llm_helper=helper, mode=mode)

            ges_mod.GoalExecutionSpecBuilder = _PatchedBuilder
            try:
                outcome = backlog.maybe_close_goal_by_overall_criteria(goal.id, cfg)
            finally:
                ges_mod.GoalExecutionSpecBuilder = orig_builder_cls

            self.assertEqual(outcome, "kept_open")
            reloaded = backlog.get(goal.id)
            self.assertEqual(reloaded.status, "active")
            self.assertIn("暂不关闭", reloaded.progress_notes)

    def test_persists_last_check_snapshot_on_close(self):
        """[implementation_record.md §11 后续建议顺序第 1 条] 判定后
        `GoalNode.overall_completion_last_check` 应写入本次结果快照。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            backlog = GoalBacklog(paths)
            goal = backlog.add_goal(title="一次性目标", description="做点事")
            ges.save_spec(paths, goal.id, _confirmed_spec_with_overall_criteria(goal.id))
            backlog.update_fields(goal.id, execution_spec_confirmed=True)
            [obj] = backlog.add_objectives_for_goal(goal.id, ["第一步"])
            backlog.set_status(obj.id, "completed")

            helper = _FakeHelper(json.dumps({"decision": "close", "reasoning": "全部标准已满足"}))
            cfg = _base_cfg(Path(tmp))

            import mini_agent.perception.goal_execution_spec as ges_mod
            orig_builder_cls = ges_mod.GoalExecutionSpecBuilder

            class _PatchedBuilder(orig_builder_cls):
                def __init__(self, cfg, llm_helper=None, mode=None):
                    super().__init__(cfg, llm_helper=helper, mode=mode)

            ges_mod.GoalExecutionSpecBuilder = _PatchedBuilder
            try:
                backlog.maybe_close_goal_by_overall_criteria(goal.id, cfg)
            finally:
                ges_mod.GoalExecutionSpecBuilder = orig_builder_cls

            reloaded = backlog.get(goal.id)
            last_check = reloaded.overall_completion_last_check
            self.assertIsNotNone(last_check)
            self.assertEqual(last_check["outcome"], "closed")
            self.assertEqual(last_check["used_agent"], False)
            self.assertIn("全部标准已满足", last_check["reasoning"])
            self.assertGreater(last_check["at"], 0)

    def test_use_agent_override_forwarded_and_persisted(self):
        """[implementation_record.md §11 后续建议顺序第 2 条] `use_agent=True`
        单次覆盖时应透传给 `evaluate_overall_completion(use_agent_override=
        True)`，即便配置文件里 `overall_completion_use_agent=False`；构造
        受限 Agent 失败时按既有兜底保守判定为 continue，但
        `overall_completion_last_check.used_agent` 仍应记为 `True`（代表
        "这次尝试走的是 agent 路径"，与是否成功无关）。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            backlog = GoalBacklog(paths)
            goal = backlog.add_goal(title="一次性目标", description="做点事")
            ges.save_spec(paths, goal.id, _confirmed_spec_with_overall_criteria(goal.id))
            backlog.update_fields(goal.id, execution_spec_confirmed=True)
            [obj] = backlog.add_objectives_for_goal(goal.id, ["第一步"])
            backlog.set_status(obj.id, "completed")

            cfg = _base_cfg(Path(tmp))
            self.assertFalse(getattr(cfg.goal_execution_spec, "overall_completion_use_agent", False))

            import mini_agent.perception.goal_execution_spec as ges_mod

            def _fail_spawn(*a, **k):
                raise RuntimeError("spawn failed (test)")

            with unittest.mock.patch(
                "mini_agent.role_agents.judge_factory.spawn_judge_agent", side_effect=_fail_spawn,
            ):
                outcome = backlog.maybe_close_goal_by_overall_criteria(goal.id, cfg, use_agent=True)

            self.assertEqual(outcome, "kept_open")
            reloaded = backlog.get(goal.id)
            last_check = reloaded.overall_completion_last_check
            self.assertIsNotNone(last_check)
            self.assertTrue(last_check["used_agent"])


# ── output_workspace.read_all_manifests() ──────────────────────────────────────

class TestReadAllManifests(unittest.TestCase):
    def test_reads_manifests_from_all_run_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            base_dir = output_workspace.goal_output_base_dir(paths, "goal_x")
            d1 = output_workspace.allocate_objective_dir(paths, "goal_x", 1)
            d2 = output_workspace.allocate_objective_dir(paths, "goal_x", 2)
            output_workspace.write_manifest(
                base_dir, d1, task_summary="第一步", started_at=time.time(),
                finished_at=time.time(), status="completed", artifacts=[], progress_note="",
            )
            output_workspace.write_manifest(
                base_dir, d2, task_summary="第二步", started_at=time.time(),
                finished_at=time.time(), status="completed", artifacts=[], progress_note="",
            )

            manifests = output_workspace.read_all_manifests(base_dir)
        self.assertEqual(len(manifests), 2)
        summaries = [m.get("task_summary") for m in manifests]
        self.assertEqual(summaries, ["第一步", "第二步"])

    def test_missing_dir_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            base_dir = output_workspace.goal_output_base_dir(paths, "no_such_goal")
            self.assertEqual(output_workspace.read_all_manifests(base_dir), [])


# ── ObjectiveExecutor 端到端集成 ────────────────────────────────────────────────

class _FakeSubmitter:
    def __init__(self):
        self._n = 0

    def __call__(self, message: str, initiator: str, meta: dict):
        self._n += 1
        return f"turn_{self._n}"


class TestObjectiveExecutorClosesParentGoal(unittest.TestCase):
    """ObjectiveExecutor._on_objective_completed() 收尾时调用
    _maybe_close_parent_goal()，最终把满足条件的一次性父 Goal 关闭。"""

    def test_last_objective_completed_closes_goal_when_llm_says_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            backlog = GoalBacklog(paths)
            goal = backlog.add_goal(title="一次性目标", description="做一件事")
            ges.save_spec(paths, goal.id, _confirmed_spec_with_overall_criteria(goal.id))
            backlog.update_fields(goal.id, execution_spec_confirmed=True)
            [obj] = backlog.add_objectives_for_goal(goal.id, ["唯一的一步"])

            cfg = _base_cfg(Path(tmp))
            helper = _FakeHelper(json.dumps({"decision": "close", "reasoning": "标准已满足"}))

            import mini_agent.perception.goal_execution_spec as ges_mod
            orig_builder_cls = ges_mod.GoalExecutionSpecBuilder

            class _PatchedBuilder(orig_builder_cls):
                def __init__(self, cfg, llm_helper=None, mode=None):
                    super().__init__(cfg, llm_helper=helper, mode=mode)

            ges_mod.GoalExecutionSpecBuilder = _PatchedBuilder
            try:
                oe = ObjectiveExecutor(
                    paths=paths,
                    submit_fn=_FakeSubmitter(),
                    llm_decompose_fn=lambda o: [f"{o.title} - 单步"],
                    goal_backlog=backlog,
                    cfg=cfg,
                )
                exec_id = oe.start(obj)
                ex = oe.get_execution(exec_id)
                turn_id = ex.current_step.turn_id
                oe.on_turn_done(turn_id, "完成了唯一的一步。")
            finally:
                ges_mod.GoalExecutionSpecBuilder = orig_builder_cls

            self.assertEqual(oe.get_execution(exec_id).status, "completed")
            reloaded_goal = backlog.get(goal.id)
            self.assertEqual(reloaded_goal.status, "completed")


if __name__ == "__main__":
    unittest.main()
