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

    def test_input_blocking_flag_set_before_erase_bar_direct_runs(self):
        """
        回归测试：精确复现"_input_blocking 置位"和"_erase_bar_direct()
        执行"之间的竞态窗口。

        早期版本把 self._input_blocking = True 放在 _erase_bar_direct()
        *之后*，这意味着从"双重哨兵 join 确认队列已空"到"标志真正
        置位"之间，存在一段跨越整次 _erase_bar_direct() 执行时长的
        窗口——如果后台线程（如会话摘要生成线程）恰好在这个窗口里
        调用 term.print()，消息会被当作"非阻塞期"消息直接渲染，
        和主线程紧接着启动的 prompt_toolkit 输入行渲染产生不可控的
        交织写入，正是用户反馈的"输入文字被立刻擦除""状态栏在 You ❯
        旁边冒出来"等乱码画面的根因。

        本测试把 _erase_bar_direct 替换为一个会先记录"此刻
        _input_blocking 是否已经是 True"、再人为暂停一小段时间的
        替身，模拟真实环境里这段操作不是零耗时的；暂停期间从另一个
        线程调用 term.print()，断言：
          1) _erase_bar_direct 被调用时，_input_blocking 必须已经是
             True（验证置位顺序已经提前）；
          2) 暂停期间到达的后台消息必须被正确缓冲，没有被渲染到
             console（验证竞态窗口已被关闭，不会有提前/交织写入）。
        """
        blocking_when_erase_called = []
        erase_called = threading.Event()
        release_erase = threading.Event()

        real_erase = self.term._erase_bar_direct

        def _slow_erase():
            blocking_when_erase_called.append(self.term._input_blocking)
            erase_called.set()
            release_erase.wait(timeout=2.0)
            real_erase()

        self.term._erase_bar_direct = _slow_erase

        def _enter():
            self.term._enter_input_mode()

        t = threading.Thread(target=_enter, daemon=True)
        t.start()
        self.assertTrue(erase_called.wait(timeout=2.0), "_erase_bar_direct 应该很快被调用到")

        # 此刻模拟后台线程（如摘要生成完成）尝试打印消息——
        # 这正是历史 bug 里"恰好命中竞态窗口"的那条消息
        self.term.print("会话摘要记忆已生成")
        time.sleep(0.05)  # 给渲染线程一点时间处理队列

        release_erase.set()
        t.join(timeout=2.0)

        self.assertEqual(
            blocking_when_erase_called, [True],
            "_erase_bar_direct() 执行时 _input_blocking 必须已经是 True"
            "（置位顺序必须在 erase 之前，关闭竞态窗口）",
        )
        self.term._console.print.assert_not_called()
        self.assertEqual(
            len(self.term._pending_during_input), 1,
            "竞态窗口内到达的后台打印消息必须被正确缓冲，而不是被渲染线程"
            "直接写到屏幕上（否则会和即将启动的 ptk 输入行渲染交织出乱码）",
        )

        self.term._exit_input_mode()
        self.term._q.join()
        self.term._console.print.assert_called_once()

    def test_exit_input_mode_requeues_pending_before_clearing_flag(self):
        """
        回归测试：_exit_input_mode() 必须先把积压消息 + redraw 重新
        入队，再清除 _input_blocking / _refresh_paused——顺序颠倒会
        在"积压消息正在被重新入队"的短暂窗口里，让另一条恰好同时到达
        的新消息抢在积压内容前面被处理，造成时间顺序错乱（新内容先
        出现，本该更早出现的积压内容反而后出现）。

        验证方式：在 _q.put 上打补丁，记录每次 put 调用时 _input_blocking
        的瞬时值；调用 _exit_input_mode() 后，检查"重新入队积压消息 +
        redraw"的所有 put 调用都发生在 _input_blocking 仍为 True 的
        时刻（最后一次把它设为 False 的赋值必须在所有相关 put 之后）。
        """
        self.term._input_blocking = True
        self.term._pending_during_input = [
            _Msg("print", (("早期积压消息",), {}))
        ]

        observed_blocking_at_put = []
        real_put = self.term._q.put

        def _tracking_put(msg):
            observed_blocking_at_put.append(self.term._input_blocking)
            return real_put(msg)

        self.term._q.put = _tracking_put

        self.term._exit_input_mode()

        self.assertTrue(
            len(observed_blocking_at_put) >= 2,
            "应该至少有积压消息 + redraw 两次 put 调用",
        )
        self.assertTrue(
            all(observed_blocking_at_put),
            "重新入队积压消息和 redraw 时，_input_blocking 必须仍为 True"
            "（清除标志必须发生在这些 put 调用之后，避免新消息插队造成乱序）",
        )
        self.assertFalse(
            self.term._input_blocking,
            "_exit_input_mode() 返回后，_input_blocking 最终必须为 False",
        )


