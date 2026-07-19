"""
perception/hot_reload.py — Skill / Agent Profile 热重载

设计目标：
  当用户在 skills/ 或 .agent/agents/ 目录中新增、修改、删除 .md 文件时，
  Agent 无需重启即可立即感知变化并重新加载。

工作方式：
  纯 mtime 轮询，无 inotify/watchdog 外部依赖。
  - 在 _agentic_loop 每个 turn 开始时调用 poll()
  - poll() 扫描所有受监视目录，对比 mtime + 文件集合变化
  - 有变化时触发注册的回调（SkillLoader._rediscover / AgentProfileLoader._rediscover）
  - 返回 ChangeReport 供 agent 打印变更通知

设计取舍：
  - 轮询间隔由 min_interval_s 控制（默认 2s），避免每次 turn 都做 I/O
  - 只扫描 .md 文件（skills/agents 均为此格式），stat() 成本可忽略
  - 不使用后台线程，完全同步，避免并发问题

可扩展性：
  通过 register() 挂载任意「目录集合 → reload 回调」，不仅限于 skills/agents。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


@dataclass
class ChangeReport:
    """一次 poll() 扫描的变更摘要。"""
    added:    list[str] = field(default_factory=list)   # 新增的文件名（stem）
    modified: list[str] = field(default_factory=list)   # 修改的文件名（stem）
    removed:  list[str] = field(default_factory=list)   # 删除的文件名（stem）
    category: str = ""                                   # 标签：如 "skill" / "agent"

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.modified or self.removed)

    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append(f"+{len(self.added)} added ({', '.join(self.added)})")
        if self.modified:
            parts.append(f"~{len(self.modified)} modified ({', '.join(self.modified)})")
        if self.removed:
            parts.append(f"-{len(self.removed)} removed ({', '.join(self.removed)})")
        prefix = f"[{self.category}] " if self.category else ""
        return prefix + "; ".join(parts) if parts else "no changes"


# ── 单个监视器（对应一组目录 + 一个重载回调）─────────────────────────────────

class _DirectoryWatch:
    """
    监视一组目录内 .md 文件的 mtime，有变化时调用 reload_fn()。
    reload_fn(dirs) 应重新扫描目录并返回新的 catalog（可为 None）。
    """

    def __init__(
        self,
        dirs: list[Path],
        reload_fn: Callable[[list[Path]], None],
        category: str = "",
        glob_pattern: str = "**/*.md",
    ) -> None:
        self._dirs = dirs
        self._reload_fn = reload_fn
        self._category = category
        self._glob = glob_pattern
        # path → mtime 快照
        self._snapshot: dict[Path, float] = {}
        self._take_snapshot()   # 初始快照，建立基线

    def _scan(self) -> dict[Path, float]:
        """扫描所有目录，返回 path → mtime 映射。"""
        result: dict[Path, float] = {}
        for d in self._dirs:
            if not d.is_dir():
                continue
            for p in d.glob(self._glob):
                try:
                    result[p] = p.stat().st_mtime
                except OSError:
                    pass
        return result

    def _take_snapshot(self) -> None:
        self._snapshot = self._scan()

    def check(self) -> ChangeReport:
        """对比当前状态与快照，返回变更报告（并更新快照 + 触发 reload）。"""
        current = self._scan()

        old_paths = set(self._snapshot)
        new_paths = set(current)

        added_paths    = new_paths - old_paths
        removed_paths  = old_paths - new_paths
        modified_paths = {
            p for p in old_paths & new_paths
            if current[p] != self._snapshot[p]
        }

        report = ChangeReport(
            added    = sorted(p.stem for p in added_paths),
            modified = sorted(p.stem for p in modified_paths),
            removed  = sorted(p.stem for p in removed_paths),
            category = self._category,
        )

        if report.has_changes:
            self._snapshot = current
            try:
                self._reload_fn(self._dirs)
            except Exception as exc:
                # reload 失败不应崩溃 agent，打印警告即可
                from mini_agent.errors import log_exception
                log_exception(exc, where='mini_agent.perception.hot_reload._DirectoryWatch.check')
                import mini_agent.ui.renderer as _R
                _R.print_warning(f"[hot-reload:{self._category}] reload error: {exc}")

        return report


# ── 主类 ──────────────────────────────────────────────────────────────────────

class HotReloader:
    """
    统一管理多个 _DirectoryWatch 的轮询调度。

    用法：
        reloader = HotReloader(min_interval_s=2.0)
        reloader.register(
            dirs=[skills_dir],
            reload_fn=skill_loader.rediscover,
            category="skill",
        )
        reloader.register(
            dirs=[agents_dir],
            reload_fn=profile_loader.rediscover,
            category="agent",
        )

        # 在每个 turn 开始时调用（有 debounce 保护）
        reports = reloader.poll()
        for r in reports:
            if r.has_changes:
                print(r.summary())

        # 手动强制 reload（/reload 命令触发）
        reports = reloader.force_reload()
    """

    def __init__(self, min_interval_s: float = 2.0) -> None:
        self._min_interval = min_interval_s
        self._last_poll: float = 0.0
        self._watches: list[_DirectoryWatch] = []

    def register(
        self,
        dirs: list[Path],
        reload_fn: Callable[[list[Path]], None],
        category: str = "",
        glob_pattern: str = "**/*.md",
    ) -> None:
        """注册一组目录 + reload 回调。可多次调用挂载不同的目录集合。"""
        self._watches.append(_DirectoryWatch(
            dirs=dirs,
            reload_fn=reload_fn,
            category=category,
            glob_pattern=glob_pattern,
        ))

    def poll(self) -> list[ChangeReport]:
        """
        有 debounce 保护的轮询。距上次调用不足 min_interval_s 时直接返回空列表。
        有变化时触发对应 reload_fn 并返回所有非空 ChangeReport。
        """
        now = time.monotonic()
        if now - self._last_poll < self._min_interval:
            return []
        self._last_poll = now
        return self._check_all()

    def force_reload(self) -> list[ChangeReport]:
        """
        跳过 debounce，强制扫描所有监视目录。供 /reload 命令使用。
        即使没有文件变化，也会重新执行 reload_fn（用于手动刷新场景）。
        """
        self._last_poll = time.monotonic()
        reports = []
        for watch in self._watches:
            # 强制：把快照清空，让所有文件都被视为"新增"，从而触发 reload
            current = watch._scan()
            watch._snapshot = {}
            r = watch.check()
            # 强制模式下即使没有文件级变化也应触发 reload；
            # 若 check() 因为快照为空而全报 added，那其实已经触发了。
            # 再次把快照设回 current，避免下次 poll 误报。
            watch._snapshot = current
            # 但我们需要确保 reload_fn 已被调用——check() 在 added 非空时已调用
            if not r.has_changes:
                # 没有文件变化时手动调用 reload（例如目录本身是空的）
                try:
                    watch._reload_fn(watch._dirs)
                except Exception as exc:
                    from mini_agent.errors import log_exception
                    log_exception(exc, where='mini_agent.perception.hot_reload.HotReloader.force_reload')
                    import mini_agent.ui.renderer as _R
                    _R.print_warning(f"[hot-reload:{watch._category}] reload error: {exc}")
            reports.append(r)
        return reports

    def _check_all(self) -> list[ChangeReport]:
        return [w.check() for w in self._watches]

    @property
    def has_watches(self) -> bool:
        return bool(self._watches)


__all__ = ["HotReloader", "ChangeReport"]
