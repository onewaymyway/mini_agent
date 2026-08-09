"""
llm/call_stats.py — 轻量 LLM 调用计数（kanban_perception_gaps_improvement_
plan.md 方向 B.2）

背景：`llm/debug_logger.py` 是一套完整的请求/响应逐条落盘机制，但默认
`enabled=False`（需要设置环境变量 `LLM_DEBUG=1`），且落盘内容是完整的
request/response body（用于调试排障，不是为统计设计的）。"这个 daemon
今天到底调用了多少次 LLM、大概花了多少 token"这个最基础的问题，此前
完全答不出来，除非临时打开调试日志再手写脚本统计。

本模块新增一个独立的、**默认开启**的轻量计数器，跟调试日志是两套东西：
只记数字（provider/model/token 数/耗时/结果分类），不含任何请求/响应
正文，天然不涉及敏感数据，可以放心默认开启。

写入策略（B.3 风险 1 的应对）：`call_with_pool()` 是 LLM 调用的主链路，
每次调用都触发一次文件写入在密集工具循环场景下有 I/O 开销；这里采用
"攒批写入"——内存里攒够 `_BATCH_SIZE` 条或超过 `_FLUSH_INTERVAL_SECONDS`
未落盘时才真正写一次文件，而不是每次调用都落盘。缓冲区按
`project_root` 字符串隔离（同进程内理论上只有一个 project_root，但测试
场景可能构造多个临时目录，按 key 隔离避免互相污染）。

降采样：复用 `evolution/growth_advisor.py::_compact_health_trend_rows()`
"文件追加 + 定期压缩旧记录"的模式，但聚合方式不同——健康度快照是
"每天一条，取最新"，调用计数需要"按天求和"（调用次数/token 数/失败数
都是要累加的，不是取某个时间点的值）。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

_BATCH_SIZE = 10
_FLUSH_INTERVAL_SECONDS = 30.0
_RAW_WINDOW_DAYS = 7          # 超过这个天数的原始记录会被压缩成每日汇总
_DEFAULT_QUERY_DAYS = 7

OUTCOME_SUCCESS = "success"
OUTCOME_ERROR = "error"
OUTCOME_KEY_SWITCH = "key_switch"
OUTCOME_CONFIG_SWITCH = "config_switch"


@dataclass
class CallStatsRecord:
    ts: float
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    outcome: str = OUTCOME_SUCCESS

    def to_dict(self) -> dict:
        return asdict(self)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    path.write_text(text + ("\n" if rows else ""), encoding="utf-8")


def _append_jsonl_batch(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ── 攒批写入缓冲区 ────────────────────────────────────────────────────────────

class _CallStatsBuffer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: list[dict] = []
        self._last_flush_at = time.time()

    def add(self, path: Path, row: dict) -> None:
        with self._lock:
            self._rows.append(row)
            due = (
                len(self._rows) >= _BATCH_SIZE
                or (time.time() - self._last_flush_at) >= _FLUSH_INTERVAL_SECONDS
            )
            if not due:
                return
            batch = self._rows
            self._rows = []
            self._last_flush_at = time.time()
        _append_jsonl_batch(path, batch)

    def flush(self, path: Path) -> None:
        """强制把缓冲区里现有的记录落盘，不等达到批量阈值——供测试和
        进程正常退出前的收尾调用（如果调用方有收尾钩子的话）。"""
        with self._lock:
            batch = self._rows
            self._rows = []
            self._last_flush_at = time.time()
        _append_jsonl_batch(path, batch)


_buffers: dict[str, _CallStatsBuffer] = {}
_buffers_lock = threading.Lock()


def _get_buffer(project_root_key: str) -> _CallStatsBuffer:
    with _buffers_lock:
        buf = _buffers.get(project_root_key)
        if buf is None:
            buf = _CallStatsBuffer()
            _buffers[project_root_key] = buf
        return buf


def record_call(
    paths: "AgentPaths",
    *,
    provider: str = "",
    model: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_ms: int = 0,
    outcome: str = OUTCOME_SUCCESS,
) -> None:
    """记一条调用计数记录（攒批写入，见模块头部说明）。失败（比如磁盘
    只读）静默忽略——调用计数是锦上添花的可观测性增强，不能因为这个
    影响真正的 LLM 调用主链路。"""
    try:
        row = CallStatsRecord(
            ts=time.time(), provider=provider, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            duration_ms=duration_ms, outcome=outcome,
        ).to_dict()
        key = str(paths.project_root)
        _get_buffer(key).add(paths.llm_call_stats_path, row)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.llm.call_stats.record_call")


def flush_now(paths: "AgentPaths") -> None:
    """测试/诊断用：强制把当前缓冲区落盘，不等批量阈值。"""
    try:
        key = str(paths.project_root)
        _get_buffer(key).flush(paths.llm_call_stats_path)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.llm.call_stats.flush_now")


# ── 按天聚合 + 降采样压缩 ──────────────────────────────────────────────────────

def _day_bucket(ts: float) -> int:
    return int(ts // 86400)


def _day_label(day_bucket: int) -> str:
    from mini_agent.time_utils import ts_to_str
    return ts_to_str(day_bucket * 86400)[:10]


def _aggregate_rows(rows: list[dict]) -> dict:
    agg = {
        "call_count": 0, "success_count": 0, "error_count": 0,
        "key_switch_count": 0, "config_switch_count": 0,
        "total_input_tokens": 0, "total_output_tokens": 0,
        "total_duration_ms": 0,
    }
    for r in rows:
        agg["call_count"] += 1
        outcome = r.get("outcome", OUTCOME_SUCCESS)
        if outcome == OUTCOME_SUCCESS:
            agg["success_count"] += 1
        elif outcome == OUTCOME_ERROR:
            agg["error_count"] += 1
        elif outcome == OUTCOME_KEY_SWITCH:
            agg["key_switch_count"] += 1
        elif outcome == OUTCOME_CONFIG_SWITCH:
            agg["config_switch_count"] += 1
        agg["total_input_tokens"] += int(r.get("input_tokens") or 0)
        agg["total_output_tokens"] += int(r.get("output_tokens") or 0)
        agg["total_duration_ms"] += int(r.get("duration_ms") or 0)
    return agg


def compact_call_stats_storage(paths: "AgentPaths", *, now: Optional[float] = None) -> int:
    """把 `_RAW_WINDOW_DAYS` 天之前的原始逐条记录压缩成每日汇总行
    （`is_daily_aggregate: true`），返回被压缩掉的原始行数（0 表示本次
    没有可压缩的旧数据）。幂等操作，可以随时安全重复调用——已经是汇总行
    的记录会原样保留、不会被二次聚合（用 `is_daily_aggregate` 标记区分）。
    """
    now = now if now is not None else time.time()
    rows = _read_jsonl(paths.llm_call_stats_path)
    if not rows:
        return 0
    cutoff = now - _RAW_WINDOW_DAYS * 86400

    recent_raw = [r for r in rows if not r.get("is_daily_aggregate") and r.get("ts", 0) >= cutoff]
    already_aggregated = [r for r in rows if r.get("is_daily_aggregate")]
    old_raw = [r for r in rows if not r.get("is_daily_aggregate") and r.get("ts", 0) < cutoff]

    if not old_raw:
        return 0

    buckets: dict[int, list[dict]] = {}
    for r in old_raw:
        buckets.setdefault(_day_bucket(r.get("ts", 0)), []).append(r)

    new_aggregates = []
    for day_bucket, day_rows in buckets.items():
        agg = _aggregate_rows(day_rows)
        new_aggregates.append({
            "ts": day_bucket * 86400,
            "day": _day_label(day_bucket),
            "is_daily_aggregate": True,
            **agg,
        })

    out = already_aggregated + new_aggregates + recent_raw
    out.sort(key=lambda r: r.get("ts", 0))
    _write_jsonl(paths.llm_call_stats_path, out)
    return len(old_raw)


def call_stats_series(paths: "AgentPaths", *, days: int = _DEFAULT_QUERY_DAYS) -> list[dict]:
    """返回最近 `days` 天、按天聚合的调用统计序列（旧→新），不区分记录
    当时是原始行还是已经压缩过的汇总行——查询侧统一在内存里按天重新聚合
    一次，调用方不需要关心存储层的降采样细节。

    每个元素：{day, call_count, success_count, error_count,
    key_switch_count, config_switch_count, total_input_tokens,
    total_output_tokens, avg_duration_ms}
    """
    rows = _read_jsonl(paths.llm_call_stats_path)
    if not rows:
        return []
    cutoff = time.time() - max(1, days) * 86400
    recent = [r for r in rows if r.get("ts", 0) >= cutoff]
    if not recent:
        return []

    buckets: dict[int, list[dict]] = {}
    for r in recent:
        buckets.setdefault(_day_bucket(r.get("ts", 0)), []).append(r)

    out = []
    for day_bucket in sorted(buckets):
        day_rows = buckets[day_bucket]
        # 汇总行和原始行可能同一天混在一起（比如压缩发生在查询窗口的
        # 边界日），统一按"是否已经是汇总行"分别处理后再相加。
        raw = [r for r in day_rows if not r.get("is_daily_aggregate")]
        pre_aggregated = [r for r in day_rows if r.get("is_daily_aggregate")]
        agg = _aggregate_rows(raw)
        for pa in pre_aggregated:
            agg["call_count"] += pa.get("call_count", 0)
            agg["success_count"] += pa.get("success_count", 0)
            agg["error_count"] += pa.get("error_count", 0)
            agg["key_switch_count"] += pa.get("key_switch_count", 0)
            agg["config_switch_count"] += pa.get("config_switch_count", 0)
            agg["total_input_tokens"] += pa.get("total_input_tokens", 0)
            agg["total_output_tokens"] += pa.get("total_output_tokens", 0)
            agg["total_duration_ms"] += pa.get("total_duration_ms", 0)
        avg_duration_ms = (
            round(agg["total_duration_ms"] / agg["call_count"], 1) if agg["call_count"] else 0.0
        )
        out.append({
            "day": _day_label(day_bucket),
            "call_count": agg["call_count"],
            "success_count": agg["success_count"],
            "error_count": agg["error_count"],
            "key_switch_count": agg["key_switch_count"],
            "config_switch_count": agg["config_switch_count"],
            "total_input_tokens": agg["total_input_tokens"],
            "total_output_tokens": agg["total_output_tokens"],
            "avg_duration_ms": avg_duration_ms,
        })
    return out
