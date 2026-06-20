"""
tests/test_edit_approval_integration.py — Stage 1.5 验证

对应 self_evolution_implementation_plan.md Stage 1.5：
  把 PermissionGuard 的 (e)dit 审批编辑接入为 _type="user_correction" 消息，
  并复用 Stage 1.4 的纠正检测逻辑生成 human_feedback lesson。

覆盖范围：
  - HType.USER_CORRECTION 枚举 + make_user_correction() 构造函数
  - is_real_user_input() / is_turn_boundary() 把 USER_CORRECTION 视为真实用户输入
  - PermissionGuard.last_edit / pop_last_edit() 机制
  - ToolExecutor 检测编辑事件并触发 on_edit_detected 回调
  - Agent._on_edit_detected 端到端：写入 history + 生成 lesson
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mini_agent.history.entry import (
    HType, make_user_correction, is_real_user_input, is_turn_boundary,
)
from mini_agent.permissions import PermissionGuard


# ── HType.USER_CORRECTION 基础行为 ────────────────────────────────────────────

def test_make_user_correction_sets_correct_type():
    msg = make_user_correction("用户把命令改成了 ls -la")
    assert msg["role"] == "user"
    assert msg["_type"] == HType.USER_CORRECTION
    assert msg["content"] == "用户把命令改成了 ls -la"


def test_user_correction_is_real_user_input():
    msg = make_user_correction("修改内容")
    assert is_real_user_input(msg) is True


def test_user_correction_is_turn_boundary():
    msg = make_user_correction("修改内容")
    assert is_turn_boundary(msg) is True


def test_user_correction_distinct_from_user_input():
    """USER_CORRECTION 和 USER_INPUT 是不同的枚举值（便于审计区分来源），
    但在"是否算真实用户输入"这个判断上行为一致。"""
    assert HType.USER_CORRECTION != HType.USER_INPUT
    assert HType.USER_CORRECTION.value == "user_correction"


# ── PermissionGuard.last_edit / pop_last_edit ────────────────────────────────

@pytest.fixture
def guard(tmp_path: Path) -> PermissionGuard:
    return PermissionGuard(auto_approve=True, project_root=tmp_path)


def test_last_edit_initially_none(guard: PermissionGuard):
    assert guard.pop_last_edit() is None


def test_pop_last_edit_clears_after_read(guard: PermissionGuard):
    guard.last_edit = {"tool_name": "bash", "original": "ls", "edited": "ls -la"}
    first = guard.pop_last_edit()
    second = guard.pop_last_edit()
    assert first == {"tool_name": "bash", "original": "ls", "edited": "ls -la"}
    assert second is None


def test_edit_repr_uses_command_field_for_bash():
    repr_str = PermissionGuard._edit_repr("bash", {"command": "rm -rf /tmp/x"})
    assert repr_str == "rm -rf /tmp/x"


def test_edit_repr_uses_json_for_other_tools():
    repr_str = PermissionGuard._edit_repr("write_file", {"path": "/tmp/x", "content": "hi"})
    assert "path" in repr_str
    assert "/tmp/x" in repr_str


# ── ToolExecutor on_edit_detected 回调接入 ───────────────────────────────────

def _make_minimal_tool_executor(guard: PermissionGuard, on_edit_detected):
    """构造一个最小化 ToolExecutor，只用于验证 on_edit_detected 回调触发逻辑。"""
    from mini_agent.tool_executor import ToolExecutor
    from mini_agent.config import AppConfig, SessionStats
    from mini_agent.tools import ToolRegistry

    cfg = AppConfig(auto_approve=True)
    registry = ToolRegistry()
    stats = SessionStats()
    return ToolExecutor(
        cfg=cfg, registry=registry, guard=guard, stats=stats,
        on_edit_detected=on_edit_detected,
    )


def test_tool_executor_invokes_callback_when_edit_detected(guard: PermissionGuard, monkeypatch):
    """模拟 guard.check() 内部产生了一次编辑（写入 last_edit），
    ToolExecutor 应在 check() 返回后检测到并触发回调。"""
    captured = []
    executor = _make_minimal_tool_executor(guard, on_edit_detected=lambda e: captured.append(e))

    # 模拟 guard.check() 被调用时顺便设置了 last_edit（模拟用户在审批时编辑了命令）
    original_check = guard.check
    def fake_check(tool_name, tool_input):
        guard.last_edit = {"tool_name": tool_name, "original": "ls", "edited": "ls -la"}
        return True
    monkeypatch.setattr(guard, "check", fake_check)

    from mini_agent.llm import LLMResponse, ToolCall
    from mini_agent.llm.base import LLMUsage
    tc = ToolCall(id="1", name="bash", input={"command": "ls -la"})
    resp = LLMResponse(text="", tool_calls=[tc], usage=LLMUsage(), stop_reason="tool_use")
    # registry 没注册 bash 工具，调用会抛异常被捕获为 tool error，但不影响 on_edit_detected 触发
    executor.execute_all(resp)

    assert len(captured) == 1
    assert captured[0]["original"] == "ls"
    assert captured[0]["edited"] == "ls -la"


def test_tool_executor_no_callback_when_no_edit(guard: PermissionGuard):
    """没有发生编辑时（last_edit 始终为 None），回调不应被触发。"""
    captured = []
    executor = _make_minimal_tool_executor(guard, on_edit_detected=lambda e: captured.append(e))

    from mini_agent.llm import LLMResponse, ToolCall
    from mini_agent.llm.base import LLMUsage
    tc = ToolCall(id="1", name="bash", input={"command": "ls"})
    resp = LLMResponse(text="", tool_calls=[tc], usage=LLMUsage(), stop_reason="tool_use")
    executor.execute_all(resp)

    assert captured == []


def test_tool_executor_works_without_callback(guard: PermissionGuard):
    """on_edit_detected 为 None（未配置）时不应抛异常（默认值场景）。"""
    from mini_agent.tool_executor import ToolExecutor
    from mini_agent.config import AppConfig, SessionStats
    from mini_agent.tools import ToolRegistry

    cfg = AppConfig(auto_approve=True)
    executor = ToolExecutor(cfg=cfg, registry=ToolRegistry(), guard=guard, stats=SessionStats())

    from mini_agent.llm import LLMResponse, ToolCall
    from mini_agent.llm.base import LLMUsage
    tc = ToolCall(id="1", name="bash", input={"command": "ls"})
    resp = LLMResponse(text="", tool_calls=[tc], usage=LLMUsage(), stop_reason="tool_use")
    # 不应抛异常
    executor.execute_all(resp)
