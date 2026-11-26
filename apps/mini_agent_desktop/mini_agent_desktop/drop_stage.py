"""E4：拖入快速面板的文件暂存（纯逻辑，不依赖 Qt）。

流程：拖入的路径 → 复制到 <根目录>/<批次目录>/ → 发送时把目录路径写进消息，告诉 agent 去那里读取。
约束：
  - 只复制，不引用原文件，也不修改原文件；
  - 不跟随符号链接（跳过并说明原因）；
  - 数量与总大小有上限，超出部分进入 skipped；
  - 过期批次目录由 cleanup_old 清理，且只动根目录下的子目录。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

DROP_DIR_NAME = "mini_agent_drop"
MAX_FILES = 20
MAX_TOTAL_BYTES = 200 * 1024 * 1024
DEFAULT_MAX_AGE_HOURS = 24.0


@dataclass(frozen=True)
class StagedFile:
    rel: str        # 相对批次目录的路径（文件夹内文件保留层级）
    size: int


@dataclass
class StagedBatch:
    dir: Optional[Path] = None
    files: list[StagedFile] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (路径, 原因)

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)


def default_drop_root() -> Path:
    """Windows：%TEMP%\\mini_agent_drop；其它系统：系统临时目录下同名子目录。"""
    base = os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir()
    return Path(base) / DROP_DIR_NAME


class _Budget:
    def __init__(self, max_files: int, max_bytes: int) -> None:
        self.files_left = max_files
        self.bytes_left = max_bytes


def _copy_one(src: Path, dst: Path, rel: str, budget: _Budget, batch: StagedBatch) -> None:
    if src.is_symlink():
        batch.skipped.append((str(src), "符号链接，未跟随"))
        return
    try:
        size = src.stat().st_size
    except OSError as exc:
        batch.skipped.append((str(src), f"无法读取：{exc}"))
        return
    if budget.files_left <= 0:
        batch.skipped.append((str(src), f"超过数量上限（{MAX_FILES} 个）"))
        return
    if size > budget.bytes_left:
        batch.skipped.append((str(src), "超过总大小上限"))
        return
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    except OSError as exc:
        batch.skipped.append((str(src), f"复制失败：{exc}"))
        return
    budget.files_left -= 1
    budget.bytes_left -= size
    batch.files.append(StagedFile(rel=rel.replace(os.sep, "/"), size=size))


def stage_paths(paths: Iterable[str | Path], base_dir: Path, *,
                max_files: int = MAX_FILES, max_total_bytes: int = MAX_TOTAL_BYTES,
                now: Optional[float] = None) -> StagedBatch:
    """把拖入的路径复制到新批次目录。没有任何可复制项时不创建目录，dir 为 None。"""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now if now is not None else time.time()))
    target = Path(base_dir) / f"{stamp}-{uuid.uuid4().hex[:8]}"
    budget = _Budget(max_files, max_total_bytes)
    batch = StagedBatch()
    used_names: set[str] = set()

    for raw in paths:
        src = Path(raw)
        if src.is_symlink():
            batch.skipped.append((str(src), "符号链接，未跟随"))
            continue
        if not src.exists():
            batch.skipped.append((str(src), "不存在"))
            continue
        if src.is_dir():
            # 文件夹：递归复制，层级保留在批次目录下的同名文件夹中
            root_name = _unique(src.name, used_names)
            for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
                dirnames[:] = [d for d in dirnames if not Path(dirpath, d).is_symlink()]
                for fn in sorted(filenames):
                    f = Path(dirpath, fn)
                    rel = Path(root_name, f.relative_to(src))
                    if batch.dir is None:
                        batch.dir = target
                    _copy_one(f, target / rel, str(rel), budget, batch)
        elif src.is_file():
            name = _unique(src.name, used_names)
            if batch.dir is None:
                batch.dir = target
            _copy_one(src, target / name, name, budget, batch)
        else:
            batch.skipped.append((str(src), "不是普通文件或文件夹"))

    if batch.dir is None:
        return batch
    if not batch.files:
        shutil.rmtree(batch.dir, ignore_errors=True)
        batch.dir = None
    return batch


def _unique(name: str, used: set[str]) -> str:
    """同名顶层项追加序号，避免相互覆盖。"""
    candidate, n = name, 2
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    while candidate in used:
        candidate = f"{stem} ({n}){'.' + ext if ext else ''}"
        n += 1
    used.add(candidate)
    return candidate


def build_message(user_text: str, batch: StagedBatch) -> str:
    """把批次信息放在用户原文前，告诉 agent 去临时目录读取。没有文件时原样返回用户文字。"""
    if batch.dir is None or not batch.files:
        return user_text
    lines = [
        f"[用户从桌面拖入了 {len(batch.files)} 个文件，已复制到临时目录：{batch.dir}]",
        "请从该目录读取这些文件（它们是副本，不是原文件；不要假定可以修改原文件）：",
    ]
    for f in batch.files[:50]:
        lines.append(f"- {f.rel}（{_fmt_size(f.size)}）")
    if len(batch.files) > 50:
        lines.append(f"- …另有 {len(batch.files) - 50} 个文件")
    if batch.skipped:
        lines.append(f"（另有 {len(batch.skipped)} 项未复制：{batch.skipped[0][1]} 等）")
    return "\n".join(lines) + "\n\n" + (user_text or "请先看看这些文件，并告诉我你发现了什么。")


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / 1024 / 1024:.1f}MB"


def cleanup_old(base_dir: Path, *, max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                now: Optional[float] = None) -> int:
    """删除超过 max_age_hours 的批次目录，返回删除数量。只处理 base_dir 的直接子目录。"""
    base = Path(base_dir)
    if not base.is_dir():
        return 0
    cutoff = (now if now is not None else time.time()) - max_age_hours * 3600
    removed = 0
    for child in base.iterdir():
        if child.is_symlink() or not child.is_dir():
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed
