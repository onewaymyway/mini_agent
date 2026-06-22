"""
tests/test_observability.py — Stage 6 观察性测试

覆盖：
  6.1  SessionTracer（traces.jsonl 追踪）
  6.3  detect_anomalies（异常行为检测）
  6.4  classify_error（工具调用因果链分类）
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from mini_agent.perception.observability import (
    SessionTracer,
    classify_error,
    detect_anomalies,
    AnomalyFlag,
    _aggregate_traces,
)


# ════════════════════════════════════════════════════════════════════════════════
# 6.4  classify_error
# ════════════════════════════════════════════════════════════════════════════════

class TestClassifyError:
    def test_permission_error(self):
        assert classify_error("PermissionError: access denied") == "permission"

    def test_permission_denied_guard(self):
        assert classify_error("[Permission denied] operation rejected") == "permission"

    def test_not_found(self):
        assert classify_error("FileNotFoundError: /tmp/foo not found") == "not_found"

    def test_timeout(self):
        assert classify_error("TimeoutError: connection timed out") == "timeout"
        assert classify_error("Request timed out after 30s") == "timeout"

    def test_network(self):
        assert classify_error("ConnectionError: ECONNREFUSED") == "network"

    def test_syntax(self):
        assert classify_error("SyntaxError: invalid syntax") == "syntax"

    def test_import_error(self):
        assert classify_error("ModuleNotFoundError: No module named 'foo'") == "import"
        assert classify_error("ImportError: cannot import name 'bar'") == "import"

    def test_parse(self):
        assert classify_error("JSONDecodeError: Expecting value") == "parse"

    def test_encoding(self):
        assert classify_error("UnicodeDecodeError: 'utf-8' codec") == "encoding"

    def test_process(self):
        assert classify_error("CalledProcessError: returned non-zero exit status") == "process"
        assert classify_error("[exit code: 1]") == "process"

    def test_key_access(self):
        assert classify_error("KeyError: 'foo'") == "key_access"
        assert classify_error("AttributeError: 'NoneType' object") == "key_access"

    def test_type_value(self):
        assert classify_error("TypeError: expected str") == "type_value"
        assert classify_error("ValueError: invalid literal") == "type_value"

    def test_io(self):
        assert classify_error("OSError: [Errno 28] No space left on device") == "io"

    def test_runtime(self):
        assert classify_error("RuntimeError: maximum recursion depth exceeded") == "runtime"

    def test_other(self):
        assert classify_error("something went wrong") == "other"
        assert classify_error("") == "other"

    def test_none_like(self):
        assert classify_error(None) == "other"  # type: ignore[arg-type]


# ════════════════════════════════════════════════════════════════════════════════
# 6.1  SessionTracer
# ════════════════════════════════════════════════════════════════════════════════

class TestSessionTracer:
    def test_span_writes_entry(self, tmp_path):
        tracer = SessionTracer(tmp_path, "sess-001")
        with tracer.span("call_llm", turn_id=1) as sp:
            sp["input_tokens"] = 500
            sp["output_tokens"] = 200

        lines = (tmp_path / "traces.jsonl").read_text().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["phase"] == "call_llm"
        assert entry["turn_id"] == 1
        assert entry["input_tokens"] == 500
        assert entry["elapsed_ms"] >= 0

    def test_multiple_spans(self, tmp_path):
        tracer = SessionTracer(tmp_path, "sess-002")
        with tracer.span("build_system", turn_id=1) as sp:
            sp["context_breakdown"] = {"system_base": 1000, "history": 500, "total": 1500}
        with tracer.span("call_llm", turn_id=1) as sp:
            sp["input_tokens"] = 1500
            sp["output_tokens"] = 300
        with tracer.span("execute_tools", turn_id=1) as sp:
            sp["tool_count"] = 3
            sp["tool_error_count"] = 1

        lines = (tmp_path / "traces.jsonl").read_text().splitlines()
        assert len(lines) == 3
        phases = [json.loads(l)["phase"] for l in lines]
        assert phases == ["build_system", "call_llm", "execute_tools"]

    def test_record_tool_event(self, tmp_path):
        tracer = SessionTracer(tmp_path, "sess-003")
        # 失败调用
        tracer.record_tool_event(
            turn_id=1, sequence_in_turn=1,
            tool_name="bash", result_str="FileNotFoundError: /tmp/x",
            is_error=True, error_category="not_found", resolves_seq=None,
        )
        # 成功调用（修复前一次）
        tracer.record_tool_event(
            turn_id=1, sequence_in_turn=2,
            tool_name="bash", result_str="ok",
            is_error=False, error_category=None, resolves_seq=1,
        )

        lines = (tmp_path / "traces.jsonl").read_text().splitlines()
        assert len(lines) == 2
        first  = json.loads(lines[0])
        second = json.loads(lines[1])
        assert first["is_error"] is True
        assert first["error_category"] == "not_found"
        assert first["resolves_seq"] is None
        assert second["is_error"] is False
        assert second["resolves_seq"] == 1

    def test_disabled_tracer_writes_nothing(self, tmp_path):
        tracer = SessionTracer(tmp_path, "sess-004", enabled=False)
        with tracer.span("call_llm", turn_id=1):
            pass
        assert not (tmp_path / "traces.jsonl").exists()

    def test_get_summary_empty(self, tmp_path):
        tracer = SessionTracer(tmp_path, "sess-005")
        summary = tracer.get_summary()
        assert summary == {}

    def test_get_summary_aggregates(self, tmp_path):
        tracer = SessionTracer(tmp_path, "sess-006")
        with tracer.span("build_system", turn_id=1) as sp:
            sp["context_breakdown"] = {"system_base": 800, "history": 200, "total": 1000}
        with tracer.span("call_llm", turn_id=1) as sp:
            sp["input_tokens"] = 1000
            sp["output_tokens"] = 300
        with tracer.span("execute_tools", turn_id=1) as sp:
            sp["tool_count"] = 4
            sp["tool_error_count"] = 1

        summary = tracer.get_summary()
        assert summary["turn_count"] == 1
        assert summary["total_input_tokens"] == 1000
        assert summary["total_output_tokens"] == 300
        assert summary["tool_error_rate"] == pytest.approx(1 / 4)
        assert "system_base" in summary["context_breakdown_avg"]

    def test_span_elapsed_ms_nonzero(self, tmp_path):
        """elapsed_ms 应当 > 0（实际计时）。"""
        tracer = SessionTracer(tmp_path, "sess-007")
        with tracer.span("call_llm", turn_id=1):
            time.sleep(0.005)

        entry = json.loads((tmp_path / "traces.jsonl").read_text().strip())
        assert entry["elapsed_ms"] > 0


# ════════════════════════════════════════════════════════════════════════════════
# 6.3  detect_anomalies
# ════════════════════════════════════════════════════════════════════════════════

def _write_activity_log(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


class TestDetectAnomalies:
    def test_no_anomaly_normal_session(self, tmp_path):
        al = tmp_path / "activity_log.jsonl"
        # 15 条历史记录，tool_count 稳定在 5±1
        records = [
            {"record_type": "session_metrics", "tool_count": 5 + (i % 3 - 1),
             "total_tokens": 1000, "duration_min": 10.0}
            for i in range(15)
        ]
        _write_activity_log(al, records)

        current = {"session_id": "new", "tool_count": 5, "total_tokens": 1000, "duration_min": 10.0}
        flags = detect_anomalies(al, current, k_sigma=3.0, min_samples=10)
        assert flags == []

    def test_tool_call_spike_detected(self, tmp_path):
        al = tmp_path / "activity_log.jsonl"
        # 20 条历史记录，tool_count 有一点自然方差（3±1），确保 std > 0
        records = [
            {"record_type": "session_metrics", "tool_count": 3 + (i % 3),
             "total_tokens": 1000, "duration_min": 5.0}
            for i in range(20)
        ]
        _write_activity_log(al, records)

        # 异常：tool_count=100（远超 3σ，均值约 4，std 约 0.8，阈值约 6.4）
        current = {"session_id": "spike", "tool_count": 100, "total_tokens": 1000, "duration_min": 5.0}
        flags = detect_anomalies(al, current, k_sigma=3.0, min_samples=10)
        flag_types = [f.flag_type for f in flags]
        assert "tool_call_spike" in flag_types

    def test_token_spike_detected(self, tmp_path):
        al = tmp_path / "activity_log.jsonl"
        # 加轻微方差，确保 std > 0
        records = [
            {"record_type": "session_metrics", "tool_count": 5,
             "total_tokens": 1000 + (i % 5) * 10, "duration_min": 5.0}
            for i in range(20)
        ]
        _write_activity_log(al, records)

        # 异常：50000 远超均值 1020 + 3σ
        current = {"session_id": "tok-spike", "tool_count": 5, "total_tokens": 50000, "duration_min": 5.0}
        flags = detect_anomalies(al, current, k_sigma=3.0, min_samples=10)
        flag_types = [f.flag_type for f in flags]
        assert "token_spike" in flag_types

    def test_min_samples_not_met_returns_empty(self, tmp_path):
        al = tmp_path / "activity_log.jsonl"
        # 仅 5 条记录，低于默认 min_samples=10
        records = [
            {"record_type": "session_metrics", "tool_count": 3,
             "total_tokens": 1000, "duration_min": 5.0}
            for _ in range(5)
        ]
        _write_activity_log(al, records)

        current = {"session_id": "x", "tool_count": 9999, "total_tokens": 9999999, "duration_min": 9999.0}
        flags = detect_anomalies(al, current, k_sigma=3.0, min_samples=10)
        assert flags == []

    def test_missing_activity_log_returns_empty(self, tmp_path):
        flags = detect_anomalies(
            tmp_path / "nonexistent.jsonl",
            {"session_id": "x", "tool_count": 5, "total_tokens": 1000, "duration_min": 5.0}
        )
        assert flags == []

    def test_anomaly_flag_to_dict(self):
        flag = AnomalyFlag(
            flag_type="tool_call_spike",
            value=100.0, baseline=5.0, threshold=10.0,
            session_id="sess-x",
        )
        d = flag.to_dict()
        assert d["flag_type"] == "tool_call_spike"
        assert d["value"] == 100.0
        assert d["session_id"] == "sess-x"
        assert "detected_at" in d

    def test_zero_variance_data(self, tmp_path):
        """所有历史值完全相同时，std=0，不触发阈值（避免除零）。"""
        al = tmp_path / "activity_log.jsonl"
        records = [
            {"record_type": "session_metrics", "tool_count": 5,
             "total_tokens": 1000, "duration_min": 5.0}
            for _ in range(20)
        ]
        _write_activity_log(al, records)

        # tool_count=6，比历史均值 5 高一点，但 std=0，无法触发
        current = {"session_id": "z", "tool_count": 6, "total_tokens": 1000, "duration_min": 5.0}
        flags = detect_anomalies(al, current, k_sigma=3.0, min_samples=10)
        assert flags == []


# ════════════════════════════════════════════════════════════════════════════════
# _aggregate_traces（traces.jsonl 聚合）
# ════════════════════════════════════════════════════════════════════════════════

class TestAggregateTraces:
    def _make_traces(self, tmp_path) -> Path:
        p = tmp_path / "traces.jsonl"
        entries = [
            # build_system turn 1
            {
                "session_id": "s1", "turn_id": 1, "phase": "build_system",
                "started_at": 0, "elapsed_ms": 50.0,
                "input_tokens": 0, "output_tokens": 0,
                "context_breakdown": {"system_base": 800, "history": 200, "total": 1000},
                "tool_count": 0, "tool_error_count": 0,
            },
            # call_llm turn 1
            {
                "session_id": "s1", "turn_id": 1, "phase": "call_llm",
                "started_at": 0, "elapsed_ms": 1200.0,
                "input_tokens": 1000, "output_tokens": 300,
                "context_breakdown": {}, "tool_count": 0, "tool_error_count": 0,
            },
            # execute_tools turn 1
            {
                "session_id": "s1", "turn_id": 1, "phase": "execute_tools",
                "started_at": 0, "elapsed_ms": 300.0,
                "input_tokens": 0, "output_tokens": 0,
                "context_breakdown": {}, "tool_count": 3, "tool_error_count": 1,
            },
            # tool_call error
            {
                "session_id": "s1", "turn_id": 1, "phase": "tool_call",
                "started_at": 0, "elapsed_ms": 0,
                "sequence_in_turn": 1, "tool_name": "bash",
                "is_error": True, "error_category": "not_found", "resolves_seq": None,
            },
        ]
        with open(p, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        return p

    def test_basic_aggregation(self, tmp_path):
        p = self._make_traces(tmp_path)
        s = _aggregate_traces(p)
        assert s["turn_count"] == 1
        assert s["total_input_tokens"] == 1000
        assert s["total_output_tokens"] == 300
        assert s["avg_call_llm_ms"] == pytest.approx(1200.0)
        assert s["avg_build_system_ms"] == pytest.approx(50.0)
        assert s["avg_execute_tools_ms"] == pytest.approx(300.0)
        assert abs(s["tool_error_rate"] - 1/3) < 0.01
        assert s["error_categories"]["not_found"] == 1
        assert s["context_breakdown_avg"]["system_base"] == pytest.approx(800.0)

    def test_empty_file(self, tmp_path):
        p = tmp_path / "traces.jsonl"
        p.write_text("")
        assert _aggregate_traces(p) == {}

    def test_malformed_lines_skipped(self, tmp_path):
        p = tmp_path / "traces.jsonl"
        p.write_text("not-json\n{\"phase\":\"call_llm\",\"turn_id\":1,\"elapsed_ms\":500,\"input_tokens\":100,\"output_tokens\":50}\n")
        s = _aggregate_traces(p)
        # 只有一条合法记录
        assert s["total_input_tokens"] == 100
