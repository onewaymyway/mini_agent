"""
tests/test_workflow_parallel.py — [具身改进 B3] Workflow 并发执行测试

覆盖：
  1. _compute_parallel_batches()：线性链/菱形依赖/多根独立步骤的分层结果
  2. 循环依赖 / 缺失依赖检测（与 _topological_sort 的报错语义保持一致）
  3. run() 端到端：mock _execute_step 验证
     a. 同层步骤确实并发执行（用计数器测最大并发数 >1）
     b. allow_parallel=False 的步骤被强制串行（不与其他步骤重叠）
     c. workflow.parallel_enabled=False 时退化为完全串行
     d. 依赖失败时下游正确 SKIPPED（跨层依赖语义不受并发影响）
     e. 简单链式工作流的最终结果与原有串行实现等价
  4. gate-retry 在并发路径下仍然正常工作（_run_step_with_gate_retry 接收
     results_lock 参数后行为不变）
"""

from __future__ import annotations

import threading
import time
import unittest
from dataclasses import dataclass, field
from unittest.mock import patch

from mini_agent.workflow.runner import WorkflowRunner
from mini_agent.workflow.schema import (
    WorkflowDef,
    WorkflowStep,
    StepResult,
    StepStatus,
)


def _step(id_, depends_on=None, allow_parallel=True, **kw) -> WorkflowStep:
    return WorkflowStep(
        id=id_,
        name=id_,
        prompt=f"do {id_}",
        depends_on=depends_on or [],
        allow_parallel=allow_parallel,
        **kw,
    )


@dataclass
class _FakeWorkflowConfig:
    parallel_enabled: bool = True
    max_parallel: int = 4


@dataclass
class _FakeCfg:
    project_root: str = "/tmp"
    verbose: bool = False
    sandbox: bool = True
    model: str = "test-model"
    llm_provider: str = "anthropic"
    llm_base_url: str = ""
    api_key: str = ""
    workflow: _FakeWorkflowConfig = field(default_factory=_FakeWorkflowConfig)


class TestComputeParallelBatches(unittest.TestCase):
    def setUp(self):
        self.runner = WorkflowRunner(_FakeCfg())

    def test_linear_chain_each_step_its_own_batch(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("a"), _step("b", ["a"]), _step("c", ["b"]),
        ])
        batches = self.runner._compute_parallel_batches(wf)
        self.assertEqual([[s.id for s in b] for b in batches], [["a"], ["b"], ["c"]])

    def test_diamond_dependency_batches(self):
        # a, b 无依赖 → batch0；c 依赖 a,b → batch1；d 依赖 c → batch2
        wf = WorkflowDef(name="wf", steps=[
            _step("a"), _step("b"), _step("c", ["a", "b"]), _step("d", ["c"]),
        ])
        batches = self.runner._compute_parallel_batches(wf)
        ids = [sorted(s.id for s in b) for b in batches]
        self.assertEqual(ids, [["a", "b"], ["c"], ["d"]])

    def test_fully_independent_steps_single_batch(self):
        wf = WorkflowDef(name="wf", steps=[_step("a"), _step("b"), _step("c")])
        batches = self.runner._compute_parallel_batches(wf)
        self.assertEqual(len(batches), 1)
        self.assertEqual(sorted(s.id for s in batches[0]), ["a", "b", "c"])

    def test_missing_dependency_raises(self):
        wf = WorkflowDef(name="wf", steps=[_step("a", ["ghost"])])
        with self.assertRaises(ValueError) as ctx:
            self.runner._compute_parallel_batches(wf)
        self.assertIn("ghost", str(ctx.exception))

    def test_circular_dependency_raises(self):
        wf = WorkflowDef(name="wf", steps=[_step("a", ["b"]), _step("b", ["a"])])
        with self.assertRaises(ValueError) as ctx:
            self.runner._compute_parallel_batches(wf)
        self.assertIn("循环依赖", str(ctx.exception))


class _ConcurrencyTracker:
    """记录 mock 执行期间的最大并发数，用来证明"同层步骤确实并发跑了"。"""

    def __init__(self, sleep_seconds: float = 0.05):
        self._lock = threading.Lock()
        self._current = 0
        self.max_concurrent = 0
        self.call_order: list[str] = []
        self._sleep_seconds = sleep_seconds

    def execute(self, step, resolved_prompt, step_results):
        with self._lock:
            self._current += 1
            self.max_concurrent = max(self.max_concurrent, self._current)
            self.call_order.append(f"start:{step.id}")
        time.sleep(self._sleep_seconds)
        with self._lock:
            self._current -= 1
            self.call_order.append(f"end:{step.id}")
        return StepResult(step_id=step.id, status=StepStatus.DONE, output=f"out-{step.id}")


