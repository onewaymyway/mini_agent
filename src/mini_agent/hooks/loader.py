"""
hooks/loader.py — Hooks 配置发现与 HookManager

配置文件格式（global ~/.agent/hooks.json 和 project <root>/.agent/hooks.json，
project 中的条目会追加在 global 之后，同 event 内 global 先执行）：

{
  "PreToolUse": [
    {"matcher": "bash|write_file", "command": "python3 .agent/hooks/check.py", "timeout": 10}
  ],
  "PostToolUse": [
    {"matcher": "*", "command": "python3 .agent/hooks/log.py"}
  ],
  "UserPromptSubmit": [...],
  "SessionStart": [...],
  "SessionEnd": [...],
  "TurnEnd": [
    {"command": "python3 .agent/hooks/turn_end_notify.py"}
  ]
}

TurnEnd 事件在每轮 Agent 回复结束、等待用户下一次输入之前触发。
stdin payload:
  {
    "assistant_output": "<本轮 assistant 最终回复文本>",
    "history": [{"role": "user"|"assistant", "content": "..."}, ...]
  }
hook stdout 可返回：
  {}                           # 不做任何事，继续等待真实用户输入
  {"context": "提示文本"}      # 输出提示，继续等待
  {"user_input": "..."}        # 替代真实用户输入，直接驱动下一轮（用于 agent-to-agent 接管）

matcher 支持 "*"（全部）或 "|" 分隔的工具名列表（PreToolUse/PostToolUse 专用，
其它事件忽略 matcher）。

除了静态配置文件，skill / 自定义子 agent 激活时可以通过
HookManager.register_dynamic(event, specs, source) 临时挂载钩子，
并在 unregister_source(source) 时移除——用于实现
"skill/agent 自带 hooks" 的能力。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .runner import HookResult, HookSpec, run_hook

KNOWN_EVENTS = (
    # Session 生命周期
    "SessionStart",
    "SessionEnd",

    # Prompt 生命周期
    "UserPromptSubmit",

    # Tool 生命周期
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",   # tool 执行失败后（result 以 "[" 开头的错误）
    "PostToolBatch",        # 一批并行 tool 全部结束后（execute_all 返回时）

    # Task / Subagent 生命周期
    "SubagentStart",        # SubAgent._run_body 进入 RUNNING 时
    "SubagentStop",         # SubAgent 进入终态（DONE/FAILED/CANCELLED）时；可阻止 = 不适用，此处为通知
    "TaskCreated",          # TaskManager.submit 时
    "TaskCompleted",        # TaskManager._handle_terminal 确认 DONE 时

    # Stop 生命周期
    "Stop",                 # agentic_loop 无工具调用，准备结束本轮输出时

    # Context Compact 生命周期
    "PreCompact",           # _auto_compress_history 执行前（可阻止）
    "PostCompact",          # _auto_compress_history 执行后

    # Ensemble（多结果合并取优）生命周期
    "EnsembleJudged",       # 一次 ensemble 运行完成评判/合并后触发（通知，不可阻止）

    # mini_agent 扩展
    "TurnEnd",              # 一轮 run_turn 结束、等待用户下一次输入前
)


def _load_hooks_file(path: Path, source: str, cwd: Optional[Path]) -> dict[str, list[HookSpec]]:
    result: dict[str, list[HookSpec]] = {}
    if not path.is_file():
        return result
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return result
    if not isinstance(data, dict):
        return result
    for event, entries in data.items():
        if event not in KNOWN_EVENTS or not isinstance(entries, list):
            continue
        specs = []
        for e in entries:
            if not isinstance(e, dict) or "command" not in e:
                continue
            specs.append(HookSpec(
                command=e["command"],
                matcher=e.get("matcher", "*"),
                timeout=float(e.get("timeout", 30.0)),
                cwd=cwd,
                source=source,
            ))
        if specs:
            result[event] = specs
    return result


def _matches(matcher: str, tool_name: str) -> bool:
    if matcher in ("", "*"):
        return True
    return tool_name in {m.strip() for m in matcher.split("|") if m.strip()}


class HookManager:
    """
    管理所有 hook 的发现、注册与触发。

    使用方式：
        mgr = HookManager(project_root)
        mgr.load()                       # 加载 global + project hooks.json
        mgr.run("UserPromptSubmit", {"prompt": user_message})
        mgr.run("PreToolUse", {"tool_name": ..., "tool_input": ...})
    """

    def __init__(self, project_root: Optional[Path] = None) -> None:
        from mini_agent.storage.paths import AgentPaths
        self.paths = AgentPaths(project_root)
        # event -> list[HookSpec]，来自配置文件（静态）
        self._static: dict[str, list[HookSpec]] = {}
        # event -> {source: list[HookSpec]}，来自 skill/agent profile 等动态注册
        self._dynamic: dict[str, dict[str, list[HookSpec]]] = {}

    # ── 加载 ───────────────────────────────────────────────────────────────

    def load(self) -> None:
        self._static.clear()
        cwd = self.paths.project_root
        for path, source in (
            (self.paths.global_hooks_config, "global"),
            (self.paths.project_hooks_config, "project"),
        ):
            loaded = _load_hooks_file(path, source, cwd)
            for event, specs in loaded.items():
                self._static.setdefault(event, []).extend(specs)

    # ── 动态注册（skill / 自定义子 agent 自带 hooks） ────────────────────────

    def register_dynamic(self, event: str, specs: list[HookSpec], source: str) -> None:
        if event not in KNOWN_EVENTS:
            return
        self._dynamic.setdefault(event, {})[source] = specs

    def register_dynamic_from_dict(self, hooks: dict, source: str, cwd: Optional[Path] = None) -> None:
        """从一个形如 hooks.json 内容的 dict（skill/agent profile 自带）批量注册。"""
        cwd = cwd or self.paths.project_root
        for event, entries in (hooks or {}).items():
            if event not in KNOWN_EVENTS or not isinstance(entries, list):
                continue
            specs = []
            for e in entries:
                if not isinstance(e, dict) or "command" not in e:
                    continue
                specs.append(HookSpec(
                    command=e["command"],
                    matcher=e.get("matcher", "*"),
                    timeout=float(e.get("timeout", 30.0)),
                    cwd=cwd,
                    source=source,
                ))
            if specs:
                self.register_dynamic(event, specs, source)

    def unregister_source(self, source: str) -> None:
        for event in list(self._dynamic):
            self._dynamic[event].pop(source, None)

    # ── 触发 ───────────────────────────────────────────────────────────────

    def _all_specs(self, event: str) -> list[HookSpec]:
        specs = list(self._static.get(event, []))
        for src_specs in self._dynamic.get(event, {}).values():
            specs.extend(src_specs)
        return specs

    def run(self, event: str, payload: dict, tool_name: Optional[str] = None) -> HookResult:
        """
        依次执行某事件下所有匹配的 hook。

        - 任一 hook 返回 block -> 立即停止并返回该 block 结果
        - context 会被拼接累积
        - modified_input 取最后一个非空结果（按顺序依次应用）
        - user_input 取最后一个非空结果（TurnEnd 专用：替代真实用户输入）
        """
        merged_context: list[str] = []
        merged_input: Optional[dict] = None
        merged_user_input: Optional[str] = None

        for spec in self._all_specs(event):
            if tool_name is not None and not _matches(spec.matcher, tool_name):
                continue
            res = run_hook(spec, payload)
            if res.blocked:
                return res
            if res.context:
                merged_context.append(res.context)
            if res.modified_input:
                merged_input = {**(merged_input or {}), **res.modified_input}
                payload = {**payload, "tool_input": merged_input}
            if res.user_input is not None:
                merged_user_input = res.user_input

        return HookResult(
            decision="allow",
            context="\n".join(merged_context),
            modified_input=merged_input,
            user_input=merged_user_input,
        )

    @property
    def has_any(self) -> bool:
        return bool(self._static) or any(self._dynamic.values())


# ── 模块级单例（与 init_task_manager / init_agent_profiles 同一模式） ────────

_hook_manager: Optional[HookManager] = None


def init_hooks(project_root: Optional[Path] = None) -> HookManager:
    global _hook_manager
    _hook_manager = HookManager(project_root)
    _hook_manager.load()
    return _hook_manager


def get_hook_manager() -> Optional[HookManager]:
    return _hook_manager
