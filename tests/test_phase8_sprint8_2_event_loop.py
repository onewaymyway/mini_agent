"""tests/test_phase8_sprint8_2_event_loop.py

对应 `next_doc/refactor_plan/09-phase8-runtime-convergence-sprint-plan.md`
Sprint 8-2 任务表第一项："`runtime/event_loop.py` 支持常驻运行：
`while running: run_once(...)`，加上基本的异常处理和优雅退出"。

覆盖点：
  - `goal_spec_provider()` 返回真实 GoalSpec 时，每次都真的调用了
    `AgentRuntime.run_once()`（而不是重新实现一遍判定逻辑）。
  - `goal_spec_provider()` 返回 None（没有可执行的 Goal）时跳过本轮，
    不算作失败。
  - `run_once()` 内部抛异常时，只记这一轮失败，循环继续跑下一轮，
    不整体崩溃。
  - `stop_event.set()` 能让 `run_forever()` 及时优雅退出。
  - `max_iterations` 达到上限后正常停止（供测试/一次性批处理场景）。
"""

from __future__ import annotations

import threading

import pytest

from mini_agent.core import reset_event_bus
from mini_agent.core.state_manager import reset_state_manager
from mini_agent.runtime import AgentRuntime, RuntimeEventLoop
from tests.test_goal_mode import FakeAgent, _FakeCfg, _confirmed_spec


@pytest.fixture(autouse=True)
def _reset_bus_and_manager():
    reset_event_bus()
    reset_state_manager()
    yield
    reset_event_bus()
    reset_state_manager()


def _make_runtime(tmp_path, outputs=None):
    agent = FakeAgent(outputs=outputs or ["did the thing"])
    cfg = _FakeCfg(tmp_path)
    return AgentRuntime(agent=agent, cfg=cfg), agent, cfg


def test_run_forever_calls_run_once_for_each_provided_goal_spec(monkeypatch, tmp_path):
    runtime, agent, _ = _make_runtime(tmp_path, outputs=["did A", "did B"])
    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: "GOAL_STATUS: DONE",
    )

    specs = [_confirmed_spec(), _confirmed_spec()]
    call_count = {"n": 0}

    def provider():
        if call_count["n"] >= len(specs):
            return None
        spec = specs[call_count["n"]]
        call_count["n"] += 1
        return spec

    loop = RuntimeEventLoop(runtime, poll_interval_seconds=0.01)
    stats = loop.run_forever(provider, max_iterations=3, keep_results=True)

    assert stats.iterations_run == 2
    assert stats.iterations_skipped == 1
    assert stats.stop_reason == "max_iterations"
    assert len(stats.results) == 2
    assert all(r.status == "done" for r in stats.results)


def test_run_forever_skips_when_provider_returns_none(monkeypatch, tmp_path):
    runtime, agent, _ = _make_runtime(tmp_path)

    loop = RuntimeEventLoop(runtime, poll_interval_seconds=0.01)
    stats = loop.run_forever(lambda: None, max_iterations=3)

    assert stats.iterations_run == 0
    assert stats.iterations_skipped == 3
    assert stats.iterations_failed == 0
    assert agent._call_idx == 0  # 从未真正跑过一次 Goal


def test_run_forever_continues_after_run_once_exception(monkeypatch, tmp_path):
    runtime, agent, _ = _make_runtime(tmp_path, outputs=["did the thing"])

    def _raise_judge(**kw):
        raise RuntimeError("boom")

    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", _raise_judge)

    calls = {"n": 0}

    def provider():
        calls["n"] += 1
        return _confirmed_spec()

    loop = RuntimeEventLoop(runtime, poll_interval_seconds=0.01)
    stats = loop.run_forever(provider, max_iterations=2)

    # 两轮都因为 run_goal_judge 抛异常而失败，但循环本身完整跑完了两轮
    # （没有被第一次异常中断），说明"单轮失败不终止常驻循环"。
    assert calls["n"] == 2
    assert stats.iterations_failed == 2
    assert stats.iterations_run == 0
    assert stats.last_error is not None


def test_run_forever_provider_exception_does_not_crash_loop(monkeypatch, tmp_path):
    runtime, agent, _ = _make_runtime(tmp_path)

    def _bad_provider():
        raise ValueError("provider broke")

    loop = RuntimeEventLoop(runtime, poll_interval_seconds=0.01)
    stats = loop.run_forever(_bad_provider, max_iterations=2)

    assert stats.iterations_failed == 2
    assert stats.iterations_run == 0


def test_run_forever_stops_promptly_on_stop_event(monkeypatch, tmp_path):
    import time as _time

    runtime, agent, _ = _make_runtime(tmp_path)
    stop_event = threading.Event()

    # 故意把 poll_interval 设得很大：如果 stop_event 不能打断
    # `Event.wait(timeout=...)`，这个测试会挂住 30 秒才结束；用一个
    # 独立线程在 0.05s 后 set()，断言 run_forever() 在远小于
    # poll_interval 的时间内就返回，证明 stop_event 确实能打断等待。
    timer = threading.Timer(0.05, stop_event.set)
    timer.start()

    loop = RuntimeEventLoop(runtime, poll_interval_seconds=30.0)
    started = _time.time()
    stats = loop.run_forever(lambda: None, stop_event=stop_event)
    elapsed = _time.time() - started

    timer.join()
    assert stats.stop_reason == "stop_event"
    assert elapsed < 5.0  # 远小于 poll_interval=30s，证明 wait() 被及时打断
