"""
tests/test_retry.py — LLM 重试策略单元测试

覆盖：
  - EmptyOutputCondition 判断逻辑
  - EmptyTextCondition 判断逻辑
  - StopReasonCondition 判断逻辑
  - RetryPolicy 重试次数控制
  - RetryPolicy.call_with_retry 成功路径
  - RetryPolicy.call_with_retry 重试路径
  - on_retry 回调触发
  - 自定义条件扩展
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from llm.base import LLMResponse, LLMUsage, ToolCall
from llm.retry import (
    RetryCondition,
    RetryPolicy,
    EmptyOutputCondition,
    EmptyTextCondition,
    StopReasonCondition,
    default_retry_policy,
    no_retry_policy,
)


# ── 辅助工厂 ─────────────────────────────────────────────────────────────────

def make_response(
    text: str = "",
    tool_calls: list | None = None,
    stop_reason: str = "end_turn",
) -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=tool_calls or [],
        usage=LLMUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        stop_reason=stop_reason,
    )


# ── EmptyOutputCondition ─────────────────────────────────────────────────────

class TestEmptyOutputCondition:
    def setup_method(self):
        self.cond = EmptyOutputCondition()

    def test_triggers_when_both_empty(self):
        resp = make_response(text="", tool_calls=[])
        assert self.cond.should_retry(resp) is True

    def test_triggers_when_text_whitespace_only(self):
        resp = make_response(text="   \n\t  ", tool_calls=[])
        assert self.cond.should_retry(resp) is True

    def test_no_trigger_when_has_text(self):
        resp = make_response(text="hello", tool_calls=[])
        assert self.cond.should_retry(resp) is False

    def test_no_trigger_when_has_tool_calls(self):
        tc = ToolCall(id="tc1", name="bash", input={"cmd": "ls"})
        resp = make_response(text="", tool_calls=[tc])
        assert self.cond.should_retry(resp) is False

    def test_no_trigger_when_has_both(self):
        tc = ToolCall(id="tc1", name="bash", input={"cmd": "ls"})
        resp = make_response(text="running...", tool_calls=[tc])
        assert self.cond.should_retry(resp) is False


# ── EmptyTextCondition ───────────────────────────────────────────────────────

class TestEmptyTextCondition:
    def setup_method(self):
        self.cond = EmptyTextCondition()

    def test_triggers_even_with_tool_calls(self):
        tc = ToolCall(id="tc1", name="bash", input={"cmd": "ls"})
        resp = make_response(text="", tool_calls=[tc])
        assert self.cond.should_retry(resp) is True

    def test_no_trigger_when_has_text(self):
        resp = make_response(text="some output")
        assert self.cond.should_retry(resp) is False


# ── StopReasonCondition ──────────────────────────────────────────────────────

class TestStopReasonCondition:
    def test_triggers_on_matching_stop_reason(self):
        cond = StopReasonCondition(stop_reasons={"max_tokens"})
        resp = make_response(text="partial...", stop_reason="max_tokens")
        assert cond.should_retry(resp) is True

    def test_no_trigger_on_other_stop_reason(self):
        cond = StopReasonCondition(stop_reasons={"max_tokens"})
        resp = make_response(text="done", stop_reason="end_turn")
        assert cond.should_retry(resp) is False

    def test_multiple_stop_reasons(self):
        cond = StopReasonCondition(stop_reasons={"max_tokens", "stop"})
        assert cond.should_retry(make_response(stop_reason="max_tokens")) is True
        assert cond.should_retry(make_response(stop_reason="stop")) is True
        assert cond.should_retry(make_response(text="ok", stop_reason="end_turn")) is False


# ── RetryPolicy ──────────────────────────────────────────────────────────────

class TestRetryPolicy:
    def test_no_retry_when_condition_not_triggered(self):
        call_count = [0]

        def call_fn():
            call_count[0] += 1
            return make_response(text="good response")

        policy = default_retry_policy(max_retries=3)
        result = policy.call_with_retry(call_fn)

        assert call_count[0] == 1  # 只调用一次
        assert result.text == "good response"

    def test_retries_on_empty_output(self):
        call_count = [0]
        responses = [
            make_response(text=""),       # 第1次：空，触发重试
            make_response(text=""),       # 第2次：仍空，触发重试
            make_response(text="final"),  # 第3次：有内容，成功
        ]

        def call_fn():
            resp = responses[call_count[0]]
            call_count[0] += 1
            return resp

        policy = default_retry_policy(max_retries=3)
        result = policy.call_with_retry(call_fn)

        assert call_count[0] == 3
        assert result.text == "final"

    def test_stops_at_max_retries(self):
        call_count = [0]

        def call_fn():
            call_count[0] += 1
            return make_response(text="")  # 始终空输出

        policy = default_retry_policy(max_retries=2)
        result = policy.call_with_retry(call_fn)

        assert call_count[0] == 3  # 首次 + 2次重试
        assert result.text == ""   # 达到上限，返回最后一次结果

    def test_on_retry_callback_triggered(self):
        retry_log = []

        def call_fn():
            return make_response(text="")

        def on_retry(attempt, reason):
            retry_log.append((attempt, reason))

        policy = default_retry_policy(max_retries=2)
        policy.call_with_retry(call_fn, on_retry=on_retry)

        assert len(retry_log) == 2
        assert retry_log[0][0] == 1
        assert retry_log[1][0] == 2

    def test_no_retry_policy_never_retries(self):
        call_count = [0]

        def call_fn():
            call_count[0] += 1
            return make_response(text="")

        policy = no_retry_policy()
        policy.call_with_retry(call_fn)

        assert call_count[0] == 1

    def test_add_condition_chainable(self):
        policy = RetryPolicy(max_retries=1, conditions=[])
        result = policy.add_condition(EmptyOutputCondition())
        assert result is policy  # 返回自身
        assert len(policy.conditions) == 1

    def test_or_logic_between_conditions(self):
        """任意条件触发即重试"""
        call_count = [0]
        # 第一次：有文本但 stop_reason=max_tokens，StopReasonCondition 触发
        # 第二次：正常
        responses = [
            make_response(text="truncated", stop_reason="max_tokens"),
            make_response(text="complete", stop_reason="end_turn"),
        ]

        def call_fn():
            resp = responses[call_count[0]]
            call_count[0] += 1
            return resp

        policy = RetryPolicy(
            max_retries=2,
            conditions=[
                EmptyOutputCondition(),
                StopReasonCondition(stop_reasons={"max_tokens"}),
            ],
        )
        result = policy.call_with_retry(call_fn)

        assert call_count[0] == 2
        assert result.text == "complete"


# ── 自定义条件扩展示例 ────────────────────────────────────────────────────────

class TestCustomConditionExtension:
    """演示如何扩展自定义重试条件。"""

    def test_custom_condition(self):
        """示例：当文本包含特定错误标志时重试。"""

        class ErrorFlagCondition(RetryCondition):
            """文本包含 [ERROR] 标志时重试。"""

            def should_retry(self, response: LLMResponse) -> bool:
                return "[ERROR]" in response.text

            @property
            def reason(self) -> str:
                return "[ErrorFlagCondition] 响应包含错误标志，触发重试"

        call_count = [0]
        responses = [
            make_response(text="[ERROR] something went wrong"),
            make_response(text="all good"),
        ]

        def call_fn():
            resp = responses[call_count[0]]
            call_count[0] += 1
            return resp

        policy = RetryPolicy(
            max_retries=2,
            conditions=[ErrorFlagCondition()],
        )
        result = policy.call_with_retry(call_fn)

        assert call_count[0] == 2
        assert result.text == "all good"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
