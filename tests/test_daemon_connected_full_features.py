"""
tests/test_daemon_connected_full_features.py
— connected 模式"完整交互功能对等"重构的回归测试

背景：daemon connected 模式（CLI 客户端通过 HTTP 连接到已运行的 daemon）
经历了三轮迭代：
  1. 状态栏刷新线程吃掉 "You ❯" 提示符（已修复）
  2. agent 流式回复被状态栏刷新打断成碎片（已修复，代价是状态栏几乎
     被迫保持暂停，形同摆设）
  3. 本次重构：connected 模式应该和本地直跑模式、web demo 一样有完整
     交互能力——状态栏持续可见、能看到工具调用过程、能在本端完成权限
     审批、同一 session 的多个客户端之间自动同步。

本次重构的核心架构变化：
  - 不再用裸 _sys.stdout.write() + 手动 _enter_input_mode()/
    _exit_input_mode()（_bar_pause/_bar_resume）打补丁式地避免竞态，
    而是把所有输出（提示符、token、工具调用展示、权限审批提示）统一
    改为通过 Terminal 的标准方法（term.print()/term.stream_token()/
    term.streaming()/term.prompt_user()/term.confirm()），这些方法内部
    把内容交给 Terminal 唯一的渲染线程串行处理，天然和状态栏刷新互斥，
    不需要任何手动暂停/恢复逻辑。
  - DaemonClient 新增 respond_permission()/list_pending_permissions()，
    stream_output() 新增 on_event 回调，转发 tool_call/tool_result/
    tool_error/info/warning/permission_req/permission_done 等之前被
    静默忽略的事件类型。
  - 新增 _render_sse_event()（复用 ui/renderer.py 的图标/摘要逻辑渲染
    工具调用过程）和 _handle_connected_permission()（在本端完成一次完整
    的审批交互，与 permissions.py::PermissionGuard._prompt_with_http()
    的"多端竞速、谁先响应算谁的"设计对接）。

本文件分两层测试：
  1. 静态源码检查：确认旧的补丁式机制（_bar_pause/_bar_resume/
     _enter_input_mode 手动调用/set_statusbar_provider(None) 当暂停
     手段）已经被彻底移除，不会被未来的修改无意中带回来。
  2. 行为测试：用 Mock 验证 DaemonClient 新方法、_render_sse_event、
     _handle_connected_permission 的核心逻辑。
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mini_agent.cli import daemon as daemon_mod


import ast


def _code_only(func) -> str:
    """
    返回函数体的"纯代码"文本（用 ast 解析后 unparse，自动去掉所有注释，
    并手动剔除文档字符串）。

    本文件大量的静态源码检查都要排除注释/文档字符串的干扰——
    daemon.py 里写了大段注释解释"为什么不能用旧的 xxx 方式"，这些注释
    会提到旧名字（_bar_pause、_enter_input_mode 等）用于说明历史背景，
    单纯的字符串 `in` 检测会把这些注释误判成真实代码调用，是过去几轮
    测试反复踩过的坑（参见 git 历史里 test_daemon_connected_statusbar.py
    早期版本的教训）。用 ast 解析再 unparse 是比"排除以 # 开头的行"更
    可靠的办法——后者对多行字符串/反复缩进的边界情况比较脆弱，ast 是
    在语法树层面操作，不存在这类边界问题。
    """
    import inspect

    src = inspect.getsource(func)
    tree = ast.parse(src)
    target = tree.body[0]
    body = target.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # 去掉文档字符串
    return ast.unparse(ast.Module(body=body, type_ignores=[]))


# ── 第一层：静态源码检查 ─────────────────────────────────────────────────────
#
# 这些测试不需要真正运行代码，只检查 run_connected_repl/_pick_session 的
# 源码文本，确认旧架构的补丁式机制已经被彻底移除。之前两轮 bug 修复
# 都是在"裸写 stdout"这个根本问题上打补丁，本次重构换成了正确的架构，
# 这里要确保这个简化不会被后续修改悄悄推翻。

def test_run_connected_repl_no_manual_bar_pause_resume():
    """
    旧架构的 _bar_pause()/_bar_resume() 函数（手动调用
    _enter_input_mode()/_exit_input_mode()）应该已经被彻底移除——
    新架构用 term.prompt_user()/term.streaming() 等标准方法，不需要
    任何手动暂停/恢复包装函数。如果这两个函数名又出现在真实代码里
    （不是注释/文档字符串里解释历史背景），说明有人把旧的补丁式机制
    重新引入了。
    """
    code = _code_only(daemon_mod.run_connected_repl)
    assert "_bar_pause" not in code, "旧的 _bar_pause() 补丁函数被重新引入了"
    assert "_bar_resume" not in code, "旧的 _bar_resume() 补丁函数被重新引入了"


def test_run_connected_repl_no_raw_stdout_write():
    """
    主循环里不应该再有任何裸 _sys.stdout.write() 调用——所有真实输出都
    应该走 term.print()/term.stream_token()/term.prompt_user()，否则
    状态栏刷新线程和这条写入路径之间又会失去互斥保护，重新引入竞态。
    """
    code = _code_only(daemon_mod.run_connected_repl)
    assert "_sys.stdout.write(" not in code, (
        "发现裸 _sys.stdout.write() 调用，绕开了 Terminal 渲染队列"
    )


def test_run_connected_repl_no_set_statusbar_provider_none_as_pause():
    """
    不应该再用 set_statusbar_provider(None) 当作"循环内暂停状态栏"的
    手段（这是第一轮 bug 的根因模式）。set_statusbar_provider(None)
    只允许出现在两处合法场景：① session 选择被取消、函数即将 return
    时的清理；② 整个 REPL 退出前的 finally 清理。这里检查真实代码里
    这个调用最多出现 2 次。
    """
    code = _code_only(daemon_mod.run_connected_repl)
    count = code.count("set_statusbar_provider(None)")
    assert count <= 2, (
        f"set_statusbar_provider(None) 出现了 {count} 次——"
        "只应该在退出清理时出现（最多两处合法场景），不应该被当作"
        "循环内暂停状态栏的手段"
    )


def test_pick_session_no_manual_input_mode_calls():
    """
    _pick_session() 不应该再手动调用 _enter_input_mode()/
    _exit_input_mode()——新架构改用 term.print()/term.prompt_user()，
    它们内部已经处理好了状态栏协调，不需要调用方自己管理。
    """
    code = _code_only(daemon_mod._pick_session)
    assert "_enter_input_mode" not in code
    assert "_exit_input_mode" not in code
    assert "set_statusbar_provider" not in code


def test_pick_session_uses_term_print_and_prompt_user():
    """_pick_session() 应该用 term.print()/term.prompt_user() 输出菜单和
    读取选择（有 term 时），不是裸 print()/input()。"""
    import inspect

    src = inspect.getsource(daemon_mod._pick_session)
    assert "term.print(" in src
    assert "term.prompt_user(" in src


def test_run_connected_repl_uses_prompt_user_for_main_input():
    """主循环读取用户输入应该用 term.prompt_user()，不是裸 input()/
    _sys.stdin.readline()（除了 term is None 的兜底分支）。"""
    import inspect

    src = inspect.getsource(daemon_mod.run_connected_repl)
    assert "_term.prompt_user()" in src
    code = _code_only(daemon_mod.run_connected_repl)
    assert "_sys.stdin.readline()" not in code


def test_run_connected_repl_uses_streaming_methods_for_tokens():
    """流式 token 输出应该用 term.stream_token()/term.stream_end()，
    不是裸 stdout 写入。"""
    import inspect

    src = inspect.getsource(daemon_mod.run_connected_repl)
    assert "_term.stream_token(" in src
    assert "_term.stream_end()" in src


# ── 第二层：DaemonClient 新方法的行为测试 ────────────────────────────────────

def _mock_urlopen_json(return_value: dict):
    """构造一个可用作 urllib.request.urlopen 的 mock 上下文管理器，
    read() 返回指定 dict 的 JSON 序列化结果。"""
    resp = MagicMock()
    resp.read.return_value = json.dumps(return_value).encode()
    resp.status = 200
    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    return cm


def test_respond_permission_success():
    """respond_permission() 成功时返回 True，且请求体包含 approve/mode。"""
    client = daemon_mod.DaemonClient(18999)
    captured_req = {}

    def fake_urlopen(req, timeout=10):
        captured_req["url"] = req.full_url
        captured_req["data"] = json.loads(req.data.decode())
        captured_req["method"] = req.get_method()
        return _mock_urlopen_json({"ok": True})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        ok = client.respond_permission("req-123", True, mode="once")

    assert ok is True
    assert captured_req["url"].endswith("/v1/permissions/req-123")
    assert captured_req["data"] == {"approve": True, "mode": "once"}
    assert captured_req["method"] == "POST"


def test_respond_permission_with_edited_input():
    """edited_input 非 None 时应该出现在请求体里。"""
    client = daemon_mod.DaemonClient(18999)
    captured = {}

    def fake_urlopen(req, timeout=10):
        captured["data"] = json.loads(req.data.decode())
        return _mock_urlopen_json({"ok": True})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client.respond_permission("req-1", True, edited_input={"command": "ls"}, mode="once")

    assert captured["data"]["edited_input"] == {"command": "ls"}


def test_respond_permission_404_returns_false_not_exception():
    """
    404（请求已被别的端处理）应该转换成 False，不应该让异常往上抛——
    调用方（_handle_connected_permission）需要能区分"已被别处处理"和
    "真正的网络错误"，但至少不应该崩溃。
    """
    client = daemon_mod.DaemonClient(18999)

    def fake_urlopen(req, timeout=10):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        ok = client.respond_permission("req-already-handled", True)

    assert ok is False


def test_respond_permission_network_error_returns_false():
    """网络层异常（非 HTTPError）也应该被吞掉，返回 False，不向上传播。"""
    client = daemon_mod.DaemonClient(18999)

    def fake_urlopen(req, timeout=10):
        raise ConnectionError("boom")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        ok = client.respond_permission("req-1", False)

    assert ok is False


def test_list_pending_permissions_parses_list():
    client = daemon_mod.DaemonClient(18999)

    def fake_urlopen(req, timeout=5):
        return _mock_urlopen_json({"permissions": [{"req_id": "a"}, {"req_id": "b"}]})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        pending = client.list_pending_permissions()

    assert pending == [{"req_id": "a"}, {"req_id": "b"}]


def test_list_pending_permissions_returns_empty_on_error():
    client = daemon_mod.DaemonClient(18999)

    def fake_urlopen(req, timeout=5):
        raise ConnectionError("boom")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        pending = client.list_pending_permissions()

    assert pending == []


# ── 第二层：_handle_sse_frame 事件转发 ───────────────────────────────────────

def _sse_frame(evt_type: str, data: dict, idx: int = 1) -> str:
    return f"id: {idx}\nevent: {evt_type}\ndata: {json.dumps(data)}\n"


def test_handle_sse_frame_forwards_tool_call_to_on_event():
    """tool_call 事件应该被转发给 on_event，不再被静默忽略——这是本次
    重构最核心的协议层修复：之前这类事件直接被丢弃，是 connected 模式
    完全看不到工具调用过程的根因。"""
    client = daemon_mod.DaemonClient(18999)
    received = []

    def on_event(evt_type, payload):
        received.append((evt_type, payload))

    frame = _sse_frame("tool_call", {"turn_id": "t1", "tool_name": "bash",
                                       "tool_input": {"command": "ls"}})
    done = client._handle_sse_frame(frame, on_token=None, on_done=None,
                                     on_error=None, on_event=on_event)

    assert done is False
    assert received == [("tool_call", {"turn_id": "t1", "tool_name": "bash",
                                          "tool_input": {"command": "ls"}})]


def test_handle_sse_frame_forwards_permission_req():
    client = daemon_mod.DaemonClient(18999)
    received = []

    frame = _sse_frame("permission_req", {
        "turn_id": "t1", "req_id": "r1", "tool_name": "bash", "tool_input": {}
    })
    client._handle_sse_frame(frame, on_token=None, on_done=None, on_error=None,
                              on_event=lambda et, p: received.append((et, p)))

    assert received[0][0] == "permission_req"
    assert received[0][1]["req_id"] == "r1"


def test_handle_sse_frame_still_handles_token_and_turn_done():
    """确认新增 on_event 参数没有破坏原有的 token/turn_done 处理路径
    （向后兼容性检查）。"""
    client = daemon_mod.DaemonClient(18999)
    tokens = []
    done_calls = []

    frame1 = _sse_frame("token", {"turn_id": "t1", "text": "hello"})
    frame2 = _sse_frame("turn_done", {"turn_id": "t1", "text": "hello"})

    d1 = client._handle_sse_frame(frame1, on_token=lambda t: tokens.append(t),
                                   on_done=lambda t, e=None: done_calls.append((t, e)),
                                   on_error=None, on_event=None)
    d2 = client._handle_sse_frame(frame2, on_token=lambda t: tokens.append(t),
                                   on_done=lambda t, e=None: done_calls.append((t, e)),
                                   on_error=None, on_event=None)

    assert d1 is False
    assert d2 is True
    assert tokens == ["hello"]
    assert done_calls == [("hello", None)]


def test_handle_sse_frame_unknown_event_ignored_safely():
    """turn_start / replay_done 等纯簿记事件应该被安全忽略，不触发
    on_event、不报错。"""
    client = daemon_mod.DaemonClient(18999)
    received = []

    frame = _sse_frame("turn_start", {"turn_id": "t1"})
    done = client._handle_sse_frame(frame, on_token=None, on_done=None,
                                     on_error=None, on_event=lambda et, p: received.append(et))

    assert done is False
    assert received == []


# ── 第二层：_render_sse_event 渲染逻辑 ───────────────────────────────────────

def test_render_sse_event_tool_call_calls_term_print():
    term = MagicMock()
    daemon_mod._render_sse_event(term, "tool_call", {
        "tool_name": "bash", "tool_input": {"command": "ls -la"}
    })
    assert term.print.called


def test_render_sse_event_tool_result_short_uses_print_or_syntax():
    term = MagicMock()
    daemon_mod._render_sse_event(term, "tool_result", {
        "tool_name": "bash", "result": "file1.txt\nfile2.txt"
    })
    # bash 结果会走 syntax（text 高亮）或 print，两者之一被调用即可
    assert term.print.called or term.syntax.called


def test_render_sse_event_tool_result_empty():
    term = MagicMock()
    daemon_mod._render_sse_event(term, "tool_result", {
        "tool_name": "bash", "result": ""
    })
    assert term.print.called


def test_render_sse_event_tool_error_calls_print():
    term = MagicMock()
    daemon_mod._render_sse_event(term, "tool_error", {
        "tool_name": "bash", "message": "command not found"
    })
    term.print.assert_called()
    args = term.print.call_args[0][0]
    assert "command not found" in args


def test_render_sse_event_info_and_warning():
    term = MagicMock()
    daemon_mod._render_sse_event(term, "info", {"message": "session created"})
    daemon_mod._render_sse_event(term, "warning", {"message": "low disk space"})
    assert term.print.call_count == 2


def test_render_sse_event_none_term_does_not_raise():
    """term=None 时应该安全跳过，不抛异常（防御性兜底）。"""
    daemon_mod._render_sse_event(None, "tool_call", {"tool_name": "bash", "tool_input": {}})


def test_render_sse_event_permission_events_not_rendered_here():
    """permission_req/permission_done 不应该被 _render_sse_event 处理——
    它们需要交互式审批流程（_handle_connected_permission），不是单纯展示。
    这里确认调用它不会意外触发 print（避免和真正的审批提示重复/冲突）。"""
    term = MagicMock()
    daemon_mod._render_sse_event(term, "permission_req", {
        "req_id": "r1", "tool_name": "bash", "tool_input": {}
    })
    term.print.assert_not_called()


def test_render_sse_event_unknown_type_does_not_raise():
    term = MagicMock()
    daemon_mod._render_sse_event(term, "some_future_event_type", {"foo": "bar"})
    # 不应该抛异常，也不强求一定要调用任何方法


# ── 第二层：rich markup 转义安全性 ───────────────────────────────────────────
#
# payload 里的字段（tool_name 之外的 summary/message/result/path/title）
# 都是不可信的外部数据（来自工具实际执行输出或用户输入）。rich Console
# 默认会把字符串里的 "[xxx]" 解析成 markup 标签——如果其中恰好出现 rich
# 认识的标签名（"bold"、"red"、"green" 等都是常见词，bash 输出/日志格式
# 里出现的概率不低），不转义就会导致这部分内容被静默吃掉，而不是报错，
# 非常隐蔽。这里用真实的 rich.console.Console 渲染（不是 mock），确认
# 这类内容能完整原样显示。

def _render_to_string(render_fn) -> str:
    """构造一个真正接 rich.Console 的假 term，跑一次渲染，返回渲染结果
    的纯文本。用于验证 markup 转义是否真的生效，而不只是检查"调用了
    print"这种表面行为。"""
    from rich.console import Console
    import io

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=200)

    class _FakeTerm:
        def print(self, *args, **kwargs):
            kwargs.pop("end", None)  # Console.print 的 end 行为细节不重要
            console.print(*args, **kwargs)

        def syntax(self, *args, **kwargs):
            pass  # syntax 渲染路径不在这里测试

    render_fn(_FakeTerm())
    return buf.getvalue()


