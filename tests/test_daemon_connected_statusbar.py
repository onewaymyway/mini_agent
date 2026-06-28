"""
tests/test_daemon_connected_statusbar.py
— daemon connected 模式状态栏 / 输出竞态回归测试

背景（两轮真实复现的 bug，同一根因的两种表现）：

  bug 1（已修复）：Windows 下实测，CLI 客户端连接 daemon、选择 session
  之后，画面上完全没有 "You ❯" 输入提示符（看起来像卡住，但其实是异步
  状态栏刷新线程把刚打印的提示符吃掉了）。

  bug 2（已修复，bug 1 修复后才暴露出来）：bug 1 修过之后，提示符正常
  显示，但 agent 的回复内容在流式输出过程中被状态栏刷新行反复打断，
  一段连续的中文句子被切成"看起来随机断开"的碎片，状态栏那一行
  （"🌐 [connected] session=... state=running ..."）会每隔几百毫秒插
  进正在输出的文本中间。

根因（两个 bug 共享同一个根因，只是触发的时间窗口不同）：
  Terminal._refresh_loop()（ui/terminal.py）判断是否要继续刷新屏幕的
  标志是 _refresh_paused，不是 "provider 是否为 None"。任何时候只要
  _refresh_paused 没有被设置，刷新线程就会每 _refresh_interval（默认
  0.25s）向渲染队列投递一条 "_refresh" 消息，render_thread 收到后会
  做一次直接写 stdout 的状态栏擦除/重绘——这个写动作和 connected REPL
  自己用裸 sys.stdout.write() 做的任何输出（提示符、流式 token、
  session 列表、结果提示）都没有协调，必然交织错乱。

  bug 1 的窗口是"打印 You ❯ 提示符、阻塞等待用户输入"期间；
  bug 2 的窗口是"用户已经输入完，daemon 正在流式返回 agent 回复"期间
  ——bug 1 修复时只暂停了"等待输入"这一段，没有意识到"流式输出"
  这一段同样需要暂停，而且原来的代码注释里写的设计意图
  （"agent 回复期间：恢复刷新线程，显示 session/state 信息"）本身就是
  错的：那正是裸 stdout 写入最频繁的时间段。

修复（统一为一条原则）：只要 connected REPL 自己还要往 stdout 写任何
内容（提示符、session 列表、流式 token、结果提示、observer 旁观输出），
刷新线程必须保持暂停（_refresh_paused 已设置）；只有在 connected REPL
确定不会再自己写 stdout 的间隙，才允许恢复。具体到代码：
  - run_connected_repl() 主循环：_bar_pause()（= _enter_input_mode()）
    在打印提示符之前调用，_bar_resume()（= _exit_input_mode()）推迟到
    一整个 turn（发消息 → 流式接收 → 打印收尾换行）彻底结束之后才调用，
    不再像最早的实现那样"读完用户输入就立刻恢复"。
  - _pick_session()：暂停范围覆盖"打印 session 列表 + 所有 input() 循环"
    整个函数体，用一次 enter/exit 包裹，而不是每次循环内反复进出。
  - _enter_input_mode()/_exit_input_mode() 内部用的是简单布尔标志，没有
    重入计数，所以 _pick_session() 增加了重入检测：调用前先看
    term._refresh_paused 是否已经被外层（run_connected_repl 主循环的
    "/session list" 分支）设置过，如果是，本函数全程不做任何 enter/exit
    调用，避免提前解除外层还想维持的暂停状态。

本测试只验证"调用了正确的方法、且时机正确"这件事本身（用 Mock 断言
调用序列/调用时机），不依赖真实终端/TTY，避免在 CI 环境里因为没有 TTY
而跳过验证。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mini_agent.cli import daemon as daemon_mod


# ── 测试用的 Terminal mock：_refresh_paused 需要是真正的状态机 ───────────────
#
# MagicMock() 的默认行为是"任何属性访问/调用都返回另一个 MagicMock，真值
# 判断为 True"——如果直接 `term = MagicMock()`，那么
# `term._refresh_paused.is_set()` 返回的是一个 MagicMock 对象，
# `not term._refresh_paused.is_set()` 永远是 False，会让 _pick_session()
# 里的重入检测误判为"外层已经暂停"，从而完全跳过 _enter_input_mode()/
# _exit_input_mode() 的调用——必须显式模拟 _refresh_paused 这个
# threading.Event 的真实语义（set()/clear()/is_set() 三者协同），
# 否则测试无法正确反映真实的 Terminal 行为。

class _FakeRefreshPaused:
    """模拟 threading.Event 的最小子集，配合 _FakeTerm 使用。"""

    def __init__(self, initial: bool = False):
        self._flag = initial

    def is_set(self) -> bool:
        return self._flag

    def set(self) -> None:
        self._flag = True

    def clear(self) -> None:
        self._flag = False


def make_fake_term(initially_paused: bool = False) -> MagicMock:
    """
    构造一个 Terminal mock：
      - _enter_input_mode()/_exit_input_mode() 是普通 MagicMock（可断言
        调用次数/顺序），但额外联动 _refresh_paused 的 set/clear，
        如实反映真实 Terminal 的行为（这样 _pick_session 内部的重入
        检测在测试里才有意义）。
      - _refresh_paused 是 _FakeRefreshPaused，初始状态由
        initially_paused 决定（模拟"外层是否已经暂停过"）。
    """
    term = MagicMock()
    term._refresh_paused = _FakeRefreshPaused(initially_paused)

    def _enter():
        term._refresh_paused.set()

    def _exit():
        term._refresh_paused.clear()

    term._enter_input_mode.side_effect = _enter
    term._exit_input_mode.side_effect = _exit
    return term


def _make_client(sessions):
    client = MagicMock()
    client.list_sessions.return_value = sessions
    client.get_status.return_value = {"session_id": sessions[0]["id"] if sessions else ""}
    return client


# ── _pick_session：未暂停时自己负责暂停/恢复 ─────────────────────────────────

def test_pick_session_uses_enter_exit_input_mode_not_set_provider(monkeypatch):
    """
    _pick_session() 在外层尚未暂停刷新线程时，必须自己调用
    term._enter_input_mode()/_exit_input_mode()，绝不能改回
    set_statusbar_provider(None) 这种不充分的旧实现（旧实现是
    "You ❯ 提示符消失" bug 的根因）。
    """
    sessions = [
        {"id": "abc12345", "title": "测试 session", "turns": 1, "age_str": "2026-06-28T11:00"},
    ]
    client = _make_client(sessions)
    term = make_fake_term(initially_paused=False)

    monkeypatch.setattr("builtins.input", lambda *_a, **_kw: "1")

    result = daemon_mod._pick_session(client, term=term)

    assert result == "abc12345"
    # 核心断言：用了官方机制，而不是 set_statusbar_provider(None)
    term._enter_input_mode.assert_called_once()
    term._exit_input_mode.assert_called_once()
    term.set_statusbar_provider.assert_not_called()
    # 函数返回后，自己暂停的必须自己恢复，不能让标志卡死在 True
    assert term._refresh_paused.is_set() is False


def test_pick_session_pauses_before_printing_list_and_input(monkeypatch):
    """
    暂停必须发生在打印 session 列表之前（不只是 input() 之前）——
    print() 同样是裸写 stdout，跟刷新线程一样没有协调，打印列表期间
    也需要受到保护。"""
    sessions = [
        {"id": "s1", "title": "t", "turns": 0, "age_str": "2026-06-28T11:00"},
    ]
    client = _make_client(sessions)
    term = make_fake_term(initially_paused=False)

    call_order: list[str] = []
    real_enter = term._enter_input_mode.side_effect
    real_exit = term._exit_input_mode.side_effect

    def _enter():
        call_order.append("enter")
        real_enter()

    def _exit():
        call_order.append("exit")
        real_exit()

    term._enter_input_mode.side_effect = _enter
    term._exit_input_mode.side_effect = _exit

    def fake_print(*args, **kwargs):
        call_order.append("print")

    monkeypatch.setattr("builtins.print", fake_print)

    def fake_input(_prompt):
        call_order.append("input")
        return "1"

    monkeypatch.setattr("builtins.input", fake_input)

    daemon_mod._pick_session(client, term=term)

    # enter 必须排在第一个 print 之前，exit 必须排在最后一个 input 之后
    assert call_order[0] == "enter"
    assert call_order.index("exit") > call_order.index("input")
    assert call_order.index("print") > call_order.index("enter")


def test_pick_session_exit_called_even_on_eof(monkeypatch):
    """input() 抛 EOFError 时，_exit_input_mode 也必须在 finally 里被调用
    （否则刷新线程会被永久卡在暂停状态，状态栏从此再也不会刷新）。"""
    sessions = [
        {"id": "s1", "title": "t", "turns": 0, "age_str": "2026-06-28T11:00"},
    ]
    client = _make_client(sessions)
    term = make_fake_term(initially_paused=False)

    def raise_eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)

    result = daemon_mod._pick_session(client, term=term)

    assert result is None
    term._enter_input_mode.assert_called_once()
    term._exit_input_mode.assert_called_once()
    assert term._refresh_paused.is_set() is False


def test_pick_session_no_history_skips_input_entirely(monkeypatch):
    """没有历史 session 时直接静默返回 ""，不应该触碰状态栏或 input()。"""
    client = _make_client([])
    term = make_fake_term(initially_paused=False)

    result = daemon_mod._pick_session(client, term=term)

    assert result == ""
    term._enter_input_mode.assert_not_called()
    term._exit_input_mode.assert_not_called()


def test_pick_session_works_without_term(monkeypatch):
    """term=None（未能拿到 Terminal 实例）时不应该抛异常——防御性分支。"""
    sessions = [
        {"id": "s1", "title": "t", "turns": 0, "age_str": "2026-06-28T11:00"},
    ]
    client = _make_client(sessions)

    monkeypatch.setattr("builtins.input", lambda *_a, **_kw: "1")

    result = daemon_mod._pick_session(client, term=None)
    assert result == "s1"


# ── _pick_session：重入保护（外层已经暂停时不能提前恢复）────────────────────

def test_pick_session_reentrant_when_already_paused(monkeypatch):
    """
    "/session list 命令执行期间结果提示又被状态栏打断" 的回归测试：
    如果调用 _pick_session() 之前刷新线程已经处于暂停状态（模拟
    run_connected_repl 主循环的 "/session list" 分支：外层已经调用过
    _bar_pause()），_pick_session() 内部绝不能再调用 _enter_input_mode()/
    _exit_input_mode()——否则会把外层还想维持的暂停状态提前解除，
    调用方返回后打印的结果提示（"[daemon] ✓ Switched to: ..."）又会
    重新暴露在状态栏竞态里。
    """
    sessions = [
        {"id": "s1", "title": "t", "turns": 0, "age_str": "2026-06-28T11:00"},
    ]
    client = _make_client(sessions)
    # 模拟外层已经暂停：_refresh_paused 初始为 True
    term = make_fake_term(initially_paused=True)

    monkeypatch.setattr("builtins.input", lambda *_a, **_kw: "1")

    result = daemon_mod._pick_session(client, term=term)

    assert result == "s1"
    # 核心断言：外层已经暂停时，本函数全程不应该调用 enter/exit
    term._enter_input_mode.assert_not_called()
    term._exit_input_mode.assert_not_called()
    # 暂停状态必须原样保持（没有被提前解除），恢复的责任留给外层
    assert term._refresh_paused.is_set() is True


def test_pick_session_reentrant_preserves_pause_on_eof(monkeypatch):
    """重入场景下，即使用户按 Ctrl-C/EOF 取消选择，外层的暂停状态也不能
    被提前解除（同上，只是覆盖异常路径）。"""
    sessions = [
        {"id": "s1", "title": "t", "turns": 0, "age_str": "2026-06-28T11:00"},
    ]
    client = _make_client(sessions)
    term = make_fake_term(initially_paused=True)

    def raise_eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)

    result = daemon_mod._pick_session(client, term=term)

    assert result is None
    term._enter_input_mode.assert_not_called()
    term._exit_input_mode.assert_not_called()
    assert term._refresh_paused.is_set() is True


# ── run_connected_repl 主循环的 _bar_pause/_bar_resume ───────────────────────
#
# run_connected_repl() 是一个体量很大、直接读写真实 stdin/stdout 的函数，
# 不便整体做端到端单测。这里改用白盒方式：直接检查源码文本，确认
# 两件事——
#   1. _bar_pause/_bar_resume 的实现必须使用 _enter_input_mode()/
#      _exit_input_mode()，不是 set_statusbar_provider(None)（bug 1）。
#   2. _bar_resume() 的调用点必须在"流式输出彻底结束之后"，不能紧跟在
#      "读完用户输入"之后就立刻调用（bug 2 的核心）。

def test_run_connected_repl_source_uses_input_mode_not_set_provider():
    """
    静态检查 run_connected_repl 函数体源码：_bar_pause/_bar_resume 的
    实现必须使用 _enter_input_mode()/_exit_input_mode()，且函数体内
    不应该再调用 set_statusbar_provider(None) 来实现"暂停状态栏"这个
    语义——这正是 bug 1 的根因模式。
    """
    import inspect

    src = inspect.getsource(daemon_mod.run_connected_repl)

    assert "_term._enter_input_mode()" in src
    assert "_term._exit_input_mode()" in src

    pause_start = src.index("def _bar_pause()")
    resume_start = src.index("def _bar_resume()")
    bar_pause_body = src[pause_start:resume_start]
    assert "set_statusbar_provider" not in bar_pause_body
    assert "_enter_input_mode" in bar_pause_body

    resume_body_end = src.index("def ", resume_start + len("def _bar_resume()"))
    bar_resume_body = src[resume_start:resume_body_end]
    assert "set_statusbar_provider" not in bar_resume_body
    assert "_exit_input_mode" in bar_resume_body


def _bar_resume_calls_in_code(snippet: str) -> bool:
    """检测代码片段里是否存在真实的 _bar_resume() 调用（忽略注释行里
    提到这个名字的情况——本文件的注释里大量引用 "_bar_resume()" 这个
    词来解释历史 bug，纯字符串包含检测会把注释误判成代码）。"""
    for line in snippet.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "_bar_resume()" in stripped:
            return True
    return False


def test_run_connected_repl_no_unconditional_resume_right_after_readline():
    """
    bug 2 最原始的回归测试：在 "line = _sys.stdin.readline()" 之后、
    到第一个内置命令判断（"if not user_input:"）之前的这一段源码区间，
    不应该出现任何 "_bar_resume()" 调用。

    这段区间是"刚拿到用户输入，还没开始判断是哪种命令"的阶段——
    如果这里出现 _bar_resume()，说明是无条件执行的（不在任何 if 分支
    内），对应的正是 bug 2 最初的错误写法："有输入了，立刻恢复状态栏"，
    会让刷新线程在发消息/流式接收期间提前恢复。
    跟 test_run_connected_repl_bar_resume_not_called_immediately_after_input
    互补：后者检查"主干成功路径到流式线程启动之间"，本测试专门盯防
    "无条件提前 resume"这一种最容易被重新引入的错误模式（比如有人为了
    图省事把 resume 挪回循环体顶部"读完输入就恢复一次，后面各分支不用
    管"）。

    注：本文件的注释里会提到 "_bar_resume()" 这个词来解释历史 bug，
    所以用 _bar_resume_calls_in_code() 过滤掉注释行，只检测真实代码。
    """
    import inspect

    src = inspect.getsource(daemon_mod.run_connected_repl)

    readline_idx = src.index("line = _sys.stdin.readline()")
    empty_check_idx = src.index("if not user_input:")
    between = src[readline_idx:empty_check_idx]

    assert not _bar_resume_calls_in_code(between), (
        "在'刚读完输入、还没判断是哪种命令'阶段发现了 _bar_resume() 调用——"
        "这是无条件执行的提前恢复，正是 bug 2 最初的错误写法"
        "（'有输入了，立刻恢复状态栏'），会导致发消息/流式接收期间"
        "状态栏刷新线程重新开始异步写 stdout，打断 agent 回复内容"
    )


def test_run_connected_repl_bar_resume_not_called_immediately_after_input():
    """
    bug 2 的核心回归测试：_bar_resume() 不能紧跟在"读取用户输入"之后
    立刻调用——必须推迟到流式输出（stream_worker / done_event.wait）
    结束之后。

    用源码文本做静态检查：取"消息发送成功"（turn_id = client.send_message(...)
    这一行）到"启动流式接收线程"（threading.Thread(target=stream_worker）
    之间这一段——这正是真正会走进 streaming 的主干路径，中间不应该再
    经过任何 continue/_bar_resume()。注意：不能直接拿
    "line = _sys.stdin.readline()" 作为起点，因为那之后到流式线程
    启动之前还夹着 /session new、/session list、/session、send_message
    失败 这几个内置命令的 continue 早退分支——它们各自调用一次
    _bar_resume() 是完全正确的（这次 input 根本不会进入 streaming，
    必须自己负责恢复），如果把整个区间一起检查会把这些合法调用误判
    成回归。只有"主干路径"（确实会启动 stream_worker 的那条路径）上
    不能有 _bar_resume()，才是 bug 2 真正要防的回归。
    """
    import inspect

    src = inspect.getsource(daemon_mod.run_connected_repl)

    send_message_idx = src.index("turn_id = client.send_message(")
    stream_thread_start_idx = src.index("threading.Thread(target=stream_worker")
    assert send_message_idx < stream_thread_start_idx, "源码结构假设不成立，请检查测试是否需要更新"

    main_path = src[send_message_idx:stream_thread_start_idx]
    # 主干路径里允许出现一次"send_message 失败"的早退 continue 分支
    # （它会调用一次 _bar_resume() 然后 continue，不会真正进入 streaming），
    # 这里要排除掉那个早退分支本身，只检查"主干（成功路径）"剩余部分。
    # 早退分支以 "if not turn_id:" 开头、"continue" 结尾。
    fail_branch_start = main_path.index("if not turn_id:")
    fail_branch_end = main_path.index("continue", fail_branch_start) + len("continue")
    success_path = main_path[:fail_branch_start] + main_path[fail_branch_end:]

    assert not _bar_resume_calls_in_code(success_path), (
        "_bar_resume() 出现在'消息已成功发送，即将进入流式输出'的主干路径上——"
        "这会让状态栏刷新线程在流式接收期间恢复，重新引入"
        "'agent 回复内容被状态栏打断'的 bug"
    )


def test_run_connected_repl_bar_resume_called_after_streaming_done():
    """
    _bar_resume() 必须在流式输出彻底结束之后才被调用——具体来说，必须
    出现在 "done_event.wait" 之后（流式接收完成的同步点之后）。
    """
    import inspect

    src = inspect.getsource(daemon_mod.run_connected_repl)

    done_wait_idx = src.index("done_event.wait(timeout=600)")
    # 从 done_event.wait 往后找下一次真实代码（非注释）里的 _bar_resume() 调用
    after = src[done_wait_idx:]
    found_at = None
    offset = 0
    for line in after.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped.startswith("#") and "_bar_resume()" in stripped:
            found_at = offset
            break
        offset += len(line)
    assert found_at is not None, (
        "未能在 done_event.wait(...) 之后找到真实的 _bar_resume() 代码调用——"
        "流式输出结束后必须显式恢复刷新线程，否则下一轮的 _bar_pause() "
        "调用前状态栏会一直保持暂停（不算错误，但偏离了设计意图）"
    )


def test_run_connected_repl_empty_input_path_resumes_bar():
    """
    用户输入为空字符串直接 continue 的分支，没有任何后续输出，必须
    自己调用一次 _bar_resume()（否则刷新线程会一直卡在暂停状态，
    状态栏从此再也不刷新，哪怕后面合法地等待下一轮输入）。
    """
    import inspect

    src = inspect.getsource(daemon_mod.run_connected_repl)

    empty_check_idx = src.index("if not user_input:")
    next_continue_idx = src.index("continue", empty_check_idx)
    between = src[empty_check_idx:next_continue_idx]
    assert _bar_resume_calls_in_code(between), (
        "'用户输入为空'分支里没有找到真实的 _bar_resume() 代码调用——"
        "这个分支会直接 continue，没有任何后续输出，必须自己负责恢复"
        "刷新线程，否则状态栏会一直卡在暂停状态"
    )
