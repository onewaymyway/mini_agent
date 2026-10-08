"""心跳卡死时：区分 waiting_lock / in_tick，并落全线程栈快照（跨平台，不依赖 SIGUSR1）。"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

from mini_agent.evolution.scheduler_heartbeat import SchedulerHeartbeat


class _Loop:
    def __init__(self, tick):
        self._tick = tick

    def should_tick(self):
        return True

    def tick(self):
        self._tick()


def _wait(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _hb(tmp_path, loop, lock):
    return SchedulerHeartbeat(
        loop, lock, interval_seconds=0.05, tick_interval_seconds=0.05,
        paths=SimpleNamespace(project_root=tmp_path), stuck_threshold_multiplier=1.0,
    )


def test_stuck_inside_tick_dumps_stacks_with_phase(tmp_path):
    release = threading.Event()
    hb = _hb(tmp_path, _Loop(lambda: release.wait(10)), threading.Lock())
    hb._alert_stuck = lambda *_a, **_k: None  # 不发通知
    hb.start()
    try:
        assert _wait(lambda: hb.suspected_stuck)
        dumps = _wait(lambda: list((Path(tmp_path) / ".agent" / "scheduler_hang_stacks").glob("stuck_*.txt")))
        assert dumps
        text = next(iter((Path(tmp_path) / ".agent" / "scheduler_hang_stacks").glob("stuck_*.txt"))).read_text("utf-8")
        assert "阶段: in_tick" in text and "scheduler-heartbeat" in text and "wait" in text
        assert hb._last_stack_dump_path
    finally:
        release.set()
        hb.stop()
        hb.join(timeout=2)


def test_stuck_waiting_for_shared_lock_is_reported_as_waiting_lock(tmp_path):
    lock = threading.Lock()
    lock.acquire()  # 别的线程长期占着 sched_lock
    hb = _hb(tmp_path, _Loop(lambda: None), lock)
    hb._alert_stuck = lambda *_a, **_k: None
    hb.start()
    try:
        assert _wait(lambda: hb.suspected_stuck)
        d = Path(tmp_path) / ".agent" / "scheduler_hang_stacks"
        assert _wait(lambda: list(d.glob("stuck_*.txt")))
        assert "阶段: waiting_lock" in next(iter(d.glob("stuck_*.txt"))).read_text("utf-8")
    finally:
        lock.release()
        hb.stop()
        hb.join(timeout=2)


def test_recovery_resets_incident_and_dump_count_is_capped(tmp_path):
    hb = _hb(tmp_path, _Loop(lambda: None), threading.Lock())
    hb._stack_dump_count = hb.STACK_DUMP_MAX_PER_INCIDENT
    hb._maybe_dump_stacks(time.time() - 100)
    assert not (Path(tmp_path) / ".agent" / "scheduler_hang_stacks").exists()