def test_render_sse_event_tool_call_preserves_bracket_content_in_command():
    """
    回归测试：bash 命令文本里包含 "[bold]...[/bold]" 这种恰好是 rich
    认识的标签名时，不转义会导致这部分内容被吃掉（亲自验证过这个具体
    失败模式——"[bold]hello[/bold]" 不转义时渲染结果会变成 "hello"，
    标签文字本身消失，没有任何错误提示）。转义后必须原样保留。
    """
    output = _render_to_string(lambda term: daemon_mod._render_sse_event(
        term, "tool_call",
        {"tool_name": "bash", "tool_input": {"command": "echo [bold]hello[/bold] world"}},
    ))
    assert "[bold]hello[/bold] world" in output


def test_render_sse_event_tool_error_preserves_bracket_content():
    output = _render_to_string(lambda term: daemon_mod._render_sse_event(
        term, "tool_error",
        {"tool_name": "bash", "message": "failed: [red]critical[/red] error"},
    ))
    assert "[red]critical[/red] error" in output


def test_render_sse_event_info_preserves_bracket_content():
    output = _render_to_string(lambda term: daemon_mod._render_sse_event(
        term, "info", {"message": "config [yellow]warning[/yellow] applied"},
    ))
    assert "[yellow]warning[/yellow] applied" in output


