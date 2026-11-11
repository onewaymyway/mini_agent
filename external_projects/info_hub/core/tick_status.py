"""调度运行状态：最后活动时间、当前运行的源、各源最近结果、最近错误。

写入 data/state/_tick_status.json。这个文件不持有 _scheduler.json 的锁，
即使 tick 卡住，看板仍能读到最后的活动时间与正在运行的源。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Optional

from core.store import atomic_write_json, read_json

MAX_ERRORS = 30
STALL_AFTER = timedelta(minutes=15)  # 同一个源或同一次 tick 超过此时长仍未结束，视为卡住


def _now() -> datetime:
    return datetime.now().astimezone()


def _path(settings):
    return settings.paths.state_dir / "_tick_status.json"


def read(settings) -> dict[str, Any]:
    return read_json(_path(settings), default={}) or {}


def _update(settings, mutate) -> None:
    data = read(settings)
    now = _now().isoformat(timespec="seconds")
    data["last_activity_at"] = now
    data["pid"] = os.getpid()
    mutate(data, now)
    atomic_write_json(_path(settings), data)


def loop_started(settings) -> None:
    _update(settings, lambda d, now: d.update(loop_started_at=now))


def tick_started(settings) -> None:
    def m(d, now):
        d.update(running=True, tick_started_at=now, current_source=None, current_source_started_at=None)
    _update(settings, m)


def tick_finished(settings, error: Optional[str] = None) -> None:
    def m(d, now):
        d.update(running=False, tick_finished_at=now, current_source=None, current_source_started_at=None)
        if error:
            _push_error(d, now, "tick", error)
    _update(settings, m)


def source_started(settings, source_id: str) -> None:
    def m(d, now):
        d.update(current_source=source_id, current_source_started_at=now)
    _update(settings, m)


def source_finished(settings, source_id: str, ok: bool, error: Optional[str] = None) -> None:
    def m(d, now):
        d.update(current_source=None, current_source_started_at=None)
        d.setdefault("source_outcomes", {})[source_id] = {"at": now, "ok": ok, "error": error}
        if error and not ok:
            _push_error(d, now, source_id, error)
    _update(settings, m)


def report_error(settings, where: str, message: str) -> None:
    _update(settings, lambda d, now: _push_error(d, now, where, message))


def _push_error(d: dict[str, Any], now: str, where: str, message: str) -> None:
    errors = d.setdefault("errors", [])
    errors.append({"at": now, "where": where, "message": message[:500]})
    del errors[:-MAX_ERRORS]


def summary(settings, interval: timedelta) -> dict[str, Any]:
    """供看板使用的汇总：循环是否活着、是否卡住、运行中的源。"""
    data = read(settings)
    now = _now()
    last_activity = _parse(data.get("last_activity_at"))
    running = bool(data.get("running"))
    started = _parse(data.get("current_source_started_at")) or _parse(data.get("tick_started_at"))
    stalled = bool(running and started and now - started > STALL_AFTER)
    # 没有运行中的任务时，活动间隔超过 2 个 tick 周期 + 缓冲，视为后台线程可能已停止
    idle_limit = interval * 2 + timedelta(minutes=5)
    loop_dead = bool(not running and (last_activity is None or now - last_activity > idle_limit))
    return {
        **data,
        "now": now.isoformat(timespec="seconds"),
        "stalled": stalled,
        "loop_suspected_dead": loop_dead,
        "running_for_sec": int((now - started).total_seconds()) if running and started else None,
        "last_activity_age_sec": int((now - last_activity).total_seconds()) if last_activity else None,
    }


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.astimezone()


def scheduler_lock_state(settings) -> str:
    """_scheduler.json 的文件锁状态。Windows 上 fcntl 不可用，无法检测。"""
    try:
        import fcntl
    except ImportError:
        return "不可检测（Windows 无文件锁）"
    lock_path = (settings.paths.state_dir / "_scheduler.json").with_name("_scheduler.json.lock")
    if not lock_path.exists():
        return "无锁文件"
    with lock_path.open("a+") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "被占用（有进程持有）"
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return "空闲"
