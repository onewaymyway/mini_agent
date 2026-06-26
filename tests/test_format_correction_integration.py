"""
tests/test_format_correction_integration.py — 格式纠错功能的 Agent 集成测试

验证范围：当模型响应里 tool_calls=[]（解析失败）但文本中含有"看起来想调用
工具但格式损坏"的痕迹时，_agentic_loop() 应该：
  1. 不直接把这个半成品输出当成最终答案 break 掉
  2. 自动以 user 角色（_type=format_correction）注入纠错提示
  3. 让 loop 继续，再调用一次 LLM，给模型重试机会
  4. 模型这次给出有效工具调用 / 正常回复后，流程能继续往下走完

同时验证：
  - 重试次数有上限（max_retries_per_turn），超过后老老实实 break，不死循环
  - 功能可以通过 cfg.format_correction.enabled=False 关掉，关掉后行为退回旧逻辑
  - 不会对正常的、不含协议关键字的最终回复误触发
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import mini_agent.tools.builtin  # noqa — register tools
from mini_agent.llm.base import LLMResponse, LLMUsage, ToolCall


def make_response(text="", tool_calls=None) -> LLMResponse:
    return LLMResponse(
        text=text, tool_calls=tool_calls or [],
        usage=LLMUsage(10, 20, 30), stop_reason="end_turn",
    )


class TestFormatCorrectionIntegration(unittest.TestCase):

    def _make_agent(self, responses: list, **cfg_overrides):
        from mini_agent.agent import Agent
        from mini_agent.config import load_config
        from mini_agent.permissions import PermissionGuard

        cfg = load_config()
        cfg.stream = False
        cfg.session.auto_save = False
        for k, v in cfg_overrides.items():
            # 支持形如 format_correction__max_retries_per_turn=1 的双下划线嵌套覆盖
            if "__" in k:
                section, field = k.split("__", 1)
                setattr(getattr(cfg, section), field, v)
            else:
                setattr(cfg, k, v)

        mock_client = MagicMock()
        mock_client.chat.side_effect = responses
        guard = PermissionGuard(auto_approve=True)
        agent = Agent(cfg=cfg, guard=guard, llm_client=mock_client)
        return agent, mock_client

    # ── 核心场景：解析失败 → 自动纠错 → 重试成功 ─────────────────────────────

    def test_broken_tool_use_triggers_correction_then_succeeds(self):
        """第一次响应是案例1式的损坏 <tool_use>，第二次响应是正常最终回复。"""
        broken_text = (
            "我来帮你看看。\n\n<tool_use>\n"
            '{"name": "bash",\n<tool_use>'
        )
        responses = [
            make_response(text=broken_text),       # 解析失败：tool_calls=[]
            make_response(text="已经处理完成。"),    # 正常最终回复
        ]
        agent, mock_client = self._make_agent(responses)

        result = agent.run_turn("帮我看一下环境")

        # 最终结果应该是第二次的正常回复，而不是中途那段半成品文本
        self.assertEqual(result, "已经处理完成。")
        # LLM 应该被调用了两次（第一次失败 + 纠错后重试一次）
        self.assertEqual(mock_client.chat.call_count, 2)
        # 历史中必须出现一条 _type=format_correction 的 user 消息
        correction_msgs = [
            m for m in agent._history if m.get("_type") == "format_correction"
        ]
        self.assertEqual(len(correction_msgs), 1)
        self.assertEqual(correction_msgs[0]["role"], "user")
        self.assertIn("System Notice", correction_msgs[0]["content"])

    def test_tag_role_confusion_case_triggers_correction(self):
        """用户报告的案例2：<tool_result> 开头、</tool_use> 收尾的标签混淆。"""
        broken_text = (
            '<tool_result>{"name": "bash", "input": {"command": "echo hi"}}'
            "\n</tool_use>"
        )
        responses = [
            make_response(text=broken_text),
            make_response(text="好了，已经执行。"),
        ]
        agent, mock_client = self._make_agent(responses)

        result = agent.run_turn("发个通知测试一下")

        self.assertEqual(result, "好了，已经执行。")
        self.assertEqual(mock_client.chat.call_count, 2)
        correction_msgs = [
            m for m in agent._history if m.get("_type") == "format_correction"
        ]
        self.assertEqual(len(correction_msgs), 1)

    def test_correction_then_valid_tool_call_executes_normally(self):
        """纠错重试后，模型给出一个真正合法的工具调用，后续工具执行流程应正常走完。"""
        broken_text = "<tool_use>\n{\"name\": \"bash\","
        tc = ToolCall(id="tc_1", name="bash", input={"command": "echo hi"})
        responses = [
            make_response(text=broken_text),
            LLMResponse(text="", tool_calls=[tc], usage=LLMUsage(), stop_reason="tool_use"),
            make_response(text="执行完毕。"),
        ]
        agent, mock_client = self._make_agent(responses)

        result = agent.run_turn("跑一下echo")

        self.assertEqual(result, "执行完毕。")
        self.assertEqual(mock_client.chat.call_count, 3)
        # 工具结果应该已正常回注历史
        tool_result_msgs = [
            m for m in agent._history
            if m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and "<tool_result>" in m["content"]
        ]
        self.assertEqual(len(tool_result_msgs), 1)

    # ── 上限保护：避免模型持续输出坏格式导致死循环 ───────────────────────────

    def test_max_retries_exhausted_falls_back_to_break(self):
        """连续多次都解析失败时，超过 max_retries_per_turn 后应停止重试并返回。"""
        broken_text = "<tool_use>\n{\"name\": \"bash\","
        # 4 次都是坏格式（超过下面设置的 max_retries_per_turn=2）
        responses = [make_response(text=broken_text) for _ in range(4)]
        agent, mock_client = self._make_agent(
            responses, format_correction__max_retries_per_turn=2,
        )

        result = agent.run_turn("一直输出坏格式")

        # 纠错最多重试 2 次：第1次响应失败(检测+注入,retry=1)，第2次响应失败
        # (检测+注入,retry=2)，第3次响应失败但 retry 已达上限 → break。
        # 因此一共调用 3 次 LLM（初始 1 次 + 2 次重试）。
        self.assertEqual(mock_client.chat.call_count, 3)
        correction_msgs = [
            m for m in agent._history if m.get("_type") == "format_correction"
        ]
        self.assertEqual(len(correction_msgs), 2)
        # 最终返回的是第三次（最后一次）调用的原始残破文本，而不是抛异常
        self.assertEqual(result, broken_text)

    # ── 开关：可以整体关闭，行为退回旧逻辑（直接 break）──────────────────────

    def test_disabled_config_falls_back_to_old_break_behavior(self):
        broken_text = "<tool_use>\n{\"name\": \"bash\","
        responses = [make_response(text=broken_text)]
        agent, mock_client = self._make_agent(
            responses, format_correction__enabled=False,
        )

        result = agent.run_turn("测试关闭开关")

        # 关闭后应该只调用一次 LLM，直接把半成品文本当成最终结果返回（旧行为）
        self.assertEqual(mock_client.chat.call_count, 1)
        self.assertEqual(result, broken_text)
        correction_msgs = [
            m for m in agent._history if m.get("_type") == "format_correction"
        ]
        self.assertEqual(len(correction_msgs), 0)

    # ── 不应误触发：正常最终回复 ──────────────────────────────────────────────

    def test_normal_final_reply_does_not_trigger_correction(self):
        responses = [make_response(text="任务已完成，没有需要执行的操作。")]
        agent, mock_client = self._make_agent(responses)

        result = agent.run_turn("帮我看一下情况")

        self.assertEqual(mock_client.chat.call_count, 1)
        self.assertEqual(result, "任务已完成，没有需要执行的操作。")
        correction_msgs = [
            m for m in agent._history if m.get("_type") == "format_correction"
        ]
        self.assertEqual(len(correction_msgs), 0)


if __name__ == "__main__":
    unittest.main()
