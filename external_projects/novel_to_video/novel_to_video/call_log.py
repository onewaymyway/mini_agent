"""call_log.py — 外部 API 调用留痕（logs/api_calls.jsonl，追加）。不记录任何 key。"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def log_call(project_dir: Path, record: Dict[str, Any]) -> None:
    logs = project_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now().isoformat(timespec="seconds"), **record}
    with (logs / "api_calls.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# 增量计数缓存：路径 → {ident（设备号, inode）, offset（已统计到的字节）, total, by_kind}。
# 日志只追加，因此每次只需解析新增的完整行；文件被替换或截断时从头统计。
_COUNT_LOCK = threading.Lock()
_COUNT_CACHE: Dict[str, Dict[str, Any]] = {}


def count_calls(project_dir: Path, kind: str | None = None) -> int:
    """统计 API 调用次数（可按 kind 过滤）。增量读取，不会因日志变长而每次全量解析（驾驶舱每次刷新都会调用）。"""
    path = Path(project_dir) / "logs" / "api_calls.jsonl"
    if not path.is_file():
        return 0
    st = path.stat()
    ident = (st.st_dev, st.st_ino)
    key = str(path)
    with _COUNT_LOCK:
        c = _COUNT_CACHE.get(key)
        if c is None or c["ident"] != ident or st.st_size < c["offset"]:
            c = {"ident": ident, "offset": 0, "total": 0, "by_kind": {}}
        if st.st_size > c["offset"]:
            with path.open("rb") as f:
                f.seek(c["offset"])
                data = f.read(st.st_size - c["offset"])
            end = data.rfind(b"\n")          # 只统计完整的行；末尾未写完的行留到下次
            if end >= 0:
                for raw in data[:end].split(b"\n"):
                    if not raw.strip():
                        continue
                    c["total"] += 1
                    try:
                        k = json.loads(raw).get("kind")
                    except ValueError:        # 进程被中途终止时可能残留一行坏数据：跳过
                        k = None
                    c["by_kind"][k] = c["by_kind"].get(k, 0) + 1
                c["offset"] += end + 1
        _COUNT_CACHE[key] = c
        if kind is None:
            return c["total"]
        return c["by_kind"].get(kind, 0)
