"""
tests/test_tick_dispatch_phase4_guard_and_deadline.py

[next_doc/tick_dispatch_only_execution_model_plan.md 阶段四] tick 线程告警 + deadline 兜底。

覆盖：
  A. tick_thread_guard：标记/可重入/异常清除、计数、严格模式、worker 线程不误报
  B. SchedulerHeartbeat：tick 内 LLM 调用被计数并写入状态文件；严格模式下 tick 异常被吞、心跳不退出
  C. ProviderMixin：_traced_chat 内的告警与严格模式；deadline 下槽位排队/限速超时；单次请求超时被压低
  D. llm/deadline：作用域、嵌套取更紧、clamp
  E. RetryPolicy.deadline_seconds：异常路径抛 LLMTimeoutError、质量条件路径返回最后响应、无 deadline 行为不变
  F. LLMHelper：timeout/deadline 作用域、background_kwargs、ask_background 兼容鸭子类型
  G. LLMClientPool：deadline 用尽后不再 fallback / 不睡 round_wait
  H. 调用点：declare_paths 使用后台预算
"""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from mini_agent.evolution import tick_thread_guard as guard
from mini_agent.evolution.scheduler_heartbeat import SchedulerHeartbeat
from mini_agent.llm import deadline as dl
from mini_agent.llm.base import (
    LLMConfig,
    LLMResponse,
    LLMTimeoutError,
    LLMUsage,
)
from mini_agent.llm.client_pool import LLMClientPool, ProviderEntry
from mini_agent.llm.providers._base_mixin import ProviderMixin
from mini_agent.llm.retry import FixedBackoff, RetryPolicy
from mini_agent.llm.service import LLMHelper, ask_background
from mini_agent.orchestrator import concurrency as conc


def _resp(text: str = "ok") -> LLMResponse:
    return LLMResponse(text=text, tool_calls=[], usage=LLMUsage(), stop_reason="end_turn")


# ── A. tick_thread_guard ──────────────────────────────────────────────────────

class TestTickThreadGuard:
    def test_conftest_enables_strict_by_default(self):
        assert guard.is_strict() is True

    def test_marker_set_nested_and_cleared(self):
        assert guard.in_tick_thread() is False
        with guard.tick_thread_scope():
            assert guard.in_tick_thread() is True
            with guard.tick_thread_scope():
                assert guard.in_tick_thread() is True
            assert guard.in_tick_thread() is True
        assert guard.in_tick_thread() is False

    def test_marker_cleared_after_exception(self):
        with pytest.raises(ValueError):
            with guard.tick_thread_scope():
                raise ValueError("boom")
        assert guard.in_tick_thread() is False

    def test_outside_tick_is_noop(self):
        assert guard.note_llm_call_in_tick_thread("x") is False
        assert guard.tick_thread_llm_calls() == 0

    def test_inside_tick_non_strict_counts_and_returns_true(self):
        guard.set_strict(False)
        with guard.tick_thread_scope():
            assert guard.note_llm_call_in_tick_thread("chat:foo") is True
            assert guard.note_llm_call_in_tick_thread("chat:foo") is True
        st = guard.stats()
        assert st["tick_thread_llm_calls"] == 2
        assert st["last_call_label"] == "chat:foo"
        assert "tick_thread_scope" in st["last_call_stack"] or st["last_call_stack"]

    def test_strict_raises_but_still_counts(self):
        guard.set_strict(True)
        with guard.tick_thread_scope():
            with pytest.raises(guard.TickThreadBlockingError):
                guard.note_llm_call_in_tick_thread("chat:foo")
        assert guard.tick_thread_llm_calls() == 1

    def test_worker_thread_is_not_flagged(self):
        """派发到别的线程里的调用不是 tick 线程——这正是“派发即合规”的判定依据。"""
        guard.set_strict(True)
        result = {}

        def _worker():
            try:
                result["flagged"] = guard.note_llm_call_in_tick_thread("chat:worker")
            except Exception as exc:  # pragma: no cover
                result["exc"] = exc

        with guard.tick_thread_scope():
            t = threading.Thread(target=_worker)
            t.start()
            t.join(5)
        assert result.get("flagged") is False
        assert "exc" not in result
        assert guard.tick_thread_llm_calls() == 0

    def test_exempt_scope_suspends_marker(self):
        with guard.tick_thread_scope():
            with guard.tick_thread_exempt():
                assert guard.in_tick_thread() is False
                assert guard.note_llm_call_in_tick_thread("x") is False
            assert guard.in_tick_thread() is True

    def test_configure_from_config(self):
        guard.configure_from_config(SimpleNamespace(scheduler=SimpleNamespace(tick_thread_llm_strict=False)))
        assert guard.is_strict() is False
        guard.configure_from_config(SimpleNamespace(scheduler=SimpleNamespace(tick_thread_llm_strict=True)))
        assert guard.is_strict() is True
        # 没有 scheduler 块时不改动
        guard.configure_from_config(SimpleNamespace())
        assert guard.is_strict() is True


# ── B. SchedulerHeartbeat ─────────────────────────────────────────────────────

class _FakeLoop:
    def __init__(self, tick_fn):
        self._tick_fn = tick_fn
        self.tick_calls = 0

    def should_tick(self):
        return True

    def tick(self):
        self.tick_calls += 1
        self._tick_fn()


def _make_heartbeat(tmp_path, tick_fn):
    loop = _FakeLoop(tick_fn)
    hb = SchedulerHeartbeat(
        loop, threading.Lock(), interval_seconds=0.5,
        tick_interval_seconds=60.0,
        paths=SimpleNamespace(project_root=tmp_path),
    )
    return hb, loop


class TestHeartbeatIntegration:
    def test_tick_runs_inside_marker_and_clears_after(self, tmp_path):
        seen = {}
        hb, _ = _make_heartbeat(tmp_path, lambda: seen.setdefault("in_tick", guard.in_tick_thread()))
        hb._maybe_tick()
        assert seen["in_tick"] is True
        assert guard.in_tick_thread() is False

    def test_llm_call_in_tick_is_counted_and_written_to_status_file(self, tmp_path):
        guard.set_strict(False)
        hb, _ = _make_heartbeat(tmp_path, lambda: guard.note_llm_call_in_tick_thread("chat:test/model"))
        hb._maybe_tick()
        hb._write_status_file()
        payload = json.loads((tmp_path / ".agent" / "scheduler_heartbeat_status.json").read_text("utf-8"))
        assert payload["tick_thread_llm_calls"] == 1
        assert payload["tick_thread_llm_last_label"] == "chat:test/model"
        assert payload["tick_thread_llm_last_at"] > 0

    def test_status_file_reports_zero_when_clean(self, tmp_path):
        hb, _ = _make_heartbeat(tmp_path, lambda: None)
        hb._maybe_tick()
        hb._write_status_file()
        payload = json.loads((tmp_path / ".agent" / "scheduler_heartbeat_status.json").read_text("utf-8"))
        assert payload["tick_thread_llm_calls"] == 0

    def test_strict_error_inside_tick_does_not_kill_heartbeat(self, tmp_path):
        guard.set_strict(True)
        hb, loop = _make_heartbeat(tmp_path, lambda: guard.note_llm_call_in_tick_thread("chat:x"))
        hb._maybe_tick()   # 不应抛出
        hb._maybe_tick()
        assert loop.tick_calls == 2
        assert hb.last_tick_finished_at > 0
        assert guard.in_tick_thread() is False


# ── C. ProviderMixin ──────────────────────────────────────────────────────────

class _StubProvider(ProviderMixin):
    """最小 provider：只实现 _do_chat，用来走完整的 _traced_chat 链路。"""

    def __init__(self, timeout: int = 120, impl=None):
        self.config = LLMConfig(provider="stub", model="stub-model", timeout=timeout)
        self.impl_calls = 0
        self._impl = impl

    def chat(self, messages, system, tools):
        return self._traced_chat(self._do_chat, messages, system, tools)

    def _do_chat(self, messages, system, tools):
        self.impl_calls += 1
        if self._impl is not None:
            return self._impl()
        return _resp("hello")


class TestProviderMixin:
    def test_chat_in_tick_thread_strict_raises_before_request(self):
        p = _StubProvider()
        guard.set_strict(True)
        with guard.tick_thread_scope():
            with pytest.raises(guard.TickThreadBlockingError):
                p.chat([{"role": "user", "content": "hi"}], "", [])
        assert p.impl_calls == 0
        assert guard.tick_thread_llm_calls() == 1

    def test_chat_in_tick_thread_non_strict_warns_and_proceeds(self):
        p = _StubProvider()
        guard.set_strict(False)
        with guard.tick_thread_scope():
            out = p.chat([{"role": "user", "content": "hi"}], "", [])
        assert out.text == "hello"
        assert p.impl_calls == 1
        assert guard.tick_thread_llm_calls() == 1

    def test_chat_outside_tick_is_untouched(self):
        p = _StubProvider()
        out = p.chat([{"role": "user", "content": "hi"}], "", [])
        assert out.text == "hello"
        assert guard.tick_thread_llm_calls() == 0

    def test_request_timeout_unchanged_without_scope(self):
        p = _StubProvider(timeout=120)
        assert p._request_timeout() == 120

    def test_request_timeout_clamped_by_scope(self):
        p = _StubProvider(timeout=120)
        with dl.deadline_scope(request_timeout=5):
            assert p._request_timeout() == 5
        with dl.deadline_scope(total_seconds=3):
            assert 1.0 <= p._request_timeout() <= 3.0
        # 配置本身更小时不被放宽
        p2 = _StubProvider(timeout=2)
        with dl.deadline_scope(request_timeout=30):
            assert p2._request_timeout() == 2

    def test_full_slot_queue_is_bounded_by_deadline(self, monkeypatch):
        """8 个并发槽位全被挂死请求占满时，带 deadline 的后台调用不再无限排队。"""
        sem = conc.CountingSemaphore(limit=1, kind="llm")
        monkeypatch.setattr(conc, "_llm_sem", sem)
        assert sem.try_acquire()
        try:
            p = _StubProvider()
            t0 = time.monotonic()
            with dl.deadline_scope(total_seconds=0.4):
                with pytest.raises(LLMTimeoutError):
                    p.chat([{"role": "user", "content": "hi"}], "", [])
            assert time.monotonic() - t0 < 3.0
            assert p.impl_calls == 0
            assert sem.waiting_count == 0   # 超时后排队记录已清理
        finally:
            sem.release()

    def test_expired_deadline_blocks_new_request(self):
        p = _StubProvider()
        with dl.deadline_scope(total_seconds=0.01):
            time.sleep(0.05)
            with pytest.raises(LLMTimeoutError):
                p.chat([{"role": "user", "content": "hi"}], "", [])
        assert p.impl_calls == 0

    def test_openai_request_kwargs_timeout_only_inside_scope(self):
        from mini_agent.llm.providers.openai import OpenAIProvider
        p = object.__new__(OpenAIProvider)
        p.config = LLMConfig(provider="openai", model="m", timeout=120)
        base = p._build_kwargs([{"role": "user", "content": "x"}], [], stream=False)
        assert "timeout" not in base      # 旧行为：客户端级超时，不传每请求 timeout
        with dl.deadline_scope(request_timeout=7):
            scoped = p._build_kwargs([{"role": "user", "content": "x"}], [], stream=False)
        assert scoped["timeout"] == 7


class TestConcurrencyBoundedWaits:
    def test_semaphore_acquire_timeout(self):
        sem = conc.CountingSemaphore(limit=1)
        assert sem.try_acquire()
        t0 = time.monotonic()
        with pytest.raises(conc.SlotWaitTimeout):
            with sem.acquire(label="x", timeout=0.2):
                pass
        assert 0.15 <= time.monotonic() - t0 < 2.0
        assert sem.waiting_count == 0
        assert sem.active_count == 1       # 超时的等待者没有偷偷拿到 slot
        sem.release()

    def test_semaphore_acquire_without_timeout_still_blocks_until_release(self):
        sem = conc.CountingSemaphore(limit=1)
        assert sem.try_acquire()
        got = []

        def _w():
            with sem.acquire(label="w"):
                got.append(1)

        t = threading.Thread(target=_w)
        t.start()
        time.sleep(0.2)
        assert not got
        sem.release()
        t.join(5)
        assert got == [1]

    def test_rate_limiter_max_wait(self):
        rl = conc.RateLimiter(max_rpm=1) if hasattr(conc, "RateLimiter") else None
        if rl is None:
            pytest.skip("RateLimiter 类名不同")
        rl.acquire()
        with pytest.raises(conc.SlotWaitTimeout):
            rl.acquire(max_wait=0.2)


# ── D. llm/deadline ───────────────────────────────────────────────────────────

class TestDeadlineModule:
    def test_no_scope_is_transparent(self):
        assert dl.remaining() is None
        assert dl.has_deadline() is False
        assert dl.effective_timeout(120) == 120
        assert dl.clamp_wait(10) == 10
        dl.check_deadline("x")   # 不抛

    def test_scope_sets_and_restores(self):
        with dl.deadline_scope(total_seconds=5, request_timeout=2):
            assert 0 < dl.remaining() <= 5
            assert dl.effective_timeout(120) == 2
        assert dl.remaining() is None

    def test_zero_or_none_is_noop(self):
        with dl.deadline_scope(total_seconds=0, request_timeout=None):
            assert dl.has_deadline() is False

    def test_nested_takes_tighter(self):
        with dl.deadline_scope(total_seconds=100, request_timeout=50):
            outer = dl.remaining()
            with dl.deadline_scope(total_seconds=1, request_timeout=10):
                assert dl.remaining() <= 1
                assert dl.effective_timeout(120) <= 1.0 + 1e-6 or dl.effective_timeout(120) == 1.0
            # 内层不能放宽外层
            with dl.deadline_scope(total_seconds=1000, request_timeout=500):
                assert dl.remaining() <= outer
                assert dl.effective_timeout(120) == 50
            assert dl.remaining() <= outer

    def test_scope_is_thread_local(self):
        seen = {}
        with dl.deadline_scope(total_seconds=5):
            t = threading.Thread(target=lambda: seen.setdefault("r", dl.remaining()))
            t.start()
            t.join(5)
        assert seen["r"] is None

    def test_check_deadline_raises_when_expired(self):
        with dl.deadline_scope(total_seconds=0.01):
            time.sleep(0.05)
            assert dl.expired()
            with pytest.raises(LLMTimeoutError):
                dl.check_deadline("t")
            assert dl.clamp_wait(10) == 0.0


# ── E. RetryPolicy.deadline_seconds ───────────────────────────────────────────

def _policy(**kw) -> RetryPolicy:
    base = dict(max_retries=5, backoff=FixedBackoff(10.0), retry_on_exception=True, network_aware=False)
    base.update(kw)
    return RetryPolicy(**base)


