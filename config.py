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


# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_MODEL = "claude-opus-4-5"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_MAX_TURNS = 50
DEFAULT_AGENT_NAME = "orzooo"


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
    use_system_tool_call: bool = False  # True = system prompt 模式，False = SDK 原生 tools
    max_llm_calls: int = 8              # LLM 请求并发上限

    # 调试日志
    debug_llm: bool = False           # 总开关：记录请求/响应
    debug_llm_console: bool = False   # 同时在终端打印调试信息
    debug_log_dir: Optional[Path] = None  # 日志目录，None=自动推断

    # Session 持久化
    session_dir: Optional[Path] = None   # Session 文件目录，None=./sessions
    session_fmt: str = "json"            # "json" 或 "jsonl"
    auto_save_session: bool = True       # 每轮对话后自动保存

    # Context injected into every system prompt
    claude_md_content: str = ""
    system_extra: str = ""         # additional system text (e.g. from --system flag)
    agent_name: str = DEFAULT_AGENT_NAME  # agent display name


def load_config(
    project_root: Optional[Path] = None,
    extra_system: str = "",
    verbose: bool = False,
    sandbox: bool = False,
    auto_approve: bool = False,
    model: Optional[str] = None,
    llm_provider: Optional[str] = None,
    llm_base_url: Optional[str] = None,
    use_system_tool_call: Optional[bool] = None,
    debug_llm: bool = False,
    debug_llm_console: bool = False,
    max_llm_calls: Optional[int] = None,
    session_dir: Optional[Path] = None,
    session_fmt: Optional[str] = None,
    auto_save_session: bool = True,
    agent_name: Optional[str] = None,
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

    # 调试日志初始化
    _debug_llm = debug_llm or os.environ.get("LLM_DEBUG", "").lower() in ("1", "true", "yes")
    _debug_console = debug_llm_console or os.environ.get("LLM_DEBUG_CONSOLE", "").lower() in ("1", "true", "yes")
    _debug_log_dir = Path(d) if (d := os.environ.get("LLM_DEBUG_LOG_DIR", "")) else None

    if _debug_llm:
        from llm.debug_logger import DebugConfig, init_debug_logger
        _dcfg = DebugConfig(
            enabled=True,
            log_to_file=True,
            log_to_console=_debug_console,
            log_dir=_debug_log_dir or root / ".claude" / "logs",
        )
        init_debug_logger(_dcfg, root)

    # system tool call 模式
    _use_sys_tc = (
        use_system_tool_call
        if use_system_tool_call is not None
        else os.environ.get("LLM_SYSTEM_TOOL_CALL", "").lower() in ("1", "true", "yes")
    )

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
        use_system_tool_call=_use_sys_tc,
        max_llm_calls=max_llm_calls or int(os.environ.get("MAX_LLM_CALLS", 8)),
        debug_llm=_debug_llm,
        debug_llm_console=_debug_console,
        debug_log_dir=_debug_log_dir,
        session_dir=session_dir or (Path(d) if (d := os.environ.get("SESSION_DIR", "")) else None),
        session_fmt=session_fmt or os.environ.get("SESSION_FMT", "json"),
        auto_save_session=auto_save_session,
        agent_name=agent_name or os.environ.get("AGENT_NAME", DEFAULT_AGENT_NAME),
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
    from datetime import datetime
    from prompts import pm

    # 当前时间
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S %A")

    return pm.build_system_prompt(
        claude_md_content=cfg.claude_md_content,
        active_skills=active_skills,
        skill_context=skill_context,
        system_extra=cfg.system_extra,
        sandbox=cfg.sandbox,
        current_time=current_time,
        agent_name=cfg.agent_name,
    )
