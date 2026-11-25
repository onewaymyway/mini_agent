"""world_forge/runlog.py — 运行日志：记录推进等流程的每个阶段，实时输出并持久化。

每次运行写入 <世界目录>/logs/<时间>_<类型>_<run_id>.jsonl，一行一条记录。
日志既可以通过 sink 回调实时显示（看板、命令行），也可以事后读取回看。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import cancel, repo, store

LEVEL_MARK = {"info": "·", "ok": "✓", "warn": "!", "error": "✗"}
_TASK = re.compile(r"^TASK: (\w+)", re.M)


HEARTBEAT_SECONDS = 15.0


def clock_now() -> str:
    """墙上时钟时间，精确到毫秒，例如 14:32:07.419。"""
    return time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"


class RunLog:
    def __init__(self, wid: str, kind: str, sink: Optional[Callable[[dict], None]] = None):
        self.wid = wid
        self.kind = kind
        self.sink = sink
        self.run_id = store.new_id("run")
        self.t0 = time.monotonic()
        self.entries: List[dict] = []
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.path: Path = repo.world_dir(wid) / "logs" / f"{stamp}_{kind}_{self.run_id}.jsonl"

    def log(self, stage: str, msg: str, level: str = "info", **data: Any) -> dict:
        entry = {"t": round(time.monotonic() - self.t0, 2), "time": store.now_iso(), "clock": clock_now(),
                 "level": level, "stage": stage, "msg": msg, "data": data}
        self.entries.append(entry)
        store.append_jsonl(self.path, entry)
        if self.sink is not None:
            self.sink(entry)
        return entry


def format_entry(e: dict) -> str:
    """每行：墙上时钟时间 + 从本次运行开始的秒数 + 级别 + 阶段 + 内容。"""
    mark = LEVEL_MARK.get(e.get("level", "info"), "·")
    clock = e.get("clock") or str(e.get("time", ""))[11:19] or "--:--:--"
    return f"{clock} [{e.get('t', 0):>8.2f}s] {mark} {e.get('stage', ''):<8} {e.get('msg', '')}"


def call_with_heartbeat(fn, log: Optional["RunLog"], label: str, *, timeout: Optional[float] = None,
                        beat: Optional[float] = None):
    """在后台线程中执行阻塞调用；期间每 beat 秒记录一次心跳，超过 timeout 秒则放弃等待。

    返回 (值, 异常, 是否超时)。超时后的后台线程会被丢弃（守护线程），不会阻塞后续步骤。
    """
    import threading

    beat = beat or HEARTBEAT_SECONDS
    box: dict = {}

    def runner():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - 异常原样交给调用方
            box["exc"] = exc

    th = threading.Thread(target=runner, daemon=True)
    t0 = time.monotonic()
    th.start()
    while True:
        remaining = None if timeout is None else timeout - (time.monotonic() - t0)
        wait = beat if remaining is None else min(beat, max(remaining, 0))
        th.join(wait)
        if not th.is_alive():
            return box.get("value"), box.get("exc"), False
        waited = time.monotonic() - t0
        if timeout is not None and waited >= timeout:
            if log is not None:
                log.log("wait", f"{label}：超过 {timeout:.0f} 秒仍未返回，已放弃等待（{clock_now()}）", level="warn")
            return None, None, True
        if log is not None:
            log.log("wait", f"{label}：仍在等待模型返回，已等待 {waited:.0f} 秒（{clock_now()}）")


class LoggedLLM:
    """包装任意 llm（需提供 ask(prompt, system=...)），记录每次模型调用的任务名、用时与输出长度。"""

    def __init__(self, llm, log: RunLog):
        self.llm = llm
        self.log = log
        self.n = 0

    def ask(self, prompt: str, system: str = "") -> str:
        cancel.checkpoint()   # 停止信号在模型调用前生效（调用本身不可中途打断）
        self.n += 1
        m = _TASK.search(prompt or "")
        task = m.group(1) if m else "模型"
        self.log.log("model", f"调用 #{self.n}（{task}）：提示词 {len(prompt or '')} 字，等待返回……")
        t = time.monotonic()
        label = f"调用 #{self.n}（{task}）"
        out, exc, _ = call_with_heartbeat(lambda: self.llm.ask(prompt, system=system), self.log, label)
        if exc is not None:
            self.log.log("model", f"{label}失败：{type(exc).__name__}：{exc}", level="error")
            raise exc
        self.log.log("model", f"{label}返回（{clock_now()}）：用时 {time.monotonic() - t:.1f} 秒，"
                              f"输出 {len(out or '')} 字", level="ok")
        return out


def list_runs(wid: str, kinds: Optional[set] = None) -> List[dict]:
    d = repo.world_dir(wid) / "logs"
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.jsonl"), reverse=True):
        parts = p.stem.split("_")
        kind = parts[1] if len(parts) > 1 else ""
        if kinds is not None and kind not in kinds:
            continue
        out.append({"path": str(p), "name": p.name, "kind": kind, "stamp": parts[0] if parts else ""})
    return out


def read_run(path: str) -> List[dict]:
    return store.read_jsonl(Path(path))