class TestRetryDeadline:
    def test_exception_path_stops_retrying_when_backoff_exceeds_budget(self):
        calls = []

        def _boom():
            calls.append(1)
            raise RuntimeError("upstream down")

        t0 = time.monotonic()
        with pytest.raises(LLMTimeoutError) as ei:
            _policy(deadline_seconds=1.0).call_with_retry(_boom)
        assert time.monotonic() - t0 < 3.0          # 没有睡 10s 退避
        assert len(calls) == 1
        assert isinstance(ei.value.__cause__, RuntimeError)

    def test_quality_condition_path_returns_last_response(self):
        calls = []

        def _empty():
            calls.append(1)
            return LLMResponse(text="", tool_calls=[], usage=LLMUsage(), stop_reason="end_turn")

        t0 = time.monotonic()
        out = _policy(deadline_seconds=1.0, retry_on_exception=False).call_with_retry(_empty)
        assert out.text == ""
        assert time.monotonic() - t0 < 3.0
        assert len(calls) == 1

    def test_each_attempt_sees_clamped_request_timeout(self):
        seen = []

        def _probe():
            seen.append(dl.effective_timeout(120))
            return _resp()

        _policy(deadline_seconds=5.0).call_with_retry(_probe)
        assert seen and seen[0] <= 5.0

    def test_deadline_expiring_mid_flight_raises_timeout_not_retry(self):
        calls = []

        def _slow_fail():
            calls.append(1)
            time.sleep(0.3)
            raise RuntimeError("slow")

        with pytest.raises(LLMTimeoutError):
            _policy(deadline_seconds=0.2, backoff=FixedBackoff(0.0), max_retries=3).call_with_retry(_slow_fail)
        assert len(calls) == 1

    def test_without_deadline_behaves_as_before(self):
        calls = []

        def _flaky():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("flaky")
            return _resp("done")

        out = _policy(backoff=FixedBackoff(0.0), max_retries=5).call_with_retry(_flaky)
        assert out.text == "done"
        assert len(calls) == 3

    def test_without_deadline_exhausted_retries_still_reraise_original(self):
        def _boom():
            raise RuntimeError("x")

        with pytest.raises(RuntimeError):
            _policy(backoff=FixedBackoff(0.0), max_retries=2).call_with_retry(_boom)

    def test_scope_is_released_after_call(self):
        _policy(deadline_seconds=5.0).call_with_retry(lambda: _resp())
        assert dl.remaining() is None

    def test_enough_budget_still_allows_retry_success(self):
        calls = []

        def _flaky():
            calls.append(1)
            if len(calls) < 2:
                raise RuntimeError("once")
            return _resp("second")

        out = _policy(deadline_seconds=10.0, backoff=FixedBackoff(0.05)).call_with_retry(_flaky)
        assert out.text == "second"


# ── F. LLMHelper ──────────────────────────────────────────────────────────────

class _FakeClient:
    def __init__(self, behaviour):
        self._behaviour = behaviour
        self.seen = []

    def chat(self, messages, system, tools):
        self.seen.append((dl.remaining(), dl.effective_timeout(120)))
        return self._behaviour()


class _FakePool:
    """只提供 LLMHelper 需要的 call_with_pool / current_entry。"""
    current_entry = None

    def __init__(self, client):
        self.client = client

    def call_with_pool(self, call_fn, retry_policy, **_kw):
        return retry_policy.call_with_retry(lambda: call_fn(self.client))


