"""world_forge/runlog.py — 运行日志：记录推进等流程的每个阶段，实时输出并持久化。

每次运行写入 <世界目录>/logs/<时间>_<类型>_<run_id>.jsonl，一行一条记录。
日志既可以通过 sink 回调实时显示（看板、命令行），也可以事后读取回看。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import repo, store

LEVEL_MARK = {"info": "·", "ok": "✓", "warn": "!", "error": "✗"}
_TASK = re.compile(r"^TASK: (\w+)", re.M)


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
        entry = {"t": round(time.monotonic() - self.t0, 2), "time": store.now_iso(), "level": level,
                 "stage": stage, "msg": msg, "data": data}
        self.entries.append(entry)
        store.append_jsonl(self.path, entry)
        if self.sink is not None:
            self.sink(entry)
        return entry


def format_entry(e: dict) -> str:
    mark = LEVEL_MARK.get(e.get("level", "info"), "·")
    return f"[{e.get('t', 0):>8.2f}s] {mark} {e.get('stage', ''):<8} {e.get('msg', '')}"


class LoggedLLM:
    """包装任意 llm（需提供 ask(prompt, system=...)），记录每次模型调用的任务名、用时与输出长度。"""

    def __init__(self, llm, log: RunLog):
        self.llm = llm
        self.log = log
        self.n = 0

    def ask(self, prompt: str, system: str = "") -> str:
        self.n += 1
        m = _TASK.search(prompt or "")
        task = m.group(1) if m else "模型"
        self.log.log("model", f"调用 #{self.n}（{task}）：提示词 {len(prompt or '')} 字，等待返回……")
        t = time.monotonic()
        try:
            out = self.llm.ask(prompt, system=system)
        except Exception as exc:
            self.log.log("model", f"调用 #{self.n}（{task}）失败：{type(exc).__name__}：{exc}", level="error")
            raise
        self.log.log("model", f"调用 #{self.n}（{task}）返回：用时 {time.monotonic() - t:.1f} 秒，"
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
