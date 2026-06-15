"""
tests/test_undo.py — 手动重试 / 回退功能单元测试

覆盖：
  - _save_turn_snapshot / _restore_turn_snapshot 快照机制
  - retry_last_turn  ：保留用户消息、丢弃旧回复、重新生成
  - rollback_turn    ：完全撤销整轮（含用户消息），同步 session
  - 边界条件：无快照时两个命令均安全降级
  - stats 在 retry / rollback 后正确还原
"""

import sys
from pathlib import Path
import os
import copy

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from unittest.mock import MagicMock, patch, call as mc

from mini_agent.llm.base import LLMResponse, LLMUsage, ToolCall


# ── 轻量 Agent stub（不依赖真实 LLM / session） ──────────────────────────────

def make_agent(responses: list[str]) -> "object":
    """
    构造一个最小化的 Agent stub，_agentic_loop 依次返回 responses。
    避免真实 LLM 调用、session IO、文件系统等外部依赖。
    """
    from pathlib import Path as _Path
    from mini_agent.config import (
        AppConfig, SessionConfig, PerceptionConfig, SkillConfig,
        CompressConfig, MemoryConfig, RetryConfig, SessionStats,
    )
    from mini_agent.agent import Agent

    # 通过真正的 dataclass __init__ 构造，子配置块都有合理默认值，
    # 只覆盖测试关心的开关，避免直接对只读的向后兼容 property 赋值。
    cfg = AppConfig(
        auto_approve=True,
        sandbox=False,
        project_root=_Path("/tmp"),
        stream=False,
        verbose=False,
        max_turns=10,
        model="test-model",
        agent_name="test",
        session=SessionConfig(auto_save=False),   # 禁用真实 session IO
        perception=PerceptionConfig(
            project_scan_enabled=False,
            file_watch_enabled=False,
            tool_cache_enabled=False,
            token_estimate_enabled=False,
        ),
        skill=SkillConfig(tracking_enabled=False, chunking_enabled=False),
        compress=CompressConfig(enabled=False),
        memory=MemoryConfig(enabled=False),
        retry=RetryConfig(max_retries=0, delay=0.0, verbose=False),
    )

    agent = Agent.__new__(Agent)
    agent.cfg          = cfg
    agent.stats        = SessionStats()
    agent._history     = []
    agent._turn_snapshot = None
    agent._file_watcher  = None
    agent._tool_cache    = None
    agent._project_snapshot = None
    agent._memory        = None
    agent._ctx_builder   = None
    agent.skill_loader   = None
    agent.guard          = MagicMock(check=MagicMock(return_value=True))
    agent.registry       = MagicMock(names=[])
    agent._session_mgr   = None
    agent._session       = None

    # 注入空重试策略
    from mini_agent.llm.retry import no_retry_policy
    agent._retry_policy = no_retry_policy()

    # _agentic_loop 按顺序返回 responses
    _iter = iter(responses)

    def _fake_loop():
        text = next(_iter, "")
        # 模拟追加 assistant 消息
        agent._history.append({"role": "assistant", "content": text})
        agent.stats.input_tokens  += 10
        agent.stats.output_tokens += 5
        agent.stats.tool_calls    += 0
        return text

    agent._agentic_loop = _fake_loop

    return agent


# ── 快照基础 ─────────────────────────────────────────────────────────────────

class TestSnapshot:
    def test_save_and_restore(self):
        agent = make_agent(["resp1"])
        agent._history = [{"role": "user", "content": "hello"}]
        agent.stats.turns        = 3
        agent.stats.input_tokens = 100

        agent._save_turn_snapshot()
        # 修改状态
        agent._history.append({"role": "assistant", "content": "hi"})
        agent.stats.turns = 5

        ok = agent._restore_turn_snapshot()
        assert ok is True
        assert len(agent._history) == 1
        assert agent.stats.turns == 3
        assert agent.stats.input_tokens == 100

    def test_restore_without_snapshot_returns_false(self):
        agent = make_agent([])
        assert agent._turn_snapshot is None
        ok = agent._restore_turn_snapshot()
        assert ok is False

    def test_snapshot_is_deep_copy(self):
        agent = make_agent([])
        agent._history = [{"role": "user", "content": "original"}]
        agent._save_turn_snapshot()
        # 修改 history 不影响快照
        agent._history[0]["content"] = "mutated"
        assert agent._turn_snapshot["history"][0]["content"] == "original"


# ── retry_last_turn ───────────────────────────────────────────────────────────

