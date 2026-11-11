"""纯文本存储工具：原子写入、JSON/JSONL 读写、文件锁。

所有产出都是人可直接阅读的文本（json / jsonl / markdown），不使用数据库。
写入统一采用"临时文件 + os.replace"，保证读者永远看到完整文件；
并发写入通过 `file_lock` 串行化（基于 fcntl.flock，适用于 Linux / macOS / Termux）。
"""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

try:  # Windows 下 fcntl 不存在，退化为无锁（单进程使用场景可接受）
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """原子写入文本文件：先写同目录临时文件，再替换目标。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, obj: Any) -> None:
    """原子写入 JSON，使用 2 空格缩进、保留中文，便于人工阅读。"""
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    atomic_write_text(path, text)


def read_json(path: Path, default: Any = None) -> Any:
    """读取 JSON，文件不存在时返回 default。文件损坏时抛出异常，不静默吞掉。"""
    path = Path(path)
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def append_jsonl(path: Path, obj: Any) -> None:
    """追加一行 JSON 到 jsonl 文件。调用方应在 file_lock 内使用以避免交错。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, sort_keys=False)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_jsonl(path: Path) -> Iterator[Any]:
    """逐行读取 jsonl，跳过空行。文件不存在时不产生任何元素。"""
    path = Path(path)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


@contextmanager
def file_lock(target: Path) -> Iterator[None]:
    """对 target 对应的 `.lock` 文件加排他锁。阻塞直到获得锁。"""
    target = Path(target)
    lock_path = target.with_name(target.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as fh:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