def test_render_sse_event_tool_result_text_preserves_bracket_content():
    """tool_result 走 Text() 包装路径（result 没有匹配到语法高亮语言时），
    同样要验证方括号内容不丢失。"""
    output = _render_to_string(lambda term: daemon_mod._render_sse_event(
        term, "tool_result",
        {"tool_name": "some_unknown_tool", "result": "status: [green]ok[/green]"},
    ))
    assert "[green]ok[/green]" in output


def test_pick_session_preserves_bracket_content_in_title():
    """
    _pick_session() 展示的 session 标题来自用户历史第一句话的摘要，
    同样是不可信外部数据。确认标题包含方括号时不会被吃掉。
    """
    client = MagicMock()
    client.list_sessions.return_value = [
        {"id": "s1", "title": "[bold]紧急[/bold] 修复一下这个 bug",
         "turns": 1, "age_str": "2026-06-28T11:00"},
    ]
    client.get_status.return_value = {"session_id": ""}

    output = _render_to_string(lambda term: _run_pick_session_noninteractive(client, term))
    assert "[bold]紧急[/bold]" in output


def _run_pick_session_noninteractive(client, term):
    """_pick_session() 会阻塞在 input()/prompt_user() 等待选择，这里用
    monkeypatch 的方式提供一个立即返回的 term.prompt_user，让函数走完
    打印列表的部分就拿到结果返回，不需要真正阻塞。"""
    term.prompt_user = lambda *_a, **_kw: "1"
    daemon_mod._pick_session(client, term=term)