class TestRawKeyListenerPauseDuringBlockingInput(unittest.TestCase):
    """
    回归测试："用户确认的时候看不到用户输入" bug。

    根因：raw_key_listener（repl.py 在 run_turn() 期间启动，用于监听
    方向键/Ctrl+C）会用 termios 显式关闭终端的 ECHO，生命周期覆盖整个
    run_turn()。但 ask_user_confirm / ask_user / ask_user_choice 这几个
    工具，以及工具执行权限的 confirm() 审批提示，都是在 run_turn()
    *内部*被调用的——它们阻塞读取 sys.stdin 时，listener 还在跑、
    ECHO 还是关闭状态，用户打的字不会被终端回显，也没有任何代码把
    它显式打印回去，于是看起来"输入凭空消失"。

    修复：_enter_input_mode() 检测到 listener 处于活跃状态时，先把它
    停掉（termios 会被还原，包括重新打开 ECHO），并记录这一事实；
    _exit_input_mode() 的最后一步据此决定是否把 listener 重新启动
    回去——只有"进入前确实是活跃的"才重启，避免在 prompt_user()
    这种"listener 本来就没在跑"的路径上意外把它启动起来。
    """

    def setUp(self):
        self.term = Terminal(status_refresh_hz=4)
        self.term._console = MagicMock()

    def tearDown(self):
        self.term.stop()

    def test_enter_input_mode_stops_active_listener_and_remembers_it(self):
        fake_listener = MagicMock()
        fake_listener.active = True

        with patch(
            "mini_agent.ui.raw_key_listener.get_listener",
            return_value=fake_listener,
        ):
            self.term._enter_input_mode()

        fake_listener.stop.assert_called_once()
        self.assertTrue(
            self.term._key_listener_was_active,
            "listener 进入前活跃，应该被记住，供 _exit_input_mode() 恢复",
        )
        self.term._exit_input_mode()

    def test_exit_input_mode_restarts_listener_only_if_it_was_active(self):
        fake_listener = MagicMock()
        fake_listener.active = True

        with patch(
            "mini_agent.ui.raw_key_listener.get_listener",
            return_value=fake_listener,
        ):
            self.term._enter_input_mode()
            fake_listener.stop.assert_called_once()

            self.term._exit_input_mode()

            fake_listener.start.assert_called_once()
        self.assertFalse(
            self.term._key_listener_was_active,
            "恢复之后应该清空标记，避免下一轮被误判",
        )

    def test_prompt_user_path_does_not_touch_inactive_listener(self):
        """
        listener 进入前本就不活跃（典型如 prompt_user() 这条路径——
        run_turn() 已经结束，repl.py 的 finally 早就调用过
        listener.stop()）：_enter_input_mode() 不应该调用 stop()，
        _exit_input_mode() 也不应该意外把它 start() 起来。
        """
        fake_listener = MagicMock()
        fake_listener.active = False

        with patch(
            "mini_agent.ui.raw_key_listener.get_listener",
            return_value=fake_listener,
        ):
            self.term._enter_input_mode()
            fake_listener.stop.assert_not_called()

            self.term._exit_input_mode()
            fake_listener.start.assert_not_called()

    def test_listener_interaction_failure_does_not_break_input_mode(self):
        """get_listener() 本身抛异常（例如模块导入失败）时，
        _enter_input_mode()/_exit_input_mode() 不应该因此崩溃——
        阻塞输入这条核心路径的健壮性不能依赖 listener 模块是否可用。"""
        with patch(
            "mini_agent.ui.raw_key_listener.get_listener",
            side_effect=RuntimeError("boom"),
        ):
            try:
                self.term._enter_input_mode()
                self.term._exit_input_mode()
            except Exception as exc:
                self.fail(f"listener 交互失败不应该让阻塞输入流程崩溃，但抛出了: {exc}")

    def test_full_cycle_restores_echo_visible_state_for_confirm(self):
        """
        端到端验证：模拟 confirm() 的真实调用模式（_enter_input_mode →
        阻塞读取 → _exit_input_mode），确认 listener 在"读取期间"
        被正确停掉（即模拟用户输入时 ECHO 已经恢复），读取完成后
        又被正确恢复运行。
        """
        fake_listener = MagicMock()
        fake_listener.active = True
        listener_active_during_read = []

        with patch(
            "mini_agent.ui.raw_key_listener.get_listener",
            return_value=fake_listener,
        ):
            self.term._enter_input_mode()
            try:
                # 模拟"阻塞读取用户输入"这一刻——此时 listener 应该
                # 已经被 stop() 过（active 在真实场景下会变为 False，
                # 这里用 mock 的调用记录直接断言）
                listener_active_during_read.append(fake_listener.stop.called)
            finally:
                self.term._exit_input_mode()

        self.assertEqual(
            listener_active_during_read, [True],
            "阻塞读取用户输入期间，listener 必须已经被停掉（ECHO 已恢复）",
        )
        fake_listener.start.assert_called_once()


