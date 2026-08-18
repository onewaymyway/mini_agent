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

from mini_agent.evolution.cron_agent_bridge import (
    make_submit_step_fn,
    _extract_tool_calls_from_history,
)
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


# ── cron_run_debug_detail_improvement_plan.md ① ─────────────────────────────
# _extract_tool_calls_from_history()：从 agent._hist 新增片段里提取工具调用
# 轨迹的纯逻辑函数，不依赖真实 Agent/LLM，用构造好的 history 条目直接覆盖。


def _assistant_reply_with_tool_use(name: str, tool_input: dict, text: str = "") -> dict:
    content = []
    if text:
        content.append({"type": "text", "text": text})
    content.append({"type": "tool_use", "id": "tc1", "name": name, "input": tool_input})
    return {"role": "assistant", "content": content, "_type": "assistant_reply"}


def _tool_result_entry(entries: list[dict]) -> dict:
    import json as _json
    blocks = [
        f"<tool_result>\n{_json.dumps({'name': e['name'], 'output': e['output']}, ensure_ascii=False)}\n</tool_result>"
        for e in entries
    ]
    return {"role": "user", "content": "\n\n".join(blocks), "_type": "tool_result"}


class TestExtractToolCallsFromHistory:
    def test_single_tool_call_pairs_correctly(self):
        history = [
            _assistant_reply_with_tool_use("read_file", {"path": "a.txt"}),
            _tool_result_entry([{"name": "read_file", "output": "file contents"}]),
        ]
        result = _extract_tool_calls_from_history(history)
        assert result == [
            {"name": "read_file", "input": {"path": "a.txt"}, "output": "file contents"}
        ]

    def test_multiple_tool_calls_preserve_order(self):
        history = [
            _assistant_reply_with_tool_use("tool_a", {"x": 1}),
            _tool_result_entry([{"name": "tool_a", "output": "out_a"}]),
            _assistant_reply_with_tool_use("tool_b", {"y": 2}),
            _tool_result_entry([{"name": "tool_b", "output": "out_b"}]),
        ]
        result = _extract_tool_calls_from_history(history)
        assert [tc["name"] for tc in result] == ["tool_a", "tool_b"]
        assert result[0]["output"] == "out_a"
        assert result[1]["output"] == "out_b"

    def test_multiple_tool_calls_in_single_turn_batch(self):
        # 一次 assistant 回复里并行发起多个 tool_use，结果批量回注在同一条
        # tool_result 消息里——顺序仍然靠 zip() 天然对应。
        content = [
            {"type": "tool_use", "id": "1", "name": "tool_a", "input": {"i": 1}},
            {"type": "tool_use", "id": "2", "name": "tool_b", "input": {"i": 2}},
        ]
        history = [
            {"role": "assistant", "content": content, "_type": "assistant_reply"},
            _tool_result_entry([
                {"name": "tool_a", "output": "r1"},
                {"name": "tool_b", "output": "r2"},
            ]),
        ]
        result = _extract_tool_calls_from_history(history)
        assert result == [
            {"name": "tool_a", "input": {"i": 1}, "output": "r1"},
            {"name": "tool_b", "input": {"i": 2}, "output": "r2"},
        ]

    def test_no_tool_use_returns_empty_list(self):
        history = [
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}], "_type": "assistant_reply"},
        ]
        assert _extract_tool_calls_from_history(history) == []

    def test_empty_history_returns_empty_list(self):
        assert _extract_tool_calls_from_history([]) == []

    def test_malformed_tool_result_json_skipped_silently(self):
        history = [
            _assistant_reply_with_tool_use("tool_a", {"x": 1}),
            {"role": "user", "content": "<tool_result>\nnot json\n</tool_result>", "_type": "tool_result"},
        ]
        # 不抛异常，损坏的记录直接跳过
        result = _extract_tool_calls_from_history(history)
        assert result == []

    def test_non_list_content_and_unknown_types_ignored(self):
        history = [
            {"role": "user", "content": "plain string", "_type": "user_input"},
            {"role": "assistant", "content": "not a list", "_type": "assistant_reply"},
        ]
        assert _extract_tool_calls_from_history(history) == []


class TestMakeSubmitStepFnToolCallExtraction:
    class _FakeHistoryManager:
        def __init__(self, entries: list[dict]):
            self._entries = entries

        @property
        def history(self) -> list[dict]:
            return list(self._entries)

    class _FakeAgentWithHistory:
        def __init__(self, response_text: str, new_entries: list[dict]):
            self._response_text = response_text
            self._last_turn_hit_max_turns = False
            self._hist = TestMakeSubmitStepFnToolCallExtraction._FakeHistoryManager([])
            self._new_entries = new_entries

        def run_turn(self, prompt_text: str) -> str:
            # 模拟 run_turn() 内部往 history 里追加了本步产生的条目
            self._hist._entries.extend(self._new_entries)
            return self._response_text

    def test_step_result_carries_extracted_tool_calls(self):
        new_entries = [
            _assistant_reply_with_tool_use("search", {"q": "foo"}),
            _tool_result_entry([{"name": "search", "output": "found it"}]),
        ]
        agent = self._FakeAgentWithHistory("done.\n[CRON_DONE]", new_entries)
        step_fn = make_submit_step_fn(agent)
        result = step_fn("go")
        assert result.tool_calls == [
            {"name": "search", "input": {"q": "foo"}, "output": "found it"}
        ]

    def test_agent_without_hist_attribute_defaults_to_empty_tool_calls(self):
        agent = _FakeAgent(response_text="[CRON_DONE]")
        step_fn = make_submit_step_fn(agent)
        result = step_fn("go")
        assert result.tool_calls == []