class TestLLMHelper:
    def test_default_call_has_no_scope(self):
        client = _FakeClient(lambda: _resp("a"))
        helper = LLMHelper(_FakePool(client), SimpleNamespace())
        assert helper.ask("hi") == "a"
        assert client.seen[0][0] is None

    def test_deadline_and_timeout_scope_active_during_call(self):
        client = _FakeClient(lambda: _resp("a"))
        helper = LLMHelper(_FakePool(client), SimpleNamespace())
        assert helper.ask("hi", timeout=7, deadline=20) == "a"
        remaining, eff = client.seen[0]
        assert remaining is not None and remaining <= 20
        assert eff == 7
        assert dl.remaining() is None            # 作用域已释放

    def test_scope_released_on_exception(self):
        def _boom():
            raise RuntimeError("x")

        helper = LLMHelper(_FakePool(_FakeClient(_boom)), SimpleNamespace())
        with pytest.raises(Exception):
            helper.ask("hi", deadline=5, max_retries=0)
        assert dl.remaining() is None

    def test_hanging_upstream_bounded_by_deadline(self):
        """上游每次都失败且退避很长：ask(deadline=…) 总耗时受限，不会睡完 3×10s。"""
        def _boom():
            raise RuntimeError("down")

        helper = LLMHelper(_FakePool(_FakeClient(_boom)), SimpleNamespace())
        policy = _policy(deadline_seconds=0)  # 不靠 policy 自带预算，验证 helper 的 deadline 作用域
        t0 = time.monotonic()
        with pytest.raises(LLMTimeoutError):
            helper.ask("hi", deadline=1.0, retry_policy=policy)
        assert time.monotonic() - t0 < 3.0

    def test_background_kwargs_from_config(self):
        cfg = SimpleNamespace(retry=SimpleNamespace(background_call_timeout_seconds=12.0, background_call_max_retries=2))
        kw = LLMHelper(_FakePool(None), cfg).background_kwargs()
        assert kw == {"timeout": 12.0, "deadline": 36.0, "max_retries": 2}

    def test_background_kwargs_defaults_when_config_missing(self):
        kw = LLMHelper(_FakePool(None), SimpleNamespace()).background_kwargs()
        assert kw == {"timeout": 30.0, "deadline": 60.0, "max_retries": 1}

    def test_background_budget_can_be_disabled_with_zero_timeout(self):
        cfg = SimpleNamespace(retry=SimpleNamespace(background_call_timeout_seconds=0, background_call_max_retries=3))
        helper = LLMHelper(_FakePool(_FakeClient(lambda: _resp("x"))), cfg)
        assert helper.background_kwargs() == {}
        # 关闭后 ask_background 与普通 ask() 一致：不建 deadline 作用域
        client = _FakeClient(lambda: _resp("x"))
        helper = LLMHelper(_FakePool(client), cfg)
        assert ask_background(helper, "hi") == "x"
        assert client.seen[0][0] is None

    def test_ask_background_uses_helper_budget(self):
        client = _FakeClient(lambda: _resp("bg"))
        helper = LLMHelper(_FakePool(client), SimpleNamespace())
        assert ask_background(helper, "hi") == "bg"
        remaining, eff = client.seen[0]
        assert remaining is not None and remaining <= 60.0
        assert eff == 30.0

    def test_ask_background_explicit_kwargs_win(self):
        client = _FakeClient(lambda: _resp("bg"))
        helper = LLMHelper(_FakePool(client), SimpleNamespace())
        ask_background(helper, "hi", timeout=3, deadline=9)
        remaining, eff = client.seen[0]
        assert remaining <= 9
        assert eff == 3

    def test_ask_background_duck_typed_helper_passthrough(self):
        class _Duck:
            def __init__(self):
                self.calls = []

            def ask(self, prompt, **kw):
                self.calls.append((prompt, kw))
                return "duck"

        d = _Duck()
        assert ask_background(d, "p") == "duck"
        assert d.calls == [("p", {})]

        class _OnlyPrompt:
            def ask(self, prompt):
                return "only"

        assert ask_background(_OnlyPrompt(), "p") == "only"


# ── G. LLMClientPool ──────────────────────────────────────────────────────────

class _FailingClient:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, system, tools):
        self.calls += 1
        raise LLMTimeoutError("upstream hang")


