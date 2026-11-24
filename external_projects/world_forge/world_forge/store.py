"""world_forge/store.py — 本地 JSON / JSONL 读写：原子写入、损坏行容错、串行化锁。

锁策略：同一线程内可重入；跨线程用 RLock；跨进程用 POSIX 文件锁（Windows 下退化为仅进程内锁）。
约定：锁只包住一次“读-改-写”，LLM 等长耗时调用必须在锁外完成。
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, List

try:  # pragma: no cover - 平台相关
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

_PROC_LOCK = threading.RLock()
_TLS = threading.local()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.is_file():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    _atomic_write_text(Path(path), json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_jsonl(path: Path) -> List[dict]:
    p = Path(path)
    if not p.is_file():
        return []
    out: List[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # 跳过损坏行，不让整个世界不可读
    return out


def write_jsonl(path: Path, records: List[dict]) -> None:
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    _atomic_write_text(Path(path), text)


def append_jsonl(path: Path, record: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


@contextlib.contextmanager
def locked(lock_file: Path) -> Iterator[None]:
    """串行化对同一世界的读-改-写。同线程重入时直接放行，避免自锁死。"""
    held = getattr(_TLS, "held", None)
    if held is None:
        held = _TLS.held = set()
    key = str(lock_file)
    if key in held:
        yield
        return
    with _PROC_LOCK:
        lock_file = Path(lock_file)
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_file, "a+") as fh:
            if fcntl is not None:
                fcntl.flock(fh, fcntl.LOCK_EX)
            held.add(key)
            try:
                yield
            finally:
                held.discard(key)
                if fcntl is not None:
                    fcntl.flock(fh, fcntl.LOCK_UN)