def test_handle_connected_permission_preserves_bracket_content_in_summary():
    """权限审批提示里的 summary（来自 tool_input，可能是用户的 bash
    命令文本）同样需要验证不被吃掉。"""
    client = MagicMock()
    client.respond_permission.return_value = True
    client.list_pending_permissions.return_value = []

    def render_with_confirm(term):
        term.confirm = lambda *_a, **_kw: "y"
        daemon_mod._handle_connected_permission(
            client, term, "req-1", "bash",
            {"command": "echo [bold]danger[/bold]"}, "turn-1",
        )

    output = _render_to_string(render_with_confirm)
    assert "[bold]danger[/bold]" in output


# ── 第二层：_handle_connected_permission 交互逻辑 ────────────────────────────

def _make_term_with_confirm(choice_sequence):
    """构造一个 term mock，confirm() 依次返回 choice_sequence 里的值。"""
    term = MagicMock()
    it = iter(choice_sequence)

    def fake_confirm(prompt_lines, choices="", default="y", interrupt_event=None):
        return next(it)

    term.confirm.side_effect = fake_confirm
    return term


def test_handle_connected_permission_approve_once():
    """选择 'y' 应该调用 respond_permission(approve=True, mode='once')
    并停止等待。"""
    client = MagicMock()
    client.respond_permission.return_value = True
    client.list_pending_permissions.return_value = []  # watcher 线程不会误触发
    term = _make_term_with_confirm(["y"])

    daemon_mod._handle_connected_permission(
        client, term, "req-1", "bash", {"command": "ls"}, "turn-1"
    )

    client.respond_permission.assert_called_once_with("req-1", True, mode="once")