class TestInputBlockingWatchdogTimeout(unittest.TestCase):
    """
    回归测试："用户输入的时候还是在刷新导致看不到用户输入" bug
    （在 raw key listener 修复之后仍然复现的第二个独立根因）。

    根因：_refresh_loop() 里的看门狗会在 _input_blocking 持续为 True
    超过 _INPUT_BLOCKING_TIMEOUT 秒后，强制清掉 _refresh_paused / 
    _input_blocking 并 flush 缓存消息 + 投递 redraw。这个机制本身是
    合理的兜底（防止某个异常路径导致标志永久卡死），但旧版本把超时
    设成 120 秒——这远小于很多完全合法的人类等待场景：用户读完提示、
    思考、打一段较长回答，或者（尤其 Termux 等移动端）中途把 App
    切到后台再切回来，都完全可能超过 120 秒。

    一旦看门狗在用户*仍然合法地*停留在 prompt_toolkit 的 .prompt()
    或 confirm() 的 readline() 里时误判"卡死"并强制复位，
    _refresh_paused 会被清掉，refresh_thread 从下一个周期开始重新
    按周期投递 _refresh 消息——状态栏从此开始一遍遍重绘，跟用户正在
    输入的那一行抢屏幕，造成"一直在刷新、看不到自己刚打的字"的画面。

    本测试验证：
      1. 默认超时值必须远大于 120 秒（锁定"足够宽松，不会在正常人类
         交互/切后台场景下误触发"这一要求，避免未来被无意调小）。
      2. 看门狗只在真正超过阈值时才触发，不会提前误触发。
      3. 看门狗触发后的具体行为：_refresh_paused 被清除（→ refresh_thread
         恢复投递 _refresh 消息，重新开始重绘状态栏，这正是"抢屏幕"
         的实际机制）、_pending_during_input 被清空并重新入队、
         _input_blocking 被复位。
    """

    def setUp(self):
        self.term = Terminal(status_refresh_hz=4)
        self.term._console = MagicMock()

    def tearDown(self):
        self.term.stop()

    def test_default_timeout_is_generous_enough_for_real_human_pauses(self):
        """
        锁定默认超时远大于 120 秒（早期复现过 bug 的阈值），并且至少
        覆盖"用户切到后台几分钟再切回来"这种完全合法的场景（这里用
        10 分钟作为下限示例，实际默认值更宽松）。
        """
        self.assertGreater(
            self.term._INPUT_BLOCKING_TIMEOUT, 120.0,
            "看门狗超时不能只有 120 秒——这远小于很多合法的人类等待场景"
            "（思考、打长回复、切到后台再切回来），会导致看门狗在用户仍在"
            "合法输入时误触发，强制恢复状态栏刷新、跟输入行抢屏幕",
        )
        self.assertGreaterEqual(
            self.term._INPUT_BLOCKING_TIMEOUT, 600.0,
            "看门狗超时应该至少能覆盖几分钟量级的合理人类等待/切后台场景",
        )

    def test_watchdog_does_not_fire_before_timeout_elapsed(self):
        """_input_blocking 持续时间小于阈值时，看门狗不应该有任何动作
        ——_refresh_paused 应保持被设置，消息不应被强制 flush。"""
        self.term._INPUT_BLOCKING_TIMEOUT = 10.0  # 缩短阈值加快测试
        self.term._input_blocking = True
        self.term._input_blocking_since = time.monotonic()  # 刚刚开始
        self.term._refresh_paused.set()
        self.term._pending_during_input = [_Msg("print", (("还没到期",), {}))]

        self.term._refresh_interval = 0.02
        time.sleep(0.1)  # 远小于阈值

        self.assertTrue(self.term._refresh_paused.is_set(), "未超时前 _refresh_paused 不应被清除")
        self.assertTrue(self.term._input_blocking, "未超时前 _input_blocking 不应被复位")
        self.assertEqual(len(self.term._pending_during_input), 1, "未超时前缓存消息不应被 flush")

    def test_watchdog_fires_after_timeout_and_resumes_refresh_thread(self):
        """
        _input_blocking 持续超过阈值后，看门狗应该：
          - 清除 _refresh_paused（→ refresh_thread 从下一周期开始恢复
            投递 _refresh 消息，重新开始重绘状态栏——这正是"抢屏幕"
            视觉症状的直接机制，本测试验证到这一步即视为复现了根因）
          - 复位 _input_blocking
          - flush 缓存消息

        用很短的阈值（0.05s）模拟"早期 120 秒阈值在足够长的人类等待
        后同样会触发"的效果，验证触发后的行为链条本身是正确可复现的。
        """
        self.term._INPUT_BLOCKING_TIMEOUT = 0.05
        self.term._input_blocking = True
        self.term._input_blocking_since = time.monotonic()
        self.term._refresh_paused.set()
        self.term._pending_during_input = [_Msg("print", (("迟到的消息",), {}))]
        self.term._refresh_interval = 0.02

        # 等待超过阈值，给 refresh_loop 足够的周期去检测到超时
        time.sleep(0.3)

        self.assertFalse(
            self.term._refresh_paused.is_set(),
            "超时后看门狗必须清除 _refresh_paused，这一步直接导致 refresh_thread"
            "恢复投递 _refresh 消息、重新开始重绘状态栏——如果用户此时仍在"
            "ptk 的 .prompt() 或 confirm() 的 readline() 里合法等待，"
            "就会出现状态栏跟输入行抢屏幕、看不到自己刚打的字的画面",
        )
        self.assertFalse(self.term._input_blocking, "超时后 _input_blocking 必须被复位")
        self.assertEqual(
            self.term._pending_during_input, [],
            "超时后缓存消息必须被 flush（重新入队），不能让 agent 输出永久卡住",
        )


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


