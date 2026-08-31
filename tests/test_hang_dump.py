"""
tests/test_hang_dump.py — notification/hang_dump.py（daemon 卡死前的
全线程栈快照）单元测试。

对应 next_doc/daemon_hang_detection_and_alert_escalation_plan.md 阶段四。
"""

from __future__ import annotations

import subprocess
import sys
import time
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

from mini_agent.notification import hang_dump


_CHILD_REGISTER_AND_SLEEP_SCRIPT = """
import sys
sys.path.insert(0, {src!r})
from pathlib import Path
from mini_agent.notification.hang_dump import register_hang_dump_handler

register_hang_dump_handler(Path({project_root!r}))
Path({pid_marker!r}).write_text(str(__import__("os").getpid()))
import time
time.sleep(30)
"""


class TestHangDumpEndToEnd(unittest.TestCase):
    """跳过 Windows：`faulthandler.register()` 的自定义信号回调在
    Windows 上不可用，见 hang_dump.py 顶部注释。"""

    def setUp(self):
        if sys.platform == "win32":
            self.skipTest("faulthandler.register(signal) 在 Windows 上不可用")
        self._tmp = TemporaryDirectory()
        self.project_root = Path(self._tmp.name) / "project"
        self.project_root.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _src_dir(self) -> str:
        return str(Path(__file__).resolve().parents[1] / "src")

    def test_capture_returns_real_stack_when_handler_registered(self):
        pid_marker = self.project_root / "child.pid"
        script_path = self.project_root / "child_register_and_sleep.py"
        script_path.write_text(
            _CHILD_REGISTER_AND_SLEEP_SCRIPT.format(
                src=self._src_dir(),
                project_root=str(self.project_root),
                pid_marker=str(pid_marker),
            ),
            encoding="utf-8",
        )
        proc = subprocess.Popen([sys.executable, str(script_path)])
        try:
            deadline = time.time() + 5
            while time.time() < deadline and not pid_marker.exists():
                time.sleep(0.05)
            self.assertTrue(pid_marker.exists(), "子进程没能在 5s 内写出 pid 标记文件")
            child_pid = int(pid_marker.read_text().strip())

            dump = hang_dump.capture_hang_stack_dump(
                child_pid, self.project_root, wait_seconds=5.0
            )
            self.assertIsNotNone(dump)
            self.assertFalse(dump.startswith("[未获取到栈快照]"), dump)
            # faulthandler 的转储里应该能看到子进程当前正卡在哪个文件/哪一行
            self.assertIn("child_register_and_sleep.py", dump)
            self.assertIn("most recent call first", dump)
        finally:
            proc.kill()
            proc.wait(timeout=5)

    def test_capture_reports_explicit_failure_when_no_process(self):
        # 一个基本不可能存在的 pid（且没有注册处理器），应该拿到一段
        # 说明性文字而不是 None 或抛异常。
        dump = hang_dump.capture_hang_stack_dump(
            2**30 - 1, self.project_root, wait_seconds=0.5
        )
        self.assertIsNotNone(dump)
        self.assertTrue(dump.startswith("[未获取到栈快照]"))

    def test_capture_reports_timeout_when_handler_not_registered(self):
        # 进程存在，但没有注册 SIGUSR1 处理器：信号的默认处置是终止进程，
        # 转储文件不会有内容——应该拿到"未获取到栈快照"而不是假装成功。
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            dump = hang_dump.capture_hang_stack_dump(
                proc.pid, self.project_root, wait_seconds=1.0
            )
            self.assertIsNotNone(dump)
            self.assertTrue(dump.startswith("[未获取到栈快照]"))
        finally:
            proc.kill()
            proc.wait(timeout=5)


class TestHangDumpWindowsFallback(unittest.TestCase):
    """不依赖真实平台——直接 monkeypatch `sys.platform`，验证 Windows
    分支的行为（不发信号、不注册，返回说明性文字/False）。"""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.project_root = Path(self._tmp.name) / "project"
        self.project_root.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_register_returns_false_on_windows(self):
        with unittest.mock.patch.object(hang_dump.sys, "platform", "win32"):
            self.assertFalse(hang_dump.register_hang_dump_handler(self.project_root))

    def test_capture_returns_explanatory_text_on_windows(self):
        with unittest.mock.patch.object(hang_dump.sys, "platform", "win32"):
            dump = hang_dump.capture_hang_stack_dump(1234, self.project_root)
        self.assertIsNotNone(dump)
        self.assertIn("Windows", dump)
        self.assertTrue(dump.startswith("[未获取到栈快照]"))


if __name__ == "__main__":
    unittest.main()
