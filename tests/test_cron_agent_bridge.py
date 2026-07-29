"""
tests/test_cron_agent_bridge.py — cron_agent_bridge.make_submit_step_fn()

对应实施记录「剩余工作 #3」：cron_agent_bridge.build_cron_agent() 依赖真实
Agent/LLM client 构造，不在这里覆盖（仍建议用 mock LLM client 或真实
daemon 冒烟验证）；但 make_submit_step_fn() 只是对一个已构建好的 Agent 的
纯逻辑包装（[CRON_DONE]/[CRON_CONTINUE] 标记解析 + _last_turn_hit_max_turns
兜底判断），不依赖真实 LLM/网络，可以用一个最小假 Agent 完整覆盖。
"""

from __future__ import annotations

import pytest

from mini_agent.evolution.cron_agent_bridge import make_submit_step_fn
from mini_agent.evolution.cron_job_executor import StepResult


class _FakeAgent:
    """最小化的 Agent 替身：只实现 run_turn() 和
    _last_turn_hit_max_turns 属性，用于隔离测试 make_submit_step_fn()
    的完成判定逻辑，不触碰真实 LLM/网络。"""

    def __init__(self, response_text: str = "", hit_max_turns: bool = False,
                 raise_error: Exception | None = None):
        self._response_text = response_text
        self._last_turn_hit_max_turns = hit_max_turns
        self._raise_error = raise_error
        self.received_prompts: list[str] = []

    def run_turn(self, prompt_text: str) -> str:
        self.received_prompts.append(prompt_text)
        if self._raise_error is not None:
            raise self._raise_error
        return self._response_text


class TestMakeSubmitStepFnCompletionMarkers:
    def test_cron_done_marker_marks_done(self):
        agent = _FakeAgent(response_text="任务已处理完毕。\n[CRON_DONE]")
        step_fn = make_submit_step_fn(agent)
        result = step_fn("do the thing")
        assert result.done is True
        assert result.error is None
        assert "[CRON_DONE]" in result.text

    def test_cron_continue_marker_marks_not_done(self):
        agent = _FakeAgent(response_text="处理了一部分。\n[CRON_CONTINUE] 下次接着处理第 5 条起")
        step_fn = make_submit_step_fn(agent)
        result = step_fn("do the thing")
        assert result.done is False
        assert result.error is None

    def test_cron_done_takes_priority_when_both_markers_present(self):
        # 理论上不该同时出现，但如果模型输出了两个标记，[CRON_DONE] 优先命中
        # （make_submit_step_fn 按 "先查 DONE 再查 CONTINUE" 的顺序判断）。
        agent = _FakeAgent(response_text="[CRON_DONE]\n[CRON_CONTINUE]")
        step_fn = make_submit_step_fn(agent)
        result = step_fn("do the thing")
        assert result.done is True


class TestMakeSubmitStepFnFallback:
    def test_no_marker_and_natural_end_marks_done(self):
        # 没有撞到内层 max_turns 预算 → 认为模型自然说完了，判定完成
        agent = _FakeAgent(response_text="做完了，但忘记打标记。", hit_max_turns=False)
        step_fn = make_submit_step_fn(agent)
        result = step_fn("do the thing")
        assert result.done is True

    def test_no_marker_and_hit_budget_marks_not_done(self):
        # 撞到了内层 max_turns 预算 → 认为还没做完，继续下一步
        agent = _FakeAgent(response_text="还在处理中...", hit_max_turns=True)
        step_fn = make_submit_step_fn(agent)
        result = step_fn("do the thing")
        assert result.done is False

    def test_missing_hit_max_turns_attribute_defaults_to_done(self):
        class _AgentWithoutFlag:
            def run_turn(self, prompt_text: str) -> str:
                return "done, no flag on this agent"

        step_fn = make_submit_step_fn(_AgentWithoutFlag())
        result = step_fn("do the thing")
        assert result.done is True


class TestMakeSubmitStepFnErrorHandling:
    def test_run_turn_exception_returns_error_result_not_raise(self):
        agent = _FakeAgent(raise_error=RuntimeError("LLM boom"))
        step_fn = make_submit_step_fn(agent)
        result = step_fn("do the thing")
        assert isinstance(result, StepResult)
        assert result.error == "LLM boom"
        assert result.done is False
        assert result.text == ""

    def test_empty_response_text_handled_gracefully(self):
        agent = _FakeAgent(response_text="")
        step_fn = make_submit_step_fn(agent)
        result = step_fn("do the thing")
        assert result.text == ""
        assert result.error is None


class TestMakeSubmitStepFnPromptPassthrough:
    def test_prompt_text_forwarded_verbatim_to_run_turn(self):
        agent = _FakeAgent(response_text="[CRON_DONE]")
        step_fn = make_submit_step_fn(agent)
        step_fn("first full prompt with progress")
        step_fn("继续")
        assert agent.received_prompts == ["first full prompt with progress", "继续"]