class TestPoolDeadline:
    def _pool(self, round_wait: float, max_rounds: int = 3):
        c1, c2 = _FailingClient(), _FailingClient()
        entries = [
            ProviderEntry(config=LLMConfig(provider="a", model="m1"), client=c1),
            ProviderEntry(config=LLMConfig(provider="b", model="m2"), client=c2),
        ]
        return LLMClientPool(entries, max_rounds=max_rounds, round_wait=round_wait), c1, c2

    def test_deadline_stops_fallback_and_skips_round_wait(self):
        pool, c1, c2 = self._pool(round_wait=30.0)
        helper = LLMHelper(pool, SimpleNamespace())
        no_retry = RetryPolicy(max_retries=0, conditions=[], retry_on_exception=False, network_aware=False)
        t0 = time.monotonic()
        with dl.deadline_scope(total_seconds=0.01):
            time.sleep(0.03)
            with pytest.raises(LLMTimeoutError):
                helper.chat([{"role": "user", "content": "x"}], retry_policy=no_retry)
        assert time.monotonic() - t0 < 3.0
        assert c1.calls + c2.calls <= 1

    def test_without_deadline_fallback_still_walks_chain(self):
        pool, c1, c2 = self._pool(round_wait=0.0, max_rounds=1)
        no_retry = RetryPolicy(max_retries=0, conditions=[], retry_on_exception=False, network_aware=False)
        with pytest.raises(LLMTimeoutError):
            pool.call_with_pool(lambda c: c.chat([], "", []), no_retry)
        assert c1.calls == 1 and c2.calls == 1     # 旧行为：链上每个 entry 各试一次

    def test_round_wait_clamped_to_remaining_budget(self):
        pool, c1, c2 = self._pool(round_wait=30.0, max_rounds=2)
        no_retry = RetryPolicy(max_retries=0, conditions=[], retry_on_exception=False, network_aware=False)
        t0 = time.monotonic()
        with dl.deadline_scope(total_seconds=0.5):
            with pytest.raises(LLMTimeoutError):
                pool.call_with_pool(lambda c: c.chat([], "", []), no_retry)
        assert time.monotonic() - t0 < 5.0         # 没有睡 30s 的 round_wait


# ── H. 调用点 ─────────────────────────────────────────────────────────────────

class TestCallSites:
    def test_declare_paths_uses_background_budget(self):
        from mini_agent.evolution.objective_executor import _default_declare_paths

        class _Helper:
            def __init__(self):
                self.kw = None

            def background_kwargs(self):
                return {"timeout": 11.0, "deadline": 22.0, "max_retries": 1}

            def ask(self, prompt, **kw):
                self.kw = kw
                return "src/a.py\nsrc/b.py"

        h = _Helper()
        assert _default_declare_paths(h, "改 a 和 b") == ["src/a.py", "src/b.py"]
        assert h.kw == {"timeout": 11.0, "deadline": 22.0, "max_retries": 1}

    def test_declare_paths_still_works_with_plain_ask_only_helper(self):
        from mini_agent.evolution.objective_executor import _default_declare_paths

        class _Plain:
            def ask(self, prompt):
                return "无"

        assert _default_declare_paths(_Plain(), "纯查询") == []

    def test_declare_paths_degrades_to_empty_on_timeout(self):
        from mini_agent.evolution.objective_executor import _default_declare_paths

        class _Hang:
            def ask(self, prompt, **kw):
                raise LLMTimeoutError("deadline")

        assert _default_declare_paths(_Hang(), "x") == []   # 调用方据此退化为哨兵路径

    def test_config_defaults(self):
        from mini_agent.config.models import RetryConfig, SchedulerConfig
        assert RetryConfig().background_call_timeout_seconds == 30.0
        assert RetryConfig().background_call_max_retries == 1
        assert SchedulerConfig().tick_thread_llm_strict is False


class TestConfigLoading:
    def test_nested_blocks_reach_app_config(self, tmp_path):
        from mini_agent.config.loader import load_config
        (tmp_path / "agent_config.json").write_text(json.dumps({
            "retry": {"background_call_timeout_seconds": 12, "background_call_max_retries": 2},
            "scheduler": {"tick_thread_llm_strict": True},
        }), encoding="utf-8")
        cfg = load_config(project_root=tmp_path)
        assert cfg.retry.background_call_timeout_seconds == 12
        assert cfg.retry.background_call_max_retries == 2
        assert cfg.scheduler.tick_thread_llm_strict is True
