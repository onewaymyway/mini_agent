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
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

_BATCH_SIZE = 10
_FLUSH_INTERVAL_SECONDS = 30.0
_RAW_WINDOW_DAYS = 7          # 超过这个天数的原始记录会被压缩成每日汇总
_DEFAULT_QUERY_DAYS = 7
# 追加写入累计多少条后触发一次压缩，让文件大小保持有界（原实现从未调用压缩，文件会无限增长）
_COMPACT_EVERY_ROWS = 5000
# 倒序读取时允许的时间乱序余量（秒）：批量刷盘可能让同一批内 ts 略有错位
_TAIL_READ_MARGIN_SECONDS = 3600
_TAIL_READ_CHUNK = 64 * 1024

# 追加与压缩互斥：压缩是"读全文件 → 重写"，期间若有追加会被覆盖丢失
_FILE_LOCK = threading.Lock()
_appends_since_compact = 0

OUTCOME_SUCCESS = "success"
OUTCOME_ERROR = "error"
OUTCOME_KEY_SWITCH = "key_switch"
OUTCOME_CONFIG_SWITCH = "config_switch"

# 旧版本写入的记录没有 source / origin 字段，以及按天压缩后的汇总行也没有，
# 统一归入这个桶。读取侧一律用 .get() 兜底，因此旧数据不会导致报错。
LEGACY_BUCKET = "legacy"
ORIGIN_MAIN = "main"

# ── 调用来源（谁发起的调用）────────────────────────────────────────────────────
# 调用点（LLMHelper / 后台任务 / skill 入口）通过 contextvar 标记来源，
# LLMClientPool 记录时读取它。contextvar 不会跨线程传播，线程内未标记的调用
# 会退回到 pool 自身的默认来源（见 LLMClientPool.configure_stats）。
_call_source_var: "ContextVar[Optional[str]]" = ContextVar("mini_agent_llm_call_source", default=None)


@contextmanager
def call_source(kind: str) -> Iterator[None]:
    """强制把这段代码里发出的调用标记为 ``kind``（覆盖外层设置）。"""
    token = _call_source_var.set(kind)
    try:
        yield
    finally:
        _call_source_var.reset(token)


@contextmanager
def call_source_if_unset(kind: str) -> Iterator[None]:
    """外层已经标记过来源时保持外层的值，否则标记为 ``kind``。"""
    if _call_source_var.get() is not None:
        yield
        return
    with call_source(kind):
        yield


def current_source(default: str) -> str:
    """当前线程上下文的来源；没有标记时返回 ``default``。"""
    return _call_source_var.get() or default


@dataclass
class CallStatsRecord:
    ts: float
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    outcome: str = OUTCOME_SUCCESS
    # 新增字段（有默认值，旧代码/旧数据不受影响）
    source: str = LEGACY_BUCKET     # 调用来源：agent / helper / skill / background / cron / ...
    origin: str = ORIGIN_MAIN       # 调用来源项目：main 或 external:<项目名>

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
    with _FILE_LOCK:
        with path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    _note_appended(path, len(rows))


def _note_appended(path: Path, n: int) -> None:
    """累计追加条数，达到阈值时就地压缩一次（锁外调用，避免与 _FILE_LOCK 嵌套）。
    压缩失败静默忽略——它只影响文件大小，不影响数据正确性。"""
    global _appends_since_compact
    with _FILE_LOCK:
        _appends_since_compact += n
        due = _appends_since_compact >= _COMPACT_EVERY_ROWS
        if due:
            _appends_since_compact = 0
    if due:
        # 放到后台线程：压缩要读全文件，不能拖慢触发它的那次 LLM 调用
        threading.Thread(
            target=_compact_in_background, args=(path,), name="llm-call-stats-compact", daemon=True,
        ).start()


