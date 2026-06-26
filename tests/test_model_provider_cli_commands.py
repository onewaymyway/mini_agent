"""
tests/test_model_provider_cli_commands.py

覆盖 /model 与 /provider switch 这两个 slash 命令的 CLI 派发层，确保它们
真正调用 Agent.switch_model() / switch_to_provider_default()（从而让后续
LLM 调用使用新的 client），而不是只修改一个不会被实际读取的配置字符串。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mini_agent.llm.base import LLMClient, LLMConfig
from mini_agent.llm.client_pool import LLMClientPool, ProviderEntry


def make_config(provider="anthropic", model="claude-opus-4-5", api_key="test-key") -> LLMConfig:
    return LLMConfig(provider=provider, model=model, api_key=api_key)


class ConcreteClient(LLMClient):
    def chat(self, messages, system, tools):
        raise NotImplementedError

    def stream(self, messages, system, tools, on_token):
        raise NotImplementedError


def make_agent_with_pool(entries_cfg):
    """构造一个携带真实 LLMClientPool（多条 entries）的轻量 Agent 替身。"""
    import mini_agent.tools.builtin  # noqa: ensure tools registered
    from mini_agent.agent import Agent
    from mini_agent.config import load_config
    from mini_agent.permissions import PermissionGuard

    cfg = load_config()
    cfg.api_key = "test"
    cfg.stream = False

    mock_client = MagicMock(spec=LLMClient)
    guard = PermissionGuard(auto_approve=True)
    agent = Agent(cfg=cfg, guard=guard, llm_client=mock_client)

    entries = []
    clients = []
    for ecfg in entries_cfg:
        c = ConcreteClient(ecfg)
        clients.append(c)
        entries.append(ProviderEntry(config=ecfg, client=c))
    agent._client_pool = LLMClientPool(entries=entries)
    agent._llm = clients[0]
    return agent, clients


class TestModelSlashCommand(unittest.TestCase):
    """/model <name> 应通过 agent.switch_model() 真正切换正在使用的 client。"""

    def test_model_command_switches_existing_entry(self):
        from mini_agent.cli.repl import _handle_slash

        cfg_a = make_config(provider="anthropic", model="claude-opus-4-5")
        cfg_b = make_config(provider="openai", model="gpt-4o")
        agent, (client_a, client_b) = make_agent_with_pool([cfg_a, cfg_b])

        _handle_slash("/model gpt-4o", agent, skill_loader=MagicMock())

        self.assertIs(agent.llm_client, client_b)
        self.assertEqual(agent.cfg.model, "gpt-4o")
        self.assertEqual(agent.cfg.llm_provider, "openai")

    def test_model_command_creates_new_entry_for_unknown_model(self):
        from mini_agent.cli.repl import _handle_slash

        cfg_a = make_config(provider="anthropic", model="claude-opus-4-5")
        agent, (client_a,) = make_agent_with_pool([cfg_a])

        new_client = ConcreteClient(make_config(provider="anthropic", model="claude-haiku-4-5"))
        with patch("mini_agent.agent.create_client", return_value=new_client):
            _handle_slash("/model claude-haiku-4-5", agent, skill_loader=MagicMock())

        self.assertIs(agent.llm_client, new_client)
        self.assertEqual(agent.cfg.model, "claude-haiku-4-5")
        # 原 provider 不变
        self.assertEqual(agent.cfg.llm_provider, "anthropic")

    def test_model_command_without_arg_shows_usage_without_crashing(self):
        from mini_agent.cli.repl import _handle_slash

        cfg_a = make_config()
        agent, _ = make_agent_with_pool([cfg_a])
        # 不应抛异常
        _handle_slash("/model", agent, skill_loader=MagicMock())


class TestProviderSwitchSlashCommand(unittest.TestCase):
    """/provider switch <name> [model] 应通过 agent.switch_to_provider_default()
    真正切换正在使用的 client，且保留 fallback chain 中其余条目。"""

    def test_provider_switch_uses_first_entry_as_default(self):
        from mini_agent.cli.commands.providers import handle_provider_cmd

        cfg_a = make_config(provider="anthropic", model="claude-opus-4-5")
        cfg_b1 = make_config(provider="openai", model="gpt-4o")
        cfg_b2 = make_config(provider="openai", model="gpt-4o-mini")
        agent, (client_a, client_b1, client_b2) = make_agent_with_pool([cfg_a, cfg_b1, cfg_b2])

        handle_provider_cmd(["switch", "openai"], agent)

        self.assertIs(agent.llm_client, client_b1)  # 第一条 = 默认模型
        self.assertEqual(agent.cfg.model, "gpt-4o")
        self.assertEqual(agent.cfg.llm_provider, "openai")
        # fallback chain 未被破坏性地坍缩成单条
        self.assertEqual(len(agent._client_pool._entries), 3)

    def test_provider_switch_with_explicit_model(self):
        from mini_agent.cli.commands.providers import handle_provider_cmd

        cfg_a = make_config(provider="anthropic", model="claude-opus-4-5")
        cfg_b1 = make_config(provider="openai", model="gpt-4o")
        cfg_b2 = make_config(provider="openai", model="gpt-4o-mini")
        agent, (client_a, client_b1, client_b2) = make_agent_with_pool([cfg_a, cfg_b1, cfg_b2])

        handle_provider_cmd(["switch", "openai", "gpt-4o-mini"], agent)

        self.assertIs(agent.llm_client, client_b2)
        self.assertEqual(agent.cfg.model, "gpt-4o-mini")

    def test_provider_switch_unknown_provider_builds_new_entry(self):
        from mini_agent.cli.commands.providers import handle_provider_cmd

        cfg_a = make_config(provider="anthropic", model="claude-opus-4-5")
        agent, (client_a,) = make_agent_with_pool([cfg_a])

        new_client = ConcreteClient(make_config(provider="openai", model="gpt-4o"))
        with patch("mini_agent.agent.create_client", return_value=new_client), \
             patch("mini_agent.llm.client_pool._get_env_api_key", return_value="env-key"):
            handle_provider_cmd(["switch", "openai", "gpt-4o"], agent)

        self.assertIs(agent.llm_client, new_client)
        self.assertEqual(agent.cfg.llm_provider, "openai")
        self.assertEqual(agent.cfg.model, "gpt-4o")


if __name__ == "__main__":
    unittest.main(verbosity=2)