class TestSimpleModeNeverShowsStatusbarOrErases(unittest.TestCase):
    """
    回归测试：simple-mode 下完全不显示状态栏，且任何情况下都不使用
    擦除/原地重绘机制（不只是"不调用"，连直接调用 _draw_bar() /
    _erase_bar() / _erase_bar_direct() 这种防御性场景也要保证零输出）。

    背景：用户反馈"simple mode 不对"——根因之一是早期实现仍然把状态栏
    以"内容变化才打印"的方式追加输出，这在 Termux 等环境里依然会产生
    大量噪音；同时担心擦除机制可能从某个未预料的路径被触发。明确需求：
        1. simple-mode 下，状态栏任何形式都不显示（不追加打印、
           不原地刷新，统统没有）
        2. 任何地方都不能使用擦除机制（ANSI \\x1b[NA\\x1b[0J 等）
    """

    def _make_simple_term(self):
        import io
        from rich.console import Console
        term = Terminal(status_refresh_hz=4, simple_mode=True)
        term._console = Console(file=io.StringIO(), highlight=False)
        return term

    def test_statusbar_never_printed_via_refresh_or_redraw(self):
        """statusbar 消息 + 周期性 _refresh + 显式 redraw，均不应产生任何输出。"""
        import io
        term = self._make_simple_term()
        buf = io.StringIO()
        original_stdout = sys.stdout
        sys.stdout = buf
        try:
            term._handle(_Msg("statusbar", ["⚡ Tasks [██░░] 2/4 running"]))
            term._handle(_Msg("_refresh", None))
            term._handle(_Msg("redraw", None))
            # 多次刷新、内容变化，依然不应输出任何内容
            term._handle(_Msg("statusbar", ["⚡ Tasks [████] 4/4 running"]))
            term._handle(_Msg("_refresh", None))
            term._handle(_Msg("redraw", None))
        finally:
            sys.stdout = original_stdout
            term.stop()

        output = buf.getvalue()
        self.assertEqual(output, "", "simple-mode 下状态栏不应有任何输出")
        self.assertNotIn("Tasks", output)

    def test_no_ansi_escape_sequences_in_full_turn_simulation(self):
        """模拟一个完整轮次（prefix + 状态栏更新 + 流式输出 + 结束），全程无 ANSI 转义序列。"""
        import io
        term = self._make_simple_term()
        buf = io.StringIO()
        original_stdout = sys.stdout
        sys.stdout = buf
        try:
            term._handle(_Msg("print", (("orzooo ❯ ",), {"end": ""})))
            for i in range(5):
                term._handle(_Msg("statusbar", [f"⚡ Tasks [{'█' * i}{'░' * (4 - i)}] {i}/4"]))
                term._handle(_Msg("_refresh", None))
            term._handle(_Msg("stream", "你好"))
            term._handle(_Msg("stream", "，世界"))
            term._handle(_Msg("stream_end", None))
        finally:
            sys.stdout = original_stdout
            term.stop()

        output = buf.getvalue()
        self.assertNotIn("\x1b[", output, "simple-mode 下不应出现任何 ANSI 转义序列")
        self.assertNotIn("Tasks", output, "simple-mode 下不应出现状态栏内容")
        self.assertIn("你好，世界", output)

    def test_draw_bar_erase_bar_are_noop_in_simple_mode_even_called_directly(self):
        """
        即使绕过正常调度、直接调用 _draw_bar()/_erase_bar()/_erase_bar_direct()
        （防御性场景：模拟未来代码不小心从别处调用这些函数），
        在 simple_mode=True 时也必须是彻底的 no-op，不产生任何输出。
        """
        import io
        term = self._make_simple_term()
        term._statusbar_lines = ["fake status line that should never print"]
        term._bar_drawn = 5  # 伪造"已经画了 5 行"的状态

        buf = io.StringIO()
        original_stdout = sys.stdout
        sys.stdout = buf
        try:
            term._draw_bar()
            term._erase_bar()
            term._erase_bar_direct()
        finally:
            sys.stdout = original_stdout
            term.stop()

        output = buf.getvalue()
        self.assertEqual(output, "", "_draw_bar/_erase_bar/_erase_bar_direct 在 simple_mode 下必须零输出")

    def test_normal_mode_unaffected_statusbar_still_shows(self):
        """对照测试：非 simple-mode 下状态栏仍应正常显示，确认本次改动没有误伤正常模式。

        _draw_bar()/_erase_bar() 内部会检查 _IS_TTY（测试环境的 stdout
        不是真实 tty，需要 patch 成 True 才能验证"正常模式下确实会画
        状态栏"这一行为本身没有被本次改动破坏）。
        """
        import io
        term = Terminal(status_refresh_hz=4, simple_mode=False)
        term._console = MagicMock()

        buf = io.StringIO()
        original_stdout = sys.stdout
        sys.stdout = buf
        try:
            with patch("mini_agent.ui.terminal._IS_TTY", True):
                term._handle(_Msg("statusbar", ["⚡ Tasks [██░░] 2/4 running"]))
                term._handle(_Msg("_refresh", None))
        finally:
            sys.stdout = original_stdout
            term.stop()

        output = buf.getvalue()
        self.assertIn("Tasks", output, "非 simple-mode 下状态栏应该正常显示")


