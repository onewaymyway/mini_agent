"""
tests/test_raw_key_listener.py

回归测试：RawKeyListener 的 Unix 实现不应在它监听用的 fd 上调用
tty.setraw()，因为 setraw() 会清掉 termios OFLAG 里的 OPOST 标志，
而 termios 设置是**终端设备级别**的（不是 fd 级别的）——/dev/tty 和
sys.stdout 即使是不同的文件描述符，只要指向同一个物理终端，OPOST
被关掉就会导致所有经由该终端的输出都不再把 "\\n" 自动转换为
"\\r\\n"，造成 terminal.py 渲染线程输出的内容逐行错位、呈阶梯状右移
（用户在 Termux 环境下反馈的 "simple-mode 不对" 现象，根因正是这里，
和 simple-mode 本身无关——普通模式同样受影响，只是被状态栏擦除/重绘
操作部分掩盖了）。

本文件使用真实 pty（而非 mock termios），端到端验证：
  1. _UnixKeyReader._setup() 之后，"/dev/tty" 一侧设置的 termios 不会
     影响另一个 fd 往同一终端写 "\\n" 时的 \\r\\n 自动转换（OPOST 保留）
  2. 监听器仍然具备原有的输入特性：不回显、不等行缓冲（ICANON off）、
     按字节立即可读
  3. teardown 后能正确恢复原始 termios 设置
"""

from __future__ import annotations

import os
import pty
import select
import sys
import termios
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mini_agent.ui.raw_key_listener import _UnixKeyReader


def _make_pty_pair():
    """创建一对 pty fd，并返回 slave 端的设备路径（用于模拟 /dev/tty）。"""
    master, slave = pty.openpty()
    return master, slave, os.ttyname(slave)


