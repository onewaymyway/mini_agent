"""
tests/test_compact_trigger_resume_snapshot.py — 回归测试：resume session 后
compact 触发器（ToolCallCountTrigger/TurnCountTrigger）增量计算错误的 bug。

根因：`history/triggers.py` 里的 `tool_call_count`/`turn_count` 触发器用
`self.stats.turns/tool_calls`（累计值，随 session 持久化）减去
`self._last_compact_turns`/`self._last_compact_tool_calls`（"上次 compact
时的快照"）算增量。快照字段此前只存在内存里、从未写进 `session.stats`——
`load_session()` 把 `self.stats.tool_calls` 从 `meta.json` 恢复成历史累计值
（比如 1000+），但快照字段在 `Agent.__init__`/`lifecycle.py` 里被重新初始化
成 0，导致 resume 之后第一次触发判断时 `delta = tool_calls - 0 = 1000+`，
几乎必然立刻触发一次不合理的 compact。

修复：`save_session()` 把两个快照字段写进 `stats` dict 一并持久化；
`load_session()` 从 `session.stats` 恢复这两个字段（旧 session 文件没有这
两个字段时，退回"视为刚 compact 过"，即等于恢复出来的 turns/tool_calls，
而不是 0）；`new_session()` 同步把两个字段重置为 0（避免同一个 Agent 实例
执行 `/session new` 后遗留旧 session 的快照值导致新 session 的触发器长期
"装死"不生效）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "src")

from mini_agent.llm.base import LLMResponse, LLMUsage


@pytest.fixture
def tmp(tmp_path):
    return tmp_path


def _make_agent(tmp, auto_save=True):
    import mini_agent.tools.builtin  # noqa: F401 registers builtin tools
    from mini_agent.agent import Agent
    from mini_agent.config import load_config
    from mini_agent.permissions import PermissionGuard

    cfg = load_config(
        project_root=tmp,
        session_dir=tmp / "sessions",
        session_fmt="json",
        auto_save_session=auto_save,
    )
    cfg.stream = False

    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(
        text="Hello!", tool_calls=[], usage=LLMUsage(5, 10, 15), stop_reason="end_turn"
    )
    guard = PermissionGuard(auto_approve=True)
    return Agent(cfg=cfg, guard=guard, llm_client=mock_llm)


def test_resume_does_not_inflate_tool_call_delta(tmp):
    agent1 = _make_agent(tmp)
    # 模拟一段较长的历史：累计发生过 200 次工具调用，其中最近一次 compact
    # 是在 tool_calls=190 时做的（distance since compact 应该只有 10）。
    agent1.stats.tool_calls = 200
    agent1.stats.turns = 40
    agent1._last_compact_tool_calls = 190
    agent1._last_compact_turns = 38
    sid = agent1.session_id
    agent1.save_session()

    agent2 = _make_agent(tmp)
    ok = agent2.load_session(sid)
    assert ok is True

    assert agent2.stats.tool_calls == 200
    assert agent2._last_compact_tool_calls == 190
    delta = agent2.stats.tool_calls - agent2._last_compact_tool_calls
    assert delta == 10  # 而不是 200（修复前的 bug：快照字段被重置为 0）


def test_resume_legacy_session_without_snapshot_fields_falls_back_safely(tmp):
    """模拟修复上线之前保存的旧 session 文件（stats 里没有这两个新字段），
    确认不会退化回"delta = 整段历史累计值"的错误行为。"""
    agent1 = _make_agent(tmp)
    agent1.stats.tool_calls = 500
    agent1.stats.turns = 80
    sid = agent1.session_id
    agent1.save_session()

    # 手动抹掉刚保存文件里的两个新字段，模拟"旧格式 session"
    import json
    meta_path = Path(agent1.session_file).parent / "meta.json" \
        if Path(agent1.session_file).is_dir() or not str(agent1.session_file).endswith(".json") \
        else Path(agent1.session_file)
    if not meta_path.exists():
        # fallback: locate meta.json under the session directory
        session_dir = tmp / "sessions" / sid
        meta_path = session_dir / "meta.json"
    data = json.loads(meta_path.read_text())
    data["stats"].pop("last_compact_turns", None)
    data["stats"].pop("last_compact_tool_calls", None)
    meta_path.write_text(json.dumps(data))

    agent2 = _make_agent(tmp)
    ok = agent2.load_session(sid)
    assert ok is True

    # 缺字段时退回"视为 resume 这一刻刚 compact 过"，delta 应该是 0，
    # 而不是等于整段历史累计的 500。
    delta = agent2.stats.tool_calls - agent2._last_compact_tool_calls
    assert delta == 0


def test_new_session_resets_stale_snapshot_from_previous_session(tmp):
    agent = _make_agent(tmp)
    agent.stats.tool_calls = 300
    agent._last_compact_tool_calls = 50  # 上一个 session 遗留的快照

    ok = agent.new_session()
    assert ok is True

    assert agent.stats.tool_calls == 0
    assert agent._last_compact_tool_calls == 0
    assert agent._last_compact_turns == 0
