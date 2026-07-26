"""
tests/test_browser_cdp_dedicated_port_fallback.py — browser-cdp skill 的
`--dedicated` 复用逻辑新增单测。

背景（用户反馈）：执行 zhihu_content_publish workflow 时，zhihu_search.py
（以及其他知乎脚本）默认的专用实例名（--name）跟 workflow 约定的
`zhihu_session` 不一致，导致 cmd_dedicated 按 name 去 registry 里查不到记录，
从而总是新开一个浏览器，而不是复用已经登录好的那个实例。

两处修复：
1. 「最省事」：zhihu_search.py / zhihu_column_search.py / zhihu_hot.py 的
   --name 默认值统一改成 zhihu_session（见对应脚本改动，本文件不重复测试
   argparse 默认值，只测下面第 2 点的通用兜底逻辑）。
2. 「治本」：cmd_dedicated 里，当按 name 找不到活着的实例、但调用方显式传了
   --port 且该端口已经有能连上的调试浏览器时，直接复用那个端口（并回填
   registry），不再新建。这样即使以后又出现 name 对不上的调用，只要显式
   指定了正确的端口，也不会误开新实例。

这里只测纯逻辑分支，不启动真实 Chrome：把 is_debug_port_alive /
version_info / _load_registry / _save_registry 都 mock 掉。
"""
from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

SKILL_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "browser-cdp"
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

import browser_launch  # noqa: E402


def _make_args(**overrides) -> argparse.Namespace:
    base = dict(
        name="zhihu_search",  # 有意跟 registry 里 zhihu_session 的记录不一致
        host="127.0.0.1",
        port=9333,  # 显式传入
        user_data_dir=None,
        headless=False,
        binary=None,
        spawn_timeout=30.0,
        start_url="about:blank",
        window_size="1366,900",
        user_agent=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class TestDedicatedExplicitPortFallback(unittest.TestCase):
    def test_reuses_alive_port_even_when_name_not_registered(self):
        """name 在 registry 里查不到，但显式 --port 指向的端口是活的 -> 应复用，不应 spawn。"""
        args = _make_args()

        with patch.object(browser_launch, "_load_registry", return_value={}), \
             patch.object(browser_launch, "_save_registry") as mock_save, \
             patch.object(browser_launch, "_read_profile_lock", return_value=None), \
             patch.object(browser_launch, "is_debug_port_alive", return_value=True), \
             patch.object(browser_launch, "version_info", return_value={"Browser": "Chrome/999"}), \
             patch.object(browser_launch, "spawn_browser") as mock_spawn:
            browser_launch.cmd_dedicated(args)

        mock_spawn.assert_not_called()
        mock_save.assert_called_once()
        saved_registry = mock_save.call_args[0][0]
        self.assertEqual(saved_registry["zhihu_search"]["port"], 9333)

    def test_spawns_new_when_explicit_port_not_alive_and_name_unregistered(self):
        """name 查不到，且显式端口也连不上 -> 才应该真正走新建流程。"""
        args = _make_args()

        with patch.object(browser_launch, "_load_registry", return_value={}), \
             patch.object(browser_launch, "_save_registry") as mock_save, \
             patch.object(browser_launch, "_read_profile_lock", return_value=None), \
             patch.object(browser_launch, "is_debug_port_alive", return_value=False), \
             patch.object(browser_launch, "find_chrome_binary", return_value="/usr/bin/google-chrome"), \
             patch.object(browser_launch, "spawn_browser") as mock_spawn, \
             patch.object(browser_launch, "wait_port_alive", return_value=(True, None)), \
             patch.object(browser_launch, "version_info", return_value={"Browser": "Chrome/999"}), \
             patch.object(browser_launch, "list_tabs", return_value=[{"id": "tab1"}]), \
             patch.object(browser_launch, "_verify_tab_state", return_value={"url": "about:blank", "title": "", "readyState": "complete"}), \
             patch.object(browser_launch, "_write_profile_lock"):
            mock_proc = Mock()
            mock_proc.pid = 12345
            mock_spawn.return_value = mock_proc
            browser_launch.cmd_dedicated(args)

        mock_spawn.assert_called_once()

    def test_does_not_treat_default_none_port_as_explicit(self):
        """--port 没有显式传（None）时，不应触发"显式端口复用"分支，走原有自动探测流程。"""
        args = _make_args(port=None)

        with patch.object(browser_launch, "_load_registry", return_value={}), \
             patch.object(browser_launch, "_save_registry"), \
             patch.object(browser_launch, "_read_profile_lock", return_value=None), \
             patch.object(browser_launch, "is_debug_port_alive", return_value=False), \
             patch.object(browser_launch, "find_chrome_binary", return_value="/usr/bin/google-chrome"), \
             patch.object(browser_launch, "spawn_browser") as mock_spawn, \
             patch.object(browser_launch, "wait_port_alive", return_value=(True, None)), \
             patch.object(browser_launch, "version_info", return_value={"Browser": "Chrome/999"}), \
             patch.object(browser_launch, "list_tabs", return_value=[{"id": "tab1"}]), \
             patch.object(browser_launch, "_verify_tab_state", return_value={"url": "about:blank", "title": "", "readyState": "complete"}), \
             patch.object(browser_launch, "_write_profile_lock"):
            mock_proc = Mock()
            mock_proc.pid = 12345
            mock_spawn.return_value = mock_proc
            browser_launch.cmd_dedicated(args)

        mock_spawn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