@unittest.skipUnless(sys.platform.startswith(("linux", "darwin")), "仅 Unix 平台适用")
class TestUnixKeyReaderPreservesOpost(unittest.TestCase):
    """核心回归测试：_setup() 不应破坏同一终端设备上其它 fd 的 OPOST。"""

    def setUp(self):
        self.master, self.slave, self.slave_path = _make_pty_pair()
        # 保存原始 termios，供 tearDown 还原（避免污染后续测试用的 pty —
        # 实际上每个测试都是新的 pty 对，这里只是防御性写法）
        self._orig_attrs = termios.tcgetattr(self.slave)

    def tearDown(self):
        try:
            termios.tcsetattr(self.slave, termios.TCSANOW, self._orig_attrs)
        except Exception:
            pass
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except Exception:
                pass

    def test_setup_does_not_clear_opost_on_shared_device(self):
        """
        模拟真实场景：_UnixKeyReader 通过 /dev/tty 路径打开一个独立 fd
        （这里用 os.open(self.slave_path) 模拟），其上的 termios 修改
        逻辑与 _setup() 完全一致，验证另一个指向同一终端的 fd（模拟
        sys.stdout）写 "\\n" 时仍应被自动转换为 "\\r\\n"。
        """
        listener_fd = os.open(self.slave_path, os.O_RDWR)

        # 手动复刻 _setup() 对 listener_fd 的处理（不复用 _find_tty_fd，
        # 避免依赖真实 /dev/tty 是否存在于测试环境中）。
        old_attrs = termios.tcgetattr(listener_fd)
        new_attrs = termios.tcgetattr(listener_fd)
        new_attrs[3] = new_attrs[3] & ~(termios.ECHO | termios.ICANON | termios.ISIG)
        new_attrs[6][termios.VMIN] = 1
        new_attrs[6][termios.VTIME] = 0
        termios.tcsetattr(listener_fd, termios.TCSANOW, new_attrs)

        try:
            # 模拟 sys.stdout：用 self.slave（另一个 fd，同一终端设备）写 "\n"
            os.write(self.slave, b"line one\n")
            os.write(self.slave, b"line two\n")
            time.sleep(0.05)
            data = os.read(self.master, 4096)
            self.assertIn(b"\r\n", data, "OPOST 被破坏：\\n 没有被自动转换为 \\r\\n")
            self.assertEqual(data, b"line one\r\nline two\r\n")
        finally:
            termios.tcsetattr(listener_fd, termios.TCSANOW, old_attrs)
            os.close(listener_fd)

    def test_old_tty_setraw_would_have_broken_opost(self):
        """
        对照测试：验证旧实现（tty.setraw）确实会破坏 OPOST，
        证明这次修复是有效的、测试本身具备区分力（不是摆设）。
        """
        import tty
        listener_fd = os.open(self.slave_path, os.O_RDWR)
        try:
            tty.setraw(listener_fd)
            os.write(self.slave, b"line one\n")
            os.write(self.slave, b"line two\n")
            time.sleep(0.05)
            data = os.read(self.master, 4096)
            # 旧行为：\n 不会被转换成 \r\n
            self.assertNotIn(b"\r\n", data)
            self.assertEqual(data, b"line one\nline two\n")
        finally:
            os.close(listener_fd)

    def test_full_setup_and_teardown_roundtrip(self):
        """
        端到端：通过真正的 _UnixKeyReader 实例（patch _find_tty_fd 返回
        测试用的 fd），验证 start()/stop() 整个生命周期内 OPOST 始终保留，
        且 stop() 后 termios 被还原成调用前的状态。
        """
        from unittest.mock import patch

        listener_fd = os.open(self.slave_path, os.O_RDWR)
        # _teardown() 会在 _fd_owned=True 时关闭 listener_fd 本身，
        # 这里提前 dup 一个独立 fd 用于 teardown 之后查询 termios——
        # termios 是设备级状态，dup 出来的 fd 与原 fd 共享同一份，
        # 不受原 fd 被 close() 影响，可以在 listener_fd 关闭后继续查询。
        probe_fd = os.dup(listener_fd)
        reader = _UnixKeyReader()

        try:
            with patch.object(reader, "_find_tty_fd", return_value=listener_fd):
                reader._fd_owned = True
                ok = reader._setup()
                self.assertTrue(ok)

                # setup 期间：另一个 fd 写 \n 仍应正常转换为 \r\n
                os.write(self.slave, b"hello\n")
                time.sleep(0.05)
                data = os.read(self.master, 4096)
                self.assertEqual(data, b"hello\r\n")

                reader._teardown()   # 内部会 close(listener_fd)

            # teardown 后，termios 应该恢复成测试开始前保存的状态
            restored = termios.tcgetattr(probe_fd)
            self.assertEqual(restored, self._orig_attrs)
        finally:
            os.close(probe_fd)

    def test_cbreak_input_characteristics_preserved(self):
        """
        确认修复后的 termios 设置仍然满足监听器原本依赖的输入特性：
        不回显、不等行缓冲（单字节立即可读），Ctrl+C 作为普通字节
        读到（不被内核 ISIG 拦截发信号）。
        """
        listener_fd = os.open(self.slave_path, os.O_RDWR)
        try:
            attrs = termios.tcgetattr(listener_fd)
            attrs[3] = attrs[3] & ~(termios.ECHO | termios.ICANON | termios.ISIG)
            attrs[6][termios.VMIN] = 1
            attrs[6][termios.VTIME] = 0
            termios.tcsetattr(listener_fd, termios.TCSANOW, attrs)

            # 模拟"按键"：从 master 端写入，相当于用户敲了 Ctrl+C
            os.write(self.master, b"\x03")
            time.sleep(0.05)
            r, _, _ = select.select([listener_fd], [], [], 0.2)
            self.assertTrue(r, "单字节应立即可读（ICANON 应已关闭）")
            data = os.read(listener_fd, 1)
            self.assertEqual(data, b"\x03", "Ctrl+C 应作为普通字节读到，不应被 ISIG 拦截")

            # 确认没有被回显回 master 侧
            r2, _, _ = select.select([self.master], [], [], 0.1)
            self.assertFalse(r2, "ECHO 应已关闭，不应有回显")
        finally:
            os.close(listener_fd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