class TestRetryLastTurn:
    def test_retry_calls_llm_again_with_same_message(self):
        agent = make_agent(["second response"])

        # 模拟已经完成一轮，历史中有快照和首轮结果
        agent._save_turn_snapshot()
        agent._history.append({"role": "user", "content": "solve this"})
        agent.stats.turns += 1
        agent._history.append({"role": "assistant", "content": "first response"})

        # retry — _agentic_loop 会消耗 "second response"
        result = agent.retry_last_turn()

        assert result == "second response"
        roles = [m["role"] for m in agent._history]
        assert roles.count("user") == 1
        assert roles.count("assistant") == 1
        user_msg = next(m for m in agent._history if m["role"] == "user")
        assert user_msg["content"] == "solve this"
        assistant_msg = next(m for m in agent._history if m["role"] == "assistant")
        assert assistant_msg["content"] == "second response"

    def test_retry_without_snapshot_returns_empty(self):
        agent = make_agent([])
        assert agent._turn_snapshot is None
        result = agent.retry_last_turn()
        assert result == ""

    def test_retry_resets_stats_before_rerun(self):
        agent = make_agent(["resp1", "resp2"])

        # 首轮
        agent._save_turn_snapshot()
        snap_input = agent.stats.input_tokens
        agent._history.append({"role": "user", "content": "q"})
        agent.stats.turns += 1
        agent.stats.input_tokens += 50   # 模拟首轮消耗
        agent._history.append({"role": "assistant", "content": "resp1"})

        agent.retry_last_turn()

        # stats.turns 来自快照 + run_turn 的 +1，不应无限累加
        assert agent.stats.turns == 1   # 快照 turns=0，run_turn +1

    def test_retry_generates_new_snapshot(self):
        """retry 后新的快照被建立，下一次 retry 仍然可用。"""
        # 第一次 retry 消耗 "r2"，第二次 retry 消耗 "r3"
        agent = make_agent(["r2", "r3"])

        agent._save_turn_snapshot()
        agent._history.append({"role": "user", "content": "q"})
        agent.stats.turns += 1
        agent._history.append({"role": "assistant", "content": "r1"})

        agent.retry_last_turn()   # 消耗 r2，同时 run_turn 建立新快照

        # 再次 retry 应该可以工作（快照不为 None）
        assert agent._turn_snapshot is not None
        result2 = agent.retry_last_turn()
        assert result2 == "r3"


# ── rollback_turn ─────────────────────────────────────────────────────────────

class TestRollbackTurn:
    def test_rollback_removes_entire_turn(self):
        agent = make_agent([])

        # 模拟 turn 0 之前的历史
        agent._history = [{"role": "user", "content": "prev q"},
                          {"role": "assistant", "content": "prev a"}]
        agent.stats.turns = 2

        # 保存快照（此时 history 有 2 条）
        agent._save_turn_snapshot()

        # 模拟 turn 3
        agent._history.append({"role": "user", "content": "new q"})
        agent._history.append({"role": "assistant", "content": "new a"})
        agent.stats.turns = 3

        ok = agent.rollback_turn()

        assert ok is True
        assert len(agent._history) == 2          # 恢复到快照时的 2 条
        assert agent.stats.turns == 2
        # 最新的消息应该是快照时的那条 assistant
        assert agent._history[-1]["content"] == "prev a"

    def test_rollback_without_snapshot_returns_false(self):
        agent = make_agent([])
        assert agent._turn_snapshot is None
        ok = agent.rollback_turn()
        assert ok is False

    def test_rollback_clears_snapshot(self):
        """回退后快照被清除，再次 rollback 应该安全失败。"""
        agent = make_agent([])
        agent._save_turn_snapshot()
        agent._history.append({"role": "user", "content": "q"})
        agent._history.append({"role": "assistant", "content": "a"})
        agent.stats.turns = 1

        agent.rollback_turn()

        assert agent._turn_snapshot is None
        ok2 = agent.rollback_turn()
        assert ok2 is False

    def test_rollback_calls_save_session_when_enabled(self):
        """回退时应调用 save_session 同步 session 文件。"""
        agent = make_agent([])
        agent.cfg.session.auto_save = True
        agent._save_turn_snapshot()
        agent._history.append({"role": "user", "content": "q"})
        agent.stats.turns = 1

        agent.save_session = MagicMock(return_value="/tmp/session.json")
        agent.rollback_turn()

        agent.save_session.assert_called_once()

    def test_rollback_does_not_call_save_session_when_disabled(self):
        agent = make_agent([])
        agent.cfg.session.auto_save = False
        agent._save_turn_snapshot()
        agent._history.append({"role": "user", "content": "q"})
        agent.stats.turns = 1

        agent.save_session = MagicMock(return_value=None)
        agent.rollback_turn()

        agent.save_session.assert_not_called()

    def test_rollback_stats_fully_restored(self):
        """rollback 后 input/output/tool_calls 统计都回到快照值。"""
        agent = make_agent([])
        agent.stats.turns         = 5
        agent.stats.input_tokens  = 1000
        agent.stats.output_tokens = 500
        agent.stats.tool_calls    = 8

        agent._save_turn_snapshot()

        # 模拟一轮额外消耗
        agent.stats.turns         += 1
        agent.stats.input_tokens  += 200
        agent.stats.output_tokens += 80
        agent.stats.tool_calls    += 3
        agent._history.append({"role": "user", "content": "q"})

        agent.rollback_turn()

        assert agent.stats.turns         == 5
        assert agent.stats.input_tokens  == 1000
        assert agent.stats.output_tokens == 500
        assert agent.stats.tool_calls    == 8


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
