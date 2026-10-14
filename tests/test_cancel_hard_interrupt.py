"""
cancel_task 硬取消：取消时真正中断 bash 子进程与进行中的 LLM 请求。

对应 next_doc/cancel_task_hard_interrupt_plan.md。覆盖：
  - CancelToken：killer 在 cancel() 时执行 / 已取消时登记立即执行 / 可注销
  - bash（流式与非流式两条路径）：取消后秒级返回、保留已产生输出、子进程被杀
  - 已取消时 bash 不启动子进程
  - 无令牌（主 Agent 等路径）时 bash 行为不变
  - ProviderMixin._call_cancellable：无令牌直通；有令牌时取消立即抛 TaskCancelled，
    不等慢请求返回；先完成则正常返回 / 原样抛异常
  - TaskCancelled 是 BaseException，不会被 `except Exception` 吞掉
  - SubAgent.cancel() 同时置位令牌
"""
from __future__ import annotations

import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from mini_agent.utils.cancel_scope import (
    CancelToken, TaskCancelled, cancel_scope, current_token,
)

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="依赖 POSIX sleep / 进程组")


# ── CancelToken ─────────────────────────────────────────────────────────────


def test_killer_runs_on_cancel_and_clears():
    tok = CancelToken()
    hits = []
    tok.register_killer(lambda: hits.append(1))
    assert not tok.is_cancelled
    tok.cancel()
    tok.cancel()                       # 可重复调用，不重复执行
    assert tok.is_cancelled and hits == [1]


def test_register_after_cancel_runs_immediately():
    tok = CancelToken()
    tok.cancel()
    hits = []
    tok.register_killer(lambda: hits.append("now"))
    assert hits == ["now"]


def test_unregister_prevents_kill():
    tok = CancelToken()
    hits = []
    h = tok.register_killer(lambda: hits.append(1))
    tok.unregister_killer(h)
    tok.cancel()
    assert hits == []