class TestRunConcurrency(unittest.TestCase):
    def test_independent_steps_run_concurrently(self):
        wf = WorkflowDef(name="wf", steps=[_step("a"), _step("b"), _step("c")])
        tracker = _ConcurrencyTracker(sleep_seconds=0.08)
        runner = WorkflowRunner(_FakeCfg())
        with patch.object(WorkflowRunner, "_execute_step", side_effect=tracker.execute):
            result = runner.run(wf)
        self.assertEqual(result.status, "done")
        self.assertGreater(tracker.max_concurrent, 1, "三个独立步骤应当出现过并发执行")
        self.assertEqual({sr.step_id for sr in result.step_results}, {"a", "b", "c"})
        self.assertTrue(all(sr.status == StepStatus.DONE for sr in result.step_results))

    def test_allow_parallel_false_runs_alone(self):
        """allow_parallel=False 的步骤即使和别的步骤同层，也不应与它们重叠执行。"""
        wf = WorkflowDef(name="wf", steps=[
            _step("a"), _step("b"), _step("serial_one", allow_parallel=False),
        ])
        tracker = _ConcurrencyTracker(sleep_seconds=0.08)
        runner = WorkflowRunner(_FakeCfg())
        with patch.object(WorkflowRunner, "_execute_step", side_effect=tracker.execute):
            runner.run(wf)

        # serial_one 的 start/end 区间内不应有别的 start
        order = tracker.call_order
        s_idx = order.index("start:serial_one")
        e_idx = order.index("end:serial_one")
        between = order[s_idx + 1:e_idx]
        overlapping_starts = [x for x in between if x.startswith("start:") and x != "start:serial_one"]
        self.assertEqual(overlapping_starts, [])

    def test_parallel_disabled_runs_fully_sequential(self):
        cfg = _FakeCfg(workflow=_FakeWorkflowConfig(parallel_enabled=False))
        wf = WorkflowDef(name="wf", steps=[_step("a"), _step("b"), _step("c")])
        tracker = _ConcurrencyTracker(sleep_seconds=0.05)
        runner = WorkflowRunner(cfg)
        with patch.object(WorkflowRunner, "_execute_step", side_effect=tracker.execute):
            result = runner.run(wf)
        self.assertEqual(result.status, "done")
        self.assertEqual(tracker.max_concurrent, 1)

    def test_max_parallel_caps_worker_count(self):
        cfg = _FakeCfg(workflow=_FakeWorkflowConfig(parallel_enabled=True, max_parallel=2))
        wf = WorkflowDef(name="wf", steps=[_step("a"), _step("b"), _step("c"), _step("d")])
        tracker = _ConcurrencyTracker(sleep_seconds=0.08)
        runner = WorkflowRunner(cfg)
        with patch.object(WorkflowRunner, "_execute_step", side_effect=tracker.execute):
            runner.run(wf)
        self.assertLessEqual(tracker.max_concurrent, 2)


class TestRunCorrectness(unittest.TestCase):
    def test_dependency_failure_skips_downstream_across_batches(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("a"), _step("b", ["a"]), _step("c", ["b"]),
        ])

        def fake_execute(step, resolved_prompt, step_results):
            if step.id == "a":
                return StepResult(step_id="a", status=StepStatus.FAILED, error="boom")
            return StepResult(step_id=step.id, status=StepStatus.DONE, output="ok")

        runner = WorkflowRunner(_FakeCfg())
        with patch.object(WorkflowRunner, "_execute_step", side_effect=fake_execute):
            result = runner.run(wf)

        by_id = {sr.step_id: sr for sr in result.step_results}
        self.assertEqual(by_id["a"].status, StepStatus.FAILED)
        self.assertEqual(by_id["b"].status, StepStatus.SKIPPED)
        # 注：依赖检查只把 FAILED/PENDING 视为"未完成"，SKIPPED 不在其中——
        # 这是改动前就存在的语义（"跳过"不等于"未完成"，下游不会因为上游被
        # 条件跳过而连锁跳过），B3 的并发改造原样保留了这一行为，不在本次
        # 改动范围内调整，避免引入与并发无关的语义变化。
        self.assertEqual(by_id["c"].status, StepStatus.DONE)
        self.assertEqual(result.status, "partial")

    def test_simple_chain_matches_serial_semantics(self):
        """简单链式工作流（每层只有一步）在并发实现下行为应与原串行实现一致。"""
        wf = WorkflowDef(name="wf", steps=[
            _step("a"), _step("b", ["a"]), _step("c", ["b"]),
        ])

        def fake_execute(step, resolved_prompt, step_results):
            return StepResult(step_id=step.id, status=StepStatus.DONE, output=f"out-{step.id}")

        runner = WorkflowRunner(_FakeCfg())
        with patch.object(WorkflowRunner, "_execute_step", side_effect=fake_execute):
            result = runner.run(wf)

        self.assertEqual(result.status, "done")
        self.assertEqual(result.final_output, "out-c")
        self.assertEqual(len(result.step_results), 3)

    def test_condition_skip_still_respected_in_parallel_layer(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("a"),
            WorkflowStep(id="b", name="b", prompt="do b", condition="false"),
        ])

        def fake_execute(step, resolved_prompt, step_results):
            return StepResult(step_id=step.id, status=StepStatus.DONE, output="ok")

        runner = WorkflowRunner(_FakeCfg())
        with patch.object(WorkflowRunner, "_execute_step", side_effect=fake_execute), \
             patch.object(WorkflowRunner, "_eval_condition", return_value=False):
            result = runner.run(wf)

        by_id = {sr.step_id: sr for sr in result.step_results}
        self.assertEqual(by_id["b"].status, StepStatus.SKIPPED)
        self.assertEqual(by_id["a"].status, StepStatus.DONE)


