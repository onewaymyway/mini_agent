"""tests/test_cli_profile_preference_commands.py

覆盖 next_doc/profile_context_sources_completeness_plan.md 方向 D：
`/profile set|unset|show` 三个 slash 子命令——此前 `set_preference()`
有实现但代码库里没有任何调用方，是一个"死功能"，这三个 CLI 子命令是
补上的第一个写入/读取入口。

测试策略：`_handle_slash()` 分支只会用到 `agent._profile_mgr`，不需要
构造完整的 `Agent` 实例——用一个只带这一个属性的假对象即可，跟仓库里
其它轻量 CLI 测试（如 `test_daemon_connected_repl_commands.py`）的做法
一致，只不过这里不需要 mock 网络。
"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from mini_agent.cli.repl import _handle_slash
from mini_agent.profile import UserProfileManager
from mini_agent.storage.paths import AgentPaths


class _FakeAgent:
    """`_handle_slash` 的 profile 分支只读写 `agent._profile_mgr`。"""

    def __init__(self, profile_mgr):
        self._profile_mgr = profile_mgr


def _run(cmd: str, agent) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _handle_slash(cmd, agent, None)
    return buf.getvalue()


class TestProfileSetUnsetShow(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))
        self.mgr = UserProfileManager(self.paths)
        self.agent = _FakeAgent(self.mgr)

    def tearDown(self):
        self._tmp.cleanup()

    def test_set_writes_preference(self):
        _run("/profile set tone 轻松幽默", self.agent)
        profile = self.mgr.load()
        self.assertEqual(profile.preferences.get("tone"), "轻松幽默")

    def test_set_value_with_spaces_joined(self):
        _run("/profile set reply_style 简洁 结构化 摘要", self.agent)
        profile = self.mgr.load()
        self.assertEqual(profile.preferences.get("reply_style"), "简洁 结构化 摘要")

    def test_set_missing_args_prints_usage_without_writing(self):
        out = _run("/profile set onlykey", self.agent)
        self.assertIn("Usage", out)
        profile = self.mgr.load()
        self.assertEqual(profile.preferences, {})

    def test_show_without_key_lists_all(self):
        self.mgr.set_preference("tone", "casual")
        self.mgr.set_preference("lang", "zh")
        out = _run("/profile show", self.agent)
        self.assertIn("tone = 'casual'", out)
        self.assertIn("lang = 'zh'", out)

    def test_show_with_key_shows_single_value(self):
        self.mgr.set_preference("tone", "casual")
        self.mgr.set_preference("lang", "zh")
        out = _run("/profile show tone", self.agent)
        self.assertIn("tone = 'casual'", out)
        self.assertNotIn("lang", out)

    def test_show_empty_preferences_hints_usage(self):
        out = _run("/profile show", self.agent)
        self.assertIn("尚未设置任何偏好", out)

    def test_get_alias_behaves_like_show(self):
        self.mgr.set_preference("tone", "casual")
        out = _run("/profile get tone", self.agent)
        self.assertIn("tone = 'casual'", out)

    def test_unset_removes_existing_key(self):
        self.mgr.set_preference("tone", "casual")
        out = _run("/profile unset tone", self.agent)
        self.assertIn("已删除偏好", out)
        profile = self.mgr.load()
        self.assertNotIn("tone", profile.preferences)

    def test_unset_missing_key_reports_not_found(self):
        out = _run("/profile unset nope", self.agent)
        self.assertIn("未找到偏好", out)

    def test_disabled_when_profile_mgr_absent(self):
        agent = _FakeAgent(None)
        out = _run("/profile set tone casual", agent)
        self.assertIn("未启用", out)
        out = _run("/profile show", agent)
        self.assertIn("未启用", out)
        out = _run("/profile unset tone", agent)
        self.assertIn("未启用", out)


if __name__ == "__main__":
    unittest.main()
