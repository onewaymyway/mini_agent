"""
tests/test_reminder_pre_tool.py — [具身改进 A3] pre_tool reminder 前馈控制测试

覆盖：
  1. loader 能正确解析 trigger_event=pre_tool 的 reminder 文件
  2. matcher.match_pre_tool 按 tool_name 正确匹配/不匹配
  3. manager.check_pre_tool 受 enabled / pre_tool_enabled 开关控制
  4. ToolExecutor.execute_all 在工具真正执行前调用 inject_reminder 回调
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from mini_agent.reminders.loader import (
    Reminder,
    ReminderCondition,
    ReminderLoader,
    TRIGGER_PRE_TOOL,
)
from mini_agent.reminders.matcher import ReminderMatcher
from mini_agent.reminders.manager import ReminderManager


def _make_pre_tool_reminder(name="warn_read", tool_name="read_file", priority=50):
    return Reminder(
        name=name,
        trigger_event=TRIGGER_PRE_TOOL,
        condition=ReminderCondition(tool_name=tool_name),
        inject_as="user",
        priority=priority,
        enabled=True,
        content="提示内容",
    )


class TestReminderLoaderPreTool(unittest.TestCase):
    def test_loader_accepts_pre_tool_trigger(self):
        """loader 解析 trigger_event: pre_tool 的文件时不应被跳过。"""
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            fp = Path(d) / "warn.md"
            fp.write_text(
                "---\n"
                "name: warn_read\n"
                "trigger_event: pre_tool\n"
                "condition:\n"
                "  tool_name: read_file\n"
                "inject_as: user\n"
                "priority: 40\n"
                "enabled: true\n"
                "---\n\n"
                "正文内容",
                encoding="utf-8",
            )
            loader = ReminderLoader(system_dir=Path(d), custom_dir=None)
            reminders = loader.load()
            self.assertEqual(len(reminders), 1)
            self.assertEqual(reminders[0].trigger_event, TRIGGER_PRE_TOOL)
            self.assertEqual(reminders[0].name, "warn_read")


class TestReminderMatcherPreTool(unittest.TestCase):
    def test_match_by_tool_name(self):
        r = _make_pre_tool_reminder(tool_name="read_file")
        matcher = ReminderMatcher([r])
        matched = matcher.match_pre_tool("read_file", {})
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].name, "warn_read")

    def test_no_match_for_other_tool(self):
        r = _make_pre_tool_reminder(tool_name="read_file")
        matcher = ReminderMatcher([r])
        matched = matcher.match_pre_tool("bash", {})
        self.assertEqual(matched, [])

    def test_empty_tool_name_condition_matches_any_tool(self):
        r = _make_pre_tool_reminder(tool_name=None)
        matcher = ReminderMatcher([r])
        matched = matcher.match_pre_tool("bash", {})
        self.assertEqual(len(matched), 1)

    def test_other_trigger_events_not_matched(self):
        """post_tool 类型的 reminder 不应在 match_pre_tool 中被匹配到。"""
        from mini_agent.reminders.loader import TRIGGER_POST_TOOL

        r = Reminder(
            name="post_only",
            trigger_event=TRIGGER_POST_TOOL,
            condition=ReminderCondition(tool_name="read_file"),
        )
        matcher = ReminderMatcher([r])
        matched = matcher.match_pre_tool("read_file", {})
        self.assertEqual(matched, [])


class TestReminderManagerPreTool(unittest.TestCase):
    def _make_manager_with(self, reminders):
        cfg = MagicMock()
        cfg.reminder.enabled = True
        cfg.reminder.verbose = False
        cfg.reminder.custom_dir = None
        cfg.reminder.max_per_turn = 3
        cfg.reminder.pre_tool_enabled = True
        cfg.prompts_dir = None
        mgr = ReminderManager(cfg)
        mgr._reminders = reminders
        mgr._matcher.update(reminders)
        return mgr, cfg

    def test_check_pre_tool_returns_matches(self):
        r = _make_pre_tool_reminder()
        mgr, _ = self._make_manager_with([r])
        result = mgr.check_pre_tool("read_file", {})
        self.assertEqual(len(result), 1)

    def test_check_pre_tool_disabled_by_config(self):
        r = _make_pre_tool_reminder()
        mgr, cfg = self._make_manager_with([r])
        cfg.reminder.pre_tool_enabled = False
        result = mgr.check_pre_tool("read_file", {})
        self.assertEqual(result, [])

    def test_check_pre_tool_disabled_globally(self):
        r = _make_pre_tool_reminder()
        mgr, cfg = self._make_manager_with([r])
        cfg.reminder.enabled = False
        result = mgr.check_pre_tool("read_file", {})
        self.assertEqual(result, [])


class TestToolExecutorPreToolInjection(unittest.TestCase):
    """验证 ToolExecutor 在工具执行前（甚至权限检查前）调用注入回调。"""

    def test_pre_tool_reminder_injected_before_execution(self):
        from mini_agent.tool_executor import ToolExecutor

        reminder = _make_pre_tool_reminder()
        reminder_mgr = MagicMock()
        reminder_mgr.check_pre_tool.return_value = [reminder]
        injected = []

        cfg = MagicMock()
        cfg.verbose = False
        cfg.tool_stats_enabled = False
        cfg.raw_output = False
        cfg.tool_result_trim_enabled = False

        registry = MagicMock()
        registry.call.return_value = "ok"

        guard = MagicMock()
        guard.check.return_value = True
        guard.pop_last_edit.return_value = None

        stats = MagicMock()
        stats.tool_calls = 0

        executor = ToolExecutor(
            cfg=cfg,
            registry=registry,
            guard=guard,
            stats=stats,
            reminder_mgr=reminder_mgr,
            inject_reminder=lambda r: injected.append(r.name),
        )

        tc = MagicMock()
        tc.name = "read_file"
        tc.input = {"path": "/tmp/x"}

        response = MagicMock()
        response.tool_calls = [tc]

        executor.execute_all(response)

        reminder_mgr.check_pre_tool.assert_called_once_with("read_file", {"path": "/tmp/x"})
        self.assertEqual(injected, ["warn_read"])

    def test_no_reminder_mgr_does_not_crash(self):
        """未配置 reminder_mgr 时，pre_tool 检查应静默跳过，不影响工具执行。"""
        from mini_agent.tool_executor import ToolExecutor

        cfg = MagicMock()
        cfg.verbose = False
        cfg.tool_stats_enabled = False
        cfg.raw_output = False
        cfg.tool_result_trim_enabled = False

        registry = MagicMock()
        registry.call.return_value = "ok"

        guard = MagicMock()
        guard.check.return_value = True
        guard.pop_last_edit.return_value = None

        stats = MagicMock()
        stats.tool_calls = 0

        executor = ToolExecutor(
            cfg=cfg, registry=registry, guard=guard, stats=stats,
        )

        tc = MagicMock()
        tc.name = "read_file"
        tc.input = {"path": "/tmp/x"}
        response = MagicMock()
        response.tool_calls = [tc]

        _, result_strs = executor.execute_all(response)
        self.assertEqual(result_strs, ["ok"])


if __name__ == "__main__":
    unittest.main()
