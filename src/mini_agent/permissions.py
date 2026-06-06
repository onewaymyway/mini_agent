"""
Permission guard.
Controls which tool calls require explicit user approval before execution.
Supports per-session allow/deny lists and sandbox enforcement.

All user-facing text is retrieved from PromptManager (prompts/fragments/permission_labels.md).

改进：
1. 新增 (e)dit 选项：批准前允许用户修改命令（bash 工具特别有用）
2. 新增 (s)how 选项：展示完整参数后再决定（summary 截断时有用）
3. 白名单按 tool_name + path_prefix 精细管理，而非宽泛字符串前缀匹配
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mini_agent.ui.terminal import term as _term
from mini_agent.prompts import pm


# Tools that are always safe (read-only, no side-effects)
_SAFE_TOOLS = frozenset(
    {"read_file", "list_dir", "glob", "grep", "web_search", "create_plan", "add_task", "start_task", "complete_task", "fail_task","get_plan_status","clear_plan"}
)

# Tools that need approval by default (write / exec / network)
_RISKY_TOOLS = frozenset(
    {"bash", "write_file", "patch_file", "create_file", "delete_file"}
)

# Dangerous shell patterns that get an extra warning
_DANGER_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\bdd\s+",
    r"\bmkfs\b",
    r">\s*/dev/",
    r"\bsudo\b",
    r"\bcurl\b.*\|\s*(bash|sh)\b",
    r"\bchmod\s+777\b",
]


@dataclass
class _AllowEntry:
    """白名单条目：按工具名 + 路径前缀精细管理。"""
    tool_name: str
    path_prefix: str   # 空字符串表示对该工具的所有调用放行


@dataclass
class PermissionGuard:
    auto_approve: bool = False
    sandbox: bool = False
    project_root: Path = field(default_factory=Path.cwd)

    # Session-level allow/deny lists (populated interactively)
    _allow_list: list[_AllowEntry] = field(default_factory=list, init=False)
    _denied_tools: set[str] = field(default_factory=set, init=False)

    def check(self, tool_name: str, tool_input: dict) -> bool:
        """
        Returns True if the tool call is allowed to proceed.
        May prompt the user interactively.
        """
        # Always-denied (session-scoped)
        if tool_name in self._denied_tools:
            msg = pm.fragment("permission_labels", "SESSION_DENIED_MSG", tool_name=tool_name)
            _term.print(f"[red]{msg}[/red]")
            return False

        # Sandbox: block all destructive tools
        if self.sandbox and tool_name in _RISKY_TOOLS:
            blocked = pm.fragment("permission_labels", "SANDBOX_BLOCKED", tool_name=tool_name)
            would_have = pm.fragment("permission_labels", "SANDBOX_WOULD_HAVE")
            _term.print(f"[yellow]{blocked}[/yellow]")
            _term.print(f"  [dim]{would_have}: {_summarise(tool_name, tool_input)}[/dim]")
            return False

        # Safe tools: always allowed
        if tool_name in _SAFE_TOOLS:
            return True

        # Auto-approve: skip prompts
        if self.auto_approve:
            return True

        # Check allow list (精细匹配：tool_name + path_prefix)
        if self._is_allowed(tool_name, tool_input):
            return True

        # Danger check
        is_dangerous = _is_dangerous(tool_name, tool_input)

        # Prompt user (may loop on 'show' / 'edit')
        return self._prompt(tool_name, tool_input, is_dangerous)

    def _is_allowed(self, tool_name: str, tool_input: dict) -> bool:
        """检查是否命中白名单（tool_name 精确匹配 + path_prefix 前缀匹配）。"""
        target_path = _extract_path(tool_name, tool_input)
        for entry in self._allow_list:
            if entry.tool_name != tool_name:
                continue
            if not entry.path_prefix:
                return True  # 对该工具全放行
            if target_path and target_path.startswith(entry.path_prefix):
                return True
        return False

    def _add_allow(self, tool_name: str, tool_input: dict) -> None:
        """将当前调用加入白名单（按工具 + 路径前缀）。"""
        path = _extract_path(tool_name, tool_input)
        if path:
            # 取路径的父目录作为前缀，避免过宽
            prefix = str(Path(path).parent) + "/"
        else:
            prefix = ""
        entry = _AllowEntry(tool_name=tool_name, path_prefix=prefix)
        if not any(e.tool_name == entry.tool_name and e.path_prefix == entry.path_prefix
                   for e in self._allow_list):
            self._allow_list.append(entry)

    def _prompt(self, tool_name: str, tool_input: dict, is_dangerous: bool) -> bool:
        """
        交互式权限询问。
        选项：
          y  / yes        — 本次批准
          a  / always     — 本次批准并加入白名单（同目录/同工具后续不再询问）
          n  / no         — 本次拒绝
          d  / deny       — 拒绝并加入黑名单（本 session 内永久拒绝该工具）
          e  / edit       — 修改命令后再批准（仅 bash 工具）
          s  / show       — 显示完整参数后重新询问
        """
        dangerous_label = pm.fragment("permission_labels", "DANGEROUS_LABEL")
        safe_label      = pm.fragment("permission_labels", "SAFE_LABEL")

        label = f"[bold red]{dangerous_label}[/bold red]" if is_dangerous else safe_label

        while True:
            summary = _summarise(tool_name, tool_input)

            _term.print(f"\n{label} Tool request: [bold]{tool_name}[/bold]")
            _term.print(f"  [dim]{summary}[/dim]")

            # 根据工具类型动态生成选项提示
            if tool_name == "bash":
                choices = "(y)es  (a)lways  (n)o  (d)eny-always  (e)dit  (s)how"
            else:
                choices = "(y)es  (a)lways  (n)o  (d)eny-always  (s)how"

            try:
                choice = _term.confirm(
                    prompt_lines=[],
                    choices=choices,
                    default="y",
                )
            except (KeyboardInterrupt, EOFError):
                _term.print("")
                return False

            if choice in ("y", "yes"):
                return True

            elif choice in ("a", "always"):
                self._add_allow(tool_name, tool_input)
                return True

            elif choice in ("n", "no"):
                return False

            elif choice in ("d", "deny"):
                self._denied_tools.add(tool_name)
                return False

            elif choice in ("s", "show"):
                # 显示完整参数后重新循环询问
                import json as _json
                _term.print(f"\n[dim]Full parameters:[/dim]")
                try:
                    _term.print(f"[dim]{_json.dumps(tool_input, ensure_ascii=False, indent=2)}[/dim]")
                except Exception:
                    _term.print(f"[dim]{tool_input!r}[/dim]")
                # 循环继续，重新询问

            elif choice in ("e", "edit") and tool_name == "bash":
                # 允许用户编辑命令后批准
                original_cmd = tool_input.get("command", "")
                _term.print(f"\n[dim]Current command:[/dim] {original_cmd}")
                edited = _read_edited_command(original_cmd)
                if edited is not None:
                    tool_input["command"] = edited
                    _term.print(f"[dim]Edited to:[/dim] {edited}")
                    return True
                # 用户取消编辑，重新询问

            else:
                # 未知输入，重新询问
                pass


def _read_edited_command(original: str) -> Optional[str]:
    """
    让用户在终端直接编辑命令。
    输入空行 → 取消（返回 None）。
    输入新命令 → 返回新命令字符串。
    """
    _term.print("[dim]Enter new command (empty line to cancel):[/dim]")
    _term._enter_input_mode()
    try:
        sys.stdout.write("  $ ")
        sys.stdout.flush()
        line = sys.stdin.readline()
        edited = line.strip()
        if not line.endswith("\n"):
            sys.stdout.write("\n")
            sys.stdout.flush()
        return edited if edited else None
    finally:
        _term._exit_input_mode()


def _summarise(tool_name: str, tool_input: dict) -> str:
    """生成单行摘要（截断长内容）。"""
    if tool_name == "bash":
        cmd = tool_input.get("command", "")
        # 显示更多内容：从 120 提升到 200 字符
        return f"$ {cmd[:200]}" + ("…" if len(cmd) > 200 else "")
    if tool_name in ("write_file", "create_file", "patch_file", "delete_file"):
        path = tool_input.get("path", tool_input.get("file_path", "?"))
        return f"{tool_name}({path})"
    return f"{tool_name}({', '.join(f'{k}={v!r}' for k, v in list(tool_input.items())[:3])})"


def _extract_path(tool_name: str, tool_input: dict) -> Optional[str]:
    """从 tool_input 中提取文件路径（用于白名单匹配）。"""
    if tool_name == "bash":
        return None  # bash 不按路径匹配，按工具整体放行
    return tool_input.get("path") or tool_input.get("file_path")


def _is_dangerous(tool_name: str, tool_input: dict) -> bool:
    if tool_name != "bash":
        return False
    cmd = tool_input.get("command", "")
    return any(re.search(p, cmd) for p in _DANGER_PATTERNS)
