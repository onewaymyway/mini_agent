"""
config/loader.py — 配置加载逻辑

拆分自原 config.py（v3，对应 self_evolution_implementation_plan.md Stage 0.4）。
本文件只放"从环境变量/JSON 文件/CLI 参数组装 AppConfig"的逻辑：
  - load_config()                — 主入口，优先级：CLI > agent_config.json > providers.json > 环境变量 > 默认值
  - _load_config_file()          — 读取 agent_config.json
  - _load_providers_config()     — 读取 providers.json（含 API key，敏感）
  - _merge_providers_into_chain()— 把 providers 全局设置合并进 fallback chain
  - _parse_name_list()           — CLI 逗号分隔字符串/列表统一转换辅助函数

数据结构定义在 config/models.py；system prompt 构建在 config/prompt_builder.py。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from mini_agent.mcp.config import MCPConfig, MCPServerConfig

from .models import (
    AppConfig,
    MemoryConfig,
    CompressConfig,
    ToolTrimConfig,
    SkillConfig,
    PerceptionConfig,
    ProfileConfig,
    SessionConfig,
    DebugConfig,
    HttpConfig,
    WebSearchConfig,
    RetryConfig,
    RoleAgentConfig,
    EnvInfoConfig,
    ReminderConfig,
    WorkdirKnowledgeConfig,
    DEFAULT_MODEL,
    DEFAULT_AGENT_NAME,
    DEFAULT_MAX_TOKENS,
)
from .prompt_builder import _read_claude_md, _resolve_prompts_dir, _resolve_skills_dir


# ════════════════════════════════════════════════════════════════════════════════
# load_config：平坦 JSON/CLI 参数 → 组装子配置块 → 返回 AppConfig
# ════════════════════════════════════════════════════════════════════════════════

def _parse_name_list(value) -> list:
    """把 CLI 字符串（逗号分隔）或列表统一转为 list[str]。
    例：
      "evaluator,coach"  → ["evaluator", "coach"]
      ["evaluator"]      → ["evaluator"]
      []                 → []
      None               → []
    """
    if not value:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v) for v in value if v]


def load_config(
    project_root: Optional[Path] = None,
    extra_system: Optional[str] = None,
    verbose: Optional[bool] = None,
    sandbox: Optional[bool] = None,
    auto_approve: Optional[bool] = None,
    model: Optional[str] = None,
    llm_provider: Optional[str] = None,
    llm_base_url: Optional[str] = None,
    use_system_tool_call: Optional[bool] = None,
    debug_llm: Optional[bool] = None,
    debug_llm_console: Optional[bool] = None,
    max_llm_calls: Optional[int] = None,
    session_dir: Optional[Path] = None,
    session_fmt: Optional[str] = None,
    auto_save_session: Optional[bool] = None,
    agent_name: Optional[str] = None,
    system_message_format: Optional[str] = None,
    config_file: Optional[Path] = None,
    claude_md_file: Optional[str] = None,
    # 以下保持与旧签名兼容，内部组装到子配置块
    memory_enabled: Optional[bool] = None,
    memory_top_k: Optional[int] = None,
    memory_global_enabled: Optional[bool] = None,
    memory_global_top_k: Optional[int] = None,
    session_summary_enabled: Optional[bool] = None,
    session_summary_min_turns: Optional[int] = None,
    session_search_enabled: Optional[bool] = None,
    auto_compress_enabled: Optional[bool] = None,
    auto_compress_threshold: Optional[float] = None,
    tool_result_trim_enabled: Optional[bool] = None,
    tool_result_trim_threshold: Optional[int] = None,
    tool_trim_bash_tail_ratio: Optional[float] = None,
    tool_trim_read_window_lines: Optional[int] = None,
    tool_trim_grep_max_lines: Optional[int] = None,
    forget_policy_enabled: Optional[bool] = None,
    skill_semantic_enabled: Optional[bool] = None,
    skill_semantic_threshold: Optional[float] = None,
    skill_tracking_enabled: Optional[bool] = None,
    skill_chunking_enabled: Optional[bool] = None,
    skill_compact_budget: Optional[int] = None,
    skill_compact_per_skill: Optional[int] = None,
    project_scan_enabled: Optional[bool] = None,
    file_watch_enabled: Optional[bool] = None,
    tool_cache_enabled: Optional[bool] = None,
    token_estimate_enabled: Optional[bool] = None,
    token_warn_threshold: Optional[float] = None,
    tool_stats_enabled: Optional[bool] = None,
    # reminder 系统
    reminder_enabled: Optional[bool] = None,
    reminders_dir: Optional[Path] = None,
    reminder_verbose: Optional[bool] = None,
    # role agent 系统
    role_agent_enabled: Optional[bool] = None,
    role_agent_allow: Optional[list] = None,
    role_agent_block: Optional[list] = None,
    role_agent_dir: Optional[Path] = None,
    # 重试退避策略
    llm_retry_backoff_mode: Optional[str] = None,
    llm_retry_backoff_step: Optional[float] = None,
    llm_retry_backoff_max_delay: Optional[float] = None,
    # providers 独立配置文件（含 API key，应加入 .gitignore）
    providers_config_file: Optional[Path] = None,
) -> AppConfig:
    """
    加载配置，优先级（高→低）：JSON 配置文件 > CLI 参数 > 环境变量 > 内置默认值。
    返回 AppConfig（含已组装好的子配置块）。
    """
    root = project_root or Path.cwd()

    # ── JSON 配置文件 ─────────────────────────────────────────────────────────
    file_cfg: dict = {}
    if config_file is not None:
        file_cfg = _load_config_file(config_file)
    else:
        default_cfg_path = root / "agent_config.json"
        if default_cfg_path.exists():
            file_cfg = _load_config_file(default_cfg_path)

    # ── Providers 独立配置文件（含 API key，默认 providers.json）─────────────
    # 优先级：--providers-config CLI 参数 > 项目根目录的 providers.json
    _providers_cfg_path: Optional[Path] = (
        providers_config_file
        or (root / "providers.json" if (root / "providers.json").exists() else None)
    )
    _providers_cfg: dict = (
        _load_providers_config(_providers_cfg_path)
        if _providers_cfg_path is not None
        else {}
    )

    def _f(key, cli_val, default=None):
        """CLI 参数 > 配置文件 > 默认值"""
        if cli_val is not None:
            return cli_val
        return file_cfg[key] if key in file_cfg else default

    def _fb(key, cli_val, default=False):
        """CLI 参数 > 配置文件 > 默认值（bool 版）"""
        if cli_val is not None:
            return bool(cli_val)
        if key in file_cfg:
            return bool(file_cfg[key])
        return default

    def _fn(key, cli_val, default):
        """CLI 参数 > 配置文件 > 默认值（数值版，类型与 default 一致）"""
        if cli_val is not None:
            return type(default)(cli_val)
        if key in file_cfg:
            return type(default)(file_cfg[key])
        return default

    # ── 从 providers.json 主配置提取基础值（chain[0] 即为主配置）────────────
    # 这些值作为"providers.json 层"，优先级低于 agent_config.json，高于环境变量。
    _raw_chain_for_main: list = _providers_cfg.get("llm_fallback_chain", [])
    _main_entry: dict = _raw_chain_for_main[0] if _raw_chain_for_main else {}
    _main_api_keys: list = _main_entry.get("api_keys") or []
    _providers_api_key: str = (
        _main_entry.get("api_key")
        or (_main_api_keys[0] if _main_api_keys else "")
    )
    _providers_provider: str = _main_entry.get("provider", "")
    _providers_model: str    = _main_entry.get("model", "")

    # ── 核心参数（优先级：CLI > agent_config.json > providers.json > 环境变量 > 默认）
    # claude_md_file 优先级：CLI 参数 > 配置文件 > 默认 "CLAUDE.md"
    _claude_md_filename = (
        claude_md_file
        or file_cfg.get("claude_md_file")
        or "CLAUDE.md"
    )
    claude_md = _read_claude_md(root, filename=_claude_md_filename)
    skills_dir = _resolve_skills_dir(root)
    prompts_dir = _resolve_prompts_dir(root)

    _llm_provider = (
        _f("provider", llm_provider)           # CLI 参数 > agent_config.json
        or _providers_provider                  # providers.json chain[0].provider
        or os.environ.get("LLM_PROVIDER", "anthropic")
    )
    _llm_base_url = (
        _f("base_url", llm_base_url)
        or _main_entry.get("base_url", "")
        or os.environ.get("LLM_BASE_URL", "")
    )
    _model = (
        _f("model", model)                     # CLI 参数 > agent_config.json
        or _providers_model                     # providers.json chain[0].model
        or os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL)
    )

    # api_key：按最终确定的 provider 读取对应环境变量
    from mini_agent.llm.client_pool import _get_env_api_key as _geak
    _env_api_key = _geak(_llm_provider) or os.environ.get("ANTHROPIC_API_KEY", "")
    api_key = (
        file_cfg.get("api_key", "")            # agent_config.json（极少用）
        or _providers_api_key                   # providers.json chain[0] 的 key
        or _env_api_key                         # 环境变量（ANTHROPIC_API_KEY 等）
    )
    _verbose      = bool(_fb("verbose",      verbose,      False))
    _sandbox      = bool(_fb("sandbox",      sandbox,      False))
    _auto_approve = bool(_fb("yes",          auto_approve, False))
    _extra_system = _f("system", extra_system) or ""
    _max_llm_calls_v = _f("max_llm_calls", max_llm_calls)
    _max_llm_calls = int(_max_llm_calls_v) if _max_llm_calls_v is not None else int(os.environ.get("MAX_LLM_CALLS", 8))
    _agent_name = _f("agent_name", agent_name) or os.environ.get("AGENT_NAME", DEFAULT_AGENT_NAME)

    # CLI > 配置文件 > 环境变量 > 默认
    _use_sys_tc_env = os.environ.get("LLM_SYSTEM_TOOL_CALL", "").lower() in ("1", "true", "yes")
    _use_sys_tc = bool(_fb("system_tool_call", use_system_tool_call, _use_sys_tc_env))

    _sys_msg_fmt_cli = system_message_format or os.environ.get("LLM_SYSTEM_MESSAGE_FORMAT", "system_field")
    _sys_msg_fmt = _f("system_message_format", _sys_msg_fmt_cli) or "system_field"
    if _sys_msg_fmt not in ("system_field", "system_role"):
        import warnings
        warnings.warn(f"[config] Unknown system_message_format={_sys_msg_fmt!r}, falling back to 'system_field'.")
        _sys_msg_fmt = "system_field"

    # ── 组装子配置块 ──────────────────────────────────────────────────────────

    _mem_path_str = file_cfg.get("memory_store_path", "")
    _mem_path = Path(_mem_path_str) if _mem_path_str else None

    memory_cfg = MemoryConfig(
        enabled=_fb("memory_enabled", memory_enabled),
        backend=_f("memory_backend", None) or "local",
        store_path=_mem_path,
        global_enabled=_fb("memory_global_enabled", memory_global_enabled, True),
        global_top_k=_fn("memory_global_top_k", memory_global_top_k, 2),
        top_k=_fn("memory_top_k", memory_top_k, 3),
        decay_half_life_days=_fn("memory_decay_half_life_days", None, 30.0),
        max_entries=_fn("memory_max_entries", None, 500),
        lesson_rules_enabled=_fb("lesson_rules_enabled", None, True),
        lesson_fail_threshold=_fn("lesson_fail_threshold", None, 3),
        correction_detection_enabled=_fb("correction_detection_enabled", None, True),
    )

    compress_cfg = CompressConfig(
        enabled=_fb("auto_compress_enabled", auto_compress_enabled),
        threshold=_fn("auto_compress_threshold", auto_compress_threshold, 0.7),
        strategy=_f("auto_compress_strategy", None) or "turn_aligned",
        forget_orphan_tool_results=_fb("forget_policy_enabled", forget_policy_enabled),
    )

    tool_trim_cfg = ToolTrimConfig(
        enabled=_fb("tool_result_trim_enabled", tool_result_trim_enabled),
        threshold=_fn("tool_result_trim_threshold", tool_result_trim_threshold, 4000),
        bash_tail_ratio=_fn("tool_trim_bash_tail_ratio", tool_trim_bash_tail_ratio, 0.6),
        read_window_lines=_fn("tool_trim_read_window_lines", tool_trim_read_window_lines, 0),
        grep_max_lines=_fn("tool_trim_grep_max_lines", tool_trim_grep_max_lines, 50),
    )

    skill_cfg = SkillConfig(
        semantic_enabled=_fb("skill_semantic_enabled", skill_semantic_enabled),
        semantic_threshold=_fn("skill_semantic_threshold", skill_semantic_threshold, 0.72),
        tracking_enabled=_fb("skill_tracking_enabled", skill_tracking_enabled),
        chunking_enabled=_fb("skill_chunking_enabled", skill_chunking_enabled),
        compact_budget=_fn("skill_compact_budget", skill_compact_budget, 25_000),
        compact_per_skill=_fn("skill_compact_per_skill", skill_compact_per_skill, 5_000),
        matcher=_f("skill_matcher", None) or "keyword",
    )

    perception_cfg = PerceptionConfig(
        project_scan_enabled=_fb("project_scan_enabled", project_scan_enabled),
        file_watch_enabled=_fb("file_watch_enabled", file_watch_enabled),
        tool_cache_enabled=_fb("tool_cache_enabled", tool_cache_enabled),
        tool_cache_max_entries=_fn("tool_cache_max_entries", None, 256),
        token_estimate_enabled=_fb("token_estimate_enabled", token_estimate_enabled),
        token_warn_threshold=_fn("token_warn_threshold", token_warn_threshold, 0.75),
        tool_stats_enabled=_fb("tool_stats_enabled", tool_stats_enabled),
    )

    _session_dir_str = (
        (str(session_dir) if session_dir else "")
        or file_cfg.get("session_dir")
        or os.environ.get("SESSION_DIR", "")
    )
    session_cfg = SessionConfig(
        dir=Path(_session_dir_str) if _session_dir_str else None,
        fmt=_f("session_fmt", session_fmt) or os.environ.get("SESSION_FMT", "json"),
        auto_save=_fb("no_save_session", None, False) is False
                  and _fb("auto_save_session", auto_save_session, True),
        summary_enabled=_fb("session_summary_enabled", session_summary_enabled),
        summary_min_turns=_fn("session_summary_min_turns", session_summary_min_turns, 4),
        search_enabled=_fb("session_search_enabled", session_search_enabled),
        backend=_f("session_backend", None) or "local",
    )

    profile_cfg = ProfileConfig(
        enabled=_fb("profile_enabled", None),
        refresh_interval_entries=_fn("profile_refresh_interval_entries", None, 3),
        min_entries=_fn("profile_min_entries", None, 1),
        max_entries_for_profile=_fn("profile_max_entries_for_profile", None, 20),
    )

    _env_debug     = os.environ.get("LLM_DEBUG", "").lower() in ("1", "true", "yes")
    _env_debug_con = os.environ.get("LLM_DEBUG_CONSOLE", "").lower() in ("1", "true", "yes")
    _debug_llm_v     = bool(_fb("debug_llm",         debug_llm,         _env_debug))
    _debug_console_v = bool(_fb("debug_llm_console", debug_llm_console, _env_debug_con))
    _debug_log_dir_str = file_cfg.get("debug_log_dir") or os.environ.get("LLM_DEBUG_LOG_DIR", "")
    _debug_log_dir = Path(_debug_log_dir_str) if _debug_log_dir_str else None
    debug_cfg = DebugConfig(
        llm_enabled=_debug_llm_v,
        llm_console=_debug_console_v,
        log_dir=_debug_log_dir,
    )
    http_cfg = HttpConfig(
        enabled=_fb("http_enabled", None),
        host=_f("http_host", None) or "127.0.0.1",
        port=int(_f("http_port", None) or 8765),
        api_token=_f("http_api_token", None) or "",
        allowed_ips=file_cfg.get("http_allowed_ips", ["127.0.0.1", "::1"]),
        cors_origins=file_cfg.get("http_cors_origins", []),
        fs_readonly=_fb("http_fs_readonly", None),
        fs_excludes=file_cfg.get("http_fs_excludes", []),
        ring_maxlen=int(_f("http_ring_maxlen", None) or 2000),
    )

    retry_cfg = RetryConfig(
        max_retries=_fn("llm_retry_max", None, 15),
        delay=_fn("llm_retry_delay", None, 5.0),
        verbose=_fb("llm_retry_verbose", None, True),
        backoff_mode=_f("llm_retry_backoff_mode", llm_retry_backoff_mode) or "fixed",
        backoff_step=_fn("llm_retry_backoff_step", llm_retry_backoff_step, 60.0),
        backoff_max_delay=_fn("llm_retry_backoff_max_delay", llm_retry_backoff_max_delay, 0.0),
    )

    # ── LLM Fallback Chain ────────────────────────────────────────────────────
    # 来源优先级：providers.json > agent_config.json（providers.json 优先整体覆盖）
    # 若 providers.json 中有 llm_fallback_chain，使用它；否则从 agent_config.json 读取
    if "llm_fallback_chain" in _providers_cfg:
        _raw_chain: list = _providers_cfg["llm_fallback_chain"]
    else:
        _raw_chain = file_cfg.get("llm_fallback_chain", [])

    # 将 providers 块的全局设置合并到 chain 每条条目中
    _llm_fallback_chain: list = _merge_providers_into_chain(_raw_chain, _providers_cfg)

    # fallback_on：providers.json > agent_config.json
    _fallback_on_raw = (
        _providers_cfg.get("llm_fallback_on")
        or file_cfg.get("llm_fallback_on")
    )
    _llm_fallback_on: Optional[list] = list(_fallback_on_raw) if _fallback_on_raw else None

    # ── 初始化调试日志（需要在 AppConfig 构建前完成）─────────────────────────
    if _debug_llm_v:
        from mini_agent.llm.debug_logger import DebugConfig as LLMDebugConfig, init_debug_logger
        _dcfg = LLMDebugConfig(
            enabled=True,
            log_to_file=True,
            log_to_console=_debug_console_v,
            log_dir=_debug_log_dir,  # None = session-relative path resolved by debug_logger
        )
        init_debug_logger(_dcfg, root)

    # ── MCP server 配置解析 ───────────────────────────────────────────────────
    mcp_servers_raw: list[dict] = file_cfg.get("mcp_servers", [])
    mcp_server_list: list[MCPServerConfig] = []
    for raw in mcp_servers_raw:
        if not isinstance(raw, dict):
            continue
        mcp_server_list.append(MCPServerConfig(
            name=raw.get("name", "unnamed"),
            transport=raw.get("transport", "stdio"),
            command=raw.get("command", ""),
            args=raw.get("args", []),
            env=raw.get("env", {}),
            url=raw.get("url", ""),
            auto_approve=bool(raw.get("auto_approve", False)),
            timeout=float(raw.get("timeout", 10.0)),
            enabled=bool(raw.get("enabled", True)),
        ))
    mcp_cfg = MCPConfig(servers=mcp_server_list)

    _ws = file_cfg.get("web_search") if isinstance(file_cfg.get("web_search"), dict) else {}
    web_search_cfg = WebSearchConfig(
        provider=_ws.get("provider") or os.environ.get("WEB_SEARCH_PROVIDER", "duckduckgo"),
        api_key=_ws.get("api_key") or os.environ.get("WEB_SEARCH_API_KEY", ""),
        max_results=int(_ws.get("max_results") or os.environ.get("WEB_SEARCH_MAX_RESULTS", 5)),
        timeout=float(_ws.get("timeout") or os.environ.get("WEB_SEARCH_TIMEOUT", 10.0)),
    )

    _rm = file_cfg.get("reminder") if isinstance(file_cfg.get("reminder"), dict) else {}
    _reminder_enabled_val = reminder_enabled if reminder_enabled is not None else _rm.get("enabled", True)
    _reminders_dir_val: Optional[Path] = None
    if reminders_dir is not None:
        _reminders_dir_val = Path(reminders_dir)
    elif _rm.get("custom_dir"):
        _reminders_dir_val = Path(_rm["custom_dir"])
    reminder_cfg = ReminderConfig(
        enabled=bool(_reminder_enabled_val),
        custom_dir=_reminders_dir_val,
        tool_error_enabled=bool(_rm.get("tool_error_enabled", True)),
        post_tool_enabled=bool(_rm.get("post_tool_enabled", True)),
        user_intent_enabled=bool(_rm.get("user_intent_enabled", True)),
        pattern_enabled=bool(_rm.get("pattern_enabled", True)),
        max_per_turn=int(_rm.get("max_per_turn", 3)),
        verbose=bool(reminder_verbose if reminder_verbose is not None else _rm.get("verbose", False)),
    )

    # ── RoleAgent 配置组装 ────────────────────────────────────────────────────
    _ra = file_cfg.get("role_agent") if isinstance(file_cfg.get("role_agent"), dict) else {}
    # 总开关：CLI 参数 > 配置文件 > 默认 False（默认不启用）
    _ra_enabled = role_agent_enabled if role_agent_enabled is not None else _ra.get("enabled", False)
    # 白名单：CLI 参数（逗号分隔字符串或列表）> 配置文件 > 空列表（全部启用）
    _ra_allow = _parse_name_list(role_agent_allow) if role_agent_allow is not None else _parse_name_list(_ra.get("allow", []))
    # 黑名单：CLI 参数 > 配置文件 > 空列表（不屏蔽）
    _ra_block = _parse_name_list(role_agent_block) if role_agent_block is not None else _parse_name_list(_ra.get("block", []))
    # 自定义目录：CLI 参数 > 配置文件 > None（使用默认）
    _ra_dir: Optional[Path] = None
    if role_agent_dir is not None:
        _ra_dir = Path(role_agent_dir).expanduser()
    elif _ra.get("agents_dir"):
        _ra_dir = Path(_ra["agents_dir"]).expanduser()
    role_agent_cfg = RoleAgentConfig(
        enabled=bool(_ra_enabled),
        allow=_ra_allow,
        block=_ra_block,
        agents_dir=_ra_dir,
    )

    # ── EnvInfo 配置组装 ────────────────────────────────────────────────────
    _ei = file_cfg.get("env_info") if isinstance(file_cfg.get("env_info"), dict) else {}
    _ei_enabled = bool(_ei.get("enabled", True))
    _ei_providers = _ei.get("providers", None)
    _ei_provider_kwargs: dict = _ei.get("provider_kwargs", {})
    _ei_include_hostname = bool(_ei.get("include_hostname", False))
    _ei_include_username = bool(_ei.get("include_username", False))
    if _ei_include_hostname or _ei_include_username:
        sys_kwargs = _ei_provider_kwargs.setdefault("builtin.system", {})
        sys_kwargs.setdefault("include_hostname", _ei_include_hostname)
        sys_kwargs.setdefault("include_username", _ei_include_username)
    env_info_cfg = EnvInfoConfig(
        enabled=_ei_enabled,
        providers=_ei_providers,
        provider_kwargs=_ei_provider_kwargs,
        include_hostname=_ei_include_hostname,
        include_username=_ei_include_username,
    )

    # ── Workdir 知识层配置组装（W2，对应设计文档 8.2 节）─────────────────────
    _wk = file_cfg.get("workdir_knowledge") if isinstance(file_cfg.get("workdir_knowledge"), dict) else {}
    workdir_knowledge_cfg = WorkdirKnowledgeConfig(
        enabled=bool(_wk.get("enabled", True)),
        work_thread_relation_days=float(_wk.get("work_thread_relation_days", 7.0)),
        open_threads_inject_limit=int(_wk.get("open_threads_inject_limit", 5)),
    )

    return AppConfig(
        api_key=api_key,
        model=_model,
        max_tokens=int(file_cfg.get("max_tokens") or os.environ.get("CLAUDE_MAX_TOKENS", DEFAULT_MAX_TOKENS)),
        project_root=root,
        skills_dir=skills_dir,
        prompts_dir=prompts_dir,
        verbose=_verbose,
        sandbox=_sandbox,
        auto_approve=_auto_approve,
        claude_md_content=claude_md,
        system_extra=_extra_system,
        llm_provider=_llm_provider,
        llm_base_url=_llm_base_url,
        use_system_tool_call=_use_sys_tc,
        max_llm_calls=_max_llm_calls,
        agent_name=_agent_name,
        system_message_format=_sys_msg_fmt,
        # 子配置块
        memory=memory_cfg,
        compress=compress_cfg,
        tool_trim=tool_trim_cfg,
        skill=skill_cfg,
        perception=perception_cfg,
        session=session_cfg,
        profile=profile_cfg,
        debug=debug_cfg,
        http=http_cfg,
        retry=retry_cfg,
        mcp=mcp_cfg,
        web_search=web_search_cfg,
        reminder=reminder_cfg,
        role_agent=role_agent_cfg,
        env_info=env_info_cfg,
        workdir_knowledge=workdir_knowledge_cfg,
        llm_fallback_chain=_llm_fallback_chain,
        llm_fallback_on=_llm_fallback_on,
    )


# ── 辅助函数（不变）──────────────────────────────────────────────────────────

def _load_config_file(path: Path) -> dict:
    import json as _json
    import warnings as _warnings
    try:
        text = path.read_text(encoding="utf-8")
        data = _json.loads(text)
        if not isinstance(data, dict):
            _warnings.warn(f"[config] {path}: expected a JSON object. Ignored.")
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


def _load_providers_config(path: Path) -> dict:
    """
    加载 providers 配置文件（默认 providers.json）。

    该文件专门存放含 API key 的敏感配置，应加入 .gitignore。
    支持的顶层字段：
      llm_fallback_chain  — 与 agent_config.json 中的同名字段相同格式
      llm_fallback_on     — fallback 触发条件
      providers           — per-provider 的全局设置（api_keys、base_url 等）

    providers 字段示例：
      {
        "anthropic": {
          "api_keys": ["sk-ant-aaa", "sk-ant-bbb"],
          "key_rotation": "round_robin",
          "key_cooldown": 60
        },
        "openai": {
          "api_keys": ["sk-openai-111", "sk-openai-222"]
        }
      }

    返回 dict，不存在时返回 {}（不报警告，属于可选文件）。
    """
    import json as _json
    import warnings as _warnings
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        data = _json.loads(text)
        if not isinstance(data, dict):
            _warnings.warn(f"[config] {path}: expected a JSON object. Ignored.")
            return {}
        return data
    except _json.JSONDecodeError as e:
        _warnings.warn(f"[config] Failed to parse providers config {path}: {e}")
        return {}
    except Exception as e:
        _warnings.warn(f"[config] Error loading providers config {path}: {e}")
        return {}


def _merge_providers_into_chain(
    chain: list,
    providers_cfg: dict,
) -> list:
    """
    将 providers 块的全局设置合并到 fallback chain 的每条条目中。

    chain 中每条条目的显式字段优先级高于 providers 块的全局设置。
    仅补充缺少的字段（不覆盖已有值）。

    例如 providers.json：
      {
        "providers": {
          "anthropic": { "api_keys": ["sk-ant-aaa", "sk-ant-bbb"], "key_rotation": "round_robin" }
        },
        "llm_fallback_chain": [
          { "provider": "anthropic", "model": "claude-opus-4-7" }
          // ↑ 该条目会自动补充 api_keys 和 key_rotation
        ]
      }
    """
    providers_map: dict = providers_cfg.get("providers", {})
    if not providers_map or not chain:
        return chain

    merged = []
    for entry in chain:
        provider_name = entry.get("provider", "")
        global_settings = providers_map.get(provider_name, {})
        if not global_settings:
            merged.append(entry)
            continue
        # 全局设置作为默认值，entry 中已有的字段优先
        combined = {**global_settings, **entry}
        merged.append(combined)
    return merged