def test_handle_connected_permission_deny():
    client = MagicMock()
    client.respond_permission.return_value = True
    client.list_pending_permissions.return_value = []
    term = _make_term_with_confirm(["n"])

    daemon_mod._handle_connected_permission(
        client, term, "req-1", "bash", {"command": "rm -rf /"}, "turn-1"
    )

    client.respond_permission.assert_called_once_with("req-1", False, mode="once")


def test_handle_connected_permission_always():
    client = MagicMock()
    client.respond_permission.return_value = True
    client.list_pending_permissions.return_value = []
    term = _make_term_with_confirm(["a"])

    daemon_mod._handle_connected_permission(
        client, term, "req-1", "bash", {"command": "ls"}, "turn-1"
    )

    client.respond_permission.assert_called_once_with("req-1", True, mode="always")


def test_handle_connected_permission_deny_always():
    client = MagicMock()
    client.respond_permission.return_value = True
    client.list_pending_permissions.return_value = []
    term = _make_term_with_confirm(["d"])

    daemon_mod._handle_connected_permission(
        client, term, "req-1", "bash", {"command": "ls"}, "turn-1"
    )

    client.respond_permission.assert_called_once_with("req-1", False, mode="deny_always")


def test_handle_connected_permission_show_then_decide():
    """选择 's' 应该展示 tool_input 详情，然后继续等待下一次选择
    （不应该提交决定）。"""
    client = MagicMock()
    client.respond_permission.return_value = True
    client.list_pending_permissions.return_value = []
    term = _make_term_with_confirm(["s", "y"])

    daemon_mod._handle_connected_permission(
        client, term, "req-1", "bash", {"command": "ls"}, "turn-1"
    )

    # 'show' 不提交决定，最终应该是因为第二次选择 'y' 才提交
    client.respond_permission.assert_called_once_with("req-1", True, mode="once")


