"""
tests/test_cron_job_executor_step_detail.py — cron_run_debug_detail_improvement_plan.md

覆盖 StepResult 新增字段以及 run_job() 写 step 事件时的截断逻辑
（_truncate_tool_calls / STEP_FULL_TEXT_MAX_CHARS）。不依赖真实 Agent/LLM，
用一个假的 submit_step_fn 驱动 CronJobExecutor.run_job()。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from mini_agent.evolution.cron_job_executor import (
    CronJobExecutor,
    StepResult,
    STEP_FULL_TEXT_MAX_CHARS,
    TOOL_INPUT_MAX_CHARS,
    TOOL_OUTPUT_MAX_CHARS,
    _truncate_tool_calls,
)


class TestStepResultDefaults:
    def test_tool_calls_defaults_to_empty_list_and_is_independent_per_instance(self):
        r1 = StepResult(text="a")
        r2 = StepResult(text="b")
        r1.tool_calls.append({"name": "x"})
        # dataclass field(default_factory=list) 保证两个实例不共享同一个列表
        assert r2.tool_calls == []


class TestTruncateToolCalls:
    def test_empty_or_none_returns_empty_list(self):
        assert _truncate_tool_calls(None) == []
        assert _truncate_tool_calls([]) == []

    def test_string_input_and_output_truncated_to_limits(self):
        calls = [{"name": "t", "input": "x" * (TOOL_INPUT_MAX_CHARS + 100),
                  "output": "y" * (TOOL_OUTPUT_MAX_CHARS + 100)}]
        result = _truncate_tool_calls(calls)
        assert len(result[0]["input"]) == TOOL_INPUT_MAX_CHARS
        assert len(result[0]["output"]) == TOOL_OUTPUT_MAX_CHARS

    def test_non_string_input_is_serialized_before_truncation(self):
        calls = [{"name": "t", "input": {"path": "a.txt", "n": 1}, "output": "ok"}]
        result = _truncate_tool_calls(calls)
        assert isinstance(result[0]["input"], str)
        assert "a.txt" in result[0]["input"]

    def test_malformed_entry_skipped_without_raising(self):
        calls = [
            {"name": "good", "input": "ok", "output": "ok"},
            "not a dict",  # .get() 会抛 AttributeError，应被静默跳过
        ]
        result = _truncate_tool_calls(calls)
        assert len(result) == 1
        assert result[0]["name"] == "good"


class _FakeWorkspace:
    """CronJobWorkspace 的最小替身：只记录 append_run_event 调用，其余方法
    返回构造 run_job() 所需的最小可用状态。"""

    def __init__(self):
        self.events: list[dict] = []
        self._state = SimpleNamespace(
            status="idle", consecutive_failures=0, last_run_started_at=0.0,
            last_run_id="", last_run_finished_at=0.0, last_step_index=0,
            last_error="", progress_summary="",
        )

    def ensure(self, **kwargs):
        pass

    def read_config(self, default=None):
        return SimpleNamespace(
            stuck_similarity_threshold=0.92, stuck_consecutive_limit=3,
            stuck_max_recoveries=2, timeout_seconds=1200, max_steps=60,
        )

    def read_state(self):
        return self._state

    def write_state(self, state):
        self._state = state

    def new_run_id(self):
        return "run_test_1"

    def render_prompt(self, task_template, run_id):
        return "prompt"

    def append_run_event(self, run_id, event):
        self.events.append(event)


class TestRunJobStepEventDetail:
    def test_step_event_carries_full_text_and_tool_calls(self, monkeypatch):
        executor = CronJobExecutor.__new__(CronJobExecutor)
        executor.circuit_breaker = None
        executor.memory_backfill_cfg = None
        executor.memory_backend = None
        executor.llm_client = None
        executor._paths = SimpleNamespace()

        ws = _FakeWorkspace()
        monkeypatch.setattr(
            "mini_agent.evolution.cron_job_executor.CronJobWorkspace",
            lambda paths, job_id: ws,
        )
        monkeypatch.setattr(
            CronJobExecutor, "_write_output_manifest", lambda self, **kw: None,
        )

        long_text = "z" * (STEP_FULL_TEXT_MAX_CHARS + 500)

        def submit_step_fn(prompt_text: str) -> StepResult:
            return StepResult(
                text=long_text, done=True,
                tool_calls=[{"name": "search", "input": {"q": "x"}, "output": "found"}],
            )

        job = SimpleNamespace(id="job1", name="测试任务", task_template="do it")
        executor.run_job(job, submit_step_fn)

        step_events = [e for e in ws.events if e.get("type") == "step"]
        assert len(step_events) == 1
        ev = step_events[0]
        assert ev["text_preview"] == long_text[:500]
        assert ev["full_text"] == long_text[:STEP_FULL_TEXT_MAX_CHARS]
        assert len(ev["full_text"]) == STEP_FULL_TEXT_MAX_CHARS
        assert ev["tool_calls"] == [
            {"name": "search", "input": '{"q": "x"}', "output": "found"}
        ]

    def test_step_event_without_tool_calls_stays_empty_list(self, monkeypatch):
        executor = CronJobExecutor.__new__(CronJobExecutor)
        executor.circuit_breaker = None
        executor.memory_backfill_cfg = None
        executor.memory_backend = None
        executor.llm_client = None
        executor._paths = SimpleNamespace()

        ws = _FakeWorkspace()
        monkeypatch.setattr(
            "mini_agent.evolution.cron_job_executor.CronJobWorkspace",
            lambda paths, job_id: ws,
        )
        monkeypatch.setattr(
            CronJobExecutor, "_write_output_manifest", lambda self, **kw: None,
        )

        def submit_step_fn(prompt_text: str) -> StepResult:
            return StepResult(text="short output", done=True)

        job = SimpleNamespace(id="job1", name="测试任务", task_template="do it")
        executor.run_job(job, submit_step_fn)

        step_events = [e for e in ws.events if e.get("type") == "step"]
        assert step_events[0]["tool_calls"] == []
        assert step_events[0]["full_text"] == "short output"
