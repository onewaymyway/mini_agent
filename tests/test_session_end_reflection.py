"""
tests/test_session_end_reflection.py — Stage 1.3 验证

对应 self_evolution_implementation_plan.md Stage 1.3：
  SessionEnd hook 真正接入（之前是预留未接的事件）+ 反思 LLM 调用生成
  结构化 lesson 候选并写入记忆。

覆盖：
  - _parse_lesson_candidates / _clamp_confidence 辅助函数（已在其他地方测试，
    这里聚焦端到端：trigger_session_end / _reflect_and_save_lessons）
  - hook_mgr.run("SessionEnd", ...) 被正确调用
  - 反思 LLM 返回的候选被正确转换为 MemoryEntry 并写入 self._memory
  - 反思失败时不应抛出异常、不应阻塞退出流程
  - memory 未启用时跳过反思（不浪费一次 LLM 调用）
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mini_agent.llm.base import LLMResponse, LLMUsage


def make_minimal_agent(tmp_path: Path, memory_enabled: bool = True):
    """构造一个最小化 Agent stub，跳过真实 __init__，只设置 SessionEnd
    相关方法依赖的字段。"""
    from mini_agent.config import AppConfig, SessionConfig, MemoryConfig, SessionStats
    from mini_agent.agent import Agent
    from mini_agent.session import Session

    cfg = AppConfig(
        auto_approve=True, project_root=tmp_path, model="test-model",
        session=SessionConfig(auto_save=False),
        memory=MemoryConfig(enabled=memory_enabled),
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


# ── trigger_session_end ──────────────────────────────────────────────────────

def test_trigger_session_end_noop_without_session(tmp_path):
    agent = make_minimal_agent(tmp_path)
    agent._session = None
    # 不应抛异常
    agent.trigger_session_end()


def test_trigger_session_end_calls_hook_manager(tmp_path, monkeypatch):
    agent = make_minimal_agent(tmp_path, memory_enabled=False)
    mock_hook_mgr = MagicMock()
    monkeypatch.setattr(
        "mini_agent.hooks.get_hook_manager", lambda: mock_hook_mgr
    )
    agent.trigger_session_end()
    mock_hook_mgr.run.assert_called_once()
    call_args = mock_hook_mgr.run.call_args
    assert call_args[0][0] == "SessionEnd"
    payload = call_args[0][1]
    assert payload["session_id"] == "sess_test1"


def test_trigger_session_end_skips_reflection_when_memory_disabled(tmp_path, monkeypatch):
    agent = make_minimal_agent(tmp_path, memory_enabled=False)
    monkeypatch.setattr("mini_agent.hooks.get_hook_manager", lambda: None)
    spy = MagicMock()
    agent._reflect_and_save_lessons = spy
    agent.trigger_session_end()
    spy.assert_not_called()


def test_trigger_session_end_hook_failure_does_not_raise(tmp_path, monkeypatch):
    """SessionEnd hook 抛异常不应阻塞退出流程。"""
    agent = make_minimal_agent(tmp_path, memory_enabled=False)
    mock_hook_mgr = MagicMock()
    mock_hook_mgr.run.side_effect = RuntimeError("hook crashed")
    monkeypatch.setattr("mini_agent.hooks.get_hook_manager", lambda: mock_hook_mgr)
    agent.trigger_session_end()  # 不应抛异常


def test_trigger_session_end_reflection_failure_does_not_raise(tmp_path, monkeypatch):
    """反思 LLM 调用失败不应阻塞退出流程，只打印警告。"""
    agent = make_minimal_agent(tmp_path, memory_enabled=True)
    monkeypatch.setattr("mini_agent.hooks.get_hook_manager", lambda: None)
    agent._reflect_and_save_lessons = MagicMock(side_effect=RuntimeError("LLM down"))
    agent.trigger_session_end()  # 不应抛异常


# ── _reflect_and_save_lessons ────────────────────────────────────────────────

def test_reflect_and_save_lessons_writes_entries(tmp_path):
    agent = make_minimal_agent(tmp_path, memory_enabled=True)
    agent._history = [
        {"role": "user", "content": "帮我修复这个bug", "_type": "user_input"},
        {"role": "assistant", "content": "已修复", "_type": "assistant_reply"},
    ]
    agent.stats.tool_stats = {"bash": {"calls": 5, "success": 3, "fail": 2, "total_len": 100}}

    candidates_json = (
        '[{"trigger": "连续调用bash失败", "outcome": "权限不足", '
        '"root_cause": "目录权限", "suggested_action": "先检查权限", "confidence": 0.7}]'
    )
    agent._llm.chat_with_retry.return_value = LLMResponse(
        text=candidates_json, tool_calls=[], usage=LLMUsage(), stop_reason="end_turn",
    )

    saved = agent._reflect_and_save_lessons()
    assert saved == 1
    agent._memory.add.assert_called_once()
    entry = agent._memory.add.call_args[0][0]
    assert entry.entry_type == "lesson"
    assert entry.source == "self_reflection"
    assert entry.trigger == "连续调用bash失败"
    assert entry.confidence == 0.7
    agent._append_memory_delta.assert_called_once()


def test_reflect_and_save_lessons_empty_array_saves_nothing(tmp_path):
    agent = make_minimal_agent(tmp_path, memory_enabled=True)
    agent._history = [{"role": "user", "content": "继续", "_type": "user_input"}]
    agent._llm.chat_with_retry.return_value = LLMResponse(
        text="[]", tool_calls=[], usage=LLMUsage(), stop_reason="end_turn",
    )
    saved = agent._reflect_and_save_lessons()
    assert saved == 0
    agent._memory.add.assert_not_called()


def test_reflect_and_save_lessons_skips_when_nothing_to_reflect(tmp_path):
    """没有用户轮次也没有工具调用统计时，不应发起 LLM 调用。"""
    agent = make_minimal_agent(tmp_path, memory_enabled=True)
    agent._history = []
    agent.stats.tool_stats = {}
    saved = agent._reflect_and_save_lessons()
    assert saved == 0
    agent._llm.chat_with_retry.assert_not_called()


def test_reflect_and_save_lessons_respects_max_lessons(tmp_path):
    agent = make_minimal_agent(tmp_path, memory_enabled=True)
    agent._history = [{"role": "user", "content": "x", "_type": "user_input"}]
    candidates = [
        {"trigger": f"t{i}", "outcome": "o", "root_cause": "", "suggested_action": "a", "confidence": 0.5}
        for i in range(10)
    ]
    import json
    agent._llm.chat_with_retry.return_value = LLMResponse(
        text=json.dumps(candidates), tool_calls=[], usage=LLMUsage(), stop_reason="end_turn",
    )
    saved = agent._reflect_and_save_lessons(max_lessons=3)
    assert saved == 3
    assert agent._memory.add.call_count == 3


def test_reflect_and_save_lessons_malformed_response_saves_nothing(tmp_path):
    """LLM 返回无法解析的文本时，应静默返回 0，不抛异常。"""
    agent = make_minimal_agent(tmp_path, memory_enabled=True)
    agent._history = [{"role": "user", "content": "x", "_type": "user_input"}]
    agent._llm.chat_with_retry.return_value = LLMResponse(
        text="我无法生成结构化数据", tool_calls=[], usage=LLMUsage(), stop_reason="end_turn",
    )
    saved = agent._reflect_and_save_lessons()
    assert saved == 0


# ── _detect_and_record_correction（Stage 1.4 Agent 集成）────────────────────

def test_detect_and_record_correction_writes_human_feedback_lesson(tmp_path):
    agent = make_minimal_agent(tmp_path, memory_enabled=True)
    agent._history = [
        {"role": "assistant", "content": "我已经用 write_file 覆盖了文件", "_type": "assistant_reply"},
    ]
    hit = agent._detect_and_record_correction("不对，应该用 patch_file 而不是 write_file")
    assert hit is True
    agent._memory.add.assert_called_once()
    entry = agent._memory.add.call_args[0][0]
    assert entry.entry_type == "lesson"
    assert entry.source == "human_feedback"
    assert entry.confidence == 0.85
    assert "write_file" in entry.trigger  # 上一轮 assistant 回复被纳入 trigger
    agent._append_memory_delta.assert_called_once()


def test_detect_and_record_correction_no_hit_for_normal_message(tmp_path):
    agent = make_minimal_agent(tmp_path, memory_enabled=True)
    hit = agent._detect_and_record_correction("请帮我写一个排序算法")
    assert hit is False
    agent._memory.add.assert_not_called()


def test_detect_and_record_correction_skipped_when_disabled(tmp_path):
    agent = make_minimal_agent(tmp_path, memory_enabled=True)
    agent.cfg.memory.correction_detection_enabled = False
    hit = agent._detect_and_record_correction("不对，应该这样做")
    assert hit is False
    agent._memory.add.assert_not_called()


def test_detect_and_record_correction_skipped_when_memory_disabled(tmp_path):
    agent = make_minimal_agent(tmp_path, memory_enabled=False)
    hit = agent._detect_and_record_correction("不对，应该这样做")
    assert hit is False


def test_detect_and_record_correction_handles_non_string():
    from mini_agent.config import AppConfig, MemoryConfig
    from mini_agent.agent import Agent
    agent = Agent.__new__(Agent)
    agent.cfg = AppConfig(memory=MemoryConfig(enabled=True))
    agent._memory = MagicMock()
    hit = agent._detect_and_record_correction(None)
    assert hit is False


# ── _on_edit_detected（Stage 1.5 Agent 集成）─────────────────────────────────

def test_on_edit_detected_appends_user_correction_to_history(tmp_path):
    agent = make_minimal_agent(tmp_path, memory_enabled=True)
    from mini_agent.history_manager import HistoryManager
    agent._hist = HistoryManager(cfg=agent.cfg, skill_loader=None)
    agent._hist._history = agent._history

    agent._on_edit_detected({
        "tool_name": "bash", "original": "rm -rf /tmp/x", "edited": "rm -rf /tmp/x --dry-run",
    })

    assert len(agent._history) == 1
    msg = agent._history[0]
    from mini_agent.history.entry import HType
    assert msg["_type"] == HType.USER_CORRECTION
    assert "rm -rf /tmp/x" in msg["content"]
    assert "--dry-run" in msg["content"]


def test_on_edit_detected_also_generates_lesson(tmp_path):
    agent = make_minimal_agent(tmp_path, memory_enabled=True)
    from mini_agent.history_manager import HistoryManager
    agent._hist = HistoryManager(cfg=agent.cfg, skill_loader=None)
    agent._hist._history = agent._history

    agent._on_edit_detected({
        "tool_name": "bash", "original": "rm -rf /tmp/x", "edited": "rm -rf /tmp/x --dry-run",
    })

    agent._memory.add.assert_called_once()
    entry = agent._memory.add.call_args[0][0]
    assert entry.source == "human_feedback"
    assert "edit" in entry.tags


def test_on_edit_detected_noop_when_no_actual_change(tmp_path):
    """edited == original 时（理论上不应发生，但做好防御）不应写入任何内容。"""
    agent = make_minimal_agent(tmp_path, memory_enabled=True)
    from mini_agent.history_manager import HistoryManager
    agent._hist = HistoryManager(cfg=agent.cfg, skill_loader=None)
    agent._hist._history = agent._history

    agent._on_edit_detected({"tool_name": "bash", "original": "ls", "edited": "ls"})

    assert len(agent._history) == 0
    agent._memory.add.assert_not_called()


def test_on_edit_detected_lesson_failure_does_not_block_history_write(tmp_path, monkeypatch):
    """lesson 生成失败不应影响 history 写入（编辑本身应始终成功记录）。"""
    agent = make_minimal_agent(tmp_path, memory_enabled=True)
    from mini_agent.history_manager import HistoryManager
    agent._hist = HistoryManager(cfg=agent.cfg, skill_loader=None)
    agent._hist._history = agent._history
    agent._memory.add.side_effect = RuntimeError("disk full")

    agent._on_edit_detected({"tool_name": "bash", "original": "ls", "edited": "ls -la"})

    assert len(agent._history) == 1  # history 写入不受影响