def test_handle_connected_permission_none_term_does_not_raise():
    """term=None 时应该直接安全返回，不尝试任何交互（没有办法在没有
    Terminal 实例的情况下做阻塞式确认）。"""
    client = MagicMock()
    daemon_mod._handle_connected_permission(
        client, None, "req-1", "bash", {"command": "ls"}, "turn-1"
    )
    client.respond_permission.assert_not_called()


def test_handle_connected_permission_interrupted_by_other_client():
    """
    模拟"别的端先决定了"的场景，用普通 Exception 模拟中断异常（验证
    _handle_connected_permission 的宽泛 except Exception 兜底分支
    确实有效——即使遇到非 _InterruptedByHTTP 类型的异常，也能正确
    依据 permission_done_event.is_set() 判断并停止重试）。

    注：_InterruptedByHTTP 现在已经在 permissions.py 里被真正定义
    （见该文件的修复记录），_handle_connected_permission 对它有精确
    捕获分支（见下面 test_handle_connected_permission_interrupted_
    with_real_exception_type），本测试专门验证宽泛兜底分支这条独立
    防线，两者互补。
    """
    client = MagicMock()
    client.list_pending_permissions.return_value = []  # req_id 已经不在列表里
    term = MagicMock()

    permission_done_event_holder = {}

    def fake_confirm(prompt_lines, choices="", default="y", interrupt_event=None):
        if interrupt_event is not None:
            permission_done_event_holder["evt"] = interrupt_event
            interrupt_event.set()  # 模拟别的端已经决定，中断本端等待
        raise RuntimeError("interrupted")

    term.confirm.side_effect = fake_confirm

    daemon_mod._handle_connected_permission(
        client, term, "req-1", "bash", {"command": "ls"}, "turn-1"
    )

    client.respond_permission.assert_not_called()


