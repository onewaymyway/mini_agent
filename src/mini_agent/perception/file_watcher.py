"""
perception/file_watcher.py — 文件变化感知。

追踪 agent 在本次 session 中读取过的文件。
每次新 turn 开始前调用 check_changes()，返回被外部修改的路径列表。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional


class FileWatcher:
    """
    轻量文件哈希缓存。无线程，无 inotify，纯按需检查。

    lifecycle：
        watcher.register(path, content)     # 在 read_file 工具执行后调用
        changed = watcher.check_changes()   # 每个 turn 开始时调用
    """

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}   # abs_path → md5

    def register(self, path: str, content: str) -> None:
        """记录文件当前内容的哈希。"""
        key = str(Path(path).resolve())
        self._cache[key] = _md5(content)

    def unregister(self, path: str) -> None:
        """显式移除某个文件的追踪（如文件已被 agent 删除）。"""
        key = str(Path(path).resolve())
        self._cache.pop(key, None)

    def check_changes(self) -> list[str]:
        """
        检查所有已注册文件，返回被外部修改（或删除）的路径列表。
        同时更新缓存，避免重复报告同一文件。
        """
        changed: list[str] = []
        for abs_path, old_hash in list(self._cache.items()):
            p = Path(abs_path)
            if not p.exists():
                changed.append(abs_path)
                del self._cache[abs_path]
                continue
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
                new_hash = _md5(content)
                if new_hash != old_hash:
                    changed.append(abs_path)
                    self._cache[abs_path] = new_hash
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.perception.file_watcher')
                pass
        return changed

    def build_change_notice(self, changed: list[str], project_root: Optional[Path] = None) -> str:
        """
        生成注入 system prompt 的变更提示文本。
        路径尽量显示相对路径（如果在 project_root 内）。
        """
        if not changed:
            return ""
        paths = []
        for p in changed:
            try:
                rel = str(Path(p).relative_to(project_root)) if project_root else p
            except ValueError:
                rel = p
            paths.append(f"`{rel}`")
        return (
            "\n⚠️  The following files were modified externally since last read: "
            + ", ".join(paths)
            + "\nRe-read them before making assumptions about their content."
        )

    def clear(self) -> None:
        self._cache.clear()

    @property
    def tracked_count(self) -> int:
        return len(self._cache)


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()
