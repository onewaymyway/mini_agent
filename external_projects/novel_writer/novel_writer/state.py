"""novel_writer/state.py — 步骤状态机、脏标记、worker 心跳与控制指令（方案 4.1 / 6.5）。

持久化文件（都在工作目录的 `_studio/` 下，见 workspace.PATHS）：
  pipeline_state.json  每个步骤的状态、尝试次数、输入哈希、错误信息
  control.json         UI/CLI → worker 的指令（run / pause / stop / run_step）
  worker.json          worker 心跳与 pid（同一本书同一时刻只允许一个 worker；心跳超时 = 孤儿）
  usage.jsonl          每次 LLM 步骤的耗时与结果

所有写入都走 `fsutil.atomic_write_json`；同进程内用锁串行化「读-改-写」，
跨进程的竞争靠\"worker 每次决策前都重新读盘\"来收敛（已知限制，见 PROJECT.md）。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from novel_writer.fsutil import atomic_write_json, read_json
from novel_writer.steps import StepDef
from novel_writer.workspace import Workspace

SCHEMA_VERSION = 1
HEARTBEAT_TIMEOUT_SEC = 90.0

# 步骤状态
ST_PENDING = "pending"
ST_RUNNING = "running"
ST_WAITING_REVIEW = "waiting_review"
ST_WAITING_USER = "waiting_user"
ST_PASSED = "passed"
ST_FAILED = "failed"
ST_SKIPPED = "skipped"
ST_STALE = "stale"
ST_INTERRUPTED = "interrupted"
ST_BLOCKED = "blocked"          # 执行器尚未实现（例如 M3 之前的章节 python 步骤）

FINISHED_STATUSES = (ST_PASSED, ST_SKIPPED)
STATUSES = (
    ST_PENDING, ST_RUNNING, ST_WAITING_REVIEW, ST_WAITING_USER, ST_PASSED, ST_FAILED,
    ST_SKIPPED, ST_STALE, ST_INTERRUPTED, ST_BLOCKED,
)

_LOCK = threading.RLock()


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_iso(text: Optional[str]) -> Optional[_dt.datetime]:
    if not text:
        return None
    try:
        return _dt.datetime.fromisoformat(text)
    except ValueError:
        return None


# ── 流水线状态 ─────────────────────────────────────────────────────────────


def load_state(ws: Workspace) -> Dict[str, Any]:
    data = read_json(ws.path("studio_state"), default=None)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("schema_version", SCHEMA_VERSION)
    data.setdefault("steps", {})
    return data


def get_step_state(ws: Workspace, step_id: str) -> Dict[str, Any]:
    entry = load_state(ws)["steps"].get(step_id) or {}
    return {"status": ST_PENDING, "attempts": 0, **entry}


def update_step(ws: Workspace, step_id: str, **fields: Any) -> Dict[str, Any]:
    """读-改-写单个步骤的状态（加锁，写前重新读盘）。返回更新后的步骤状态。"""
    with _LOCK:
        state = load_state(ws)
        entry = {"status": ST_PENDING, "attempts": 0, **state["steps"].get(step_id, {})}
        entry.update(fields)
        entry["updated_at"] = now_iso()
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"非法步骤状态 {fields['status']!r}")
        state["steps"][step_id] = entry
        atomic_write_json(ws.path("studio_state"), state)
        return entry


def reset_step(ws: Workspace, step_id: str) -> Dict[str, Any]:
    """回到 pending（用于重跑）。保留 attempts 与历史，清除错误与待答复项。"""
    return update_step(ws, step_id, status=ST_PENDING, error=None, needs_user=None)


# ── 输入哈希与脏标记（方案 5.2）────────────────────────────────────────────


def _strip_glob(spec: str) -> str:
    return spec[len("glob:"):] if spec.startswith("glob:") else spec


def expand_inputs(ws: Workspace, specs: Iterable[str]) -> List[Path]:
    files: List[Path] = []
    for spec in specs:
        pattern = _strip_glob(spec)
        if "*" in pattern:
            files.extend(sorted(p for p in ws.root.glob(pattern) if p.is_file()))
        else:
            files.append(ws.root / pattern)
    return files


def input_hash(ws: Workspace, step: StepDef) -> str:
    """步骤输入的指纹：技能名 + 每个输入文件的相对路径与内容。文件不存在记为 MISSING。"""
    h = hashlib.sha256()
    h.update(f"{step.executor}:{step.skill}:{step.handler}".encode("utf-8"))
    for path in expand_inputs(ws, step.inputs):
        h.update(str(path.relative_to(ws.root)).encode("utf-8"))
        if path.is_file():
            h.update(path.read_bytes())
        else:
            h.update(b"MISSING")
    return h.hexdigest()


def mark_stale(ws: Workspace, plan: List[StepDef]) -> List[str]:
    """已通过的步骤若输入哈希变化（上游被重新生成/手改），标记为 stale。

    没有记录过哈希的旧步骤只补记录，不标脏（避免升级后整本书被误判）。
    返回新标脏的步骤 id 列表。
    """
    stale_ids: List[str] = []
    with _LOCK:
        state = load_state(ws)
        changed = False
        for step in plan:
            entry = state["steps"].get(step.id)
            if not entry or entry.get("status") != ST_PASSED:
                continue
            current = input_hash(ws, step)
            recorded = entry.get("input_hash")
            if recorded is None:
                entry["input_hash"] = current
                changed = True
            elif recorded != current:
                entry.update(status=ST_STALE, note="上游产物已变化，需要重跑", updated_at=now_iso())
                stale_ids.append(step.id)
                changed = True
        if changed:
            atomic_write_json(ws.path("studio_state"), state)
    return stale_ids


def mark_orphans_interrupted(ws: Workspace) -> List[str]:
    """worker 启动时调用（已持有 worker 锁）：遗留的 running 步骤一律视为中断。"""
    fixed: List[str] = []
    state = load_state(ws)
    for sid, entry in list(state["steps"].items()):
        if entry.get("status") == ST_RUNNING:
            update_step(ws, sid, status=ST_INTERRUPTED, error="上次运行中断（worker 心跳超时），可直接重跑")
            fixed.append(sid)
    return fixed


# ── worker 心跳 / 锁 ───────────────────────────────────────────────────────


def read_worker(ws: Workspace) -> Dict[str, Any]:
    return read_json(ws.path("studio_worker"), default={}) or {}


def worker_alive(ws: Workspace, *, now: Optional[_dt.datetime] = None, timeout: float = HEARTBEAT_TIMEOUT_SEC) -> bool:
    info = read_worker(ws)
    if info.get("state") != "running":
        return False
    beat = _parse_iso(info.get("heartbeat"))
    if beat is None:
        return False
    now = now or _dt.datetime.now().astimezone()
    if beat.tzinfo is None:
        beat = beat.replace(tzinfo=now.tzinfo)
    return (now - beat).total_seconds() < timeout


def acquire_worker(ws: Workspace, *, mode: str) -> bool:
    """尝试占用 worker 锁。已有存活的 worker 时返回 False。"""
    import os

    with _LOCK:
        if worker_alive(ws):
            return False
        atomic_write_json(ws.path("studio_worker"), {
            "pid": os.getpid(), "state": "running", "mode": mode,
            "started_at": now_iso(), "heartbeat": now_iso(), "step": None, "reason": None,
        })
        return True


def heartbeat(ws: Workspace, **fields: Any) -> None:
    with _LOCK:
        info = read_worker(ws)
        info.update(fields)
        info["heartbeat"] = now_iso()
        atomic_write_json(ws.path("studio_worker"), info)


def release_worker(ws: Workspace, reason: str) -> None:
    with _LOCK:
        info = read_worker(ws)
        info.update({"state": "stopped", "reason": reason, "step": None, "step_title": None,
                     "step_started_at": None, "heartbeat": now_iso(), "finished_at": now_iso()})
        atomic_write_json(ws.path("studio_worker"), info)


# ── 运行日志（实时输出；追加写入，不会被下一次运行覆盖，界面始终能看到上一次的结果）──


def log_line(ws: Workspace, text: str) -> None:
    """向 `_studio/run.log` 追加一行（带时间戳）。worker 与子 agent 的输出都写到这里。"""
    path = ws.path("studio_log")
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%H:%M:%S")
    with _LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {text}\n")
            f.flush()


def read_log_tail(ws: Workspace, lines: int = 120, max_bytes: int = 200_000) -> List[str]:
    path = ws.path("studio_log")
    if not path.is_file():
        return []
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            f.readline()  # 丢弃可能不完整的第一行
        data = f.read().decode("utf-8", errors="replace")
    return [ln for ln in data.splitlines() if ln.strip()][-lines:]


LAST_RUN_FILE = "last_run.json"


def write_last_run(ws: Workspace, record: Dict[str, Any]) -> None:
    atomic_write_json(ws.studio / LAST_RUN_FILE, record)


def read_last_run(ws: Workspace) -> Optional[Dict[str, Any]]:
    data = read_json(ws.studio / LAST_RUN_FILE, default=None)
    return data if isinstance(data, dict) else None


# ── 控制指令 ───────────────────────────────────────────────────────────────

CONTROL_DEFAULT: Dict[str, Any] = {"command": "none", "mode": "until_gate", "run_step": None}


def read_control(ws: Workspace) -> Dict[str, Any]:
    data = read_json(ws.path("studio_control"), default=None)
    return {**CONTROL_DEFAULT, **(data if isinstance(data, dict) else {})}


def write_control(ws: Workspace, **fields: Any) -> Dict[str, Any]:
    with _LOCK:
        data = {**read_control(ws), **fields, "issued_at": now_iso()}
        atomic_write_json(ws.path("studio_control"), data)
        return data


# ── 用量留痕 ───────────────────────────────────────────────────────────────


def append_usage(ws: Workspace, record: Dict[str, Any]) -> None:
    path = ws.path("studio_usage")
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"at": now_iso(), **record}, ensure_ascii=False)
    with _LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
