"""
prompts — Prompt management package.

Quick start:
    from prompts import pm

    # Render a template prompt
    text = pm.render("system/agent_core")

    # Render with variables
    text = pm.render("system/project_context", claude_md_content="...")

    # Get a UI text fragment
    banner = pm.fragment("cli_messages", "BANNER")
    msg    = pm.fragment("cli_messages", "REPL_STARTUP_MODEL", model="claude-opus-4-5")

    # Build the full system prompt
    system = pm.build_system_prompt(
        claude_md_content=cfg.claude_md_content,
        active_skills=["python-expert"],
        skill_context="...",
        sandbox=cfg.sandbox,
    )
"""

from .manager import (
    PromptManager,
    PromptNotFoundError,
    PromptRenderError,
    get_prompt_manager,
    reset_prompt_manager,
)

# 模块级默认实例，直接 `from prompts import pm` 即可使用
pm = get_prompt_manager()

__all__ = [
    "pm",
    "PromptManager",
    "PromptNotFoundError",
    "PromptRenderError",
    "get_prompt_manager",
    "reset_prompt_manager",
]
