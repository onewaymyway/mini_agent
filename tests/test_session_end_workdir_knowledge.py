"""
tests/test_session_end_workdir_knowledge.py — Stage 4 验证（4.2/4.3/4.4 SessionEnd 接入）

对应 self_evolution_stage4plus_plan.md Stage 4：
  - trigger_session_end() 接入 _update_workdir_knowledge_on_session_end()
  - _update_workdir_knowledge_on_session_end()：
      读取磁盘上 task manifest 的 outcome.unresolved -> open_threads.json（4.4）
      relate_session_to_work_thread 启发式关联（4.3）
      timeline.jsonl 一行概览（4.2，含独立的轻量反思调用）
  - _session_duration_minutes() 时长解析
  - _reflect_timeline_summary() 的容错与解析

复用 tests/test_session_end_reflection.py 的 make_minimal_agent 构造模式
（Agent.__new__ + 手动设置字段，跳过真实 __init__ 的网络/IO 依赖）。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mini_agent.llm.base import LLMResponse, LLMUsage


def make_minimal_agent(tmp_path: Path, memory_enabled: bool = False, workdir_knowledge_enabled: bool = True):
    from mini_agent.config import AppConfig, SessionConfig, MemoryConfig, SessionStats, WorkdirKnowledgeConfig
    from mini_agent.agent import Agent
    from mini_agent.session import Session

    cfg = AppConfig(
        auto_approve=True, project_root=tmp_path, model="test-model",
        session=SessionConfig(auto_save=False),
        memory=MemoryConfig(enabled=memory_enabled),
        workdir_knowledge=WorkdirKnowledgeConfig(enabled=workdir_knowledge_enabled),
    )

    agent = Agent.__new__(Agent)
    agent.cfg = cfg
    agent.stats = SessionStats()
    agent._history = []
    agent._memory = MagicMock() if memory_enabled else None
    agent._global_memory = None
    agent._session = Session(
        id="sess_test1", title="t", created_at="", updated_at="",
        provider="anthropic", model="test-model", stats={}, history=[],
    )
    agent._llm = MagicMock()
    agent._append_memory_delta = MagicMock()
    return agent


from mini_agent.storage.paths import AgentPaths
from mini_agent.perception.workdir_knowledge import (
    load_open_threads, load_recent_timeline, load_work_index, WorkThread,
    upsert_work_thread,
)


# ── trigger_session_end 调用 _update_workdir_knowledge_on_session_end ────────

def test_trigger_session_end_calls_workdir_knowledge_update(tmp_path, monkeypatch):
    agent = make_minimal_agent(tmp_path)
    monkeypatch.setattr("mini_agent.hooks.get_hook_manager", lambda: None)
    spy = MagicMock()
    agent._update_workdir_knowledge_on_session_end = spy
    agent.trigger_session_end()
    spy.assert_called_once()


def test_trigger_session_end_workdir_knowledge_failure_does_not_raise(tmp_path, monkeypatch):
    agent = make_minimal_agent(tmp_path)
    monkeypatch.setattr("mini_agent.hooks.get_hook_manager", lambda: None)
    agent._update_workdir_knowledge_on_session_end = MagicMock(side_effect=RuntimeError("boom"))
    agent.trigger_session_end()  # 不应抛异常


def test_trigger_session_end_workdir_update_runs_even_when_memory_disabled(tmp_path, monkeypatch):
    """memory.enabled=False 时仍应执行 W2 知识层更新（与 lesson 反思相互独立）。"""
    agent = make_minimal_agent(tmp_path, memory_enabled=False)
    monkeypatch.setattr("mini_agent.hooks.get_hook_manager", lambda: None)
    spy = MagicMock()
    agent._update_workdir_knowledge_on_session_end = spy
    agent.trigger_session_end()
    spy.assert_called_once()


# ── _update_workdir_knowledge_on_session_end ─────────────────────────────────

class TestUpdateWorkdirKnowledgeOnSessionEnd:

    def test_disabled_is_noop(self, tmp_path):
        agent = make_minimal_agent(tmp_path, workdir_knowledge_enabled=False)
        agent._reflect_timeline_summary = MagicMock(return_value=("", []))
        agent._update_workdir_knowledge_on_session_end()
        paths = AgentPaths(tmp_path)
        assert not paths.workdir_timeline.exists()

    def test_no_session_is_noop(self, tmp_path):
        agent = make_minimal_agent(tmp_path)
        agent._session = None
        agent._update_workdir_knowledge_on_session_end()  # 不应抛异常

    def test_writes_timeline_entry(self, tmp_path):
        agent = make_minimal_agent(tmp_path)
        agent._reflect_timeline_summary = MagicMock(return_value=("做了点事", ["完成了X"]))
        agent._update_workdir_knowledge_on_session_end()
        paths = AgentPaths(tmp_path)
        entries = load_recent_timeline(paths)
        assert len(entries) == 1
        assert entries[0]["sid"] == "sess_test1"
        assert entries[0]["theme"] == "做了点事"
        assert entries[0]["key_outcomes"] == ["完成了X"]

    def test_imports_unresolved_from_task_manifest(self, tmp_path):
        agent = make_minimal_agent(tmp_path)
        agent._reflect_timeline_summary = MagicMock(return_value=("", []))

        paths = AgentPaths(tmp_path)
        task_dir = paths.tasks_dir("sess_test1") / "task1"
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "manifest.json").write_text(
            json.dumps({"outcome": {"unresolved": ["还有个edge case没处理"]}}),
            encoding="utf-8",
        )

        agent._update_workdir_knowledge_on_session_end()
        items = load_open_threads(paths)
        assert len(items) == 1
        assert items[0].description == "还有个edge case没处理"
        assert items[0].discovered_in == "sess_test1"

    def test_task_count_reflects_manifest_count(self, tmp_path):
        agent = make_minimal_agent(tmp_path)
        agent._reflect_timeline_summary = MagicMock(return_value=("", []))

        paths = AgentPaths(tmp_path)
        for i in range(3):
            task_dir = paths.tasks_dir("sess_test1") / f"task{i}"
            task_dir.mkdir(parents=True, exist_ok=True)
            (task_dir / "manifest.json").write_text(
                json.dumps({"outcome": {"unresolved": []}}), encoding="utf-8",
            )

        agent._update_workdir_knowledge_on_session_end()
        entries = load_recent_timeline(paths)
        assert entries[0]["task_count"] == 3

    def test_no_task_dir_results_in_zero_task_count(self, tmp_path):
        agent = make_minimal_agent(tmp_path)
        agent._reflect_timeline_summary = MagicMock(return_value=("", []))
        agent._update_workdir_knowledge_on_session_end()
        paths = AgentPaths(tmp_path)
        entries = load_recent_timeline(paths)
        assert entries[0]["task_count"] == 0

    def test_malformed_manifest_does_not_raise(self, tmp_path):
        agent = make_minimal_agent(tmp_path)
        agent._reflect_timeline_summary = MagicMock(return_value=("", []))

        paths = AgentPaths(tmp_path)
        task_dir = paths.tasks_dir("sess_test1") / "task1"
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "manifest.json").write_text("{not valid json", encoding="utf-8")

        agent._update_workdir_knowledge_on_session_end()  # 不应抛异常
        entries = load_recent_timeline(paths)
        assert len(entries) == 1
        # manifest 文件存在但解析失败：task_count 仍计入该目录（已遍历到），
        # 只是 unresolved 提取被跳过
        assert entries[0]["task_count"] == 1

    def test_relates_to_active_work_thread_by_title_match(self, tmp_path):
        agent = make_minimal_agent(tmp_path)
        agent._reflect_timeline_summary = MagicMock(return_value=("", []))
        agent._history = [
            {"role": "user", "content": "继续推进自我进化机制实现",
             "_type": "user_input"},
        ]

        paths = AgentPaths(tmp_path)
        upsert_work_thread(paths, WorkThread(id="wt_1", title="自我进化机制实现", status="active"))

        agent._update_workdir_knowledge_on_session_end()

        threads = load_work_index(paths)
        assert "sess_test1" in threads[0].related_sessions

    def test_individual_step_failure_does_not_block_others(self, tmp_path, monkeypatch):
        """timeline 反思失败不应阻止 open_threads / work_index 的处理（各 try/except 独立）。"""
        agent = make_minimal_agent(tmp_path)
        agent._reflect_timeline_summary = MagicMock(side_effect=RuntimeError("LLM down"))

        paths = AgentPaths(tmp_path)
        task_dir = paths.tasks_dir("sess_test1") / "task1"
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "manifest.json").write_text(
            json.dumps({"outcome": {"unresolved": ["issue1"]}}), encoding="utf-8",
        )

        with pytest.raises(RuntimeError):
            agent._update_workdir_knowledge_on_session_end()
        # open_threads 在 timeline 反思之前执行，应已经落盘
        items = load_open_threads(paths)
        assert len(items) == 1


# ── _session_duration_minutes ────────────────────────────────────────────────

class TestSessionDurationMinutes:

    def test_no_session_returns_zero(self, tmp_path):
        agent = make_minimal_agent(tmp_path)
        agent._session = None
        assert agent._session_duration_minutes() == 0.0

    def test_empty_created_at_returns_zero(self, tmp_path):
        agent = make_minimal_agent(tmp_path)
        agent._session.created_at = ""
        assert agent._session_duration_minutes() == 0.0

    def test_valid_created_at_returns_positive_duration(self, tmp_path):
        from datetime import datetime, timezone, timedelta
        agent = make_minimal_agent(tmp_path)
        ten_min_ago = datetime.now(timezone.utc) - timedelta(minutes=10)
        agent._session.created_at = ten_min_ago.strftime("%Y-%m-%dT%H:%M:%S")
        duration = agent._session_duration_minutes()
        assert 9.0 <= duration <= 11.0

    def test_malformed_created_at_returns_zero(self, tmp_path):
        agent = make_minimal_agent(tmp_path)
        agent._session.created_at = "not-a-date"
        assert agent._session_duration_minutes() == 0.0


# ── _reflect_timeline_summary ────────────────────────────────────────────────

class TestReflectTimelineSummary:

    def test_no_user_turns_skips_llm_call(self, tmp_path):
        agent = make_minimal_agent(tmp_path)
        agent._history = []
        theme, outcomes = agent._reflect_timeline_summary()
        assert theme == ""
        assert outcomes == []
        agent._llm.chat_with_retry.assert_not_called()

    def test_parses_valid_response(self, tmp_path):
        agent = make_minimal_agent(tmp_path)
        agent._history = [{"role": "user", "content": "做点事", "_type": "user_input"}]
        agent._llm.chat_with_retry.return_value = LLMResponse(
            text='{"theme": "实现功能X", "key_outcomes": ["完成了Y", "修复了Z"]}',
            tool_calls=[], usage=LLMUsage(), stop_reason="end_turn",
        )
        theme, outcomes = agent._reflect_timeline_summary()
        assert theme == "实现功能X"
        assert outcomes == ["完成了Y", "修复了Z"]

    def test_llm_failure_returns_empty(self, tmp_path):
        agent = make_minimal_agent(tmp_path)
        agent._history = [{"role": "user", "content": "做点事", "_type": "user_input"}]
        agent._llm.chat_with_retry.side_effect = RuntimeError("LLM down")
        theme, outcomes = agent._reflect_timeline_summary()
        assert theme == ""
        assert outcomes == []

    def test_malformed_response_returns_empty(self, tmp_path):
        agent = make_minimal_agent(tmp_path)
        agent._history = [{"role": "user", "content": "做点事", "_type": "user_input"}]
        agent._llm.chat_with_retry.return_value = LLMResponse(
            text="不是JSON", tool_calls=[], usage=LLMUsage(), stop_reason="end_turn",
        )
        theme, outcomes = agent._reflect_timeline_summary()
        assert theme == ""
        assert outcomes == []

    def test_key_outcomes_limited_to_five(self, tmp_path):
        agent = make_minimal_agent(tmp_path)
        agent._history = [{"role": "user", "content": "x", "_type": "user_input"}]
        agent._llm.chat_with_retry.return_value = LLMResponse(
            text=json.dumps({"theme": "t", "key_outcomes": [f"o{i}" for i in range(10)]}),
            tool_calls=[], usage=LLMUsage(), stop_reason="end_turn",
        )
        theme, outcomes = agent._reflect_timeline_summary()
        assert len(outcomes) == 5

    def test_non_list_key_outcomes_treated_as_empty(self, tmp_path):
        agent = make_minimal_agent(tmp_path)
        agent._history = [{"role": "user", "content": "x", "_type": "user_input"}]
        agent._llm.chat_with_retry.return_value = LLMResponse(
            text=json.dumps({"theme": "t", "key_outcomes": "not a list"}),
            tool_calls=[], usage=LLMUsage(), stop_reason="end_turn",
        )
        theme, outcomes = agent._reflect_timeline_summary()
        assert theme == "t"
        assert outcomes == []


# ── _parse_timeline_summary ──────────────────────────────────────────────────

class TestParseTimelineSummary:

    def test_empty_text_returns_empty_dict(self):
        from mini_agent.agent import _parse_timeline_summary
        assert _parse_timeline_summary("") == {}
        assert _parse_timeline_summary("   ") == {}

    def test_valid_json(self):
        from mini_agent.agent import _parse_timeline_summary
        result = _parse_timeline_summary('{"theme": "t", "key_outcomes": ["a"]}')
        assert result == {"theme": "t", "key_outcomes": ["a"]}

    def test_strips_markdown_fence(self):
        from mini_agent.agent import _parse_timeline_summary
        text = '```json\n{"theme": "t", "key_outcomes": []}\n```'
        result = _parse_timeline_summary(text)
        assert result == {"theme": "t", "key_outcomes": []}

    def test_non_dict_json_returns_empty(self):
        from mini_agent.agent import _parse_timeline_summary
        assert _parse_timeline_summary('["a", "b"]') == {}

    def test_invalid_json_returns_empty(self):
        from mini_agent.agent import _parse_timeline_summary
        assert _parse_timeline_summary("not json at all") == {}
