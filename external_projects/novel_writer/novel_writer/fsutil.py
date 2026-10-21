"""novel_writer/fsutil.py — 原子写文件等小工具（工作目录内所有 JSON 状态文件都走这里）。"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

# Windows 上目标文件若被其它进程（界面读取、杀毒、另一个 worker）短暂占用，os.replace / 打开会抛
# PermissionError。这是瞬时竞争，退避重试即可；重试仍失败才向上抛出。
_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)


def _replace_with_retry(src: str, dst: Path) -> None:
    last: Exception | None = None
    for delay in (0.0,) + _RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            last = exc
    raise last  # type: ignore[misc]


def atomic_write_text(path: Path, text: str) -> None:
    """先写同目录临时文件再 `os.replace`，避免崩溃/并发读到写了一半的文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        _replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    """读取 JSON；文件不存在或损坏时返回 default（不抛异常，调用方自行判断）。

    遇到 PermissionError（文件被其它进程短暂占用）会退避重试，而不是直接当作「没有这个文件」。
    """
    path = Path(path)
    last: Exception | None = None
    for delay in (0.0,) + _RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except PermissionError as exc:
            last = exc
            continue
        except (OSError, ValueError):
            return default
    if last is not None:
        raise last
    return default
