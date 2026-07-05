"""
perception/observability.py — Stage 6 观察性核心模块

覆盖设计文档第 9 章（观察性）以下子项：
  6.1  时序性能追踪（Tracing）：traces.jsonl
  6.3  异常行为检测：基线推导 + anomaly_flags
  6.4  工具调用因果链：turn_id / sequence_in_turn / error_category / resolves_seq

使用方式（见 agent.py 接入点）：
  tracer = SessionTracer(session_dir, session_id)
  with tracer.span("build_system") as sp:
      sp["context_breakdown"] = {...}

  # 工具调用完成后：
  tracer.record_tool_event(turn_id, seq, tool_name, result_str, is_error,
                           error_category, resolves_seq)
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator, Optional

# ════════════════════════════════════════════════════════════════════════════════
# 6.4  error_category 分类
# ════════════════════════════════════════════════════════════════════════════════

# 异常类名 → error_category 映射（复用 lesson_rules.py 的正则模式基础）
_EXCEPTION_CATEGORY: list[tuple[re.Pattern, str]] = [
    (re.compile(r"PermissionError|PermissionDenied",           re.I), "permission"),
    (re.compile(r"FileNotFoundError|FileNotFound|NoSuchFile",   re.I), "not_found"),
    (re.compile(r"TimeoutError|Timeout|timed?\s+out",          re.I), "timeout"),
    (re.compile(r"ConnectionError|ConnectionRefused|ECONNREFUSED|NetworkError", re.I), "network"),
    (re.compile(r"SyntaxError",                                re.I), "syntax"),
    (re.compile(r"ModuleNotFoundError|ImportError",            re.I), "import"),
    (re.compile(r"JSONDecodeError|json.*decode",               re.I), "parse"),
    (re.compile(r"UnicodeDecodeError|UnicodeError|Codec",      re.I), "encoding"),
    (re.compile(r"CalledProcessError|\[exit code:\s*[1-9]",    re.I), "process"),
    (re.compile(r"KeyError|AttributeError|IndexError",         re.I), "key_access"),
    (re.compile(r"TypeError|ValueError",                       re.I), "type_value"),
    (re.compile(r"OSError|IOError",                            re.I), "io"),
    (re.compile(r"RuntimeError",                               re.I), "runtime"),
]

_PERMISSION_DENIED_RE = re.compile(
    r"\[Permission denied\]|\[DENIED\]|requires.*approval|not.*allowed",
    re.I
)


def classify_error(result_str: str) -> str:
    """
    把工具调用结果字符串映射到 error_category 枚举值。

    返回值枚举（与设计文档 11.4 节保持一致）：
      permission / not_found / timeout / network / syntax / import /
      parse / encoding / process / key_access / type_value / io / runtime / other

    调用方应先用 is_tool_error() 确认确实是错误，再调用本函数细分类别。
    """
    if not result_str:
        return "other"

    # 特判：权限拒绝（PermissionGuard 返回格式）
    if _PERMISSION_DENIED_RE.search(result_str):
        return "permission"

    for pattern, category in _EXCEPTION_CATEGORY:
        if pattern.search(result_str):
            return category

    return "other"


# ════════════════════════════════════════════════════════════════════════════════
# 6.1  traces.jsonl — session 级时序追踪
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class TraceEntry:
    """一条 trace 记录（追加到 session_dir/traces.jsonl）。"""
    session_id: str
    turn_id:    int           # run_turn 调用计数，从 1 开始
    phase:      str           # build_system / call_llm / execute_tools / inject_reminder
    started_at: float = field(default_factory=time.time)
    elapsed_ms: float = 0.0
    # 仅 call_llm 阶段有意义
    input_tokens:  int = 0
    output_tokens: int = 0
    # 仅 build_system 阶段有意义（context_breakdown）
    context_breakdown: dict = field(default_factory=dict)
    # 仅 execute_tools 阶段有意义
    tool_count:     int = 0
    tool_error_count: int = 0
    # 自由扩展字段
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "session_id":         self.session_id,
            "turn_id":            self.turn_id,
            "phase":              self.phase,
            "started_at":         self.started_at,
            "elapsed_ms":         round(self.elapsed_ms, 2),
            "input_tokens":       self.input_tokens,
            "output_tokens":      self.output_tokens,
            "context_breakdown":  self.context_breakdown,
            "tool_count":         self.tool_count,
            "tool_error_count":   self.tool_error_count,
            **self.extra,
        }


class SessionTracer:
    """
    session 级时序追踪器。

    - 追加记录到 `<session_dir>/traces.jsonl`
    - 通过 span() context manager 自动计时
    - 存储成本控制（设计文档开放问题 7 答案）：
      traces.jsonl 随 session 生命周期保留，session 结束后可归档；
      长期只保留 /diagnostics 聚合的统计摘要，不维护全局清理任务。
    """

    def __init__(self, session_dir: Path, session_id: str, enabled: bool = True) -> None:
        self._session_dir = session_dir
        self._session_id = session_id
        self._enabled = enabled
        self._traces_path: Optional[Path] = None
        if enabled:
            self._traces_path = session_dir / "traces.jsonl"

    # ── public API ─────────────────────────────────────────────────────────────

    @contextmanager
    def span(self, phase: str, turn_id: int = 0) -> Generator[dict, None, None]:
        """
        Context manager：进入时记录开始时间，退出时计算 elapsed_ms 并写入文件。

        用法：
          with tracer.span("build_system", turn_id=self.stats.turns) as sp:
              sp["context_breakdown"] = {...}
        """
        entry: dict = {
            "session_id": self._session_id,
            "turn_id":    turn_id,
            "phase":      phase,
            "started_at": time.time(),
            "elapsed_ms": 0.0,
            "input_tokens":       0,
            "output_tokens":      0,
            "context_breakdown":  {},
            "tool_count":         0,
            "tool_error_count":   0,
        }
        t0 = time.time()
        try:
            yield entry
        finally:
            entry["elapsed_ms"] = round((time.time() - t0) * 1000, 2)
            self._append(entry)

    def record_tool_event(
        self,
        *,
        turn_id:       int,
        sequence_in_turn: int,
        tool_name:     str,
        result_str:    str,
        is_error:      bool,
        error_category: Optional[str] = None,
        resolves_seq:  Optional[int] = None,
    ) -> None:
        """
        写入单次工具调用的因果链记录到 traces.jsonl。

        6.4 节新增字段：
          turn_id          — 所属 turn 计数
          sequence_in_turn — 当前 turn 内的第几次工具调用（从 1 开始）
          error_category   — 错误分类（见 classify_error），非错误时为 None
          resolves_seq     — 若本次调用修复了之前某次失败，记录那次的 sequence_in_turn
        """
        entry: dict = {
            "session_id":       self._session_id,
            "turn_id":          turn_id,
            "phase":            "tool_call",
            "started_at":       time.time(),
            "elapsed_ms":       0.0,
            "sequence_in_turn": sequence_in_turn,
            "tool_name":        tool_name,
            "is_error":         is_error,
            "error_category":   error_category,
            "resolves_seq":     resolves_seq,
        }
        self._append(entry)

    def record_internal_state(self, *, turn_id: int, state: dict) -> None:
        """
        [具身改进 B1] 写入一次 ProprioceptionModule.sense() 快照到 traces.jsonl。

        phase="internal_state"，不计入 elapsed_ms（这是一次 O(1) 快照，没有
        耗时意义）。Phase G 扫描可以读取这类记录，分析 frustration /
        cognitive_load 的历史趋势（"某类任务系统性地让 agent 感到挫败"）。
        """
        entry: dict = {
            "session_id": self._session_id,
            "turn_id":    turn_id,
            "phase":      "internal_state",
            "started_at": time.time(),
            "elapsed_ms": 0.0,
            "state":      state,
        }
        self._append(entry)

    def get_summary(self) -> dict:
        """
        读取本 session 的 traces.jsonl，返回聚合摘要（供 /diagnostics 使用）。

        返回结构：
          {
            "turn_count": int,
            "total_elapsed_ms": float,
            "avg_call_llm_ms": float,
            "avg_build_system_ms": float,
            "avg_execute_tools_ms": float,
            "total_input_tokens": int,
            "total_output_tokens": int,
            "context_breakdown_avg": dict,   # 各分项平均占比
            "tool_error_rate": float,        # 工具调用错误率
            "error_categories": dict,        # category → count
          }
        """
        if not self._traces_path or not self._traces_path.exists():
            return {}
        return _aggregate_traces(self._traces_path)

    # ── private ────────────────────────────────────────────────────────────────

    def _append(self, entry: dict) -> None:
        if not self._enabled or not self._traces_path:
            return
        try:
            self._traces_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._traces_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.observability')
            pass


def _aggregate_traces(path: Path) -> dict:
    """从 traces.jsonl 聚合统计摘要（/diagnostics 的数据来源）。"""
    call_llm_times: list[float] = []
    build_system_times: list[float] = []
    execute_tools_times: list[float] = []
    total_elapsed = 0.0
    total_input_tokens = 0
    total_output_tokens = 0
    context_breakdown_sums: dict[str, float] = {}
    context_breakdown_counts: dict[str, int] = {}
    tool_calls_total = 0
    tool_calls_error = 0
    error_categories: dict[str, int] = {}
    turn_ids: set[int] = set()

    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                phase = entry.get("phase", "")
                elapsed = entry.get("elapsed_ms", 0.0)
                total_elapsed += elapsed

                if "turn_id" in entry:
                    turn_ids.add(entry["turn_id"])

                if phase == "call_llm":
                    call_llm_times.append(elapsed)
                    total_input_tokens  += entry.get("input_tokens", 0)
                    total_output_tokens += entry.get("output_tokens", 0)

                elif phase == "build_system":
                    build_system_times.append(elapsed)
                    bd = entry.get("context_breakdown", {})
                    for k, v in bd.items():
                        context_breakdown_sums[k]   = context_breakdown_sums.get(k, 0.0) + v
                        context_breakdown_counts[k] = context_breakdown_counts.get(k, 0) + 1

                elif phase == "execute_tools":
                    execute_tools_times.append(elapsed)
                    tool_calls_total += entry.get("tool_count", 0)
                    tool_calls_error += entry.get("tool_error_count", 0)

                elif phase == "tool_call":
                    if entry.get("is_error"):
                        cat = entry.get("error_category") or "other"
                        error_categories[cat] = error_categories.get(cat, 0) + 1

    except Exception:
        return {}

    if not turn_ids and not call_llm_times and not build_system_times:
        return {}

    def _avg(lst: list[float]) -> float:
        return round(sum(lst) / len(lst), 2) if lst else 0.0

    bd_avg = {
        k: round(context_breakdown_sums[k] / context_breakdown_counts[k], 1)
        for k in context_breakdown_sums
    }

    return {
        "turn_count":            len(turn_ids),
        "total_elapsed_ms":      round(total_elapsed, 2),
        "avg_call_llm_ms":       _avg(call_llm_times),
        "avg_build_system_ms":   _avg(build_system_times),
        "avg_execute_tools_ms":  _avg(execute_tools_times),
        "total_input_tokens":    total_input_tokens,
        "total_output_tokens":   total_output_tokens,
        "context_breakdown_avg": bd_avg,
        "tool_error_rate":       round(tool_calls_error / max(tool_calls_total, 1), 4),
        "error_categories":      error_categories,
    }


# ════════════════════════════════════════════════════════════════════════════════
# 6.3  异常行为检测
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class AnomalyFlag:
    """单条异常告警。"""
    flag_type:   str     # tool_call_spike / token_spike / session_duration_spike
    value:       float   # 实测值
    baseline:    float   # 基线均值
    threshold:   float   # 触发阈值（baseline + k * std）
    session_id:  str = ""
    detected_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "flag_type":   self.flag_type,
            "value":       round(self.value, 3),
            "baseline":    round(self.baseline, 3),
            "threshold":   round(self.threshold, 3),
            "session_id":  self.session_id,
            "detected_at": self.detected_at,
        }


def detect_anomalies(
    activity_log_path: Path,
    current_session: dict,
    k_sigma: float = 3.0,
    min_samples: int = 10,
) -> list[AnomalyFlag]:
    """
    从 activity_log.jsonl（Stage 5.3 产出）推导基线，检测当前 session 是否异常。

    参数：
      activity_log_path — global activity_log.jsonl 路径
      current_session   — 当前 session 的统计（含 tool_count / tokens / duration_min）
      k_sigma           — 触发阈值倍数（默认 3σ）
      min_samples       — 小于此样本数时不计算基线（结果不稳定）

    返回：AnomalyFlag 列表，为空表示无异常。
    """
    if not activity_log_path.exists():
        return []

    tool_counts:   list[float] = []
    token_totals:  list[float] = []
    durations:     list[float] = []

    try:
        with open(activity_log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # 只读 session_metrics 记录（与主 activity_log 行区分）
                if rec.get("record_type") != "session_metrics":
                    continue
                if rec.get("tool_count") is not None:
                    tool_counts.append(float(rec["tool_count"]))
                if rec.get("total_tokens") is not None:
                    token_totals.append(float(rec["total_tokens"]))
                if rec.get("duration_min") is not None:
                    durations.append(float(rec["duration_min"]))
    except Exception:
        return []

    flags: list[AnomalyFlag] = []
    sid = current_session.get("session_id", "")

    def _check(series: list[float], value: float, flag_type: str) -> None:
        if len(series) < min_samples:
            return
        mean = sum(series) / len(series)
        variance = sum((x - mean) ** 2 for x in series) / len(series)
        std = math.sqrt(variance)
        if std < 1e-9:
            return
        threshold = mean + k_sigma * std
        if value > threshold:
            flags.append(AnomalyFlag(
                flag_type=flag_type,
                value=value,
                baseline=mean,
                threshold=threshold,
                session_id=sid,
            ))

    _check(tool_counts,  float(current_session.get("tool_count", 0)),   "tool_call_spike")
    _check(token_totals, float(current_session.get("total_tokens", 0)),  "token_spike")
    _check(durations,    float(current_session.get("duration_min", 0)),  "session_duration_spike")

    return flags
