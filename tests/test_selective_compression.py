"""
tests/test_selective_compression.py — SelectiveStrategy 单元测试

覆盖：
  - 基本压缩行为（高价值消息优先保留）
  - 最小 user_input 保留数量
  - dedup：skill_context / reminder 只保留最新一条
  - _fix_orphans：tool_result 必须配对 assistant_reply（含 tool_use）
  - 历史太短时原样返回
  - 自定义权重覆盖默认权重
  - 压缩结果含 _type 占位符
  - _last_user_msg 精确识别
  - reminder 去重守卫（_reminder_already_in_turn）
"""

import sys
import pytest

sys.path.insert(0, "src")

from mini_agent.history.compression import SelectiveStrategy, _fix_orphans
from mini_agent.history.entry import (
    HType,
    make_user_input, make_tool_result, make_assistant_reply,
    make_skill_context, make_reminder, make_compressed, make_compact_summary,
)
from mini_agent.context_builder import _last_user_msg


# ── helpers ───────────────────────────────────────────────────────────────────

def _assistant_with_tool(tool_name: str = "bash") -> dict:
    """返回含 tool_use block 的 assistant_reply。"""
    return make_assistant_reply([
        {"type": "tool_use", "id": f"t_{tool_name}", "name": tool_name, "input": {}},
    ])

class _FakeCfg:
    class compress:
        selective_weights = None
        selective_min_user_turns = 3


cfg = _FakeCfg()


# ── 基础用例 ──────────────────────────────────────────────────────────────────

class TestSelectiveStrategyBasic:

    def test_short_history_returned_unchanged(self):
        history = [make_user_input("hi"), make_assistant_reply([{"type": "text", "text": "hello"}])]
        result = SelectiveStrategy().compress(history, cfg)
        assert result == history

    def test_result_starts_with_compressed_placeholder(self):
        history = self._make_history(turns=8)
        result = SelectiveStrategy().compress(history, cfg)
        assert result[0].get("_type") == HType.COMPRESSED
        assert result[1].get("_type") == HType.COMPACT_SUMMARY

    def test_user_inputs_preserved_above_min(self):
        """最近 min_user_turns 条 user_input 必须出现在结果里。"""
        history = self._make_history(turns=10)
        strategy = SelectiveStrategy(min_user_turns=3)
        result = strategy.compress(history, cfg)
        user_inputs = [m for m in result if m.get("_type") == HType.USER_INPUT]
        assert len(user_inputs) >= 3

    def test_result_shorter_than_input(self):
        history = self._make_history(turns=10)
        result = SelectiveStrategy().compress(history, cfg)
        assert len(result) < len(history)

    def test_no_orphan_tool_results(self):
        """结果中每条 tool_result 前面必须有含 tool_use 的 assistant_reply。"""
        history = self._make_history_with_tools(turns=8)
        result = SelectiveStrategy().compress(history, cfg)
        for i, msg in enumerate(result):
            if msg.get("_type") == HType.TOOL_RESULT:
                assert i > 0, "tool_result 不能是第一条"
                prev = result[i - 1]
                content = prev.get("content", [])
                has_tool_use = (
                    isinstance(content, list)
                    and any(b.get("type") == "tool_use" for b in content if isinstance(b, dict))
                )
                assert has_tool_use, f"位置 {i} 的 tool_result 没有对应的 tool_use"

    def _make_history(self, turns: int) -> list:
        h = []
        for i in range(turns):
            h.append(make_user_input(f"用户消息 {i+1}"))
            h.append(make_assistant_reply([{"type": "text", "text": f"回复 {i+1}"}]))
        return h

    def _make_history_with_tools(self, turns: int) -> list:
        h = []
        for i in range(turns):
            h.append(make_user_input(f"用户消息 {i+1}"))
            h.append(_assistant_with_tool("bash"))
            h.append(make_tool_result(f"<tool_result>\n{{\"output\": \"ok {i}\"}}\n</tool_result>"))
        return h


# ── 去重逻辑 ──────────────────────────────────────────────────────────────────

