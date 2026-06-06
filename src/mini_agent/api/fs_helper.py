"""
api/fs_helper.py — 文件系统操作封装

所有路径操作都被 jail 在 project_root 内，禁止 ../ 越级。
fs_readonly=True 时所有写操作抛 PermissionError。
"""

from __future__ import annotations

import base64
import fnmatch
import mimetypes
import os
import shutil
import time
from pathlib import Path
from typing import Optional

from .models import FileEntry, FsListResponse, FsReadResponse, FsStatResponse


class FsHelper:
    """文件系统操作封装，所有路径都被限制在 project_root 内。"""

    # 默认敏感文件黑名单（glob 模式）
    DEFAULT_EXCLUDES = [
        "*.key", "*.pem", "*.p12", "*.pfx",
        ".env", ".env.*",
        "agent_api.key",
    ]

    def __init__(
        self,
        project_root: Path,
        readonly: bool = False,
        excludes: Optional[list[str]] = None,
    ) -> None:
        self._root    = project_root.resolve()
        self._readonly = readonly
        self._excludes = excludes if excludes is not None else self.DEFAULT_EXCLUDES

    # ── 路径安全 ──────────────────────────────────────────────────────────

    def _safe_path(self, rel: str) -> Path:
        """
        解析相对路径为绝对路径，确保在 project_root 内。
        抛 ValueError 表示越界；抛 PermissionError 表示命中黑名单。
        """
        # 去掉开头的 /，避免 Path('/') / '/abs' = '/abs' 绕过 jail
        clean = rel.lstrip("/").lstrip("\\")
        full  = (self._root / clean).resolve()

        # Jail 检查
        try:
            full.relative_to(self._root)
        except ValueError:
            raise ValueError(f"Path {rel!r} escapes project root")

        # 黑名单检查
        name = full.name
        for pat in self._excludes:
            if fnmatch.fnmatch(name, pat):
                raise PermissionError(f"Access to {name!r} is not allowed")

        return full

    def _rel(self, full: Path) -> str:
        """绝对路径 → 相对于 project_root 的字符串。"""
        return str(full.relative_to(self._root))

    def _check_write(self) -> None:
        if self._readonly:
            raise PermissionError("Filesystem is read-only (fs_readonly=true)")

    # ── 目录列表 ──────────────────────────────────────────────────────────

    def list_dir(self, rel_path: str = "") -> FsListResponse:
        target = self._safe_path(rel_path) if rel_path else self._root

        if not target.exists():
            raise FileNotFoundError(f"{rel_path!r} does not exist")
        if not target.is_dir():
            raise NotADirectoryError(f"{rel_path!r} is not a directory")

        entries: list[FileEntry] = []
        for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            # 跳过黑名单文件
            skip = False
            for pat in self._excludes:
                if fnmatch.fnmatch(item.name, pat):
                    skip = True
                    break
            if skip:
                continue
            try:
                st = item.stat()
                entries.append(FileEntry(
                    name   = item.name,
                    path   = self._rel(item),
                    is_dir = item.is_dir(),
                    size   = st.st_size if item.is_file() else 0,
                    mtime  = st.st_mtime,
                ))
            except OSError:
                continue

        return FsListResponse(
            path    = self._rel(target) if target != self._root else "",
            entries = entries,
            total   = len(entries),
        )

    # ── 读文件 ────────────────────────────────────────────────────────────

    def read_file(self, rel_path: str) -> FsReadResponse:
        full = self._safe_path(rel_path)

        if not full.exists():
            raise FileNotFoundError(f"{rel_path!r} does not exist")
        if full.is_dir():
            raise IsADirectoryError(f"{rel_path!r} is a directory")

        size = full.stat().st_size

        # 尝试 UTF-8 文本读取；失败则 base64
        try:
            content = full.read_text(encoding="utf-8")
            return FsReadResponse(
                path=self._rel(full), content=content,
                encoding="utf-8", size=size,
            )
        except (UnicodeDecodeError, ValueError):
            content = base64.b64encode(full.read_bytes()).decode("ascii")
            return FsReadResponse(
                path=self._rel(full), content=content,
                encoding="base64", size=size,
            )

    # ── 写文件 ────────────────────────────────────────────────────────────

    def write_file(self, rel_path: str, content: str, encoding: str = "utf-8") -> None:
        self._check_write()
        full = self._safe_path(rel_path)
        full.parent.mkdir(parents=True, exist_ok=True)

        if encoding == "base64":
            full.write_bytes(base64.b64decode(content))
        else:
            full.write_text(content, encoding="utf-8")

    # ── 目录创建 ──────────────────────────────────────────────────────────

    def mkdir(self, rel_path: str) -> None:
        self._check_write()
        full = self._safe_path(rel_path)
        full.mkdir(parents=True, exist_ok=True)

    # ── 删除 ──────────────────────────────────────────────────────────────

    def delete(self, rel_path: str, recursive: bool = False) -> None:
        self._check_write()
        full = self._safe_path(rel_path)

        if not full.exists():
            raise FileNotFoundError(f"{rel_path!r} does not exist")
        if full.is_dir():
            if not recursive:
                raise IsADirectoryError(
                    f"{rel_path!r} is a directory; set recursive=true to delete"
                )
            shutil.rmtree(full)
        else:
            full.unlink()

    # ── 重命名/移动 ───────────────────────────────────────────────────────

    def rename(self, src: str, dst: str) -> None:
        self._check_write()
        full_src = self._safe_path(src)
        full_dst = self._safe_path(dst)

        if not full_src.exists():
            raise FileNotFoundError(f"{src!r} does not exist")
        full_dst.parent.mkdir(parents=True, exist_ok=True)
        full_src.rename(full_dst)

    # ── 文件详情 ──────────────────────────────────────────────────────────

    def stat(self, rel_path: str) -> FsStatResponse:
        full = self._safe_path(rel_path)
        if not full.exists():
            return FsStatResponse(
                path=rel_path, is_dir=False, size=0, mtime=0.0, exists=False
            )
        st = full.stat()
        return FsStatResponse(
            path   = self._rel(full),
            is_dir = full.is_dir(),
            size   = st.st_size,
            mtime  = st.st_mtime,
            exists = True,
        )

    # ── 搜索 ──────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        search_content: bool = False,
        max_results: int = 50,
    ) -> list[FileEntry]:
        """按文件名（和可选文件内容）搜索，结果限制在 max_results 条。"""
        results: list[FileEntry] = []
        q_lower = query.lower()

        for full in self._root.rglob("*"):
            if len(results) >= max_results:
                break
            # 跳过黑名单
            skip = any(fnmatch.fnmatch(full.name, pat) for pat in self._excludes)
            if skip:
                continue

            matched = q_lower in full.name.lower()

            if not matched and search_content and full.is_file():
                try:
                    text = full.read_text(encoding="utf-8", errors="ignore")
                    matched = q_lower in text.lower()
                except OSError:
                    pass

            if matched:
                try:
                    st = full.stat()
                    results.append(FileEntry(
                        name   = full.name,
                        path   = self._rel(full),
                        is_dir = full.is_dir(),
                        size   = st.st_size if full.is_file() else 0,
                        mtime  = st.st_mtime,
                    ))
                except OSError:
                    pass

        return results
