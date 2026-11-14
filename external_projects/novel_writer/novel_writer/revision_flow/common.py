"""revision_flow 公共工具：路径、JSON、哈希、错误类型。"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, List

from novel_writer.fsutil import atomic_write_json, read_json

VERSIONS_DIR = "_versions"
REVISION_DIR = "_revision"
# 完稿内容目录：版本快照只复制这些目录（不含 _studio 运行状态、导出产物与项目状态）
CONTENT_DIRS: tuple = ("大纲规划", "正文", "章节总结", "一致性检查", "修订记录")
VERSION_RE = re.compile(r"^v(\d{3})$")


class FlowError(ValueError):
    """修改流程的业务错误（向调用方返回可读信息）。"""


def versions_root(ws) -> Path:
    return ws.root / VERSIONS_DIR


def revision_root(ws) -> Path:
    return ws.root / REVISION_DIR


def version_dir(ws, vid: str) -> Path:
    if not VERSION_RE.match(vid or ""):
        raise FlowError(f"版本号格式错误：{vid!r}")
    return versions_root(ws) / vid


def sub_dir(ws, name: str) -> Path:
    d = revision_root(ws) / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_json(path: Path, default: Any = None) -> Any:
    return read_json(path, default=default)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, data)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def list_content_files(root: Path) -> List[str]:
    """返回 root 下完稿内容目录里的全部文件相对路径（posix，已排序）。"""
    out: List[str] = []
    for name in CONTENT_DIRS:
        base = root / name
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_file():
                out.append(p.relative_to(root).as_posix())
    return sorted(out)


def make_writable(path: Path) -> None:
    """递归去掉写权限以外的修改：目录与文件都恢复可写（用于复制出的派生版本）。"""
    import os
    for p in [path, *path.rglob("*")]:
        try:
            mode = os.stat(p).st_mode
            os.chmod(p, mode | 0o200)
        except OSError:
            pass


def make_readonly(root: Path) -> None:
    import os
    for p in root.rglob("*"):
        if p.is_file():
            try:
                os.chmod(p, 0o444)
            except OSError:
                pass


def new_id(prefix: str, now_iso: str) -> str:
    stamp = re.sub(r"[^0-9]", "", now_iso)[:14]
    return f"{prefix}_{stamp}"


def require_ids(items: Iterable[Any]) -> None:
    for x in items:
        if not isinstance(x, str) or not x.strip():
            raise FlowError("列表项必须是非空字符串")