def _compact_in_background(path: Path) -> None:
    try:
        _compact_file(path, time.time())
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.llm.call_stats._compact_in_background")


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
    source: str = LEGACY_BUCKET,
    origin: str = ORIGIN_MAIN,
) -> None:
    """记一条调用计数记录（攒批写入，见模块头部说明）。失败（比如磁盘
    只读）静默忽略——调用计数是锦上添花的可观测性增强，不能因为这个
    影响真正的 LLM 调用主链路。"""
    try:
        row = CallStatsRecord(
            ts=time.time(), provider=provider, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            duration_ms=duration_ms, outcome=outcome,
            source=source or LEGACY_BUCKET, origin=origin or ORIGIN_MAIN,
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
    """公开入口（签名不变）：见 ``_compact_file``。"""
    return _compact_file(paths.llm_call_stats_path, now if now is not None else time.time())


def _compact_file(path: Path, now: float) -> int:
    """把 `_RAW_WINDOW_DAYS` 天之前的原始逐条记录压缩成每日汇总行
    （`is_daily_aggregate: true`），返回被压缩掉的原始行数（0 表示本次
    没有可压缩的旧数据）。幂等操作，可以随时安全重复调用——已经是汇总行
    的记录会原样保留、不会被二次聚合（用 `is_daily_aggregate` 标记区分）。
    """
    with _FILE_LOCK:
        return _compact_file_locked(path, now)


def _compact_file_locked(path: Path, now: float) -> int:
    rows = _read_jsonl(path)
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
    # 先写临时文件再原子替换，避免压缩中途异常导致原文件被截断
    tmp = path.with_suffix(path.suffix + ".tmp")
    _write_jsonl(tmp, out)
    tmp.replace(path)
    return len(old_raw)


def _parse_line(raw: bytes) -> Optional[dict]:
    raw = raw.strip()
    if not raw:
        return None
    try:
        row = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return row if isinstance(row, dict) else None


def _read_window(path: Path, cutoff: float) -> list[dict]:
    """从文件末尾倒序读取 ``ts >= cutoff`` 的行，返回文件顺序的列表。

    成本只与窗口大小有关，与文件总大小（历史总量）无关——这是接口超时的修复点：
    原实现每次全量读取整个文件，历史越长越慢。读到比 cutoff 早出一个余量的行即停止。
    """
    if not path.exists():
        return []
    try:
        size = path.stat().st_size
    except OSError:
        return []
    out_rev: list[dict] = []
    stop_at = cutoff - _TAIL_READ_MARGIN_SECONDS
    try:
        with path.open("rb") as f:
            pos = size
            carry = b""          # 上一块末尾不完整的行（它的开头在更早的块里）
            while pos > 0:
                step = min(_TAIL_READ_CHUNK, pos)
                pos -= step
                f.seek(pos)
                chunk = f.read(step)
                lines = (chunk + carry).split(b"\n")
                carry = lines[0]
                done = False
                for raw in reversed(lines[1:]):
                    row = _parse_line(raw)
                    if row is None:
                        continue
                    ts = row.get("ts", 0) or 0
                    if ts < stop_at:
                        done = True
                        break
                    if ts >= cutoff:
                        out_rev.append(row)
                if done:
                    carry = b""
                    break
            if carry and pos == 0:
                row = _parse_line(carry)
                if row is not None and (row.get("ts", 0) or 0) >= cutoff:
                    out_rev.append(row)
    except OSError:
        return []
    out_rev.reverse()
    return out_rev


def call_stats_series(paths: "AgentPaths", *, days: int = _DEFAULT_QUERY_DAYS) -> list[dict]:
    """返回最近 `days` 天、按天聚合的调用统计序列（旧→新），不区分记录
    当时是原始行还是已经压缩过的汇总行——查询侧统一在内存里按天重新聚合
    一次，调用方不需要关心存储层的降采样细节。

    每个元素：{day, call_count, success_count, error_count,
    key_switch_count, config_switch_count, total_input_tokens,
    total_output_tokens, avg_duration_ms}
    """
    cutoff = time.time() - max(1, days) * 86400
    recent = _read_window(paths.llm_call_stats_path, cutoff)
    return _series_from_rows(recent, days)


def _series_from_rows(recent: list[dict], days: int) -> list[dict]:
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


# ── 按来源 / 按项目拆分（兼容旧数据）──────────────────────────────────────────

def _empty_agg() -> dict:
    return {
        "call_count": 0, "success_count": 0, "error_count": 0,
        "key_switch_count": 0, "config_switch_count": 0,
        "total_input_tokens": 0, "total_output_tokens": 0,
        "total_duration_ms": 0,
    }


def _merge_raw_row(agg: dict, r: dict) -> None:
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


def _merge_aggregate_row(agg: dict, pa: dict) -> None:
    """合并已压缩的每日汇总行（它的 call_count 等字段已经是求和结果）。"""
    for key in ("call_count", "success_count", "error_count", "key_switch_count",
                "config_switch_count", "total_input_tokens", "total_output_tokens",
                "total_duration_ms"):
        agg[key] += int(pa.get(key) or 0)


def _finalize_agg(agg: dict) -> dict:
    out = dict(agg)
    out["avg_duration_ms"] = (
        round(agg["total_duration_ms"] / agg["call_count"], 1) if agg["call_count"] else 0.0
    )
    return out


def call_stats_breakdown(paths: "AgentPaths", *, days: int = _DEFAULT_QUERY_DAYS) -> dict:
    """返回最近 ``days`` 天内按来源（``by_source``）和按项目（``by_origin``）拆分的汇总。

    兼容说明：
      - 旧版本记录没有 source / origin 字段 → 归入 ``legacy`` 桶；
      - 已被压缩成每日汇总的行没有来源信息 → 同样归入 ``legacy`` 桶；
      - 文件不存在或为空 → 两个字段都是 ``{}``，不抛异常。
    """
    empty = {"by_source": {}, "by_origin": {}}
    cutoff = time.time() - max(1, days) * 86400
    recent = _read_window(paths.llm_call_stats_path, cutoff)
    if not recent:
        return empty
    return _breakdown_from_rows(recent)


def call_stats_summary(paths: "AgentPaths", *, days: int = _DEFAULT_QUERY_DAYS) -> dict:
    """一次读取窗口内的记录，同时返回 series 与 breakdown。

    接口层应使用它而不是分别调用两个函数：两次调用意味着读两遍文件。
    """
    cutoff = time.time() - max(1, days) * 86400
    rows = _read_window(paths.llm_call_stats_path, cutoff)
    return {
        "series": _series_from_rows(rows, days),
        "breakdown": _breakdown_from_rows(rows) if rows else {"by_source": {}, "by_origin": {}},
    }


def _breakdown_from_rows(recent: list[dict]) -> dict:
    """按来源 / 项目拆分（输入为已经限定在窗口内的行）。"""

    by_source: dict[str, dict] = {}
    by_origin: dict[str, dict] = {}
    for r in recent:
        if r.get("is_daily_aggregate"):
            _merge_aggregate_row(by_source.setdefault(LEGACY_BUCKET, _empty_agg()), r)
            _merge_aggregate_row(by_origin.setdefault(LEGACY_BUCKET, _empty_agg()), r)
            continue
        src = str(r.get("source") or LEGACY_BUCKET)
        org = str(r.get("origin") or LEGACY_BUCKET)
        _merge_raw_row(by_source.setdefault(src, _empty_agg()), r)
        _merge_raw_row(by_origin.setdefault(org, _empty_agg()), r)

    return {
        "by_source": {k: _finalize_agg(v) for k, v in by_source.items()},
        "by_origin": {k: _finalize_agg(v) for k, v in by_origin.items()},
    }
