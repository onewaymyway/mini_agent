"""
tool_executor.py — 工具执行器

职责：
- 权限检查（PermissionGuard）
- 工具调用（ToolRegistry）
- 结果截断（按工具类型分策略）
- 结果缓存（ToolResultCache）
- 文件变化注册（FileWatcher）
- 工具统计（SessionStats）

从 Agent 中拆出，Agent 只需持有一个 ToolExecutor 实例并调用 execute_all()。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

import mini_agent.ui.renderer as R

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from mini_agent.llm import LLMResponse
    from mini_agent.permissions import PermissionGuard
    from mini_agent.tools import ToolRegistry
    from mini_agent.perception.tool_cache import ToolResultCache
    from mini_agent.perception.file_watcher import FileWatcher
    from mini_agent.config import SessionStats


class ToolExecutor:
    """
    执行一批工具调用并返回结果字符串列表。
    所有副作用（权限、缓存、统计、文件追踪）都在这里管理。
    """

    def __init__(
        self,
        cfg: "AppConfig",
        registry: "ToolRegistry",
        guard: "PermissionGuard",
        stats: "SessionStats",
        tool_cache: Optional["ToolResultCache"] = None,
        file_watcher: Optional["FileWatcher"] = None,
        file_changes_list: Optional[list] = None,   # _pending_file_changes 的引用
        file_changes_lock=None,
    ) -> None:
        self.cfg = cfg
        self.registry = registry
        self.guard = guard
        self.stats = stats
        self.tool_cache = tool_cache
        self.file_watcher = file_watcher
        self._pending_file_changes = file_changes_list  # 共享引用
        self._file_changes_lock = file_changes_lock

    def execute_all(self, response: "LLMResponse") -> tuple[list, list[str]]:
        """
        执行 response 中所有工具调用，返回 (tool_calls, result_strs)。
        """
        result_strs: list[str] = []

        for tc in response.tool_calls:
            R.print_tool_call(tc.name, tc.input, verbose=self.cfg.verbose)
            self.stats.tool_calls += 1

            allowed = self.guard.check(tc.name, tc.input)
            if not allowed:
                result_str = "[Tool call denied by user]"
                R.print_tool_error(tc.name, "denied by user")
                if self.cfg.tool_stats_enabled:
                    self.stats.record_tool_call(tc.name, False, 0)
            else:
                # [SYS-TOOLCACHE] 检查缓存
                cached = None
                if self.tool_cache:
                    cached = self.tool_cache.get(tc.name, tc.input)

                if cached is not None:
                    result_str = cached
                    R.print_tool_result(tc.name, f"[cache] {result_str[:80]}...")
                    if self.cfg.tool_stats_enabled:
                        self.stats.record_tool_call(tc.name, True, len(result_str))
                else:
                    try:
                        result = self.registry.call(tc.name, tc.input)
                        result_str = str(result) if not isinstance(result, str) else result

                        # [SYS-TRIM] 工具调用结果截断（按工具类型分策略）
                        result_str = self._trim_result(tc.name, result_str)

                        R.print_tool_result(tc.name, result_str)

                        # [SYS-TOOLCACHE] 写入缓存
                        if self.tool_cache:
                            self.tool_cache.put(tc.name, tc.input, result_str)

                        # [SYS-WATCH] 注册 read_file 的文件（供后台线程追踪）
                        if self.file_watcher and tc.name == "read_file":
                            _path = tc.input.get("path", "")
                            if _path:
                                self.file_watcher.register(_path, result_str)

                        if self.cfg.tool_stats_enabled:
                            self.stats.record_tool_call(tc.name, True, len(result_str))
                    except Exception as e:
                        result_str = f"[tool error: {e}]"
                        R.print_tool_error(tc.name, str(e))
                        if self.cfg.tool_stats_enabled:
                            self.stats.record_tool_call(tc.name, False, 0)

            result_strs.append(result_str)

        return response.tool_calls, result_strs

    def _trim_result(self, tool_name: str, result: str) -> str:
        """
        [SYS-TRIM] 按工具类型分策略截断长结果，策略参数可通过 config 调整。
        """
        if not self.cfg.tool_result_trim_enabled:
            return result
        threshold = self.cfg.tool_result_trim_threshold
        if len(result) <= threshold:
            return result

        lines = result.splitlines()
        total = len(lines)

        if tool_name == "bash":
            head_ratio = getattr(self.cfg, "tool_trim_bash_head_ratio", 0.7)
            if total > 20:
                head_n = max(12, int(total * head_ratio))
                tail_n = max(4, total - head_n)
                head_n = min(head_n, total - tail_n)
                omitted = total - head_n - tail_n
                if omitted > 0:
                    return (
                        "\n".join(lines[:head_n])
                        + f"\n... [{omitted} lines omitted] ...\n"
                        + "\n".join(lines[-tail_n:])
                    )

        elif tool_name == "read_file":
            window = getattr(self.cfg, "tool_trim_read_window_lines", 0)
            if window == 0:
                window = min(total, max(30, threshold // 60))
            if total > 30 and window < total:
                head_n = window // 2
                tail_n = window - head_n
                omitted = total - head_n - tail_n
                return (
                    "\n".join(lines[:head_n])
                    + f"\n... [{omitted} lines omitted — use start_line/end_line to read specific range] ...\n"
                    + "\n".join(lines[-tail_n:])
                )

        elif tool_name in ("grep", "glob"):
            max_lines = getattr(self.cfg, "tool_trim_grep_max_lines", 50)
            if total > max_lines:
                omitted = total - max_lines
                return (
                    "\n".join(lines[:max_lines])
                    + f"\n... [{omitted} more matches omitted] ..."
                )

        # 通用策略
        if total > 30:
            head_n, tail_n = 15, 5
            omitted = total - head_n - tail_n
            if omitted > 0:
                return (
                    "\n".join(lines[:head_n])
                    + f"\n... [{omitted} lines omitted] ...\n"
                    + "\n".join(lines[-tail_n:])
                )

        return result[:threshold] + f"\n... [{len(result)-threshold} chars omitted]"
