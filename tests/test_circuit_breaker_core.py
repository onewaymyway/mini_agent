"""
tests/test_circuit_breaker_core.py — CircuitBreakerCore 单元测试
[daemon_stability_and_ux_improvement_plan.md 第 1 项 / P2-1]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mini_agent.evolution.circuit_breaker_core import (
    CircuitBreakerCore, classify_error_type,
)


class TestReportAttemptFailure:
    def test_consecutive_same_error_type_escalates_at_threshold(self):
        b = CircuitBreakerCore()
        assert b.report_attempt_failure("s1", "TimeoutError", threshold=2) is False
        assert b.report_attempt_failure("s1", "TimeoutError", threshold=2) is True

    def test_different_error_type_resets_count(self):
        b = CircuitBreakerCore()
        assert b.report_attempt_failure("s1", "TimeoutError", threshold=2) is False
        # 换了一种错误类型，连续计数重新从 1 开始，不应立即触发
        assert b.report_attempt_failure("s1", "ValueError", threshold=2) is False

    def test_scopes_are_independent(self):
        b = CircuitBreakerCore()
        b.report_attempt_failure("s1", "TimeoutError", threshold=2)
        # s2 是全新 scope，不受 s1 已有计数影响
        assert b.report_attempt_failure("s2", "TimeoutError", threshold=2) is False

    def test_reset_scope_failures_clears_count(self):
        b = CircuitBreakerCore()
        b.report_attempt_failure("s1", "TimeoutError", threshold=3)
        b.reset_scope_failures("s1")
        assert b.report_attempt_failure("s1", "TimeoutError", threshold=2) is False
        assert b.report_attempt_failure("s1", "TimeoutError", threshold=2) is True


class TestReportBreadthFailure:
    def test_not_enabled_without_threshold(self):
        b = CircuitBreakerCore(distinct_scope_threshold=None)
        assert b.report_breadth_failure("s1", "TimeoutError") is False
        assert b.tripped is False

    def test_trips_when_distinct_scopes_reach_threshold(self):
        b = CircuitBreakerCore(distinct_scope_threshold=2)
        assert b.report_breadth_failure("s1", "TimeoutError") is False
        assert b.tripped is False
        assert b.report_breadth_failure("s2", "TimeoutError") is True
        assert b.tripped is True
        assert "TimeoutError" in b.trip_reason

    def test_same_scope_repeated_does_not_count_twice(self):
        b = CircuitBreakerCore(distinct_scope_threshold=2)
        b.report_breadth_failure("s1", "TimeoutError")
        # 同一个 scope 再失败一次，不增加"不同 scope"的计数
        assert b.report_breadth_failure("s1", "TimeoutError") is False
        assert b.tripped is False

    def test_different_error_types_tracked_separately(self):
        b = CircuitBreakerCore(distinct_scope_threshold=2)
        b.report_breadth_failure("s1", "TimeoutError")
        # 不同 error_type 各自累计，互不影响
        assert b.report_breadth_failure("s2", "ValueError") is False
        assert b.tripped is False

    def test_only_trips_once(self):
        b = CircuitBreakerCore(distinct_scope_threshold=2)
        b.report_breadth_failure("s1", "TimeoutError")
        assert b.report_breadth_failure("s2", "TimeoutError") is True
        # 已经触发过，后续调用不再返回 True（即使又出现新的不同 scope）
        assert b.report_breadth_failure("s3", "TimeoutError") is False

    def test_on_trip_callback_invoked_with_error_type_and_scopes(self):
        calls = []
        b = CircuitBreakerCore(
            distinct_scope_threshold=2,
            on_trip=lambda et, ids: calls.append((et, ids)),
        )
        b.report_breadth_failure("s1", "TimeoutError")
        b.report_breadth_failure("s2", "TimeoutError")
        assert calls == [("TimeoutError", ["s1", "s2"])]

    def test_on_trip_exception_does_not_propagate(self):
        def boom(et, ids):
            raise RuntimeError("boom")
        b = CircuitBreakerCore(distinct_scope_threshold=1, on_trip=boom)
        # 不应抛出，只是回调内部异常被吞掉
        assert b.report_breadth_failure("s1", "TimeoutError") is True

    def test_log_fn_invoked_for_both_kinds_of_events(self):
        events = []
        b = CircuitBreakerCore(
            distinct_scope_threshold=1,
            log_fn=lambda et, data: events.append(et),
        )
        b.report_attempt_failure("s1", "TimeoutError", threshold=1)
        b.report_breadth_failure("s1", "TimeoutError")
        assert "consecutive_failure_escalated" in events
        assert "circuit_breaker_tripped" in events

    def test_reset_trip_allows_re_tripping(self):
        b = CircuitBreakerCore(distinct_scope_threshold=2)
        b.report_breadth_failure("s1", "TimeoutError")
        b.report_breadth_failure("s2", "TimeoutError")
        assert b.tripped is True
        b.reset_trip()
        assert b.tripped is False
        assert b.trip_reason is None
        # reset 后 distinct_scopes 清空，需要重新累积到阈值才会再次触发
        assert b.report_breadth_failure("s3", "TimeoutError") is False
        assert b.report_breadth_failure("s4", "TimeoutError") is True


class TestClassifyErrorType:
    def test_timeout_keywords(self):
        assert classify_error_type("步骤超时（超过 600s 未收到执行结果）") == "timeout"
        assert classify_error_type("Connection timed out after 30s") == "timeout"

    def test_rate_limit_keywords(self):
        assert classify_error_type("HTTP 429 Too Many Requests") == "rate_limit"

    def test_auth_keywords(self):
        assert classify_error_type("401 Unauthorized") == "auth"

    def test_connection_keywords(self):
        assert classify_error_type("网络连接失败") == "connection"

    def test_tool_protocol_keywords(self):
        assert classify_error_type("结果健全性校验未通过（协议残留）") == "tool_protocol"

    def test_stuck_keywords(self):
        assert classify_error_type("连续多步结果高度相似，判定原地打转") == "stuck"

    def test_unmatched_falls_back_to_other(self):
        assert classify_error_type("一些完全不相关的错误信息") == "other"

    def test_empty_message_falls_back_to_other(self):
        assert classify_error_type("") == "other"
        assert classify_error_type(None) == "other"