def test_failing_killer_does_not_break_cancel():
    tok = CancelToken()
    ok = []
    tok.register_killer(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    tok.register_killer(lambda: ok.append(1))
    tok.cancel()
    assert tok.is_cancelled and ok == [1]


def test_cancel_scope_binds_and_restores_thread_local():
    assert current_token() is None
    tok = CancelToken()
    with cancel_scope(tok):
        assert current_token() is tok
    assert current_token() is None


def test_task_cancelled_is_not_swallowed_by_except_exception():
    assert not issubclass(TaskCancelled, Exception)
    try:
        try:
            raise TaskCancelled("x")
        except Exception:
            pytest.fail("TaskCancelled 不应被 except Exception 捕获")
    except TaskCancelled:
        pass


# ── bash ────────────────────────────────────────────────────────────────────


def _run_bash_cancel(command: str, stream: bool, cancel_after: float = 0.6):
    """在带令牌的线程里跑 bash，cancel_after 秒后取消；返回 (结果, 耗时)。"""
    import mini_agent.tools.builtin as b

    old = b._BASH_STREAM_OUTPUT_ENABLED
    b._BASH_STREAM_OUTPUT_ENABLED = stream
    tok = CancelToken()
    box = {}

    def _target():
        with cancel_scope(tok):
            t0 = time.monotonic()
            box["out"] = b.bash(command, timeout=60)
            box["elapsed"] = time.monotonic() - t0

    th = threading.Thread(target=_target, daemon=True)
    try:
        th.start()
        time.sleep(cancel_after)
        tok.cancel()
        th.join(timeout=10)
        assert not th.is_alive(), "bash 取消后应在数秒内返回"
    finally:
        b._BASH_STREAM_OUTPUT_ENABLED = old
    return box["out"], box["elapsed"]


@posix_only
@pytest.mark.parametrize("stream", [False, True])
def test_bash_is_killed_on_cancel(stream, tmp_path):
    marker = tmp_path / "after_sleep.txt"
    # 先输出一行，再长睡；若进程没被杀，睡醒后会写 marker
    cmd = f"echo started; sleep 20; echo done > {marker}"
    out, elapsed = _run_bash_cancel(cmd, stream)
    assert elapsed < 8, f"取消后应立刻返回，实际 {elapsed:.1f}s"
    assert "cancelled by cancel_task" in out
    assert "started" in out                    # 已产生的输出保留
    time.sleep(0.5)
    assert not marker.exists()                 # 进程树确实被杀，没有继续执行


@posix_only
def test_bash_kills_grandchildren_process_group(tmp_path):
    marker = tmp_path / "grandchild.txt"
    cmd = f"(sleep 3; echo x > {marker}) & wait"
    out, elapsed = _run_bash_cancel(cmd, stream=True)
    assert "cancelled by cancel_task" in out
    time.sleep(3.5)
    assert not marker.exists(), "孙子进程也应被一起杀掉"


@posix_only
@pytest.mark.parametrize("stream", [False, True])
def test_bash_not_started_when_already_cancelled(stream, tmp_path):
    import mini_agent.tools.builtin as b

    marker = tmp_path / "should_not_exist.txt"
    old = b._BASH_STREAM_OUTPUT_ENABLED
    b._BASH_STREAM_OUTPUT_ENABLED = stream
    try:
        tok = CancelToken()
        tok.cancel()
        with cancel_scope(tok):
            out = b.bash(f"echo hi > {marker}", timeout=10)
    finally:
        b._BASH_STREAM_OUTPUT_ENABLED = old
    assert "not started" in out
    assert not marker.exists()


@posix_only
@pytest.mark.parametrize("stream", [False, True])
def test_bash_unchanged_without_token(stream):
    import mini_agent.tools.builtin as b

    old = b._BASH_STREAM_OUTPUT_ENABLED
    b._BASH_STREAM_OUTPUT_ENABLED = stream
    try:
        assert current_token() is None
        out = b.bash("echo plain", timeout=10)
    finally:
        b._BASH_STREAM_OUTPUT_ENABLED = old
    assert "plain" in out and "cancelled" not in out


# ── LLM 请求 ────────────────────────────────────────────────────────────────


def _mixin():
    from mini_agent.llm.providers._base_mixin import ProviderMixin
    return ProviderMixin, SimpleNamespace()


def test_call_cancellable_passthrough_without_token():
    P, obj = _mixin()
    assert P._call_cancellable(obj, lambda a, b=0: a + b, 1, b=2) == 3


def test_call_cancellable_returns_fast_result_with_token():
    P, obj = _mixin()
    with cancel_scope(CancelToken()):
        assert P._call_cancellable(obj, lambda: "ok") == "ok"


def test_call_cancellable_propagates_exception_with_token():
    P, obj = _mixin()

    def _boom():
        raise ValueError("bad")

    with cancel_scope(CancelToken()):
        with pytest.raises(ValueError):
            P._call_cancellable(obj, _boom)


def test_call_cancellable_cancel_returns_immediately_without_waiting_for_slow_request():
    P, obj = _mixin()
    tok = CancelToken()
    release = threading.Event()
    box = {}

    def _slow():
        release.wait(timeout=10)       # 模拟一个很慢的 LLM 请求
        return "late"

    def _target():
        with cancel_scope(tok):
            t0 = time.monotonic()
            try:
                P._call_cancellable(obj, _slow)
            except TaskCancelled:
                box["cancelled"] = True
            box["elapsed"] = time.monotonic() - t0

    th = threading.Thread(target=_target, daemon=True)
    th.start()
    time.sleep(0.3)
    tok.cancel()
    th.join(timeout=3)
    release.set()
    assert not th.is_alive()
    assert box.get("cancelled") is True
    assert box["elapsed"] < 2, "取消后不应等慢请求返回"


def test_call_cancellable_raises_when_already_cancelled_and_never_calls_impl():
    P, obj = _mixin()
    tok = CancelToken()
    tok.cancel()
    called = []
    with cancel_scope(tok):
        with pytest.raises(TaskCancelled):
            P._call_cancellable(obj, lambda: called.append(1))
    assert called == []


# ── SubAgent ────────────────────────────────────────────────────────────────


def test_sub_agent_cancel_sets_token_and_runs_killers():
    from mini_agent.orchestrator.sub_agent import SubAgent

    sa = SubAgent.__new__(SubAgent)    # 不构造完整依赖，只验证 cancel 接线
    sa._cancel_event = threading.Event()
    sa._cancel_token = CancelToken(sa._cancel_event)
    sa._lock = threading.Lock()
    sa.record = SimpleNamespace(
        status=None, finished_at=None, result=None,
        append_log=lambda *_a, **_k: None,
    )
    killed = []
    sa._cancel_token.register_killer(lambda: killed.append(1))
    sa.cancel()
    assert sa._cancel_event.is_set() and killed == [1]