class TestSelectiveDedup:

    def test_skill_context_only_latest_kept(self):
        """多条 skill_context 只保留最新一条。"""
        history = [
            make_user_input("第一轮"),
            make_assistant_reply([{"type": "text", "text": "回复1"}]),
            make_skill_context("skill v1"),
            make_user_input("第二轮"),
            make_assistant_reply([{"type": "text", "text": "回复2"}]),
            make_skill_context("skill v2"),
            make_user_input("第三轮"),
            make_assistant_reply([{"type": "text", "text": "回复3"}]),
            make_skill_context("skill v3"),  # 最新，应保留
            make_user_input("第四轮"),
            make_assistant_reply([{"type": "text", "text": "回复4"}]),
        ]
        result = SelectiveStrategy().compress(history, cfg)
        skill_contents = [m["content"] for m in result if m.get("_type") == HType.SKILL_CONTEXT]
        assert len(skill_contents) <= 1, f"应只保留 1 条 skill_context，实际: {skill_contents}"
        if skill_contents:
            assert skill_contents[0] == "skill v3", "应保留最新的 skill_context"

    def test_reminder_only_latest_kept(self):
        """多条同类 reminder 只保留最新一条。"""
        history = [
            make_user_input("第一轮"),
            make_assistant_reply([{"type": "text", "text": "ok"}]),
            make_reminder("user", "注意安全 v1"),
            make_user_input("第二轮"),
            make_assistant_reply([{"type": "text", "text": "ok"}]),
            make_reminder("user", "注意安全 v2"),
            make_user_input("第三轮"),
            make_assistant_reply([{"type": "text", "text": "ok"}]),
            make_reminder("user", "注意安全 v3"),
            make_user_input("第四轮"),
            make_assistant_reply([{"type": "text", "text": "ok"}]),
        ]
        result = SelectiveStrategy().compress(history, cfg)
        reminders = [m for m in result if m.get("_type") == HType.REMINDER]
        assert len(reminders) <= 1, f"应只保留 1 条 reminder，实际: {len(reminders)}"


# ── _fix_orphans ──────────────────────────────────────────────────────────────

class TestFixOrphans:

    def test_tool_result_pulls_in_assistant(self):
        """keep 集合包含 tool_result 时，对应的 assistant_reply(tool_use) 必须被拉入。"""
        history = [
            make_user_input("do something"),          # 0
            _assistant_with_tool("bash"),              # 1  ← 应被拉入
            make_tool_result("<tool_result>ok</tool_result>"),  # 2  ← 手动放入 keep
        ]
        keep = {2}  # 只选了 tool_result
        result = _fix_orphans(history, keep)
        assert 1 in result, "应将 assistant_reply(tool_use) 拉入 keep"

    def test_assistant_with_tool_pulls_in_result(self):
        """keep 集合包含含 tool_use 的 assistant_reply 时，紧随的 tool_result 也被拉入。"""
        history = [
            make_user_input("do something"),          # 0
            _assistant_with_tool("bash"),              # 1  ← 放入 keep
            make_tool_result("<tool_result>ok</tool_result>"),  # 2  ← 应被拉入
        ]
        keep = {0, 1}
        result = _fix_orphans(history, keep)
        assert 2 in result, "应将 tool_result 拉入 keep"

    def test_plain_assistant_no_side_effect(self):
        """普通 assistant_reply（无 tool_use）不触发 _fix_orphans 逻辑。"""
        history = [
            make_user_input("hi"),                    # 0
            make_assistant_reply([{"type": "text", "text": "hello"}]),  # 1
        ]
        keep = {0, 1}
        result = _fix_orphans(history, keep)
        assert result == {0, 1}


# ── 自定义权重 ────────────────────────────────────────────────────────────────

