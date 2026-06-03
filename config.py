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

    # 消息格式：
    #   "system_field"  — 使用顶层 system 参数（默认，Anthropic/OpenAI 原生格式）
    #   "system_role"   — 将 system 内容作为 role="system" 的首条消息注入
    system_message_format: str = "system_field"


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
    system_message_format: Optional[str] = None,
    config_file: Optional[Path] = None,
) -> AppConfig:
    """Load config from environment + CLAUDE.md + optional JSON config file, return AppConfig.

    参数优先级（从高到低）：
      1. JSON 配置文件（--config 指定）
      2. 命令行参数
      3. 环境变量
      4. 内置默认值
    """
    root = project_root or Path.cwd()

    # ── JSON 配置文件加载 ─────────────────────────────────────────────────────
    file_cfg: dict = {}
    if config_file is not None:
        file_cfg = _load_config_file(config_file)
    else:
        # 自动查找项目根目录下的 agent_config.json
        default_cfg_path = root / "agent_config.json"
        if default_cfg_path.exists():
            file_cfg = _load_config_file(default_cfg_path)

    def _f(key: str, cli_val, default=None):
        """从配置文件或命令行取值；文件优先级更高。"""
        return file_cfg[key] if key in file_cfg else (cli_val if cli_val is not None else default)

    # API key
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    # CLAUDE.md
    claude_md = _read_claude_md(root)

    # Skills directory (project-local first, then ~/.claude/skills)
    skills_dir = _resolve_skills_dir(root)

    _llm_provider = _f("provider", llm_provider) or os.environ.get("LLM_PROVIDER", "anthropic")
    _llm_base_url = _f("base_url", llm_base_url) or os.environ.get("LLM_BASE_URL", "")

    # 调试日志初始化
    _debug_llm = bool(_f("debug_llm", debug_llm or None)) or os.environ.get("LLM_DEBUG", "").lower() in ("1", "true", "yes")
    _debug_console = bool(_f("debug_llm_console", debug_llm_console or None)) or os.environ.get("LLM_DEBUG_CONSOLE", "").lower() in ("1", "true", "yes")
    _debug_log_dir_str = file_cfg.get("debug_log_dir") or os.environ.get("LLM_DEBUG_LOG_DIR", "")
    _debug_log_dir = Path(_debug_log_dir_str) if _debug_log_dir_str else None

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
    _use_sys_tc_cli = (
        use_system_tool_call
        if use_system_tool_call is not None
        else os.environ.get("LLM_SYSTEM_TOOL_CALL", "").lower() in ("1", "true", "yes")
    )
    _use_sys_tc = bool(_f("system_tool_call", _use_sys_tc_cli or None))

    # system 消息格式
    _sys_msg_fmt_cli = system_message_format or os.environ.get("LLM_SYSTEM_MESSAGE_FORMAT", "system_field")
    _sys_msg_fmt = _f("system_message_format", _sys_msg_fmt_cli) or "system_field"
    if _sys_msg_fmt not in ("system_field", "system_role"):
        import warnings
        warnings.warn(
            f"[config] Unknown system_message_format={_sys_msg_fmt!r}, "
            "falling back to 'system_field'. Valid values: 'system_field', 'system_role'."
        )
        _sys_msg_fmt = "system_field"

    # 其他参数
    _model = _f("model", model) or os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL)
    _verbose = bool(_f("verbose", verbose or None))
    _sandbox = bool(_f("sandbox", sandbox or None))
    _auto_approve = bool(_f("yes", auto_approve or None))
    _extra_system = _f("system", extra_system) or ""
    _max_llm_calls_val = _f("max_llm_calls", max_llm_calls)
    _max_llm_calls = int(_max_llm_calls_val) if _max_llm_calls_val is not None else int(os.environ.get("MAX_LLM_CALLS", 8))
    _session_fmt = _f("session_fmt", session_fmt) or os.environ.get("SESSION_FMT", "json")
    _auto_save = not bool(_f("no_save_session", None))  # file flag is "no_save_session"
    _agent_name = _f("agent_name", agent_name) or os.environ.get("AGENT_NAME", DEFAULT_AGENT_NAME)

    _session_dir_str = file_cfg.get("session_dir") or (str(session_dir) if session_dir else "") or os.environ.get("SESSION_DIR", "")
    _session_dir = Path(_session_dir_str) if _session_dir_str else None

    return AppConfig(
        api_key=api_key,
        model=_model,
        max_tokens=int(file_cfg.get("max_tokens") or os.environ.get("CLAUDE_MAX_TOKENS", DEFAULT_MAX_TOKENS)),
        project_root=root,
        skills_dir=skills_dir,
        verbose=_verbose,
        sandbox=_sandbox,
        auto_approve=_auto_approve,
        claude_md_content=claude_md,
        system_extra=_extra_system,
        llm_provider=_llm_provider,
        llm_base_url=_llm_base_url,
        use_system_tool_call=_use_sys_tc,
        max_llm_calls=_max_llm_calls,
        debug_llm=_debug_llm,
        debug_llm_console=_debug_console,
        debug_log_dir=_debug_log_dir,
        session_dir=_session_dir,
        session_fmt=_session_fmt,
        auto_save_session=_auto_save,
        agent_name=_agent_name,
        system_message_format=_sys_msg_fmt,
    )


def _load_config_file(path: Path) -> dict:
    """
    从 JSON 配置文件加载参数，返回 dict。
    加载失败时打印警告并返回空 dict，不中断启动。

    支持的字段名与命令行参数保持一致（下划线形式），例如：
      {
        "model": "claude-opus-4-5",
        "provider": "openai",
        "base_url": "https://...",
        "system_tool_call": true,
        "system_message_format": "system_role",
        "verbose": false,
        "sandbox": false,
        "yes": false,
        "max_turns": 50,
        "max_llm_calls": 8,
        "workers": 4,
        "session_dir": "./sessions",
        "session_fmt": "json",
        "no_save_session": false,
        "agent_name": "orzooo",
        "debug_llm": false,
        "debug_llm_console": false,
        "debug_log_dir": "",
        "max_tokens": 8192,
        "system": ""
      }
    """
    import json as _json
    import warnings as _warnings
    try:
        text = path.read_text(encoding="utf-8")
        data = _json.loads(text)
        if not isinstance(data, dict):
            _warnings.warn(f"[config] {path}: expected a JSON object, got {type(data).__name__}. Ignored.")
            return {}
        return data
    except FileNotFoundError:
        _warnings.warn(f"[config] Config file not found: {path}")
        return {}
    except _json.JSONDecodeError as e:
        _warnings.warn(f"[config] Failed to parse config file {path}: {e}")
        return {}
    except Exception as e:
        _warnings.warn(f"[config] Error loading config file {path}: {e}")
        return {}


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