@unittest.skipUnless(hasattr(__import__("signal"), "SIGWINCH"), "SIGWINCH 仅在 POSIX 平台存在")
class TestSigwinchRearm(unittest.TestCase):
    """
    回归测试：prompt_toolkit 会通过 asyncio loop.add_signal_handler() 接管
    SIGWINCH，并在 .prompt() 返回时把 OS 级 handler 显式复位为 SIG_DFL
    （而不是还原成我们注册的 _on_sigwinch）。

    如果不在 _exit_input_mode() 里重新挂载，用户第一次提交输入之后，
    整个进程生命周期内的所有后续 resize 都会变成"没人处理"的 SIG_DFL，
    _bar_drawn / rich Console 宽度缓存从此失真——这正是
    "调整窗口大小，下面的 You ❯ 会闪烁、消失看不到" 的根因。

    本测试不依赖真实的 prompt_toolkit/asyncio 交互，只验证我们自己的
    契约：_exit_input_mode() 必须无条件重新调用 signal.signal() 把
    self._on_sigwinch 挂回 SIGWINCH，不管之前发生了什么。
    """

    def setUp(self):
        self.term = Terminal(status_refresh_hz=4)
        self.term._console = MagicMock()

    def tearDown(self):
        self.term.stop()

    def test_exit_input_mode_rearms_handler_after_external_reset(self):
        import signal

        # 模拟 prompt_toolkit 的 asyncio loop 在 .prompt() 返回时
        # 把 OS handler 复位为 SIG_DFL 这一真实行为。
        signal.signal(signal.SIGWINCH, signal.SIG_DFL)
        self.assertEqual(
            signal.getsignal(signal.SIGWINCH),
            signal.SIG_DFL,
            "前置条件：handler 已被外部（模拟 ptk）复位为 SIG_DFL",
        )

        self.term._exit_input_mode()

        self.assertEqual(
            signal.getsignal(signal.SIGWINCH),
            self.term._on_sigwinch,
            "_exit_input_mode() 之后，SIGWINCH 必须重新指向 self._on_sigwinch，"
            "不能停留在 ptk 复位后的 SIG_DFL",
        )

    def test_rearm_is_idempotent_across_multiple_input_rounds(self):
        """模拟多轮 prompt_user()：每轮结束后 handler 都应正确挂回，
        不会在第二轮、第三轮开始"失效"。

        注意：bound method 每次属性访问都会产生一个新的包装对象，
        所以这里用 == 比较（同一 __self__ + __func__ 即相等），
        不能用 is（恒为 False，是 Python bound method 的已知特性，
        不代表 handler 没挂上）。
        """
        import signal

        for _ in range(3):
            # 进入输入前先模拟 ptk 接管+复位（即上一轮 .prompt() 的效果）
            signal.signal(signal.SIGWINCH, signal.SIG_DFL)
            self.term._exit_input_mode()
            self.assertEqual(
                signal.getsignal(signal.SIGWINCH),
                self.term._on_sigwinch,
                "多轮输入后 handler 应始终被正确重新挂载",
            )

    def test_on_sigwinch_invalidates_console_width_cache_but_not_bar_drawn(self):
        """
        触发一次 resize 信号：
          - Rich Console 的宽度缓存应该被清空（影响 panel/markdown/syntax
            等走 Console 渲染的内容，下次渲染时重新测量终端宽度）；
          - 但 self._bar_drawn 绝对不能被触碰。

        背景（真实复现过的严重回归 bug）：早期版本在这里会把 _bar_drawn
        强行置 0，理由是"避免用旧行数超界擦除"，但状态栏本身是纯字符串
        write、行数只取决于 len(self._statusbar_lines)，跟终端宽度变化
        没有关系；把 _bar_drawn 清零等于谎称"屏幕上没有内容待擦除"，
        导致下一次绘制不擦除旧内容、直接在下面追加——状态栏被一遍遍
        重复打印、不断堆叠（resize 越多，堆叠越多）。
        """
        self.term._bar_drawn = 5
        self.term._console._width = 120
        self.term._console._height = 40

        self.term._on_sigwinch(None, None)

        self.assertEqual(
            self.term._bar_drawn, 5,
            "_bar_drawn 必须保持不变——它是屏幕上真实占用行数的唯一权威记录，"
            "resize 不会改变状态栏的逻辑行数，清零会导致下次绘制不擦除旧内容、"
            "造成状态栏重复堆叠的严重回归（已在生产环境真实复现）",
        )
        self.assertIsNone(self.term._console._width)
        self.assertIsNone(self.term._console._height)

    def test_on_sigwinch_settled_also_does_not_touch_bar_drawn(self):
        """debounce settle 回调同样不能触碰 _bar_drawn，理由同上。"""
        self.term._bar_drawn = 3
        self.term._on_sigwinch_settled()
        self.assertEqual(self.term._bar_drawn, 3)

    def test_sigwinch_during_bar_below_prefix_does_not_corrupt_erase_math(self):
        """
        回归测试：复现"等待 LLM 响应时状态栏被画在 agent ❯ 下方
        （_bar_below_prefix=True），此时收到一次 SIGWINCH"的真实场景。

        三阶段状态机（见 _handle() 里 stream/stream_end 分支）依赖
        `lines_up = (self._bar_drawn if self._bar_drawn > 0 else 0) + 1`
        来正确计算"需要上移多少行才能回到 agent ❯ 所在行的行首"。
        如果 SIGWINCH 把 _bar_drawn 清零，这个计算会得出错误的、
        过小的行数，导致只上移 1 行（停在状态栏内部某一行，而不是
        真正的 agent ❯ 那一行），_bar_below_prefix 下方原本画着的
        状态栏内容不会被完全擦除，紧接着的 \\x1b[0J 又会在错误位置
        清除/重画，画面上呈现状态栏被重复打印的乱码。

        本测试只验证不变量本身：SIGWINCH 触发后，_bar_below_prefix
        状态机依赖的 _bar_drawn 必须维持触发前的真实值，使得
        lines_up 的计算结果不受 resize 影响。
        """
        self.term._bar_below_prefix = True
        self.term._bar_drawn = 2  # 假设状态栏当前真实占用 2 行

        self.term._on_sigwinch(None, None)

        lines_up = (self.term._bar_drawn if self.term._bar_drawn > 0 else 0) + 1
        self.assertEqual(
            lines_up, 3,
            "SIGWINCH 不应该改变 _bar_below_prefix 状态机算出的 lines_up，"
            "否则会在 stream/stream_end 分支里上移错误的行数，造成擦除不完整、"
            "状态栏重复堆叠（真实复现过的乱码画面）",
        )