def test_handle_connected_permission_interrupted_with_real_exception_type():
    """
    用真正的 mini_agent.permissions._InterruptedByHTTP 类型触发中断
    （而不是普通 Exception），验证 _handle_connected_permission 里的
    精确 except _InterruptedByHTTP 分支能正常捕获——这条测试依赖
    permissions.py 里这个类被真正定义这个修复（之前这个类只在注释里
    提到，每个使用点各自动态生成一个不同的本地类，跨模块精确捕获
    永远会失败，只能依赖宽泛的 except Exception 兜底）。
    """
    from mini_agent.permissions import _InterruptedByHTTP

    client = MagicMock()
    client.list_pending_permissions.return_value = []
    term = MagicMock()

    def fake_confirm(prompt_lines, choices="", default="y", interrupt_event=None):
        if interrupt_event is not None:
            interrupt_event.set()
        raise _InterruptedByHTTP()

    term.confirm.side_effect = fake_confirm

    daemon_mod._handle_connected_permission(
        client, term, "req-1", "bash", {"command": "ls"}, "turn-1"
    )

    client.respond_permission.assert_not_called()


def test_handle_connected_permission_watcher_detects_external_decision():
    """
    后台 watcher 线程检测到 req_id 不再出现在 list_pending_permissions()
    结果里时（说明别的端已经处理了），应该设置 permission_done_event，
    让本端的等待提前结束。用真实的 threading.Event + 真实的后台线程
    验证这个轮询逻辑本身能正常工作（不是单纯 mock 掉）。
    """
    client = MagicMock()
    # 第一次调用还在 pending，第二次（0.5s 后）已经不在了
    call_count = {"n": 0}

    def fake_list_pending():
        call_count["n"] += 1
        if call_count["n"] <= 1:
            return [{"req_id": "req-1"}]
        return []

    client.list_pending_permissions.side_effect = fake_list_pending

    term = MagicMock()

    def fake_confirm(prompt_lines, choices="", default="y", interrupt_event=None):
        # 模拟本端用户一直不输入：等 interrupt_event 被 watcher 设置
        if interrupt_event is not None:
            interrupt_event.wait(timeout=5)
            if interrupt_event.is_set():
                raise RuntimeError("interrupted by watcher")
        return "y"

    term.confirm.side_effect = fake_confirm

    daemon_mod._handle_connected_permission(
        client, term, "req-1", "bash", {"command": "ls"}, "turn-1"
    )

    # 本端没有真正提交决定（是被别的端"抢先"决定的）
    client.respond_permission.assert_not_called()
