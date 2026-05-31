"""
Permission guard.
Controls which tool calls require explicit user approval before execution.
Supports per-session allow/deny lists and sandbox enforcement.

All user-facing text is retrieved from PromptManager (prompts/fragments/permission_labels.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from prompts import pm

console = Console()


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
class PermissionGuard:
    auto_approve: bool = False
    sandbox: bool = False
    project_root: Path = field(default_factory=Path.cwd)

    # Session-level allow/deny lists (populated interactively)
    _allowed_patterns: list[str] = field(default_factory=list, init=False)
    _denied_tools: set[str] = field(default_factory=set, init=False)

    def check(self, tool_name: str, tool_input: dict) -> bool:
        """
        Returns True if the tool call is allowed to proceed.
        May prompt the user interactively.
        """
        # Always-denied (session-scoped)
        if tool_name in self._denied_tools:
            msg = pm.fragment("permission_labels", "SESSION_DENIED_MSG", tool_name=tool_name)
            console.print(f"[red]{msg}[/red]")
            return False

        # Sandbox: block all destructive tools
        if self.sandbox and tool_name in _RISKY_TOOLS:
            blocked = pm.fragment("permission_labels", "SANDBOX_BLOCKED", tool_name=tool_name)
            would_have = pm.fragment("permission_labels", "SANDBOX_WOULD_HAVE")
            console.print(f"[yellow]{blocked}[/yellow]")
            console.print(f"  [dim]{would_have}: {_summarise(tool_name, tool_input)}[/dim]")
            return False

        # Safe tools: always allowed
        if tool_name in _SAFE_TOOLS:
            return True

        # Auto-approve: skip prompts
        if self.auto_approve:
            return True

        # Already approved by pattern
        summary = _summarise(tool_name, tool_input)
        for pattern in self._allowed_patterns:
            if pattern in summary:
                return True

        # Danger check
        is_dangerous = _is_dangerous(tool_name, tool_input)

        # Prompt user
        return self._prompt(tool_name, summary, is_dangerous)

    def _prompt(self, tool_name: str, summary: str, is_dangerous: bool) -> bool:
        from orchestrator.status_bar import printing_context, pause, resume
        dangerous_label = pm.fragment("permission_labels", "DANGEROUS_LABEL")
        safe_label      = pm.fragment("permission_labels", "SAFE_LABEL")
        choice_hint     = pm.fragment("permission_labels", "CHOICE_HINT")

        label = f"[bold red]{dangerous_label}[/bold red]" if is_dangerous else safe_label

        # 先在 printing_context 里打印审批信息，再 pause 等待输入
        with printing_context():
            console.print(f"\n{label} Tool request: [bold]{tool_name}[/bold]")
            console.print(f"  [dim]{summary}[/dim]")

        # pause：擦除状态栏，等待用户输入（输入完成前不重绘）
        pause()
        import sys
        sys.stdout.write(f"  {choice_hint} : ")
        sys.stdout.flush()

        try:
            choice = input().strip().lower() or "y"
        except (EOFError, KeyboardInterrupt):
            resume()
            return False

        resume()  # 恢复状态栏

        if choice in ("y", "yes"):
            return True
        elif choice in ("a", "always"):
            self._allowed_patterns.append(summary[:40])
            return True
        elif choice in ("d", "deny"):
            self._denied_tools.add(tool_name)
            return False
        else:
            return False


def _summarise(tool_name: str, tool_input: dict) -> str:
    if tool_name == "bash":
        cmd = tool_input.get("command", "")
        return f"$ {cmd[:120]}"
    if tool_name in ("write_file", "create_file", "patch_file", "delete_file"):
        path = tool_input.get("path", tool_input.get("file_path", "?"))
        return f"{tool_name}({path})"
    return f"{tool_name}({', '.join(f'{k}={v!r}' for k, v in list(tool_input.items())[:3])})"


def _is_dangerous(tool_name: str, tool_input: dict) -> bool:
    if tool_name != "bash":
        return False
    cmd = tool_input.get("command", "")
    return any(re.search(p, cmd) for p in _DANGER_PATTERNS)
