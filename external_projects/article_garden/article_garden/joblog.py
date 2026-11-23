"""joblog.py — 文章库级运行日志（追加写入 <库根>/logs/job.jsonl），供看板实时展示与步骤时间表汇总。

每条记录：{ts, scope, level, message, ...}。scope 用来区分来源页面（pipeline、outline、script、voice、
storyboard、render、export …）。写入加锁，多线程安全；读取容忍不完整的行（进程被中途终止时最后一行可能是半行）。

结构化事件（带 event 字段，由 timeline.py 汇总成「步骤时间表」）：
  run_start / run_end     一次整体运行（如「流水线 G1」）
  step_start / step_end   其中的一个步骤；item 不为空时是某个阶段下的子步骤（如某一章）

线程绑定：bind(项目目录, scope) 之后，该线程里任意深度的代码都可用 bound_log() 写日志，不必层层传参。
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import traceback
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

LOG_FILE = "logs/job.jsonl"
LEVELS = ("info", "warn", "error", "ok")
HEARTBEAT_SEC = 30.0          # 长耗时步骤每隔多少秒写一条“仍在进行”
TAIL_BYTES = 2 * 1024 * 1024  # 读取日志尾部时最多读取的字节数，避免反复扫描很大的文件
_TB_LIMIT = 20000
_LOCK = threading.Lock()
_TLS = threading.local()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def log_path(project_dir: Path) -> Path:
    return Path(project_dir) / LOG_FILE


def log(project_dir: Path, scope: str, message: str, level: str = "info", **fields: Any) -> None:
    if level not in LEVELS:
        level = "info"
    row = {"ts": _now(), "scope": scope, "level": level, "message": message, **fields}
    line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
    path = log_path(project_dir)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)


def _read_tail_lines(path: Path) -> List[str]:
    size = path.stat().st_size
    with path.open("rb") as f:
        start = max(0, size - TAIL_BYTES)
        f.seek(start)
        data = f.read()
    lines = data.decode("utf-8", errors="replace").split("\n")
    if start > 0 and lines:
        lines = lines[1:]  # 第一行可能被截断
    return lines


def tail(project_dir: Path, limit: int = 200, scopes: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    """最近 limit 条日志（可按 scope 过滤）。"""
    path = log_path(project_dir)
    if not path.is_file():
        return []
    wanted = set(scopes) if scopes else None
    keep: deque = deque(maxlen=max(1, limit))
    for line in _read_tail_lines(path):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if wanted is None or row.get("scope") in wanted:
            keep.append(row)
    return list(keep)


def format_row(row: Dict[str, Any]) -> str:
    mark = {"info": "  ", "ok": "✔ ", "warn": "⚠ ", "error": "✘ "}.get(row.get("level"), "  ")
    ts = str(row.get("ts", ""))[11:19]
    first, *rest = str(row.get("message", "")).split("\n")
    return "\n".join([f"{ts} {mark}{first}"] + [f"    │ {ln}" for ln in rest])


def last_activity(project_dir: Path, scopes: Optional[Iterable[str]] = None) -> Optional[datetime]:
    """最近一条日志的时间；没有日志返回 None。用来判断另一个进程（如命令行）是否仍在运行。"""
    rows = tail(project_dir, 1, scopes)
    if not rows:
        return None
    try:
        return datetime.fromisoformat(rows[-1]["ts"])
    except (KeyError, ValueError):
        return None


def recently_active(project_dir: Path, scopes: Optional[Iterable[str]] = None, within_sec: float = 3 * HEARTBEAT_SEC,
                    now: Optional[datetime] = None) -> bool:
    ts = last_activity(project_dir, scopes)
    if ts is None:
        return False
    return ((now or datetime.now()) - ts).total_seconds() <= within_sec


# ---------------------------------------------------------------- 线程绑定

@contextlib.contextmanager
def bind(project_dir: Path, scope: str) -> Iterator[None]:
    prev = getattr(_TLS, "ctx", None)
    _TLS.ctx = (Path(project_dir), scope)
    try:
        yield
    finally:
        _TLS.ctx = prev


def current_ctx() -> Optional[Tuple[Path, str]]:
    return getattr(_TLS, "ctx", None)


def infer_level(message: str) -> str:
    if any(k in message for k in ("失败", "异常", "错误", "error", "Error")):
        return "error"
    if any(k in message for k in ("完成", "成功", "通过")):
        return "ok"
    return "info"


def bound_log(message: str, level: Optional[str] = None) -> None:
    """写入当前线程绑定的项目与 scope；未绑定时什么也不做。"""
    ctx = current_ctx()
    if ctx:
        log(ctx[0], ctx[1], message, level=level or infer_level(message))


def format_tb(exc: Optional[BaseException] = None) -> Optional[str]:
    """异常的完整堆栈文本；过长时保留末尾（最内层的调用点）。"""
    try:
        tb = ("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if exc is not None
              else traceback.format_exc())
    except Exception:  # noqa: BLE001
        return None
    if not tb or tb.strip() == "NoneType: None":
        return None
    return tb if len(tb) <= _TB_LIMIT else "…（前面的堆栈已省略）\n" + tb[-_TB_LIMIT:]


# ---------------------------------------------------------------- 运行与步骤事件

def run_start(project_dir: Path, scope: str, name: str) -> str:
    """开始一次整体运行，返回 run_id；之后本线程里的 step() 自动归属这次运行。"""
    run_id = uuid.uuid4().hex[:8]
    _TLS.run = (run_id, name)
    log(project_dir, scope, f"{name}：开始", event="run_start", run=name, run_id=run_id, started_at=_now(),
        pid=os.getpid())
    return run_id


def run_end(project_dir: Path, scope: str, run_id: str, name: str, status: str = "ok",
            error: Optional[str] = None, tb: Optional[str] = None, started_at: Optional[str] = None) -> None:
    """status：ok / waiting（等待人工确认）/ stopped / failed。"""
    text = {"ok": "完成", "waiting": "暂停（等待人工确认）", "stopped": "已停止"}.get(status, "失败")
    level = {"ok": "ok", "waiting": "warn", "stopped": "warn"}.get(status, "error")
    msg = f"{name}：{text}" + (f"：{error}" if error and status != "ok" else "")
    log(project_dir, scope, msg, level=level, event="run_end", run=name, run_id=run_id, status=status,
        started_at=started_at, ended_at=_now(), error=error, traceback=tb)
    _TLS.run = None


@contextlib.contextmanager
def run(project_dir: Path, scope: str, name: str) -> Iterator[str]:
    """包住一次整体运行：开始与结束（成功 / 停止 / 失败）都会写入记录。"""
    rid = run_start(project_dir, scope, name)
    try:
        yield rid
    except BaseException as exc:  # noqa: BLE001
        stopped = type(exc).__name__ in ("Cancelled", "BudgetStop")
        run_end(project_dir, scope, rid, name, status="stopped" if stopped else "failed",
                error=None if stopped else f"{type(exc).__name__}: {exc}", tb=None if stopped else format_tb(exc))
        raise
    else:
        run_end(project_dir, scope, rid, name, status="ok")


def _pid_alive(pid: Any, started_at: Optional[datetime] = None) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.name == "nt":  # Windows 上 os.kill(pid, 0) 不安全，改用心跳时间近似判断
        return started_at is not None and (datetime.now() - started_at).total_seconds() < 3 * HEARTBEAT_SEC
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def current_stage() -> Optional[str]:
    """当前线程所在的步骤名（用于把模型调用归到对应步骤）。"""
    return getattr(_TLS, "stage", None)


def running_runs(project_dir: Path, scopes: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    """真正在运行的任务：有开始记录、没有结束记录，且进程仍然存在。
    进程已不存在（崩溃、被杀）的任务不算运行中，它们只是“中断”的历史记录。"""
    rows = tail(project_dir, limit=20000)
    started: Dict[str, Dict[str, Any]] = {}
    ended = set()
    for r in rows:
        if r.get("event") == "run_start" and r.get("run_id"):
            started[r["run_id"]] = r
        elif r.get("event") == "run_end" and r.get("run_id"):
            ended.add(r["run_id"])
    wanted = set(scopes) if scopes else None
    out = []
    for rid, r in started.items():
        if rid in ended or (wanted and r.get("scope") not in wanted):
            continue
        try:
            when = datetime.fromisoformat(r["ts"])
        except (KeyError, ValueError):
            when = None
        if _pid_alive(r.get("pid"), when):
            out.append(r)
    return out


def is_running(project_dir: Path, scopes: Optional[Iterable[str]] = None) -> bool:
    return bool(running_runs(project_dir, scopes))


@contextlib.contextmanager
def step(project_dir: Path, scope: str, stage: str, item: Optional[str] = None, *,
         heartbeat: bool = True, every: Optional[float] = None) -> Iterator[Dict[str, Any]]:
    """记录一个步骤：开始、结束（含用时与状态）、失败原因与堆栈；耗时久时定期写“仍在进行”。

    yield 一个 dict：调用方可设置 info["status"]（如 "waiting"）与 info["detail"]（结果说明）。
    异常照常抛出；用户停止（Cancelled）记为 stopped，不当作错误。
    """
    run = getattr(_TLS, "run", None)
    run_id, run_name = run if run else (None, None)
    label = stage if not item else f"{stage} · {item}"
    started = _now()
    t0 = time.monotonic()
    info: Dict[str, Any] = {"status": "ok", "detail": "", "progress": ""}
    prev_stage = getattr(_TLS, "stage", None)
    _TLS.stage = label                  # 本步骤内发出的模型调用，会记录到这一步名下
    log(project_dir, scope, f"{label}：开始", event="step_start", run=run_name, run_id=run_id, stage=stage,
        item=item, started_at=started)

    stop = threading.Event()
    if heartbeat:
        interval = HEARTBEAT_SEC if every is None else every

        def _beat() -> None:
            while not stop.wait(interval):
                where = f"；当前：{info['progress']}" if info.get("progress") else ""
                log(project_dir, scope, f"{label}：仍在进行（已 {time.monotonic() - t0:.0f} 秒）{where}")

        threading.Thread(target=_beat, daemon=True).start()

    def _end(status: str, error: Optional[str] = None, tb: Optional[str] = None) -> None:
        secs = round(time.monotonic() - t0, 1)
        text = {"ok": "完成", "waiting": "等待人工确认", "stopped": "已停止"}.get(status, "失败")
        level = {"ok": "ok", "waiting": "warn", "stopped": "warn"}.get(status, "error")
        detail = f"（{info['detail']}）" if info.get("detail") else ""
        log(project_dir, scope, f"{label}：{text}{detail}（{secs} 秒）" + (f"：{error}" if error else ""),
            level=level, event="step_end", run=run_name, run_id=run_id, stage=stage, item=item, status=status,
            started_at=started, ended_at=_now(), seconds=secs, error=error, detail=info.get("detail") or None,
            traceback=tb)

    try:
        yield info
    except BaseException as exc:
        stop.set()
        _TLS.stage = prev_stage
        if type(exc).__name__ == "Cancelled":
            _end("stopped", "用户请求停止")
        else:
            _end("failed", f"{type(exc).__name__}: {exc}", format_tb(exc))
        raise
    else:
        stop.set()
        _TLS.stage = prev_stage
        _end(str(info.get("status") or "ok"))
