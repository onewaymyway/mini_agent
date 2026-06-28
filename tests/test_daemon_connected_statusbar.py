"""
tests/test_daemon_connected_statusbar.py
— daemon connected 模式状态栏 / You ❯ 提示符竞态回归测试

背景（真实复现的 bug）：
  Windows 下实测，CLI 客户端连接 daemon、选择 session 之后，画面上完全
  没有 "You ❯" 输入提示符（看起来像卡住，但其实是异步状态栏刷新线程
  把刚打印的提示符吃掉了）。

根因：
  早期实现里 _pick_session() 和 run_connected_repl() 主循环只是调用
  Terminal.set_statusbar_provider(None) 再设回去，企图"关闭状态栏"。
  但 Terminal._refresh_loop() 判断是否要继续刷新屏幕的标志是
  _refresh_paused，不是 "provider 是否为 None"——provider 设成 None
  只是让 refresh_loop 跳过"拉取新内容"那一步，它依然会无条件向渲染
  队列投递 "_refresh" 心跳消息，render_thread 收到后会做一次基于
  "光标在状态栏正下方"假设的相对 ANSI 擦除，但此刻光标实际停在刚
  打印出来的 "You ❯ " 提示符后面，于是这次异步擦除会把它整行吃掉。

修复：
  改为调用 Terminal official 的 _enter_input_mode()/_exit_input_mode()
  （tools/user_input.py、permissions.py 等阻塞输入点都是这么用的），
  它们会设置/清除 _refresh_paused，并用双重哨兵 + q.join() 确保
  render_thread 真正空闲后才返回，从根本上消除竞态。

本测试只验证"调用了正确的方法"这件事本身（用 Mock 断言调用序列），
不依赖真实终端/TTY，避免在 CI 环境里因为没有 TTY 而跳过验证。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mini_agent.cli import daemon as daemon_mod


# ── _pick_session ────────────────────────────────────────────────────────────

def _make_client(sessions):
    client = MagicMock()
    client.list_sessions.return_value = sessions
    client.get_status.return_value = {"session_id": sessions[0]["id"] if sessions else ""}
    return client


def test_pick_session_uses_enter_exit_input_mode_not_set_provider(monkeypatch, capsys):
    """
    _pick_session() 阻塞 input() 前后必须调用 term._enter_input_mode()/
    _exit_input_mode()，绝不能改回 set_statusbar_provider(None) 这种
    不充分的旧实现（旧实现是 "You ❯ 提示符消失" bug 的根因）。
    """
    sessions = [
        {"id": "abc12345", "title": "测试 session", "turns": 1, "age_str": "2026-06-28T11:00"},
    ]
    client = _make_client(sessions)
    term = MagicMock()

    monkeypatch.setattr("builtins.input", lambda *_a, **_kw: "1")

    result = daemon_mod._pick_session(client, term=term)

    assert result == "abc12345"
    # 核心断言：用了官方机制，而不是 set_statusbar_provider(None)
    term._enter_input_mode.assert_called_once()
    term._exit_input_mode.assert_called_once()
    term.set_statusbar_provider.assert_not_called()


def test_pick_session_calls_enter_before_exit_in_correct_order(monkeypatch):
    """暂停必须发生在阻塞 input() 之前，恢复必须发生在 input() 返回之后
    （顺序错了等于没修复——擦除动作必须先于打印提示符完成）。"""
    sessions = [
        {"id": "s1", "title": "t", "turns": 0, "age_str": "2026-06-28T11:00"},
    ]
    client = _make_client(sessions)
    term = MagicMock()

    call_order = []
    term._enter_input_mode.side_effect = lambda: call_order.append("enter")
    term._exit_input_mode.side_effect = lambda: call_order.append("exit")

    def fake_input(_prompt):
        call_order.append("input")
        return "1"

    monkeypatch.setattr("builtins.input", fake_input)

    daemon_mod._pick_session(client, term=term)

    assert call_order == ["enter", "input", "exit"]


def test_pick_session_exit_called_even_on_eof(monkeypatch):
    """input() 抛 EOFError 时，_exit_input_mode 也必须在 finally 里被调用
    （否则刷新线程会被永久卡在暂停状态，状态栏从此再也不会刷新）。"""
    sessions = [
        {"id": "s1", "title": "t", "turns": 0, "age_str": "2026-06-28T11:00"},
    ]
    client = _make_client(sessions)
    term = MagicMock()

    def raise_eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)

    result = daemon_mod._pick_session(client, term=term)

    assert result is None
    term._enter_input_mode.assert_called_once()
    term._exit_input_mode.assert_called_once()


def test_pick_session_no_history_skips_input_entirely(monkeypatch):
    """没有历史 session 时直接静默返回 ""，不应该触碰状态栏或 input()。"""
    client = _make_client([])
    term = MagicMock()

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


# ── run_connected_repl 主循环的 _bar_pause/_bar_resume ───────────────────────
#
# run_connected_repl() 是一个体量很大、直接读写真实 stdin/stdout 的函数，
# 不便整体做端到端单测。这里改用白盒方式：直接从源码文本里抽取
# _bar_pause/_bar_resume 两个内部函数定义，确认它们调用的是
# _enter_input_mode/_exit_input_mode 而不是 set_statusbar_provider——
# 这是回归保护的核心诉求：防止以后又被"简化"回旧的错误实现。

def test_run_connected_repl_source_uses_input_mode_not_set_provider():
    """
    静态检查 run_connected_repl 函数体源码：_bar_pause/_bar_resume 的
    实现必须使用 _enter_input_mode()/_exit_input_mode()，且函数体内
    （除显式退出前的清理分支外）不应该再调用 set_statusbar_provider(None)
    来实现"暂停状态栏"这个语义——这正是本次要修复的 bug 模式。
    """
    import inspect

    src = inspect.getsource(daemon_mod.run_connected_repl)

    assert "_term._enter_input_mode()" in src
    assert "_term._exit_input_mode()" in src

    # _bar_pause 函数体本身不能再用 set_statusbar_provider(None) 的旧写法
    pause_start = src.index("def _bar_pause()")
    resume_start = src.index("def _bar_resume()")
    bar_pause_body = src[pause_start:resume_start]
    assert "set_statusbar_provider" not in bar_pause_body
    assert "_enter_input_mode" in bar_pause_body

    main_loop_start = src.index("REPL 主循环")
    resume_body_end = src.index("def ", resume_start + len("def _bar_resume()"))
    bar_resume_body = src[resume_start:resume_body_end]
    assert "set_statusbar_provider" not in bar_resume_body
    assert "_exit_input_mode" in bar_resume_body
