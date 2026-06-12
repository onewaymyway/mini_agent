"""
hooks — 用户自定义钩子机制

参考 Claude Code 的 hooks 设计：用户在 .agent/hooks.json（项目级）
或 ~/.agent/hooks.json（全局级）中声明命令，
在关键事件点（PreToolUse / PostToolUse / UserPromptSubmit / SessionStart / SessionEnd）
被自动调用。

公开 API：
    from mini_agent.hooks import HookManager, init_hooks, get_hook_manager
"""

from __future__ import annotations

from .loader import HookManager, init_hooks, get_hook_manager
from .runner import HookResult

__all__ = ["HookManager", "init_hooks", "get_hook_manager", "HookResult"]