class TestSigwinchDebounceSettle(unittest.TestCase):
    """
    回归测试：Termux 等移动端终端模拟器上，应用切到后台再切回前台时，
    SIGWINCH 送达的时刻底层 pty 尺寸可能还没真正稳定（过渡态值，或
    短时间内连发多次）。如果只在信号处理函数里"立即"重绘，用到的可能
    是错误的、未稳定的尺寸，且后续可能不会再有信号纠正它。

    本测试验证 debounce-settle 机制本身的契约：
      1. 短时间内连续多次 SIGWINCH，只应在"安静期"之后真正 settle 一次
         （验证 timer 被正确取消重置，不会每次都立刻触发）。
      2. settle 时，如果有活跃的 ptk Application，调用其线程安全的
         invalidate()，而不是走 redraw 消息队列。
      3. settle 时，如果没有活跃的 ptk Application 且不在阻塞输入期间，
         走 redraw 消息队列（覆盖 confirm() 的 readline 路径和非阻塞期间
         的 agent 运行场景）。
      4. 阻塞输入但没有活跃 ptk app（如 confirm() 的 readline 路径）时，
         settle 不应该误投 redraw 消息（避免撕裂正在显示的确认提示符）。
    """

    def setUp(self):
        self.term = Terminal(status_refresh_hz=4)
        self.term._console = MagicMock()
        # debounce 时间设短一点，加快测试速度
        self.term._SIGWINCH_DEBOUNCE_SECONDS = 0.05

    def tearDown(self):
        self.term.stop()

    def test_burst_of_sigwinch_settles_only_once(self):
        """连续触发多次 SIGWINCH（模拟 resize 过渡动画），settle 回调
        应该只在最后一次信号之后的安静期触发一次，而不是每次都触发。"""
        settle_calls = []
        self.term._on_sigwinch_settled = lambda: settle_calls.append(1)

        for _ in range(5):
            self.term._on_sigwinch(None, None)
            time.sleep(0.01)  # 间隔小于 debounce 时长，模拟连续抖动

        # 此刻 debounce 还没到期，不应该已经 settle 过
        self.assertEqual(len(settle_calls), 0)

        # 等待超过 debounce 时长，应该恰好 settle 一次
        time.sleep(0.15)
        self.assertEqual(len(settle_calls), 1, "连续抖动应只触发一次 settle，而非每次信号都触发")

    def test_settled_invalidates_active_ptk_app_instead_of_redraw_queue(self):
        """有活跃 ptk app 时，settle 应该调用 app.invalidate()（线程安全），
        不应该走 _q 的 redraw 消息（那是给"没有 ptk 在跑"的场景用的）。"""
        fake_app = MagicMock()
        self.term._active_ptk_app = fake_app
        self.term._input_blocking = True  # 模拟正阻塞在 ptk 的 .prompt() 里

        self.term._on_sigwinch_settled()

        fake_app.invalidate.assert_called_once()

    def test_settled_falls_back_to_redraw_queue_when_no_ptk_app_and_not_blocking(self):
        """没有活跃 ptk app、且不在阻塞输入期间（agent 正常运行/状态栏可见）
        时，settle 应该把 redraw 消息投进队列。"""
        self.term._active_ptk_app = None
        self.term._input_blocking = False
        self.term._refresh_paused.clear()

        self.term._on_sigwinch_settled()

        # 排空队列检查是否有 redraw 消息
        found = False
        try:
            while True:
                msg = self.term._q.get_nowait()
                if msg.kind == "redraw":
                    found = True
                self.term._q.task_done()
        except Exception:
            pass
        self.assertTrue(found, "非阻塞场景下 settle 应该投递 redraw 消息")

    def test_settled_does_not_leak_redraw_when_blocking_without_ptk_app(self):
        """confirm() 走的是裸 readline，没有 ptk app，但仍处于
        _input_blocking=True。这种情况下 settle 不应该投 redraw 消息——
        否则会撕裂正在显示的确认提示符（这正是 _input_blocking 标志本来
        要保护的场景）。"""
        self.term._active_ptk_app = None
        self.term._input_blocking = True

        self.term._on_sigwinch_settled()

        found = False
        try:
            while True:
                msg = self.term._q.get_nowait()
                if msg.kind == "redraw":
                    found = True
                self.term._q.task_done()
        except Exception:
            pass
        self.assertFalse(found, "阻塞输入期间（无 ptk app）不应该投 redraw 消息")

    def test_settled_does_not_crash_when_invalidate_raises(self):
        """ptk app.invalidate() 内部抛异常（例如 app 已经退出/loop 已关闭）
        不应该让 settle 回调本身崩掉（它跑在独立的 Timer 线程上，未捕获
        异常会被 threading 静默打印但不影响主流程；这里仍然要求我们自己
        做好防御，不依赖 threading 的默认行为）。"""
        fake_app = MagicMock()
        fake_app.invalidate.side_effect = RuntimeError("loop closed")
        self.term._active_ptk_app = fake_app

        try:
            self.term._on_sigwinch_settled()
        except Exception as exc:
            self.fail(f"_on_sigwinch_settled 不应该向外抛异常，但抛出了: {exc}")

    def test_stop_cancels_pending_debounce_timer(self):
        """term.stop() 应该取消尚未触发的 debounce 定时器，避免遗留的
        daemon 线程在解释器关闭过程中触发回调。"""
        settle_calls = []
        self.term._on_sigwinch_settled = lambda: settle_calls.append(1)
        self.term._SIGWINCH_DEBOUNCE_SECONDS = 1.0  # 拉长，确保 stop() 先于触发执行

        self.term._on_sigwinch(None, None)  # 启动一个 debounce 定时器
        self.assertIsNotNone(self.term._sigwinch_debounce_timer)

        self.term.stop()
        self.assertIsNone(self.term._sigwinch_debounce_timer, "stop() 后应清空定时器引用")

        # 等过原定的触发时间点，确认 cancel 真的生效，settle 没有被调用
        time.sleep(1.2)
        self.assertEqual(settle_calls, [], "stop() 取消的定时器不应该再触发 settle 回调")


if __name__ == "__main__":
    unittest.main()