class TestCustomWeights:

    def test_custom_weight_zero_drops_type(self):
        """将 reminder 权重设为 0，压缩后 reminder 不出现在保留段。"""
        history = []
        for i in range(6):
            history.append(make_user_input(f"msg {i}"))
            history.append(make_assistant_reply([{"type": "text", "text": f"reply {i}"}]))
            history.append(make_reminder("user", f"reminder {i}"))

        strategy = SelectiveStrategy(weights={"reminder": 0.0})
        result = strategy.compress(history, cfg)
        reminders_in_result = [m for m in result if m.get("_type") == HType.REMINDER]
        # 权重 0 + 无位置加成 → 全部被截断（除非在最新 25% 里）
        # 至少比原来少
        original_reminders = sum(1 for m in history if m.get("_type") == HType.REMINDER)
        assert len(reminders_in_result) < original_reminders

    def test_tool_result_low_weight_preferentially_dropped(self):
        """tool_result 权重低，长历史下应优先被截断。"""
        history = []
        for i in range(8):
            history.append(make_user_input(f"任务 {i}"))
            history.append(_assistant_with_tool("bash"))
            history.append(make_tool_result(f"<tool_result>output {i}</tool_result>"))

        result = SelectiveStrategy().compress(history, cfg)
        tr_count = sum(1 for m in result if m.get("_type") == HType.TOOL_RESULT)
        ui_count = sum(1 for m in result if m.get("_type") == HType.USER_INPUT)
        # user_input 保留比例应高于 tool_result
        assert ui_count >= tr_count, (
            f"user_input({ui_count}) 应 >= tool_result({tr_count})，"
            "否则高价值内容被过度截断"
        )


# ── _last_user_msg 精确识别 ───────────────────────────────────────────────────

class TestLastUserMsg:

    def test_returns_real_user_input(self):
        history = [
            make_user_input("真实消息"),
            make_assistant_reply([{"type": "text", "text": "ok"}]),
            make_skill_context("# Skills"),           # 不是真实用户输入
            make_reminder("user", "注意！"),           # 不是真实用户输入
            make_tool_result("<tool_result>x</tool_result>"),  # 不是真实用户输入
        ]
        assert _last_user_msg(history) == "真实消息"

    def test_skips_tool_result(self):
        history = [
            make_user_input("真实消息"),
            make_tool_result("<tool_result>output</tool_result>"),
        ]
        assert _last_user_msg(history) == "真实消息"

    def test_skips_skill_context(self):
        history = [
            make_user_input("真实消息"),
            make_skill_context("# Skills\n..."),
        ]
        assert _last_user_msg(history) == "真实消息"

    def test_backward_compatible_no_type(self):
        """无 _type 字段时，向后兼容逻辑应排除前缀消息。"""
        history = [
            {"role": "user", "content": "真实消息"},
            {"role": "user", "content": "<tool_result>output</tool_result>"},
            {"role": "user", "content": "[Previous conversation compressed]"},
        ]
        assert _last_user_msg(history) == "真实消息"

    def test_empty_history(self):
        assert _last_user_msg([]) == ""


# ── reminder 去重守卫（集成测试） ─────────────────────────────────────────────

class TestReminderDedup:
    """测试 _reminder_already_in_turn 逻辑（通过直接调用方法）。"""

    def _make_agent(self):
        from mini_agent.agent import Agent
        from mini_agent.config import AppConfig
        from mini_agent.history_manager import HistoryManager

        agent = Agent.__new__(Agent)
        agent.cfg = AppConfig()
        agent._history = []
        agent._hist = HistoryManager(cfg=agent.cfg)
        agent._hist._history = agent._history
        return agent

    def test_no_reminder_in_turn(self):
        agent = self._make_agent()
        agent._history.append(make_user_input("用户消息"))
        assert not agent._reminder_already_in_turn("my_reminder")

    def test_reminder_already_injected(self):
        agent = self._make_agent()
        agent._history.append(make_user_input("用户消息"))
        agent._history.append(make_reminder("user", "my_reminder: 注意！"))
        assert agent._reminder_already_in_turn("my_reminder")

    def test_reminder_in_previous_turn_not_counted(self):
        """上一轮注入的 reminder 不算作当前轮重复。"""
        agent = self._make_agent()
        agent._history.append(make_user_input("第一轮"))
        agent._history.append(make_reminder("user", "my_reminder: 注意！"))
        agent._history.append(make_assistant_reply([{"type": "text", "text": "ok"}]))
        agent._history.append(make_user_input("第二轮"))  # 新 turn 开始
        # 第二轮还没注入 reminder
        assert not agent._reminder_already_in_turn("my_reminder")

    def test_different_reminder_not_blocked(self):
        agent = self._make_agent()
        agent._history.append(make_user_input("用户消息"))
        agent._history.append(make_reminder("user", "other_reminder: 注意！"))
        # 不同的 reminder name，不应被去重
        assert not agent._reminder_already_in_turn("my_reminder")
