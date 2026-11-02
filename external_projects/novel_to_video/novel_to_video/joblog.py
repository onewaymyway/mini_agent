"""joblog.py — 项目级运行日志（追加写入 logs/job.jsonl），供界面实时展示。

每条记录：{ts, scope, level, message, ...}。scope 用于区分来源页面：
  assets（全书素材）、chapter（章节流水线）、queue（批量队列）、final（全书合成）、gender（性别确认）
写入加锁，多线程安全；读取时容忍不完整的行（运行中途被截断）。
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

LOG_FILE = "logs/job.jsonl"
LEVELS = ("info", "warn", "error", "ok")
_LOCK = threading.Lock()


def log(project_dir: Path, scope: str, message: str, level: str = "info", **fields: Any) -> None:
    if level not in LEVELS:
        level = "info"
    path = Path(project_dir) / LOG_FILE
    row = {"ts": datetime.now().isoformat(timespec="seconds"), "scope": scope,
           "level": level, "message": message, **fields}
    line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)


def tail(project_dir: Path, limit: int = 200, scopes: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    path = Path(project_dir) / LOG_FILE
    if not path.is_file():
        return []
    wanted = set(scopes) if scopes else None
    keep: deque = deque(maxlen=max(1, limit))
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue  # 进程被中途终止时最后一行可能不完整
            if wanted is None or row.get("scope") in wanted:
                keep.append(row)
    return list(keep)


def format_row(row: Dict[str, Any]) -> str:
    mark = {"info": "  ", "ok": "✔ ", "warn": "⚠ ", "error": "✘ "}.get(row.get("level"), "  ")
    ts = str(row.get("ts", ""))[11:19]
    first, *rest = str(row.get("message", "")).split("\n")
    return "\n".join([f"{ts} {mark}{first}"] + [f"    │ {ln}" for ln in rest])


# ---------------------------------------------------------------- 线程绑定与输出转发
#
# 后台任务在线程中运行，而 skill 客户端通过 print 输出重试、失败、轮询等过程信息。
# 这里把“当前线程绑定的项目与 scope”记在线程本地变量中，并把标准输出按行转发到日志，
# 同时保留原有的命令行输出。未绑定的线程不受影响。

import contextlib
import io
import sys

_TLS = threading.local()
_MSG_LIMIT = 400


@contextlib.contextmanager
def bind(project_dir: Path, scope: str):
    prev = getattr(_TLS, "ctx", None)
    _TLS.ctx = (Path(project_dir), scope)
    try:
        yield
    finally:
        _TLS.ctx = prev


def current_ctx():
    """当前线程绑定的 (project_dir, scope)，未绑定返回 None。供 cancel.call 把绑定传给 helper 线程。"""
    return getattr(_TLS, "ctx", None)


def infer_level(message: str) -> str:
    if "不可重试" in message:
        return "warn"
    if any(k in message for k in ("失败", "异常", "错误", "error", "Error")):
        return "error"
    if any(k in message for k in ("完成", "成功", "通过")):
        return "ok"
    return "info"


def bound_log(message: str, level: Optional[str] = None) -> None:
    """写入当前线程绑定的项目与 scope；未绑定时什么也不做。"""
    ctx = getattr(_TLS, "ctx", None)
    if not ctx:
        return
    log(ctx[0], ctx[1], message, level=level or infer_level(message))


class _ConsoleTee(io.TextIOBase):
    """同时写入原始控制台与日志（按行）。"""

    def __init__(self, original):
        self._original = original
        self._pending = ""

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        try:
            self._original.write(s)
        except Exception:
            pass
        self._pending += s
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            text = line.strip()
            if text:
                bound_log(text[:_MSG_LIMIT])
        return len(s)

    def flush(self) -> None:
        try:
            self._original.flush()
        except Exception:
            pass


def console_tee() -> _ConsoleTee:
    return _ConsoleTee(sys.stdout)


@contextlib.contextmanager
def timed(project_dir: Path, scope: str, label: str):
    """记录一个耗时步骤：开始、完成（含耗时）或失败（含原因）。异常照常抛出。"""
    import time as _time
    t0 = _time.monotonic()
    log(project_dir, scope, f"{label}…")
    try:
        yield
    except Exception as exc:
        log(project_dir, scope, f"{label} 失败（{_time.monotonic() - t0:.1f} 秒）：{exc}", level="error")
        raise
    log(project_dir, scope, f"{label} 完成（{_time.monotonic() - t0:.1f} 秒）", level="ok")


HEARTBEAT_SEC = 30.0   # 长时间阶段每隔多少秒写一条“仍在进行”


@contextmanager
def heartbeat(project_dir: Path, scope: str, label: str, every: Optional[float] = None):
    """包住一段可能很慢（网络请求、模型推理）的调用：每隔 every 秒写一条“仍在进行”，
    结束时写“完成（x 秒）”或“异常结束”。这样卡住时日志里能看到卡在哪一步、已经等了多久。"""
    interval = HEARTBEAT_SEC if every is None else every
    stop = threading.Event()
    start = time.monotonic()
    log(project_dir, scope, f"{label}：开始")

    def _beat():
        while not stop.wait(interval):
            log(project_dir, scope, f"{label}：仍在进行（已 {time.monotonic() - start:.0f} 秒）", level="info")

    t = threading.Thread(target=_beat, daemon=True)
    t.start()
    try:
        yield
    except BaseException as exc:
        stop.set()
        if type(exc).__name__ == "Cancelled":   # 用户主动停止：不是错误
            log(project_dir, scope, f"{label}：已停止（{time.monotonic() - start:.1f} 秒）", level="warn")
        else:
            log(project_dir, scope, f"{label}：异常结束（{time.monotonic() - start:.1f} 秒）", level="error")
        raise
    else:
        stop.set()
        log(project_dir, scope, f"{label}：完成（{time.monotonic() - start:.1f} 秒）", level="info")
