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
        self._mcp_manager = None  # 由 Agent 在 _init_components 后注入

    def execute_all(self, response: "LLMResponse") -> tuple[list, list[str]]:
        """
        执行 response 中所有工具调用，返回 (tool_calls, result_strs)。
        """
        result_strs: list[str] = []

        for tc in response.tool_calls:
            R.print_tool_call(tc.name, tc.input, verbose=self.cfg.verbose)
            self.stats.tool_calls += 1

            # [SYS-HOOKS] PreToolUse：可阻断或修改工具输入
            from mini_agent.hooks import get_hook_manager
            hook_mgr = get_hook_manager()
            tool_input = tc.input
            if hook_mgr is not None:
                pre = hook_mgr.run("PreToolUse", {"tool_name": tc.name, "tool_input": tool_input}, tool_name=tc.name)
                if pre.blocked:
                    result_str = f"[blocked by hook: {pre.reason or 'PreToolUse hook denied'}]"
                    R.print_tool_error(tc.name, pre.reason or "blocked by PreToolUse hook")
                    if self.cfg.tool_stats_enabled:
                        self.stats.record_tool_call(tc.name, False, 0)
                    result_strs.append(result_str)
                    continue
                if pre.modified_input:
                    tool_input = pre.modified_input

            allowed = self.guard.check(tc.name, tool_input)
            if not allowed:
                result_str = "[Tool call denied by user]"
                R.print_tool_error(tc.name, "denied by user")
                if self.cfg.tool_stats_enabled:
                    self.stats.record_tool_call(tc.name, False, 0)
            else:
                # [SYS-TOOLCACHE] 检查缓存
                cached = None
                if self.tool_cache:
                    cached = self.tool_cache.get(tc.name, tool_input)

                if cached is not None:
                    result_str = cached
                    R.print_tool_result(tc.name, f"[cache] {result_str[:80]}...")
                    if self.cfg.tool_stats_enabled:
                        self.stats.record_tool_call(tc.name, True, len(result_str))
                else:
                    try:
                        # [SYS-MCP] MCP 工具路由：MCP 工具由 MCPManager 代理调用
                        if (
                            self._mcp_manager is not None
                            and self._mcp_manager.is_mcp_tool(tc.name)
                        ):
                            result = self._mcp_manager.call_tool_sync(tc.name, tool_input)
                        else:
                            result = self.registry.call(tc.name, tool_input)
                        result_str = str(result) if not isinstance(result, str) else result

                        # [SYS-TRIM] 工具调用结果截断（按工具类型分策略）
                        result_str = self._trim_result(tc.name, result_str)

                        R.print_tool_result(tc.name, result_str)

                        # [SYS-TOOLCACHE] 写入缓存
                        if self.tool_cache:
                            self.tool_cache.put(tc.name, tool_input, result_str)

                        # [SYS-TOOLCACHE] 写操作执行成功后，立即使目标文件缓存失效。
                        # 覆盖的场景：
                        #   write_file / create_file / patch_file / delete_file
                        # 时机：工具调用成功后（result_str 不以 "[error" 开头）才失效，
                        # 避免把失败的写入也当作有效失效触发器。
                        if (
                            self.tool_cache
                            and tc.name in ("write_file", "create_file", "patch_file", "delete_file")
                            and not result_str.startswith("[error")
                        ):
                            _target_path = tool_input.get("path", "")
                            if _target_path:
                                self.tool_cache.invalidate_file(_target_path)

                        # [SYS-WATCH] 注册 read_file 的文件（供后台线程追踪）
                        if self.file_watcher and tc.name == "read_file":
                            _path = tool_input.get("path", "")
                            if _path:
                                self.file_watcher.register(_path, result_str)

                        if self.cfg.tool_stats_enabled:
                            self.stats.record_tool_call(tc.name, True, len(result_str))
                    except Exception as e:
                        result_str = f"[tool error: {e}]"
                        R.print_tool_error(tc.name, str(e))
                        if self.cfg.tool_stats_enabled:
                            self.stats.record_tool_call(tc.name, False, 0)

            # [SYS-HOOKS] PostToolUse：可注入额外上下文（拼接到结果后）
            if hook_mgr is not None:
                post = hook_mgr.run(
                    "PostToolUse",
                    {"tool_name": tc.name, "tool_input": tool_input, "tool_result": result_str},
                    tool_name=tc.name,
                )
                if post.context:
                    result_str = result_str + f"\n\n[hook note] {post.context}"

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
            # 尾部权重更高：实际输出/错误/exit 通常在尾部
            tail_ratio = getattr(self.cfg.tool_trim, "bash_tail_ratio", 0.6)
            if total > 20:
                tail_n = max(8, int(total * tail_ratio))
                head_n = max(5, int(total * 0.3))
                if head_n + tail_n >= total:
                    head_n = total // 3
                    tail_n = total - head_n
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