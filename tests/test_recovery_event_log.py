"""
tests/test_recovery_event_log.py

覆盖 next_doc/kanban_execution_visibility_and_control_plan.md 阶段 B：
evolution/recovery_event_log.py 的环形缓冲行为 ——

  - record_recovery_event() 追加的记录可以被 recent_recovery_events() 读到
  - 按时间倒序（最新的在最前面）
  - 超过容量上限（50 条）后自动丢弃最老的记录
"""

from __future__ import annotations

from mini_agent.evolution import recovery_event_log as log_mod


def setup_function(_):
    log_mod._reset_for_tests()


def test_record_and_read_single_event():
    log_mod.record_recovery_event("cron_job", "user:job1", "超时未返回", now=100.0)
    events = log_mod.recent_recovery_events()
    assert len(events) == 1
    assert events[0] == {"time": 100.0, "kind": "cron_job", "id": "user:job1", "detail": "超时未返回"}


def test_events_ordered_newest_first():
    log_mod.record_recovery_event("cron_job", "job1", "a", now=1.0)
    log_mod.record_recovery_event("objective_step", "exec1:0", "b", now=2.0)
    log_mod.record_recovery_event("isolated_pool", "", "c", now=3.0)
    events = log_mod.recent_recovery_events()
    assert [e["id"] for e in events] == ["", "exec1:0", "job1"]


def test_ring_buffer_caps_at_50_entries():
    for i in range(60):
        log_mod.record_recovery_event("cron_job", f"job{i}", "x", now=float(i))
    events = log_mod.recent_recovery_events()
    assert len(events) == 50
    # 最新的 50 条应该是 job59 .. job10（倒序）
    assert events[0]["id"] == "job59"
    assert events[-1]["id"] == "job10"