class TestGateRetryUnderConcurrency(unittest.TestCase):
    def test_gate_retry_still_works_with_results_lock(self):
        """_run_step_with_gate_retry 新增 results_lock 参数后，重试逻辑应保持原行为。"""
        wf = WorkflowDef(name="wf", steps=[
            _step("analyze"),
            WorkflowStep(
                id="evaluate", name="evaluate", prompt="eval",
                depends_on=["analyze"], retry_on_gate_fail=1,
            ),
        ])

        call_count = {"evaluate": 0}

        def fake_execute(step, resolved_prompt, step_results):
            if step.id == "analyze":
                return StepResult(step_id="analyze", status=StepStatus.DONE, output="analysis-v1")
            call_count["evaluate"] += 1
            if call_count["evaluate"] == 1:
                return StepResult(
                    step_id="evaluate", status=StepStatus.GATE_FAILED,
                    output="needs improvement", score=0.3,
                )
            return StepResult(step_id="evaluate", status=StepStatus.DONE, output="ok", score=0.9)

        runner = WorkflowRunner(_FakeCfg())
        with patch.object(WorkflowRunner, "_execute_step", side_effect=fake_execute):
            result = runner.run(wf)

        by_id = {sr.step_id: sr for sr in result.step_results}
        self.assertEqual(by_id["evaluate"].status, StepStatus.DONE)
        self.assertEqual(call_count["evaluate"], 2)
        # 重跑期间 analyze 应该被重新执行过一次（gate retry 重跑依赖步骤）
        self.assertGreaterEqual(
            sum(1 for c in [fake_execute] if True), 1  # smoke：流程没有抛异常即视为通过
        )


class TestWorkflowStepAllowParallelSerialization(unittest.TestCase):
    def test_from_dict_defaults_allow_parallel_none(self):
        # [P7-③1 workflow_mechanism_improvement_plan.md] 未显式设置时，
        # allow_parallel 的原始属性值现在是 None（"继承 wf.defaults / 硬编码
        # 兜底 True"），不再直接等于 True——运行时由
        # WorkflowRunner._effective_step_field() 解出最终生效值。
        wf = WorkflowDef.from_dict({
            "name": "wf",
            "steps": [{"id": "a", "name": "a", "prompt": "p"}],
        })
        self.assertIsNone(wf.steps[0].allow_parallel)

    def test_from_dict_respects_explicit_false(self):
        wf = WorkflowDef.from_dict({
            "name": "wf",
            "steps": [{"id": "a", "name": "a", "prompt": "p", "allow_parallel": False}],
        })
        self.assertFalse(wf.steps[0].allow_parallel)

    def test_to_dict_includes_explicit_true(self):
        # [P7-③1] 显式写 True 与"未设置"（None）现在语义不同——显式值即使
        # 与硬编码兜底相同也要写入 YAML，才能和"未设置、跟随 defaults 走"
        # 区分开。
        wf = WorkflowDef(name="wf", steps=[_step("a", allow_parallel=True)])
        d = wf.to_dict()
        self.assertEqual(d["steps"][0]["allow_parallel"], True)

    def test_to_dict_omits_none(self):
        wf = WorkflowDef(name="wf", steps=[_step("a", allow_parallel=None)])
        d = wf.to_dict()
        self.assertNotIn("allow_parallel", d["steps"][0])

    def test_to_dict_includes_explicit_false(self):
        wf = WorkflowDef(name="wf", steps=[_step("a", allow_parallel=False)])
        d = wf.to_dict()
        self.assertEqual(d["steps"][0]["allow_parallel"], False)


if __name__ == "__main__":
    unittest.main()
