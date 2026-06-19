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


class TestStreamTokenFilterToolUseTagBoundary(unittest.TestCase):
    """
    回归测试：_filter_token() 在 <tool_use>...</tool_use> 标签跨流式
    token 边界时的处理。

    背景（用户反馈的第二个 bug，出现在 _input_blocking 修复之后）：
        某些模型用文本协议内嵌工具调用：
            <tool_use>\\n{...json...}\\n</tool_use>
        这段标签会和普通对话文字混在同一个流式输出里，由 _filter_token()
        负责把标签本身过滤掉，只把可见对话文字透传给屏幕。

        当 "</tool_use>" 这个结束标签恰好被切分在两个 token 之间
        （例如上一个 token 以 "...</tool" 结尾，下一个 token 是 "_use>"）
        旧实现在"抑制中、未找到完整结束标签"分支有这样的逻辑：
            tail = text[i:]
            self._pending_stream = tail if len(tail) <= 11 else ""
        当 tail（通常是一大段还未结束的 JSON 工具调用内容）长度超过 11
        时，会把 tail 整体丢弃为空字符串——但 tail 的末尾恰好包含
        "</tool" 这样的标签前缀，本该被保留用于和下一个 token 的
        "_use>" 拼接继续查找。一旦被整体丢弃，_suppress_stream 永远
        等不到完整的 "</tool_use>" 出现，从而永久卡在"抑制状态"，
        后续所有正常对话文本（包括下一轮 LLM 调用打印的 "orzooo ❯"
        前缀）都会被无声吞掉，屏幕上只剩下结束标签的残片（如 "_use>"）。

    修复：
        无论 tail 多长，都只截取其末尾 10 个字符（足以拼出
        "</tool_use>" 任意前缀）保留到 _pending_stream，其余部分按
        当前所处的抑制/非抑制状态正常丢弃或输出。
    """

    def setUp(self):
        self.term = Terminal(status_refresh_hz=4)
        self.term._console = MagicMock()

    def tearDown(self):
        self.term.stop()

    def _flush(self, output: list[str]) -> str:
        """模拟 stream_end()：把残留的 _pending_stream 当作可见文本 flush 出来。"""
        if self.term._pending_stream:
            output.append(self.term._pending_stream)
        return "".join(output)

    def test_end_tag_split_across_two_tokens(self):
        """"</tool_use>" 被切成 "</tool" + "_use>" 两个 token 时，应能正确识别结束。"""
        out = []
        out.append(self.term._filter_token("前面的对话文字。"))
        out.append(self.term._filter_token("<tool_use>\n{\"name\": \"x\"}\n</tool"))
        out.append(self.term._filter_token("_use>"))
        out.append(self.term._filter_token("后面继续输出更多正常对话文字内容用于测试。"))
        final = self._flush(out)

        self.assertIn("前面的对话文字", final)
        self.assertIn("后面继续输出更多正常对话文字内容用于测试", final)
        self.assertNotIn("_use>", final)
        self.assertNotIn("<tool_use>", final)
        self.assertNotIn("tool_use", final)
        # 标签处理完后过滤器状态应恢复正常（不再处于抑制状态）
        self.assertFalse(self.term._suppress_stream)

    def test_end_tag_split_with_long_json_payload(self):
        """
        模拟真实场景：工具调用 JSON 内容很长（远超 11 字符），结束标签
        仍然跨 token 边界——这正是触发旧 bug 的精确条件。
        """
        out = []
        out.append(self.term._filter_token(
            "好的！我来为你处理这个任务。让我先创建文件。"
        ))
        out.append(self.term._filter_token(
            "<tool_use>\n{\"id\": \"tc_abc123\", \"name\": \"create_file\", "
            "\"input\": {\"path\": \"test_result/comic_4panel/theme_concept.yaml\", "
            "\"content\": \"some fairly long json payload here to ensure tail exceeds eleven chars\"}}\n</tool"
        ))
        out.append(self.term._filter_token("_use>"))
        out.append(self.term._filter_token(
            "\n\n文件已创建，接下来继续生成下一步内容，这段文字应当完整显示出来。"
        ))
        final = self._flush(out)

        self.assertIn("好的！我来为你处理这个任务", final)
        self.assertIn("文件已创建，接下来继续生成下一步内容，这段文字应当完整显示出来", final)
        self.assertNotIn("_use>", final)
        self.assertNotIn("create_file", final)  # JSON内容本身不应泄漏到可见输出
        self.assertFalse(self.term._suppress_stream)

    def test_start_tag_split_across_two_tokens(self):
        """"<tool_use>" 起始标签也可能被切分，验证同样能正确识别。"""
        out = []
        out.append(self.term._filter_token("正常文字开头<tool"))
        out.append(self.term._filter_token("_use>\n{\"name\": \"x\"}\n</tool_use>"))
        out.append(self.term._filter_token("结尾正常文字"))
        final = self._flush(out)

        self.assertIn("正常文字开头", final)
        self.assertIn("结尾正常文字", final)
        self.assertNotIn("tool_use", final)
        self.assertFalse(self.term._suppress_stream)

    def test_complete_tag_within_single_token(self):
        """标签完整出现在单个 token 内（最常见情况）应继续正常工作。"""
        out = []
        out.append(self.term._filter_token(
            "前缀文字<tool_use>\n{\"name\": \"x\"}\n</tool_use>后缀文字"
        ))
        final = self._flush(out)
        self.assertEqual(final, "前缀文字后缀文字")

    def test_multiple_tool_use_blocks_in_sequence(self):
        """连续多个 <tool_use> 块（多次工具调用）都应被正确过滤，互不干扰。"""
        out = []
        out.append(self.term._filter_token("第一段。<tool_use>\n{\"a\":1}\n</tool"))
        out.append(self.term._filter_token("_use>中间段。<tool_use>\n{\"b\":2}\n</tool_use>结尾段。"))
        final = self._flush(out)

        self.assertIn("第一段", final)
        self.assertIn("中间段", final)
        self.assertIn("结尾段", final)
        self.assertNotIn("tool_use", final)
        self.assertFalse(self.term._suppress_stream)

    def test_full_handle_pipeline_prefix_not_lost(self):
        """
        端到端：通过真实的 print(end="") + stream_token + stream_end 链路
        （与 agent.py 中实际调用顺序一致），验证 "orzooo ❯" 前缀和后续
        正常文字都完整显示，不会被吞掉。

        注意：Terminal 内部有两路输出通道——
          - print(end="") 这类消息经 self._console.print() 写（rich Console
            持有自己的输出文件句柄，不走 sys.stdout 的重定向）
          - stream/stream_end 分支直接 sys.stdout.write()
        两路都要捕获到同一个 buffer 才能验证完整的渲染序列。
        """
        import io, re
        from rich.console import Console

        buf = io.StringIO()
        term = Terminal(status_refresh_hz=4)
        term._console = Console(file=buf, force_terminal=True, width=120)

        original_stdout = sys.stdout
        sys.stdout = buf
        try:
            term.print(
                "\n[bold blue]orzooo[/bold blue][bold cyan] ❯ [/bold cyan]", end=""
            )
            tokens = [
                "好的！我来处理这个任务。",
                "<tool_use>\n{\"id\": \"tc_1\", \"name\": \"create_file\"}\n</tool",
                "_use>",
                "\n\n任务已完成，这是后续说明文字。",
            ]
            for tok in tokens:
                term.stream_token(tok)
            term.stream_end()
            term._q.join()
            time.sleep(0.2)
        finally:
            sys.stdout = original_stdout
            term.stop()

        output = buf.getvalue()
        clean = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", output)

        self.assertIn("orzooo", output)
        self.assertIn("任务已完成，这是后续说明文字", clean)
        self.assertNotIn("_use>", clean)
        self.assertNotIn("tool_use", clean)


if __name__ == "__main__":
    unittest.main()