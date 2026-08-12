"""tests/test_growth_advisor_pursuit_increment_llm_review.py

覆盖 growth_advisor_autonomy_deepening_plan_v2.md 方向 1：
`evaluate_cycle_increment()` 的可选 LLM 复核步骤。

  - 默认关闭（`llm_review_enabled=False` / 不传 `llm_helper`）时行为与
    改动前完全一致，不触发任何 LLM 调用。
  - 只在规则式初筛已经判定 `low_increment=True` 时才触发复核。
  - LLM 复核结果（llm_reviewed/llm_verdict/llm_reason）不覆盖规则式
    `low_increment` 判断本身。
  - `record_pursuit_cycle_signal()` 的 streak 计数只看规则式
    `low_increment`，不因为 LLM 复核结果而改变。
  - LLM 调用失败/响应解析失败时优雅降级，不影响主流程。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution import growth_advisor as ga
from mini_agent.evolution import output_workspace as ow
from mini_agent.evolution.goal_cron_bridge import reap_finished_cycles
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _write_cycle(paths, goal_id, cycle_no, covered_subtopics):
    base_dir = ow.goal_output_base_dir(paths, goal_id)
    cycle_dir = ow.allocate_cycle_dir(paths, goal_id, cycle_no)
    progress_note = (
        "本轮小结\n```handoff\n"
        + json.dumps({"covered_subtopics": covered_subtopics})
        + "\n```"
    )
    ow.write_manifest(base_dir, cycle_dir, progress_note=progress_note, status="completed")


class FakeLlmHelper:
    """固定返回一段 JSON 响应的假 llm_helper，记录被调用次数供断言。"""

    def __init__(self, response: str):
        self.response = response
        self.calls = 0

    def ask(self, prompt: str) -> str:
        self.calls += 1
        return self.response


class ExplodingLlmHelper:
    def __init__(self):
        self.calls = 0

    def ask(self, prompt: str) -> str:
        self.calls += 1
        raise RuntimeError("llm 调用失败")


class TestEvaluateCycleIncrementLlmReviewDefaultOff(unittest.TestCase):
    def test_disabled_by_default_no_llm_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_cycle(paths, "g1", 1, ["a", "b"])
            _write_cycle(paths, "g1", 2, ["a", "b"])  # 完全重复 → low_increment=True
            helper = FakeLlmHelper(json.dumps({"has_real_progress": True, "reason": "x"}))
            result = ga.evaluate_cycle_increment(paths, "g1", llm_helper=helper)
            self.assertTrue(result["low_increment"])
            self.assertFalse(result["llm_reviewed"])
            self.assertIsNone(result["llm_verdict"])
            self.assertEqual(helper.calls, 0)

    def test_enabled_but_no_helper_no_llm_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_cycle(paths, "g1", 1, ["a", "b"])
            _write_cycle(paths, "g1", 2, ["a", "b"])
            result = ga.evaluate_cycle_increment(paths, "g1", llm_review_enabled=True)
            self.assertTrue(result["low_increment"])
            self.assertFalse(result["llm_reviewed"])


class TestEvaluateCycleIncrementLlmReviewTriggering(unittest.TestCase):
    def test_only_triggers_when_rule_flags_low_increment(self):
        """规则式判定 low_increment=False 时（本轮全是新话题），即使开启
        复核也不应该触发 LLM 调用——只对被规则标记的轮次追加复核。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_cycle(paths, "g1", 1, ["a"])
            _write_cycle(paths, "g1", 2, ["b", "c", "d"])  # 全部新增，不重叠
            helper = FakeLlmHelper(json.dumps({"has_real_progress": False, "reason": "x"}))
            result = ga.evaluate_cycle_increment(
                paths, "g1", llm_helper=helper, llm_review_enabled=True,
            )
            self.assertFalse(result["low_increment"])
            self.assertFalse(result["llm_reviewed"])
            self.assertEqual(helper.calls, 0)

    def test_llm_agrees_with_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_cycle(paths, "g1", 1, ["a", "b"])
            _write_cycle(paths, "g1", 2, ["a", "b"])
            helper = FakeLlmHelper(json.dumps({"has_real_progress": False, "reason": "确实在重复"}))
            result = ga.evaluate_cycle_increment(
                paths, "g1", llm_helper=helper, llm_review_enabled=True,
            )
            self.assertTrue(result["low_increment"])
            self.assertTrue(result["llm_reviewed"])
            self.assertTrue(result["llm_verdict"])  # verdict=True 表示同意规则判断
            self.assertEqual(result["llm_reason"], "确实在重复")
            self.assertEqual(helper.calls, 1)

    def test_llm_disagrees_with_rule_does_not_override_low_increment(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_cycle(paths, "g1", 1, ["性能优化"])
            _write_cycle(paths, "g1", 2, ["性能优化"])  # 字面重复，规则判定低增量
            helper = FakeLlmHelper(json.dumps({
                "has_real_progress": True, "reason": "标题相同但深入到了具体子问题",
            }))
            result = ga.evaluate_cycle_increment(
                paths, "g1", llm_helper=helper, llm_review_enabled=True,
            )
            # 规则式判断本身不受 LLM 复核影响
            self.assertTrue(result["low_increment"])
            self.assertTrue(result["llm_reviewed"])
            self.assertFalse(result["llm_verdict"])  # verdict=False 表示不同意规则判断
            self.assertEqual(result["llm_reason"], "标题相同但深入到了具体子问题")

    def test_llm_call_failure_gracefully_degrades(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_cycle(paths, "g1", 1, ["a", "b"])
            _write_cycle(paths, "g1", 2, ["a", "b"])
            helper = ExplodingLlmHelper()
            result = ga.evaluate_cycle_increment(
                paths, "g1", llm_helper=helper, llm_review_enabled=True,
            )
            self.assertTrue(result["low_increment"])
            self.assertFalse(result["llm_reviewed"])
            self.assertIsNone(result["llm_verdict"])
            self.assertEqual(helper.calls, 1)

    def test_malformed_llm_response_gracefully_degrades(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_cycle(paths, "g1", 1, ["a", "b"])
            _write_cycle(paths, "g1", 2, ["a", "b"])
            helper = FakeLlmHelper("这不是 JSON")
            result = ga.evaluate_cycle_increment(
                paths, "g1", llm_helper=helper, llm_review_enabled=True,
            )
            self.assertTrue(result["low_increment"])
            self.assertFalse(result["llm_reviewed"])

    def test_records_llm_call_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_cycle(paths, "g1", 1, ["a", "b"])
            _write_cycle(paths, "g1", 2, ["a", "b"])
            helper = FakeLlmHelper(json.dumps({"has_real_progress": False, "reason": "x"}))
            ga.evaluate_cycle_increment(paths, "g1", llm_helper=helper, llm_review_enabled=True)
            status = ga.llm_call_status_snapshot(paths)
            self.assertIn("pursuit_increment_review", status)
            self.assertEqual(status["pursuit_increment_review"]["outcome"], "success")


class TestRecordPursuitCycleSignalDoesNotChangeStreakLogic(unittest.TestCase):
    def test_llm_verdict_does_not_affect_streak_or_saturation(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            # 即使 LLM 认为有实质推进（llm_verdict=False），streak 仍然
            # 只按传入的 low_increment 累加。
            for _ in range(3):
                signal = ga.record_pursuit_cycle_signal(
                    paths, "g1", True,
                    llm_reviewed=True, llm_verdict=False, llm_reason="LLM 认为有推进",
                )
            self.assertEqual(signal["streak"], 3)
            self.assertTrue(signal["saturated"])

            snapshot = ga.get_pursuit_saturation(paths, "g1")
            self.assertEqual(snapshot["streak"], 3)
            self.assertTrue(snapshot["saturated"])
            self.assertTrue(snapshot["llm_reviewed"])
            self.assertFalse(snapshot["llm_verdict"])
            self.assertEqual(snapshot["llm_reason"], "LLM 认为有推进")

    def test_trend_rows_include_llm_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ga.record_pursuit_cycle_signal(
                paths, "g1", True,
                llm_reviewed=True, llm_verdict=True, llm_reason="确实重复",
            )
            trend = ga.get_pursuit_saturation_trend(paths, "g1")
            self.assertEqual(len(trend), 1)
            self.assertTrue(trend[0]["llm_reviewed"])
            self.assertTrue(trend[0]["llm_verdict"])
            self.assertEqual(trend[0]["llm_reason"], "确实重复")

    def test_default_params_backward_compatible(self):
        """不传 llm_* 参数时（既有调用方的写法）行为与改动前一致。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            signal = ga.record_pursuit_cycle_signal(paths, "g1", True)
            self.assertEqual(signal["streak"], 1)
            snapshot = ga.get_pursuit_saturation(paths, "g1")
            self.assertFalse(snapshot["llm_reviewed"])
            self.assertIsNone(snapshot["llm_verdict"])
            self.assertEqual(snapshot["llm_reason"], "")


class TestProcessPursuitCycleCompletionThreadsLlmHelper(unittest.TestCase):
    def test_llm_review_disabled_in_cfg_skips_llm(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)

            class FakeGoal:
                id = "g1"
                title = "持续调研 X"
                tags = ["growth_advisor"]

            class FakeCfg:
                pursuit_increment_llm_review_enabled = False

            _write_cycle(paths, "g1", 1, ["a", "b"])
            _write_cycle(paths, "g1", 2, ["a", "b"])
            helper = FakeLlmHelper(json.dumps({"has_real_progress": False, "reason": "x"}))
            ga.process_pursuit_cycle_completion(paths, FakeGoal(), llm_helper=helper, cfg=FakeCfg())
            self.assertEqual(helper.calls, 0)

    def test_llm_review_enabled_in_cfg_triggers_llm(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)

            class FakeGoal:
                id = "g1"
                title = "持续调研 X"
                tags = ["growth_advisor"]

            class FakeCfg:
                pursuit_increment_llm_review_enabled = True

            _write_cycle(paths, "g1", 1, ["a", "b"])
            _write_cycle(paths, "g1", 2, ["a", "b"])
            helper = FakeLlmHelper(json.dumps({"has_real_progress": False, "reason": "确实重复"}))
            ga.process_pursuit_cycle_completion(paths, FakeGoal(), llm_helper=helper, cfg=FakeCfg())
            self.assertEqual(helper.calls, 1)
            snapshot = ga.get_pursuit_saturation(paths, "g1")
            self.assertTrue(snapshot["llm_reviewed"])
            self.assertTrue(snapshot["llm_verdict"])

    def test_reap_finished_cycles_accepts_llm_helper_provider_without_raising(self):
        """[集成] reap_finished_cycles() 新增的 llm_helper_provider 参数
        即使传入也不应该在没有可回收子节点时抛异常——跟改动前的既有
        用例（不传该参数）行为一致，只是多验证一下新签名可用。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.load()
            goal_backlog.add_goal(
                title="持续调研 X", description="", tags=["growth_advisor"],
            )
            reaped = reap_finished_cycles(
                goal_backlog, llm_helper_provider=lambda: FakeLlmHelper("{}"),
            )
            self.assertEqual(reaped, 0)


if __name__ == "__main__":
    unittest.main()
