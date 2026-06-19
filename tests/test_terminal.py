"""
tests/test_terminal.py — Terminal 阻塞输入期间消息缓存的回归测试

背景（用户反馈的 bug）：
    一轮 agent 回复结束后，save_session() 触发后台线程生成会话摘要 /
    用户画像，同时主线程立即进入 REPL 的 prompt_user()（显示 "You ❯"
    并阻塞等待输入）。后台线程随后调用 term.print()（R.print_info 等）
    打印"摘要已生成"之类的消息，这些消息被渲染线程直接写 stdout，
    与 prompt_toolkit 正在管理的输入行渲染竞争，导致：
      - 提示信息错乱地插入到 "You ❯" 之后
      - 打印后又无条件重绘状态栏，看起来像是"卡在那"还在运行

修复：
    Terminal 新增 _input_blocking 标志。_enter_input_mode()（prompt_user /
    confirm 都会经过）期间，_handle() 对 print/rule/panel/syntax/markdown
    类消息只缓存（_pending_during_input），不写屏幕；_exit_input_mode()
    时把缓存消息重新入队，统一在脱离阻塞输入上下文后补打印。

本文件验证：
    1. _input_blocking=True 时，_handle() 把相关消息缓存而非渲染
    2. _exit_input_mode() 后，缓存消息被重新入队并渲染
    3. 不写屏幕的消息类型（_noop/_refresh/statusbar）不受影响，
       仍然正常处理（不会被错误缓存导致状态栏卡死）
    4. 模拟"后台线程在阻塞输入期间调用 term.print()"的真实时序，
       确认消息不会在 _input_blocking 期间被渲染
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, "src")

from mini_agent.ui.terminal import Terminal, _Msg


class TestInputBlockingMessageCache(unittest.TestCase):
    """验证 _input_blocking 期间消息被缓存而非直接写屏幕。"""

    def setUp(self):
        # 创建一个 Terminal 实例，但不依赖真实 TTY / prompt_toolkit。
        # render_thread / refresh_thread 仍会启动（daemon），但我们直接
        # 调用 _handle() 做单元级验证，绕开真实的队列调度时序。
        self.term = Terminal(status_refresh_hz=4)
        # 避免真的往 stdout 写控制字符干扰测试输出
        self.term._console = MagicMock()

    def tearDown(self):
        self.term.stop()

    def test_print_cached_during_input_blocking(self):
        """_input_blocking=True 时，print 消息应被缓存，不调用 console.print。"""
        self.term._input_blocking = True
        self.term._handle(_Msg("print", (("hello",), {})))
        self.term._console.print.assert_not_called()
        self.assertEqual(len(self.term._pending_during_input), 1)
        self.assertEqual(self.term._pending_during_input[0].kind, "print")

    def test_rule_panel_syntax_markdown_all_cached(self):
        """rule/panel/syntax/markdown 同样应被缓存。"""
        self.term._input_blocking = True
        self.term._handle(_Msg("rule", ("title", {})))
        self.term._handle(_Msg("panel", ("content", {})))
        self.term._handle(_Msg("syntax", ("code", "python", {})))
        self.term._handle(_Msg("markdown", "# hi"))
        self.assertEqual(len(self.term._pending_during_input), 4)
        kinds = [m.kind for m in self.term._pending_during_input]
        self.assertEqual(kinds, ["rule", "panel", "syntax", "markdown"])
        self.term._console.print.assert_not_called()
        self.term._console.rule.assert_not_called()

    def test_print_not_cached_when_not_blocking(self):
        """正常（非阻塞输入）状态下，print 应照常立即渲染。"""
        self.term._input_blocking = False
        self.term._handle(_Msg("print", (("hello",), {})))
        self.term._console.print.assert_called_once()
        self.assertEqual(len(self.term._pending_during_input), 0)

    def test_noop_and_refresh_not_affected_by_blocking(self):
        """
        不写屏幕的内部消息类型（_noop 等）即使在 _input_blocking 期间
        也应正常被消费，不应被缓存（否则 _enter_input_mode 的双哨兵
        排空机制会被破坏）。
        """
        self.term._input_blocking = True
        # _noop 不应该出现在 pending 列表里
        self.term._handle(_Msg("_noop", None))
        self.assertEqual(len(self.term._pending_during_input), 0)

    def test_statusbar_message_not_cached(self):
        """statusbar 消息（只更新内部缓存，不写屏幕）不应被缓存拦截。"""
        self.term._input_blocking = True
        self.term._handle(_Msg("statusbar", ["line1"]))
        self.assertEqual(self.term._statusbar_lines, ["line1"])
        self.assertEqual(len(self.term._pending_during_input), 0)


class TestExitInputModeFlushesPending(unittest.TestCase):
    """验证 _exit_input_mode() 会把阻塞期间积压的消息重新入队渲染。"""

    def setUp(self):
        self.term = Terminal(status_refresh_hz=4)
        self.term._console = MagicMock()

    def tearDown(self):
        self.term.stop()

    def test_pending_messages_flushed_after_exit(self):
        # 模拟已经处于阻塞输入态，并有消息被缓存
        self.term._input_blocking = True
        self.term._refresh_paused.set()
        cached_msg = _Msg("print", (("background result",), {}))
        self.term._pending_during_input.append(cached_msg)

        self.term._exit_input_mode()

        # 标志应清除，pending 列表应清空
        self.assertFalse(self.term._input_blocking)
        self.assertEqual(self.term._pending_during_input, [])
        self.assertFalse(self.term._refresh_paused.is_set())

        # 给渲染线程一点时间处理重新入队的消息
        self.term._q.join()
        self.term._console.print.assert_called_with("background result")

    def test_no_pending_messages_exit_is_noop_safe(self):
        """没有积压消息时，exit 不应抛错，且仍会触发一次 redraw。"""
        self.term._input_blocking = True
        self.term._refresh_paused.set()
        try:
            self.term._exit_input_mode()
        except Exception as e:
            self.fail(f"_exit_input_mode raised unexpectedly: {e}")
        self.assertFalse(self.term._input_blocking)


class TestBackgroundThreadDuringBlockingInput(unittest.TestCase):
    """
    端到端模拟用户反馈的真实场景：
    主线程进入 _enter_input_mode()（阻塞输入态）期间，
    另一个线程调用 term.print()，验证消息不会立刻被渲染，
    只有在 _exit_input_mode() 之后才会出现。
    """

    def setUp(self):
        self.term = Terminal(status_refresh_hz=20)
        self.term._console = MagicMock()

    def tearDown(self):
        self.term.stop()

    def test_background_print_does_not_leak_during_blocking_input(self):
        self.term._enter_input_mode()
        try:
            # 模拟后台摘要线程在用户阻塞输入期间打印消息
            bg_done = threading.Event()

            def _bg_worker():
                self.term.print("会话摘要记忆已生成")
                bg_done.set()

            t = threading.Thread(target=_bg_worker, daemon=True)
            t.start()
            bg_done.wait(timeout=2.0)
            t.join(timeout=2.0)

            # 给渲染线程一点时间——即使消息已入队，只要仍处于
            # _input_blocking，就不应该被渲染到 console
            time.sleep(0.3)
            self.term._console.print.assert_not_called()
            self.assertEqual(len(self.term._pending_during_input), 1)
        finally:
            self.term._exit_input_mode()

        # 退出阻塞输入态后，消息应被补打印
        self.term._q.join()
        self.term._console.print.assert_called_once()


if __name__ == "__main__":
    unittest.main()
