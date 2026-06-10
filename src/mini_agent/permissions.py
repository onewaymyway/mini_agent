"""
Permission guard.
Controls which tool calls require explicit user approval before execution.
Supports per-session allow/deny lists and sandbox enforcement.

All user-facing text is retrieved from PromptManager (prompts/fragments/permission_labels.md).

改进：
1. 新增 (e)dit 选项：批准前允许用户修改命令（bash 工具特别有用）
2. 新增 (s)how 选项：展示完整参数后再决定（summary 截断时有用）
3. 白名单按 tool_name + path_prefix 精细管理，而非宽泛字符串前缀匹配
4. 权限配置持久化：allow/deny 列表保存到工作目录 agent_permissions.json，
   下次启动自动加载，在当前工作目录内永久生效
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mini_agent.ui.terminal import term as _term
from mini_agent.prompts import pm
from mini_agent.storage.paths import AgentPaths


class _InterruptedByHTTP(Exception):
    """HTTP 端先响应，打断 CLI 的 stdin 等待。"""


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

    # Session-level allow/deny lists (populated interactively or loaded from file)
    _allow_list: list[_AllowEntry] = field(default_factory=list, init=False)
    _denied_tools: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        """构造完成后自动从配置文件加载持久化权限。"""
        self._load_permissions()

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

        # HTTP + CLI 双路审批：若 HTTP 服务已启动，同时向 HTTP 端推送权限请求，
        # 并在命令行展示完整权限信息，任意一端先响应即可。
        http_gate = _get_http_gate()
        if http_gate is not None:
            turn_id = _get_current_turn_id()
            approved, edited_input = self._prompt_with_http(
                tool_name, tool_input, is_dangerous, http_gate, turn_id
            )
            if approved and edited_input:
                # 用户从 HTTP 端修改了参数，写回 tool_input（in-place）
                tool_input.clear()
                tool_input.update(edited_input)
            return approved

        # Prompt user (may loop on 'show' / 'edit')
        return self._prompt(tool_name, tool_input, is_dangerous)

    def _is_allowed(self, tool_name: str, tool_input: dict) -> bool:
        """检查是否命中白名单（tool_name 精确匹配 + path_prefix 前缀匹配）。"""
        target_path = _normalize_path(_extract_path(tool_name, tool_input))
        for entry in self._allow_list:
            if entry.tool_name != tool_name:
                continue
            if not entry.path_prefix:
                return True  # 对该工具全放行
            norm_prefix = _normalize_path(entry.path_prefix)
            if target_path and target_path.startswith(norm_prefix):
                return True
        return False

    def _add_allow(self, tool_name: str, tool_input: dict) -> None:
        """将当前调用加入白名单（按工具 + 路径前缀），并持久化到配置文件。"""
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
            self._save_permissions()

    # ── 权限持久化 ────────────────────────────────────────────────────────

    def _permissions_path(self) -> Path:
        """返回权限配置文件路径：<project_root>/.agent/permissions.json"""
        return AgentPaths(self.project_root).permissions

    def _load_permissions(self) -> None:
        """从工作目录的 agent_permissions.json 加载持久化权限配置。"""
        path = self._permissions_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return

            # 加载白名单
            for entry in data.get("allow_list", []):
                if isinstance(entry, dict) and "tool_name" in entry:
                    a = _AllowEntry(
                        tool_name=entry["tool_name"],
                        path_prefix=entry.get("path_prefix", ""),
                    )
                    # 去重
                    if not any(e.tool_name == a.tool_name and e.path_prefix == a.path_prefix
                               for e in self._allow_list):
                        self._allow_list.append(a)

            # 加载黑名单
            for tool_name in data.get("denied_tools", []):
                if isinstance(tool_name, str):
                    self._denied_tools.add(tool_name)

            if self._allow_list or self._denied_tools:
                allow_count = len(self._allow_list)
                deny_count = len(self._denied_tools)
                _term.print(
                    f"[dim]Loaded permissions from .agent/permissions.json: "
                    f"{allow_count} allowed, {deny_count} denied[/dim]"
                )
        except Exception as e:
            _term.print(f"[yellow]Warning: failed to load .agent/permissions.json: {e}[/yellow]")

    def _save_permissions(self) -> None:
        """将当前 allow/deny 列表持久化到 .agent/permissions.json。"""
        path = self._permissions_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "allow_list": [
                    {"tool_name": e.tool_name, "path_prefix": e.path_prefix}
                    for e in self._allow_list
                ],
                "denied_tools": sorted(self._denied_tools),
            }
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            _term.print(f"[yellow]Warning: failed to save .agent/permissions.json: {e}[/yellow]")

    def _prompt_with_http(
        self,
        tool_name: str,
        tool_input: dict,
        is_dangerous: bool,
        http_gate,
        turn_id: str,
    ) -> tuple[bool, Optional[dict]]:
        """
        HTTP 模式下的双路审批：
        - 向 HTTP 端推送 SSE 权限请求事件（web demo 可见并自动刷新）
        - 同时在命令行完整显示权限信息，接受 CLI 端输入
        - 任意一端先响应即可

        CLI 额外选项 (w)ait — 放弃命令行输入，完全交由 HTTP 端处理
        """
        import threading as _threading
        import uuid as _uuid

        dangerous_label = pm.fragment("permission_labels", "DANGEROUS_LABEL")
        safe_label      = pm.fragment("permission_labels", "SAFE_LABEL")
        label = f"[bold red]{dangerous_label}[/bold red]" if is_dangerous else safe_label

        summary = _summarise(tool_name, tool_input)
        _term.print(f"\n{label} Tool request: [bold]{tool_name}[/bold]")
        _term.print(f"  [dim]{summary}[/dim]")
        _term.print(f"[dim]  （HTTP 端也已收到此请求，可在 Web 界面审批）[/dim]")

        req_id = str(_uuid.uuid4())

        # 注册 pending 并广播 SSE 事件（使用公开 API）
        pending = http_gate.register_pending(req_id, tool_name, tool_input, turn_id)

        # 结果容器：由 CLI 或 HTTP 监听线程写入
        result: dict = {"decided": False, "approved": False, "edited_input": None, "source": ""}
        result_lock = _threading.Lock()
        decided_event = _threading.Event()

        # ── HTTP 端监听线程 ────────────────────────────────────────────
        def _http_watcher():
            """等待 HTTP 端响应（pending.event 被 respond() 设置），写入结果后通知主线程。"""
            responded = pending.event.wait(timeout=http_gate._timeout)
            if decided_event.is_set():
                return  # CLI 已先决定，忽略
            with result_lock:
                if not result["decided"]:
                    result["decided"]      = True
                    result["approved"]     = pending.approved if responded else False
                    result["edited_input"] = pending.edited_input
                    result["source"]       = "http" if responded else "timeout"
            decided_event.set()

        http_thread = _threading.Thread(target=_http_watcher, daemon=True)
        http_thread.start()

        # ── CLI 端交互循环 ─────────────────────────────────────────────
        if tool_name == "bash":
            choices = "(y)es  (a)lways  (n)o  (d)eny-always  (e)dit  (s)how  (w)ait-HTTP"
        else:
            choices = "(y)es  (a)lways  (n)o  (d)eny-always  (s)how  (w)ait-HTTP"

        cli_decided = False
        while not decided_event.is_set():
            try:
                choice = _term.confirm(
                    prompt_lines=[],
                    choices=choices,
                    default="y",
                    interrupt_event=decided_event,   # 让 confirm 在 HTTP 端响应后自动退出
                )
            except (KeyboardInterrupt, EOFError):
                _term.print("")
                choice = "n"
            except _InterruptedByHTTP:
                # HTTP 端已先响应，退出 CLI 循环
                break

            if decided_event.is_set():
                break  # HTTP 端已先响应

            if choice in ("y", "yes"):
                cli_decided = True
                with result_lock:
                    result.update({"decided": True, "approved": True, "source": "cli"})
                decided_event.set()
                break

            elif choice in ("a", "always"):
                self._add_allow(tool_name, tool_input)
                cli_decided = True
                with result_lock:
                    result.update({"decided": True, "approved": True, "source": "cli"})
                decided_event.set()
                break

            elif choice in ("n", "no"):
                cli_decided = True
                with result_lock:
                    result.update({"decided": True, "approved": False, "source": "cli"})
                decided_event.set()
                break

            elif choice in ("d", "deny"):
                self._denied_tools.add(tool_name)
                self._save_permissions()
                cli_decided = True
                with result_lock:
                    result.update({"decided": True, "approved": False, "source": "cli"})
                decided_event.set()
                break

            elif choice in ("s", "show"):
                import json as _json
                _term.print(f"\n[dim]Full parameters:[/dim]")
                try:
                    _term.print(f"[dim]{_json.dumps(tool_input, ensure_ascii=False, indent=2)}[/dim]")
                except Exception:
                    _term.print(f"[dim]{tool_input!r}[/dim]")

            elif choice in ("e", "edit") and tool_name == "bash":
                original_cmd = tool_input.get("command", "")
                _term.print(f"\n[dim]Current command:[/dim] {original_cmd}")
                edited = _read_edited_command(original_cmd)
                if edited is not None:
                    tool_input["command"] = edited
                    _term.print(f"[dim]Edited to:[/dim] {edited}")
                    cli_decided = True
                    with result_lock:
                        result.update({"decided": True, "approved": True, "source": "cli"})
                    decided_event.set()
                    break

            elif choice in ("w", "wait"):
                _term.print("[dim]⏳ 等待 HTTP 端审批，命令行将不再接受输入...[/dim]")
                decided_event.wait(timeout=http_gate._timeout + 5)
                break

        # CLI 先决定时，取消 HTTP pending（唤醒监听线程让它退出）
        if cli_decided:
            http_gate.cancel_pending(req_id)
        http_thread.join(timeout=2)

        approved     = result.get("approved", False)
        edited_input = result.get("edited_input")
        source       = result.get("source", "unknown")

        # 广播最终审批结果给所有 SSE 客户端
        http_gate.broadcast_done(req_id, approved, source, turn_id)

        result_label = "[green]approved[/green]" if approved else "[red]denied[/red]"
        _term.print(f"[dim]  → Permission {result_label} (via {source})[/dim]")
        return approved, edited_input


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
                self._save_permissions()
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


def _normalize_path(path: Optional[str]) -> Optional[str]:
    """规范化路径：去掉 ./ 前缀，统一路径格式。"""
    if not path:
        return path
    # 去掉 ./ 前缀（可能多个）
    while path.startswith("./"):
        path = path[2:]
    return path

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


# ── HTTP bridge 懒加载辅助（避免 permissions <-> api 循环依赖）────────────────

def _get_http_gate():
    """
    懒加载 HttpPermissionGate 单例。
    只有在 HTTP 服务真正启动后（bridge 已初始化）才返回非 None。
    """
    try:
        from mini_agent.api.bridge import get_bridge
        bridge = get_bridge()
        # 只有 broadcaster 已绑定 asyncio loop（即 server 已 start）才算激活
        if bridge.broadcaster._loop is not None:
            return bridge.permission_gate
    except Exception:
        pass
    return None


def _get_current_turn_id() -> str:
    """获取当前正在执行的 turn_id（由 AgentRunner 写入 agent._http_turn_id）。"""
    try:
        from mini_agent.api.bridge import get_bridge
        bridge = get_bridge()
        if bridge.agent:
            return getattr(bridge.agent, "_http_turn_id", "")
    except Exception:
        pass
    return ""