"""
Configuration and session context management.
Loads CLAUDE.md project context, .env settings, and tracks session state.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── Default model ─────────────────────────────────────────────────────────────
DEFAULT_MODEL = "claude-opus-4-5"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_MAX_TURNS = 50


@dataclass
class SessionStats:
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    start_time: float = field(default_factory=time.time)

    @property
    def elapsed(self) -> str:
        s = int(time.time() - self.start_time)
        return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"

    def summary(self) -> str:
        return (
            f"Turns: {self.turns} | "
            f"Tokens in/out: {self.input_tokens}/{self.output_tokens} | "
            f"Tool calls: {self.tool_calls} | "
            f"Elapsed: {self.elapsed}"
        )


@dataclass
class AppConfig:
    # Anthropic API
    api_key: str = ""
    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_turns: int = DEFAULT_MAX_TURNS

    # Paths
    project_root: Path = field(default_factory=Path.cwd)
    skills_dir: Optional[Path] = None  # ~/.claude/skills or project .claude/skills

    # Behaviour
    verbose: bool = False          # show raw JSON tool calls
    sandbox: bool = False          # dry-run: no destructive writes / execs
    auto_approve: bool = False     # skip permission prompts
    stream: bool = True            # stream tokens as they arrive

    # LLM provider（传给 llm.LLMConfig.from_app_config）
    llm_provider: str = "anthropic"   # "anthropic" | "openai" | "ollama" | ...
    llm_base_url: str = ""            # 自定义 endpoint（可选）

    # Context injected into every system prompt
    claude_md_content: str = ""
    system_extra: str = ""         # additional system text (e.g. from --system flag)


def load_config(
    project_root: Optional[Path] = None,
    extra_system: str = "",
    verbose: bool = False,
    sandbox: bool = False,
    auto_approve: bool = False,
    model: Optional[str] = None,
    llm_provider: Optional[str] = None,
    llm_base_url: Optional[str] = None,
) -> AppConfig:
    """Load config from environment + CLAUDE.md, return AppConfig."""
    root = project_root or Path.cwd()

    # API key
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    # CLAUDE.md
    claude_md = _read_claude_md(root)

    # Skills directory (project-local first, then ~/.claude/skills)
    skills_dir = _resolve_skills_dir(root)

    llm_provider = llm_provider or os.environ.get("LLM_PROVIDER", "anthropic")
    llm_base_url = llm_base_url or os.environ.get("LLM_BASE_URL", "")

    return AppConfig(
        api_key=api_key,
        model=model or os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL),
        max_tokens=int(os.environ.get("CLAUDE_MAX_TOKENS", DEFAULT_MAX_TOKENS)),
        project_root=root,
        skills_dir=skills_dir,
        verbose=verbose,
        sandbox=sandbox,
        auto_approve=auto_approve,
        claude_md_content=claude_md,
        system_extra=extra_system,
        llm_provider=llm_provider,
        llm_base_url=llm_base_url,
    )


def _read_claude_md(root: Path) -> str:
    """Read CLAUDE.md from project root (or parent dirs), return content."""
    search_dirs = [root] + list(root.parents)[:3]
    for d in search_dirs:
        p = d / "CLAUDE.md"
        if p.exists():
            try:
                return p.read_text(encoding="utf-8")
            except Exception:
                pass
    return ""


def _resolve_skills_dir(root: Path) -> Optional[Path]:
    candidates = [
        root / ".claude" / "skills",
        Path.home() / ".claude" / "skills",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def build_system_prompt(
    cfg: AppConfig,
    active_skills: list[str],
    skill_context: str = "",
) -> str:
    """
    Compose the full system prompt.
    Delegates to PromptManager — all prompt text lives in prompts/system/*.md.
    """
    from prompts import pm
    return pm.build_system_prompt(
        claude_md_content=cfg.claude_md_content,
        active_skills=active_skills,
        skill_context=skill_context,
        system_extra=cfg.system_extra,
        sandbox=cfg.sandbox,
    )
