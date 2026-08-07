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
    TechRadarConfig,
    EcosystemPositioningConfig,
    RetryConfig,
    EnsembleConfig,
    RoleAgentConfig,
    GoalModeConfig,
    TurnJudgeConfig,
    EnvInfoConfig,
    ReminderConfig,
    ProprioceptionConfig,
    AffordanceConfig,
    WorkflowConfig,
    FormatCorrectionConfig,
    PrivacyConfig,
    WorkdirKnowledgeConfig,
    GlobalKnowledgeConfig,
    DigestAdvisorConfig,
    CronConfig,
    AutonomyConfig,
    ObservabilityConfig,
    SchedulerConfig,
    DEFAULT_MODEL,
    DEFAULT_AGENT_NAME,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MAX_TURNS,
)
from .prompt_builder import _read_claude_md, _resolve_prompts_dir, _resolve_skills_dir
from .param_registry import NESTED_CONFIG_BLOCKS, load_all_nested_blocks, load_nested_block, apply_overrides


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
    simple_mode: Optional[bool] = None,
    raw_output: Optional[bool] = None,
    show_reasoning: Optional[bool] = None,
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
    raw_store_enabled: Optional[bool] = None,
    raw_store_max_entries: Optional[int] = None,
    raw_store_max_total_chars: Optional[int] = None,
    smart_summary_enabled: Optional[bool] = None,
    smart_summary_threshold: Optional[int] = None,
    smart_summary_max_input_chars: Optional[int] = None,
    smart_summary_model: Optional[str] = None,
    forget_policy_enabled: Optional[bool] = None,
    skill_semantic_enabled: Optional[bool] = None,
    skill_semantic_threshold: Optional[float] = None,
    skill_tracking_enabled: Optional[bool] = None,
    skill_chunking_enabled: Optional[bool] = None,
    skill_compact_budget: Optional[int] = None,
    skill_compact_per_skill: Optional[int] = None,
    skill_keyword_activation_enabled: Optional[bool] = None,
    skill_auto_unload_enabled: Optional[bool] = None,
    skill_auto_unload_idle_seconds: Optional[int] = None,
    project_scan_enabled: Optional[bool] = None,
    file_watch_enabled: Optional[bool] = None,
    tool_cache_enabled: Optional[bool] = None,
    token_estimate_enabled: Optional[bool] = None,
    token_warn_threshold: Optional[float] = None,
    tool_stats_enabled: Optional[bool] = None,
    artifact_auto_detect_enabled: Optional[bool] = None,
    # reminder 系统
    reminder_enabled: Optional[bool] = None,
    reminders_dir: Optional[Path] = None,
    reminder_verbose: Optional[bool] = None,
    # 工具调用格式纠错系统
    format_correction_enabled: Optional[bool] = None,
    format_correction_max_retries: Optional[int] = None,
    format_correction_verbose: Optional[bool] = None,
    # 隐私信息保护
    privacy_enabled: Optional[bool] = None,
    privacy_secrets: Optional[list] = None,   # [{"name": str, "value": str}, ...]
    privacy_verbose: Optional[bool] = None,
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

    # providers.json 里的 api_key 自动注入为标准环境变量（只补充，不覆盖已有值）
    # 这样各 provider 实现在初始化时无需额外传参即可读取到 key
    from mini_agent.llm.client_pool import inject_env_from_providers as _inject_env
    _inject_env(_providers_cfg)

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
    _simple_mode_env = os.environ.get("MINI_AGENT_SIMPLE_MODE", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    _simple_mode  = bool(_fb("simple_mode",  simple_mode,  _simple_mode_env))
    _raw_output_env = os.environ.get("MINI_AGENT_RAW_OUTPUT", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    _raw_output   = bool(_fb("raw_output",   raw_output,   _raw_output_env))
    _show_reasoning_env = os.environ.get("MINI_AGENT_SHOW_REASONING", "").strip().lower() not in (
        "0", "false", "no", "off",
    ) if os.environ.get("MINI_AGENT_SHOW_REASONING") else True
    _show_reasoning = bool(_fb("show_reasoning", show_reasoning, _show_reasoning_env))
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

    # per_turn_retrieval_enabled 支持两种写法：
    #   1) 扁平键 "memory_per_turn_retrieval_enabled"（与本文件里其余
    #      memory_* 键的既有约定一致，CLI/环境变量扩展时也走这条）
    #   2) 嵌套写法 {"memory": {"per_turn_retrieval_enabled": false}}（更符合
    #      直觉、且与 MemoryConfig 字段名直接对应，这里额外兼容，避免用户
    #      按字段名嵌套写却被静默忽略）
    # 注意：本文件其余 library_index_enabled/library_shelf_search_enabled/
    # library_wiki_search_primary 等字段目前都还没有接入 file_cfg，只能通过
    # 代码里直接构造 MemoryConfig(...) 设置，如果也需要从 agent_config.json
    # 配置，需要照这里的模式各自补一条。
    _mem_dict = file_cfg.get("memory") if isinstance(file_cfg.get("memory"), dict) else {}
    _per_turn_retrieval_default = bool(_mem_dict.get("per_turn_retrieval_enabled", True))

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
        per_turn_retrieval_enabled=_fb(
            "memory_per_turn_retrieval_enabled", None, _per_turn_retrieval_default
        ),
    )

    compress_cfg = CompressConfig(
        enabled=_fb("auto_compress_enabled", auto_compress_enabled),
        threshold=_fn("auto_compress_threshold", auto_compress_threshold, 0.7),
        strategy=_f("auto_compress_strategy", None) or "turn_aligned",
        forget_orphan_tool_results=_fb("forget_policy_enabled", forget_policy_enabled),
        # ── 触发器开关（每个独立配置，默认关闭/宽松，向后兼容）──────────────
        turn_count_trigger_enabled=_fb("compact_turn_count_trigger_enabled", None, False),
        max_turns_before_compact=_fn("compact_max_turns", None, 20),
        tool_call_count_trigger_enabled=_fb("compact_tool_call_count_trigger_enabled", None, False),
        max_tool_calls_before_compact=_fn("compact_max_tool_calls", None, 50),
        topic_shift_detection=_f("compact_topic_shift_detection", None) or "off",
        topic_shift_keyword_overlap_threshold=_fn(
            "compact_topic_shift_keyword_overlap_threshold", None, 0.15
        ),
        topic_shift_min_budget_pct=_fn(
            "compact_topic_shift_min_budget_pct", None, 0.2
        ),
        redundancy_detection_enabled=_fb("compact_redundancy_detection_enabled", None, False),
        redundancy_tool_result_ratio=_fn("compact_redundancy_tool_result_ratio", None, 0.6),
        compact_cooldown_turns=_fn("compact_cooldown_turns", None, 3),
        require_confirmation=_fb("compact_require_confirmation", None, False),
        max_message_chars_for_compact=_fn("compact_max_message_chars", None, 10000),
        # [BUGFIX] 以下三个字段此前漏了从配置文件读取，dataclass 默认值会
        # 无条件生效，导致 agent_config.json 里配置的 model_context_window
        # 等值永远不生效（compact_precheck_enabled 默认 True、
        # compact_precheck_threshold 默认 0.85 恰好"看起来正常"，容易被忽略；
        # model_context_window 默认 0，最容易暴露问题）。
        compact_precheck_enabled=_fb("compact_precheck_enabled", None, True),
        compact_precheck_threshold=_fn("compact_precheck_threshold", None, 0.85),
        model_context_window=_fn("model_context_window", None, 0),
        # [compact_mechanism_improvement_plan P0/P1/P2，2026-07 三次更新]
        # 此前遗漏了从配置文件读取（dataclass 默认值无条件生效，导致
        # agent_config.json 里配置这些字段永远不生效），补齐 flat-key 映射：
        goal_aware_weighting_enabled=_fb("compact_goal_aware_weighting_enabled", None, False),
        decision_extraction_on_compact_with_skills_enabled=_fb(
            "compact_decision_extraction_enabled", None, False
        ),
        decision_recall_tool_enabled=_fb("compact_decision_recall_tool_enabled", None, True),
        safe_point_gating_enabled=_fb("compact_safe_point_gating_enabled", None, False),
        composite_intensity_enabled=_fb("compact_composite_intensity_enabled", None, False),
        composite_intensity_threshold=_fn(
            "compact_composite_intensity_threshold", None, 1.2
        ),
        audit_enabled=_fb("compact_audit_enabled", None, False),
        audit_async=_fb("compact_audit_async", None, True),
        **(
            {"audit_compact_reasons": file_cfg["compact_audit_reasons"]}
            if isinstance(file_cfg.get("compact_audit_reasons"), list)
            else {}
        ),
    )

    tool_trim_cfg = ToolTrimConfig(
        enabled=_fb("tool_result_trim_enabled", tool_result_trim_enabled),
        threshold=_fn("tool_result_trim_threshold", tool_result_trim_threshold, 4000),
        bash_tail_ratio=_fn("tool_trim_bash_tail_ratio", tool_trim_bash_tail_ratio, 0.6),
        read_window_lines=_fn("tool_trim_read_window_lines", tool_trim_read_window_lines, 0),
        grep_max_lines=_fn("tool_trim_grep_max_lines", tool_trim_grep_max_lines, 50),
        raw_store_enabled=_fb("raw_store_enabled", raw_store_enabled, True),
        raw_store_max_entries=_fn("raw_store_max_entries", raw_store_max_entries, 128),
        raw_store_max_total_chars=_fn("raw_store_max_total_chars", raw_store_max_total_chars, 5_000_000),
        smart_summary_enabled=_fb("smart_summary_enabled", smart_summary_enabled, False),
        smart_summary_threshold=_fn("smart_summary_threshold", smart_summary_threshold, 12000),
        smart_summary_max_input_chars=_fn(
            "smart_summary_max_input_chars", smart_summary_max_input_chars, 60000
        ),
        smart_summary_model=_f("smart_summary_model", smart_summary_model) or "",
    )

    skill_cfg = SkillConfig(
        semantic_enabled=_fb("skill_semantic_enabled", skill_semantic_enabled),
        semantic_threshold=_fn("skill_semantic_threshold", skill_semantic_threshold, 0.72),
        tracking_enabled=_fb("skill_tracking_enabled", skill_tracking_enabled),
        chunking_enabled=_fb("skill_chunking_enabled", skill_chunking_enabled),
        compact_budget=_fn("skill_compact_budget", skill_compact_budget, 25_000),
        compact_per_skill=_fn("skill_compact_per_skill", skill_compact_per_skill, 5_000),
        matcher=_f("skill_matcher", None) or "keyword",
        keyword_activation_enabled=_fb("skill_keyword_activation_enabled", skill_keyword_activation_enabled, default=False),
        auto_unload_enabled=_fb("skill_auto_unload_enabled", skill_auto_unload_enabled, default=True),
        auto_unload_idle_seconds=_fn("skill_auto_unload_idle_seconds", skill_auto_unload_idle_seconds, 1800),
    )

    perception_cfg = PerceptionConfig(
        project_scan_enabled=_fb("project_scan_enabled", project_scan_enabled),
        file_watch_enabled=_fb("file_watch_enabled", file_watch_enabled),
        tool_cache_enabled=_fb("tool_cache_enabled", tool_cache_enabled),
        tool_cache_max_entries=_fn("tool_cache_max_entries", None, 256),
        token_estimate_enabled=_fb("token_estimate_enabled", token_estimate_enabled),
        token_warn_threshold=_fn("token_warn_threshold", token_warn_threshold, 0.75),
        tool_stats_enabled=_fb("tool_stats_enabled", tool_stats_enabled),
        artifact_auto_detect_enabled=_fb("artifact_auto_detect_enabled", artifact_auto_detect_enabled),
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
        multi_user_enabled=_fb("http_multi_user_enabled", None),
    )

    retry_cfg = RetryConfig(
        max_retries=_fn("llm_retry_max", None, 15),
        delay=_fn("llm_retry_delay", None, 5.0),
        verbose=_fb("llm_retry_verbose", None, True),
        backoff_mode=_f("llm_retry_backoff_mode", llm_retry_backoff_mode) or "fixed",
        backoff_step=_fn("llm_retry_backoff_step", llm_retry_backoff_step, 60.0),
        backoff_max_delay=_fn("llm_retry_backoff_max_delay", llm_retry_backoff_max_delay, 0.0),
    )

    ensemble_cfg = EnsembleConfig(
        mode=_f("ensemble_mode", None) or "off",
        granularity=_f("ensemble_granularity", None) or "both",
        n=int(_fn("ensemble_n", None, 3)),
        execution=_f("ensemble_execution", None) or "parallel",
        max_concurrency=int(_fn("ensemble_max_concurrency", None, 3)),
        judge_strategy=_f("ensemble_judge_strategy", None) or "llm_judge",
        judge_model=_f("ensemble_judge_model", None) or None,
        judge_provider=_f("ensemble_judge_provider", None) or None,
        early_stop_on_consensus=_fb("ensemble_early_stop_on_consensus", None, True),
        max_extra_cost_ratio=_fn("ensemble_max_extra_cost_ratio", None, 2.0),
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

    # [统一参数机制] 以下这批"字段名与 dataclass 一一对应、无 CLI 覆盖"的
    # 嵌套 block，统一通过 param_registry.NESTED_CONFIG_BLOCKS 通用加载，
    # 不再逐个手写 `XxxConfig(field=int(_x.get(...)), ...)`——新增字段只
    # 需要改 models.py 里的 dataclass 定义，这里不需要再改。见
    # docs/param-system-guide.md。
    _nested_blocks = load_all_nested_blocks(file_cfg, NESTED_CONFIG_BLOCKS)
    tech_radar_cfg = _nested_blocks["tech_radar"]
    # web_search 是"配置文件 > 环境变量 > 默认值"三级回退，用 load_nested_block
    # 的 env_fallback 参数单独处理（其它 block 都只有"配置文件 > 默认值"两级，
    # 已经在上面 load_all_nested_blocks 里统一处理过，这里不重复）。
    web_search_cfg = load_nested_block(
        file_cfg.get("web_search") if isinstance(file_cfg.get("web_search"), dict) else {},
        WebSearchConfig,
        env_fallback={
            "provider": "WEB_SEARCH_PROVIDER",
            "api_key": "WEB_SEARCH_API_KEY",
            "max_results": "WEB_SEARCH_MAX_RESULTS",
            "timeout": "WEB_SEARCH_TIMEOUT",
        },
    )
    ecosystem_positioning_cfg = _nested_blocks["ecosystem_positioning"]

    # [统一参数机制] reminder 里多数字段（tool_error_enabled 等 6 个开关 +
    # max_per_turn）走通用加载；enabled/custom_dir/verbose 这 3 个字段额外
    # 支持 CLI 覆盖（custom_dir 还要做 str→Path 转换），通用加载跑完之后
    # 用 apply_overrides() 按 "CLI 参数 > 配置文件 > 默认值" 的优先级覆盖，
    # 逻辑与改造前完全一致，只是不再需要为 tool_error_enabled 这些没有
    # CLI 覆盖的字段手写 bool(...) 转换。
    _rm = file_cfg.get("reminder") if isinstance(file_cfg.get("reminder"), dict) else {}
    _reminders_dir_val: Optional[Path] = None
    if reminders_dir is not None:
        _reminders_dir_val = Path(reminders_dir)
    elif _rm.get("custom_dir"):
        _reminders_dir_val = Path(_rm["custom_dir"])
    reminder_cfg = apply_overrides(
        _nested_blocks["reminder"],
        enabled=reminder_enabled,
        custom_dir=_reminders_dir_val,
        verbose=reminder_verbose,
    )

    # ── 工具调用格式纠错配置组装 ──────────────────────────────────────────────
    # [统一参数机制] format_correction 只有 enabled/max_retries_per_turn/
    # verbose 三个字段，且都支持 CLI 覆盖，通用加载 + apply_overrides 即可。
    format_correction_cfg = apply_overrides(
        _nested_blocks["format_correction"],
        enabled=format_correction_enabled,
        max_retries_per_turn=format_correction_max_retries,
        verbose=format_correction_verbose,
    )

    # ── 隐私保护配置组装 ──────────────────────────────────────────────────────
    # [统一参数机制] enabled/verbose 走通用 CLI 覆盖；secrets 是"配置文件
    # 里的 + 代码传入的"合并（去重由 PrivacyGuard 负责），不是简单的
    # "谁优先级高用谁"，所以单独算好合并结果后一起通过 apply_overrides
    # 覆盖进去；auto_env_patterns/placeholder_prefix 没有 CLI 覆盖，通用
    # 加载直接读 file_cfg 里的值即可，不需要在这里重复处理。
    _pv = file_cfg.get("privacy") if isinstance(file_cfg.get("privacy"), dict) else {}
    _pv_secrets = list(_pv.get("secrets", []))
    if privacy_secrets:
        _pv_secrets = _pv_secrets + [s for s in privacy_secrets if s not in _pv_secrets]
    privacy_cfg = apply_overrides(
        _nested_blocks["privacy"],
        enabled=privacy_enabled,
        secrets=_pv_secrets,
        verbose=privacy_verbose,
    )

    # ── RoleAgent 配置组装 ────────────────────────────────────────────────────
    # [统一参数机制] enabled 走通用 CLI 覆盖；allow/block 需要
    # `_parse_name_list()` 解析逗号分隔字符串，agents_dir 需要 str→Path，
    # 都是通用机制处理不了的转换，预先算好最终值后通过 apply_overrides
    # 覆盖进去。
    _ra = file_cfg.get("role_agent") if isinstance(file_cfg.get("role_agent"), dict) else {}
    _ra_allow = _parse_name_list(role_agent_allow) if role_agent_allow is not None else _parse_name_list(_ra.get("allow", []))
    _ra_block = _parse_name_list(role_agent_block) if role_agent_block is not None else _parse_name_list(_ra.get("block", []))
    _ra_dir: Optional[Path] = None
    if role_agent_dir is not None:
        _ra_dir = Path(role_agent_dir).expanduser()
    elif _ra.get("agents_dir"):
        _ra_dir = Path(_ra["agents_dir"]).expanduser()
    role_agent_cfg = apply_overrides(
        _nested_blocks["role_agent"],
        enabled=role_agent_enabled,
        allow=_ra_allow,
        block=_ra_block,
        agents_dir=_ra_dir,
    )

    # ── GoalMode 配置组装 ────────────────────────────────────────────────────
    # [SYS-GOAL-MODE] 目前只支持从配置文件的 goal_mode: {...} 块读取，暂不接 CLI 参数
    # （/goal 命令本身的运行时参数走命令行 slash 参数，不走这里）。
    # [统一参数机制] goal_mode / turn_judge 同样字段名一一对应，走通用加载
    # （见上方 tech_radar 处的说明）。
    goal_mode_cfg = _nested_blocks["goal_mode"]
    turn_judge_cfg = _nested_blocks["turn_judge"]

    # ── EnvInfo 配置组装 ────────────────────────────────────────────────────
    # [统一参数机制] enabled/providers/provider_kwargs/include_hostname/
    # include_username 都通用加载；provider_kwargs 里 "builtin.system" 这
    # 一项是从 include_hostname/include_username 派生出来的（不是配置文件
    # 里直接写的字段），通用加载结束后单独做一次派生填充。
    env_info_cfg = _nested_blocks["env_info"]
    if env_info_cfg.include_hostname or env_info_cfg.include_username:
        sys_kwargs = env_info_cfg.provider_kwargs.setdefault("builtin.system", {})
        sys_kwargs.setdefault("include_hostname", env_info_cfg.include_hostname)
        sys_kwargs.setdefault("include_username", env_info_cfg.include_username)

    # [统一参数机制] workdir_knowledge / global_knowledge / proprioception /
    # affordance 同样走通用加载（见上方 tech_radar 处的说明）。
    workdir_knowledge_cfg = _nested_blocks["workdir_knowledge"]
    global_knowledge_cfg = _nested_blocks["global_knowledge"]
    proprioception_cfg = _nested_blocks["proprioception"]
    affordance_cfg = _nested_blocks["affordance"]

    # [具身改进 B3][workflow机制改进计划.md] Workflow 执行/看护配置组装
    # [统一参数机制] 走通用加载，无 CLI 覆盖。这里额外修复了一个此前存在
    # 的 bug：手写构造代码里从未给 `max_total_tokens` /
    # `session_to_workflow_enabled` / `condition_static_check_enabled` /
    # `dry_run_preview_on_generate` / `git_hint_enabled` 这 5 个字段传值，
    # 导致它们和曾经的 autonomy/observability 一样，无论 agent_config.json
    # 里写了什么，实际生效的永远是 dataclass 默认值——通用加载统一处理
    # 全部字段，不会再有"手写代码漏传某个字段"的问题。
    # `approval_wait_timeout_seconds`/`human_input_wait_timeout_seconds`
    # 这两个"默认值非 None，但允许显式 null 覆盖成 None"的字段，由
    # `load_nested_block()` 的显式 null 规则统一处理，不需要像原来那样
    # 为每个字段单独写一次"是否在 dict 里 / 是否为 None"的三态判断。
    workflow_cfg = _nested_blocks["workflow"]

    # [统一参数机制] digest_advisor / cron / autonomy（含
    # fairness_time_slicing_enabled 等 P1-P4 字段）/ observability 同样走
    # 通用加载（见上方 tech_radar 处的说明）。以前 autonomy/observability
    # 两个 block 曾长期"写了当没写"（AppConfig(...) 构造从未传
    # autonomy=/observability=），本次统一改造后由 load_all_nested_blocks
    # 一次性正确加载全部 12 个嵌套 block，不会再出现这类"某个 block 漏接"
    # 的问题——所有 block 的加载路径完全一致，是同一段通用代码。
    digest_advisor_cfg = _nested_blocks["digest_advisor"]
    growth_advisor_cfg = _nested_blocks["growth_advisor"]
    cron_cfg = _nested_blocks["cron"]
    autonomy_cfg = _nested_blocks["autonomy"]
    observability_cfg = _nested_blocks["observability"]
    # [goal_cron_unified_scheduler_improvement_plan.md P5 遗留缺陷修复]
    # `scheduler` block 此前从未被 `_nested_blocks` 提取、也从未传给
    # `AppConfig(...)`（见 param_registry.py 里 `NestedBlockSpec("scheduler",
    # ...)` 新增处的说明），本次一并补齐，其余 block 的加载路径完全不变。
    scheduler_cfg = _nested_blocks["scheduler"]

    return AppConfig(
        api_key=api_key,
        model=_model,
        max_tokens=int(file_cfg.get("max_tokens") or os.environ.get("CLAUDE_MAX_TOKENS", DEFAULT_MAX_TOKENS)),
        project_root=root,
        skills_dir=skills_dir,
        prompts_dir=prompts_dir,
        verbose=_verbose,
        sandbox=_sandbox,
        simple_mode=_simple_mode,
        raw_output=_raw_output,
        show_reasoning=_show_reasoning,
        auto_approve=_auto_approve,
        claude_md_content=claude_md,
        system_extra=_extra_system,
        llm_provider=_llm_provider,
        llm_base_url=_llm_base_url,
        use_system_tool_call=_use_sys_tc,
        max_llm_calls=_max_llm_calls,
        max_turns=_fn("max_turns", None, DEFAULT_MAX_TURNS),
        # [SYS-MAX-TURNS-POLICY] "stop" | "continue" | "compact_continue"
        max_turns_on_limit=(_f("max_turns_on_limit", None) or "stop"),
        max_turns_hard_limit=_fn("max_turns_hard_limit", None, DEFAULT_MAX_TURNS * 5),
        agent_name=_agent_name,
        system_message_format=_sys_msg_fmt,
        notepad_enabled=_fb("notepad_enabled", None, True),
        # [compact_mechanism_improvement_plan P2-B]
        recall_history_enabled=_fb("recall_history_enabled", None, False),
        recall_history_mode=_f("recall_history_mode", None) or "keyword",
        # [SYS-BASH-STREAM] bash 工具是否实时打印子进程输出（边跑边看），
        # 默认 False，保持旧版一次性返回行为不变；agent_config.json 里
        # 配置 "bash_stream_output_enabled": true 即可开启。
        bash_stream_output_enabled=_fb("bash_stream_output_enabled", None, False),
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
        ensemble=ensemble_cfg,
        mcp=mcp_cfg,
        web_search=web_search_cfg,
        tech_radar=tech_radar_cfg,
        ecosystem_positioning=ecosystem_positioning_cfg,
        reminder=reminder_cfg,
        proprioception=proprioception_cfg,
        affordance=affordance_cfg,
        workflow=workflow_cfg,
        format_correction=format_correction_cfg,
        role_agent=role_agent_cfg,
        goal_mode=goal_mode_cfg,
        turn_judge=turn_judge_cfg,
        env_info=env_info_cfg,
        workdir_knowledge=workdir_knowledge_cfg,
        global_knowledge=global_knowledge_cfg,
        privacy=privacy_cfg,
        digest_advisor=digest_advisor_cfg,
        growth_advisor=growth_advisor_cfg,
        cron=cron_cfg,
        autonomy=autonomy_cfg,
        scheduler=scheduler_cfg,
        observability=observability_cfg,
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
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.config.loader._load_config_file')
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
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.config.loader._load_providers_config')
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