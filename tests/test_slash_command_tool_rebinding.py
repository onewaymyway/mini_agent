"""
tests/test_slash_command_tool_rebinding.py

对应对话记录里定位的绑定 bug：run_slash_command 工具的闭包捕获了具体的
Agent 实例，注册在跨 Agent 实例共享的全局 registry 上。此前用
"registry 里已经有这个名字就跳过注册" 做幂等，会把工具永久绑死在第一个
注册它的 Agent 实例上。修复方式是 register_slash_command_tool() 内部改用
override=True，允许后一个 Agent 实例的注册覆盖前一个——本测试只覆盖
"注册层面确实会重新绑定"这一点，不依赖真实 Agent/LLM/REPL。
"""

from __future__ import annotations

import pytest

from mini_agent.tools import ToolRegistry
from mini_agent.tools.slash_command import register_slash_command_tool


class _FakeAgentHolder:
    """最小化替身：register_slash_command_tool() 只需要一个可以被闭包捕获的
    对象，真正调用 run_slash_command 时才会用到 agent/skill_loader，这里
    不需要覆盖那条路径（那部分依赖真实 REPL._handle_slash，不在本测试范围）。"""

    def __init__(self, label: str):
        self.label = label
        self.skill_loader = None


class TestRunSlashCommandRebinding:
    def test_second_registration_does_not_raise(self):
        # 修复前：默认 override=False，同一个 registry 上第二次注册会
        # 直接 ValueError("already registered")。
        registry = ToolRegistry()
        agent_a = _FakeAgentHolder("a")
        agent_b = _FakeAgentHolder("b")

        register_slash_command_tool(registry, agent_a)
        # 不应该抛异常——这是本次修复要验证的核心行为。
        register_slash_command_tool(registry, agent_b)

        assert "run_slash_command" in registry.names

    def test_second_registration_rebinds_to_latest_agent(self):
        # 修复后应该是"后注册的覆盖先注册的"，工具闭包指向最新的 agent，
        # 而不是永远停留在第一个注册它的 agent 上。
        registry = ToolRegistry()
        agent_a = _FakeAgentHolder("a")
        agent_b = _FakeAgentHolder("b")

        register_slash_command_tool(registry, agent_a)
        tool_def_after_a = registry.get("run_slash_command")

        register_slash_command_tool(registry, agent_b)
        tool_def_after_b = registry.get("run_slash_command")

        # 两次注册应该产生不同的函数对象（各自闭包捕获了不同的 agent），
        # 且 registry 里最终留下的是最后一次注册的那个。
        assert tool_def_after_a.fn is not tool_def_after_b.fn
        assert registry.get("run_slash_command").fn is tool_def_after_b.fn

    def test_third_agent_still_only_one_tool_registered(self):
        # 多次重复注册（模拟多次 cron 触发）不会在 registry 里堆积出多个
        # 同名工具，也不会累积异常。
        registry = ToolRegistry()
        for i in range(5):
            register_slash_command_tool(registry, _FakeAgentHolder(f"agent_{i}"))

        assert registry.names.count("run_slash_command") == 1
