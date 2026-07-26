"""
tests/test_browser_cdp_detect_running.py — browser-cdp skill 新增的
"检测系统里是否已有调试浏览器在跑"逻辑的单测。
（对应用户反馈："cdp skill 应该有先检测当前调试浏览器是不是已经启动，
如果已经启动就不要再启动新的了的这种机制"）

只测纯字符串解析部分（_extract_debug_ports_from_cmdlines），不依赖真实系统
进程列表/真实 Chrome（那部分在 find_running_debug_chrome_ports() 里，属于
"扫系统进程"的 I/O 边界，不适合在单测里断言具体输出）。

browser_launch.py 是 .claude/skills/browser-cdp/ 下的独立脚本（用 sibling
import 方式引用 cdp_client/utils，不是 mini_agent 包的一部分），需要把 skill
目录加进 sys.path 才能 import。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "browser-cdp"
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

import browser_launch  # noqa: E402


class TestExtractDebugPortsFromCmdlines(unittest.TestCase):
    def test_extracts_single_port(self):
        lines = [
            r'"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir=C:\temp\profile',
        ]
        self.assertEqual(browser_launch._extract_debug_ports_from_cmdlines(lines), [9222])

    def test_extracts_and_dedupes_multiple_ports_sorted(self):
        lines = [
            "/usr/bin/google-chrome --remote-debugging-port=9333 --user-data-dir=/tmp/a",
            "/usr/bin/google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/b",
            "/usr/bin/google-chrome --remote-debugging-port=9333 --user-data-dir=/tmp/a --duplicate-process-arg",
        ]
        self.assertEqual(browser_launch._extract_debug_ports_from_cmdlines(lines), [9222, 9333])

    def test_ignores_lines_without_debug_port_arg(self):
        lines = [
            "/usr/bin/google-chrome --type=renderer --no-sandbox",
            "some unrelated process --port=8080",  # 不是 --remote-debugging-port，不应误判
        ]
        self.assertEqual(browser_launch._extract_debug_ports_from_cmdlines(lines), [])

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(browser_launch._extract_debug_ports_from_cmdlines([]), [])

    def test_malformed_port_number_is_skipped_not_crashing(self):
        # 正则要求 \d+，理论上不会匹配出非数字，这里主要确认函数对奇怪输入
        # 也不抛异常（防御性测试）。
        lines = ["chrome --remote-debugging-port= --user-data-dir=/tmp/x"]
        self.assertEqual(browser_launch._extract_debug_ports_from_cmdlines(lines), [])


class TestFindRunningDebugChromePortsIsSafe(unittest.TestCase):
    def test_does_not_raise_even_if_scan_fails(self):
        # 不 mock 掉 subprocess——直接在当前（很可能没有 chrome 在跑的）沙箱环境
        # 里跑一次真实调用，只验证它不抛异常、返回一个 list。
        result = browser_launch.find_running_debug_chrome_ports()
        self.assertIsInstance(result, list)


if __name__ == "__main__":
    unittest.main()
