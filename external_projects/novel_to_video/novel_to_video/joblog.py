"""joblog.py — 项目级运行日志（追加写入 logs/job.jsonl），供界面实时展示。

每条记录：{ts, scope, level, message, ...}。scope 用于区分来源页面：
  assets（全书素材）、chapter（章节流水线）、queue（批量队列）、final（全书合成）、gender（性别确认）
写入加锁，多线程安全；读取时容忍不完整的行（运行中途被截断）。
"""

from __future__ import annotations

import json
import threading
from collections import deque
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
    return f"{ts} {mark}{row.get('message', '')}"
