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
    GoalExecutionSpecConfig,
    ExecutionPhaseConfig,
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
    CycleTuningConfig,
    DEFAULT_MODEL,
    DEFAULT_AGENT_NAME,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MAX_TURNS,
)
from .prompt_builder import _read_claude_md, _resolve_prompts_dir, _resolve_skills_dir
from .param_registry import (
    NESTED_CONFIG_BLOCKS,
    load_all_nested_blocks,
    load_nested_block,
    load_nested_block_with_flat_compat,
    apply_overrides,
)


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

    # [external_projects_agent_skill_workflow_integration_plan.md 第2节]
    # 外部项目（`<root>/project.yaml` 存在）调用 workflow/skill_agent 等
    # 主 agent 功能时，LLM 相关配置（provider/model/api_key）应该沿用
    # 主 agent 项目的配置，而不是要求每个外部项目自己维护一份
    # agent_config.json/providers.json——外部项目关心的是自己的业务逻辑
    # （抓取、报告），不应该也不需要重复管理 API key。约定：外部项目固定
    # 挂在 `<主项目根>/external_projects/<name>/` 下（与
    # `external_projects_workspace_plan.md` 5.1 节的目录结构一致），据此
    # 直接推导主项目根，不需要额外配置或猜测式向上遍历。
    _main_project_root = _resolve_main_project_root(root)

    # ── JSON 配置文件 ─────────────────────────────────────────────────────────
    file_cfg: dict = {}
    if config_file is not None:
        file_cfg = _load_config_file(config_file)
    else:
        default_cfg_path = root / "agent_config.json"
        if not default_cfg_path.exists() and _main_project_root is not None:
            default_cfg_path = _main_project_root / "agent_config.json"
        if default_cfg_path.exists():
            file_cfg = _load_config_file(default_cfg_path)

    # ── Providers 独立配置文件（含 API key，默认 providers.json）─────────────
    # 优先级：--providers-config CLI 参数 > 项目根目录的 providers.json >
    # 主 agent 项目根目录的 providers.json（外部项目专属 fallback，见上）
    _providers_cfg_path: Optional[Path] = providers_config_file
    if _providers_cfg_path is None:
        if (root / "providers.json").exists():
            _providers_cfg_path = root / "providers.json"
        elif _main_project_root is not None and (_main_project_root / "providers.json").exists():
            _providers_cfg_path = _main_project_root / "providers.json"
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

    # [flat_nested_config_unification_migration_plan.md Stage 2 第 4 类]
    # per_turn_retrieval_enabled 现在统一走下面 `load_nested_block_with_
    # flat_compat()` 的通用优先级（nested 写法 > 旧扁平键
    # "memory_per_turn_retrieval_enabled" > dataclass 默认值 True）。
    # 迁移前这里是反过来的（扁平键存在时会覆盖 nested 写法），是本次迁移
    # 顺带修正的一处历史不一致，其余 library_index_enabled/wiki_* 等字段
    # 本来就只支持 nested 写法，不受影响。
    # memory：30 字段，flat key 与字段名不一致（memory_top_k → top_k 等）。
    # enabled/global_enabled/global_top_k/top_k 有 CLI 覆盖需求；
    # store_path 需要 str→Path 转换，单独用 apply_overrides 处理；其余
    # （decay_half_life_days/max_entries/lesson_*/correction_detection_
    # enabled/per_turn_retrieval_enabled 等）只走 nested + flat 兼容层
    # （flat_key_map 里登记的都是历史上确实存在过的扁平 key，字段名与
    # flat key 相同的这批此前直接是 dataclass 默认值生效，现在同样能被
    # 对应的扁平 key 覆盖，属于本次迁移顺带补齐的兼容面，不改变"这些字段
    # 默认值不变"这个事实）。library_index_*/wiki_*/embedding_*/
    # consolidation_*/lifecycle_* 这批本来就只支持 nested 写法，不受影响。
    memory_cfg = apply_overrides(
        load_nested_block_with_flat_compat(
            file_cfg, "memory", MemoryConfig,
            flat_key_map={
                "enabled": "memory_enabled",
                "backend": "memory_backend",
                "global_enabled": "memory_global_enabled",
                "global_top_k": "memory_global_top_k",
                "top_k": "memory_top_k",
                "decay_half_life_days": "memory_decay_half_life_days",
                "max_entries": "memory_max_entries",
                "lesson_rules_enabled": "lesson_rules_enabled",
                "lesson_fail_threshold": "lesson_fail_threshold",
                "correction_detection_enabled": "correction_detection_enabled",
                "per_turn_retrieval_enabled": "memory_per_turn_retrieval_enabled",
            },
        ),
        enabled=(bool(memory_enabled) if memory_enabled is not None else None),
        store_path=_mem_path,
        global_enabled=(bool(memory_global_enabled) if memory_global_enabled is not None else None),
        global_top_k=(int(memory_global_top_k) if memory_global_top_k is not None else None),
        top_k=(int(memory_top_k) if memory_top_k is not None else None),
    )

    # [flat_nested_config_unification_migration_plan.md Stage 2 第 4 类]
    # compress：40 字段。enabled/threshold/forget_orphan_tool_results 三个
    # 有 CLI/函数参数覆盖需求，用 apply_overrides 处理；其余（含此前
    # extraction_trigger_*/entity_digest_*/selective_* 等从未接入 flat
    # key、只能靠 nested 写法的字段）统一走 flat_key_map，nested 优先、
    # 旧 flat key 兜底，dataclass 默认值垫底。
    # `strategy` 字段是个例外：历史遗留行为是"不管 nested/flat 是否设置，
    # 只要 CLI 未传，就用硬编码的 'turn_aligned' 而不是 dataclass 默认值
    # 'compact_with_skills'"（`_f("auto_compress_strategy", None) or
    # "turn_aligned"` 这行本身就是"永远非 None"，此前会无条件覆盖掉
    # nested 里设置的 strategy）——按迁移计划"不改变行为"的要求，这里原样
    # 保留这个历史怪癖，不放进 flat_key_map（放进去会被 nested 优先规则
    # 打破，导致默认值从 turn_aligned 变成 compact_with_skills）。是否修正
    # 这处不一致留给后续独立的技术债处理，不在本次迁移范围内。
    compress_cfg = apply_overrides(
        load_nested_block_with_flat_compat(
            file_cfg, "compress", CompressConfig,
            flat_key_map={
                "enabled": "auto_compress_enabled",
                "threshold": "auto_compress_threshold",
                "forget_orphan_tool_results": "forget_policy_enabled",
                "turn_count_trigger_enabled": "compact_turn_count_trigger_enabled",
                "max_turns_before_compact": "compact_max_turns",
                "tool_call_count_trigger_enabled": "compact_tool_call_count_trigger_enabled",
                "max_tool_calls_before_compact": "compact_max_tool_calls",
                "topic_shift_detection": "compact_topic_shift_detection",
                "topic_shift_keyword_overlap_threshold": "compact_topic_shift_keyword_overlap_threshold",
                "topic_shift_min_budget_pct": "compact_topic_shift_min_budget_pct",
                "redundancy_detection_enabled": "compact_redundancy_detection_enabled",
                "redundancy_tool_result_ratio": "compact_redundancy_tool_result_ratio",
                "compact_cooldown_turns": "compact_cooldown_turns",
                "require_confirmation": "compact_require_confirmation",
                "max_message_chars_for_compact": "compact_max_message_chars",
                "compact_precheck_enabled": "compact_precheck_enabled",
                "compact_precheck_threshold": "compact_precheck_threshold",
                "model_context_window": "model_context_window",
                "goal_aware_weighting_enabled": "compact_goal_aware_weighting_enabled",
                "decision_extraction_on_compact_with_skills_enabled": "compact_decision_extraction_enabled",
                "decision_recall_tool_enabled": "compact_decision_recall_tool_enabled",
                "safe_point_gating_enabled": "compact_safe_point_gating_enabled",
                "composite_intensity_enabled": "compact_composite_intensity_enabled",
                "composite_intensity_threshold": "compact_composite_intensity_threshold",
                "audit_enabled": "compact_audit_enabled",
                "audit_async": "compact_audit_async",
                "audit_compact_reasons": "compact_audit_reasons",
            },
        ),
        enabled=(bool(auto_compress_enabled) if auto_compress_enabled is not None else None),
        threshold=(float(auto_compress_threshold) if auto_compress_threshold is not None else None),
        strategy=_f("auto_compress_strategy", None) or "turn_aligned",
        forget_orphan_tool_results=(bool(forget_policy_enabled) if forget_policy_enabled is not None else None),
    )

    # [flat_nested_config_unification_migration_plan.md Stage 2 第 4 类]
    # tool_trim：15 字段。除 large_file_threshold_bytes/list_dir_show_size/
    # large_file_warn_marker（本来就只有 nested 写法）外，其余均有
    # CLI/函数参数覆盖需求，nested/flat 兼容层跑完后用 apply_overrides
    # 处理 CLI 优先级，与迁移前行为一致。
    tool_trim_cfg = apply_overrides(
        load_nested_block_with_flat_compat(
            file_cfg, "tool_trim", ToolTrimConfig,
            flat_key_map={
                "enabled": "tool_result_trim_enabled",
                "threshold": "tool_result_trim_threshold",
                "bash_tail_ratio": "tool_trim_bash_tail_ratio",
                "read_window_lines": "tool_trim_read_window_lines",
                "grep_max_lines": "tool_trim_grep_max_lines",
                "raw_store_enabled": "raw_store_enabled",
                "raw_store_max_entries": "raw_store_max_entries",
                "raw_store_max_total_chars": "raw_store_max_total_chars",
                "smart_summary_enabled": "smart_summary_enabled",
                "smart_summary_threshold": "smart_summary_threshold",
                "smart_summary_max_input_chars": "smart_summary_max_input_chars",
                "smart_summary_model": "smart_summary_model",
            },
        ),
        enabled=(bool(tool_result_trim_enabled) if tool_result_trim_enabled is not None else None),
        threshold=(int(tool_result_trim_threshold) if tool_result_trim_threshold is not None else None),
        bash_tail_ratio=(float(tool_trim_bash_tail_ratio) if tool_trim_bash_tail_ratio is not None else None),
        read_window_lines=(int(tool_trim_read_window_lines) if tool_trim_read_window_lines is not None else None),
        grep_max_lines=(int(tool_trim_grep_max_lines) if tool_trim_grep_max_lines is not None else None),
        raw_store_enabled=(bool(raw_store_enabled) if raw_store_enabled is not None else None),
        raw_store_max_entries=(int(raw_store_max_entries) if raw_store_max_entries is not None else None),
        raw_store_max_total_chars=(int(raw_store_max_total_chars) if raw_store_max_total_chars is not None else None),
        smart_summary_enabled=(bool(smart_summary_enabled) if smart_summary_enabled is not None else None),
        smart_summary_threshold=(int(smart_summary_threshold) if smart_summary_threshold is not None else None),
        smart_summary_max_input_chars=(int(smart_summary_max_input_chars) if smart_summary_max_input_chars is not None else None),
        smart_summary_model=smart_summary_model,
    )

    # [flat_nested_config_unification_migration_plan.md Stage 2 第 3 类]
    # skill：11 字段，flat key 与字段名不一致（skill_semantic_enabled →
    # semantic_enabled 等）。semantic_enabled/threshold、tracking_enabled、
    # chunking_enabled、compact_budget/per_skill、keyword_activation_enabled、
    # auto_unload_enabled/idle_seconds 有 CLI/函数参数覆盖需求；matcher、
    # candidate_reminder_enabled 没有，只走 nested/flat 兼容层。
    skill_cfg = apply_overrides(
        load_nested_block_with_flat_compat(
            file_cfg, "skill", SkillConfig,
            flat_key_map={
                "semantic_enabled": "skill_semantic_enabled",
                "semantic_threshold": "skill_semantic_threshold",
                "tracking_enabled": "skill_tracking_enabled",
                "chunking_enabled": "skill_chunking_enabled",
                "compact_budget": "skill_compact_budget",
                "compact_per_skill": "skill_compact_per_skill",
                "matcher": "skill_matcher",
                "keyword_activation_enabled": "skill_keyword_activation_enabled",
                "auto_unload_enabled": "skill_auto_unload_enabled",
                "auto_unload_idle_seconds": "skill_auto_unload_idle_seconds",
            },
        ),
        semantic_enabled=(bool(skill_semantic_enabled) if skill_semantic_enabled is not None else None),
        semantic_threshold=(float(skill_semantic_threshold) if skill_semantic_threshold is not None else None),
        tracking_enabled=(bool(skill_tracking_enabled) if skill_tracking_enabled is not None else None),
        chunking_enabled=(bool(skill_chunking_enabled) if skill_chunking_enabled is not None else None),
        compact_budget=(int(skill_compact_budget) if skill_compact_budget is not None else None),
        compact_per_skill=(int(skill_compact_per_skill) if skill_compact_per_skill is not None else None),
        keyword_activation_enabled=(bool(skill_keyword_activation_enabled) if skill_keyword_activation_enabled is not None else None),
        auto_unload_enabled=(bool(skill_auto_unload_enabled) if skill_auto_unload_enabled is not None else None),
        auto_unload_idle_seconds=(int(skill_auto_unload_idle_seconds) if skill_auto_unload_idle_seconds is not None else None),
    )

    # [flat_nested_config_unification_migration_plan.md Stage 2 第 1 类]
    # perception：flat key 与字段名完全一致。`load_config()` 的同名参数
    # 目前没有对应 CLI flag（parser.py 里没有 --project-scan 等），但
    # `load_config()` 是公开函数，调用方仍可能直接传参，因此保留
    # `apply_overrides` 处理这批"函数参数"，只是通用加载部分换成
    # `load_nested_block_with_flat_compat()`（nested 优先，flat key 兼容）。
    perception_cfg = apply_overrides(
        load_nested_block_with_flat_compat(
            file_cfg, "perception", PerceptionConfig,
            flat_key_map={
                "project_scan_enabled": "project_scan_enabled",
                "file_watch_enabled": "file_watch_enabled",
                "tool_cache_enabled": "tool_cache_enabled",
                "tool_cache_max_entries": "tool_cache_max_entries",
                "token_estimate_enabled": "token_estimate_enabled",
                "token_warn_threshold": "token_warn_threshold",
                "tool_stats_enabled": "tool_stats_enabled",
                "artifact_auto_detect_enabled": "artifact_auto_detect_enabled",
            },
        ),
        # 只处理"函数参数"这一层覆盖；flat key / nested 已经在上面
        # `load_nested_block_with_flat_compat()` 里按优先级处理过，这里不
        # 重复读 file_cfg，避免 `_fb` 的默认值语义把 flat_compat 已经算出的
        # 结果无条件覆盖回去（那样等于让 nested 写法永远不起作用）。
        project_scan_enabled=(bool(project_scan_enabled) if project_scan_enabled is not None else None),
        file_watch_enabled=(bool(file_watch_enabled) if file_watch_enabled is not None else None),
        tool_cache_enabled=(bool(tool_cache_enabled) if tool_cache_enabled is not None else None),
        token_estimate_enabled=(bool(token_estimate_enabled) if token_estimate_enabled is not None else None),
        token_warn_threshold=(float(token_warn_threshold) if token_warn_threshold is not None else None),
        tool_stats_enabled=(bool(tool_stats_enabled) if tool_stats_enabled is not None else None),
        artifact_auto_detect_enabled=(bool(artifact_auto_detect_enabled) if artifact_auto_detect_enabled is not None else None),
    )

    # [flat_nested_config_unification_migration_plan.md Stage 2 第 2 类]
    # session：7 字段，flat key 与字段名有少量不一致（session_fmt → fmt、
    # auto_save_session → auto_save）。dir/fmt/auto_save/summary_enabled/
    # summary_min_turns/search_enabled 有 CLI/函数参数覆盖需求，通用加载
    # 跑完后用 `apply_overrides()` 按"CLI 参数 > 配置文件 > 默认值"覆盖，
    # `backend` 没有 flat key 历史（此前也从未被读取），只走 nested。
    _session_dir_str = (
        (str(session_dir) if session_dir else "")
        or file_cfg.get("session_dir")
        or os.environ.get("SESSION_DIR", "")
    )
    session_cfg = apply_overrides(
        load_nested_block_with_flat_compat(
            file_cfg, "session", SessionConfig,
            flat_key_map={
                "fmt": "session_fmt",
                "auto_save": "auto_save_session",
                "summary_enabled": "session_summary_enabled",
                "summary_min_turns": "session_summary_min_turns",
                "search_enabled": "session_search_enabled",
                "backend": "session_backend",
            },
            env_fallback={"fmt": "SESSION_FMT"},
        ),
        dir=Path(_session_dir_str) if _session_dir_str else None,
        fmt=session_fmt,
        auto_save=(
            False if _fb("no_save_session", None, False) else
            (bool(auto_save_session) if auto_save_session is not None else None)
        ),
        summary_enabled=(bool(session_summary_enabled) if session_summary_enabled is not None else None),
        summary_min_turns=(int(session_summary_min_turns) if session_summary_min_turns is not None else None),
        search_enabled=(bool(session_search_enabled) if session_search_enabled is not None else None),
    )

    # [flat_nested_config_unification_migration_plan.md Stage 2 第 2 类]
    # profile：5 字段，flat key 前缀不一致（profile_enabled → enabled 等），
    # 没有 CLI 覆盖需求，直接走 `load_nested_block_with_flat_compat()`。
    # `enabled` 缺省时走 dataclass 默认值 `True`，与迁移前"没写
    # profile_enabled 时显式传 True"的行为一致，不再需要在这里手写默认值。
    profile_cfg = load_nested_block_with_flat_compat(
        file_cfg, "profile", ProfileConfig,
        flat_key_map={
            "enabled": "profile_enabled",
            "refresh_interval_entries": "profile_refresh_interval_entries",
            "min_entries": "profile_min_entries",
            "max_entries_for_profile": "profile_max_entries_for_profile",
            "stale_after_days": "profile_stale_after_days",
            "force_refresh_after_days": "profile_force_refresh_after_days",
        },
    )

    # [flat_nested_config_unification_migration_plan.md Stage 2 第 1 类]
    # debug：3 字段。优先级：CLI/函数参数 > nested 写法（{"debug": {...}}）
    # > 旧 flat key（debug_llm 等）> 环境变量（LLM_DEBUG 等，沿用原有
    # "1"/"true"/"yes" 的宽松字符串判断，不复用 `load_nested_block()`
    # 通用的 `bool(raw_v)` 转换，避免把非空字符串 "0" 误判为 True）>
    # dataclass 默认值。
    _debug_dict = file_cfg.get("debug") if isinstance(file_cfg.get("debug"), dict) else {}
    _debug_llm_explicit = _debug_dict.get("llm_enabled", file_cfg.get("debug_llm"))
    _debug_console_explicit = _debug_dict.get("llm_console", file_cfg.get("debug_llm_console"))
    _env_debug     = os.environ.get("LLM_DEBUG", "").lower() in ("1", "true", "yes")
    _env_debug_con = os.environ.get("LLM_DEBUG_CONSOLE", "").lower() in ("1", "true", "yes")
    _debug_llm_v = bool(debug_llm) if debug_llm is not None else (
        bool(_debug_llm_explicit) if _debug_llm_explicit is not None else _env_debug
    )
    _debug_console_v = bool(debug_llm_console) if debug_llm_console is not None else (
        bool(_debug_console_explicit) if _debug_console_explicit is not None else _env_debug_con
    )
    _debug_log_dir_str = (
        _debug_dict.get("log_dir")
        or file_cfg.get("debug_log_dir")
        or os.environ.get("LLM_DEBUG_LOG_DIR", "")
    )
    _debug_log_dir = Path(_debug_log_dir_str) if _debug_log_dir_str else None
    # [capability_debug_plan] 同一套优先级规则：nested > 旧 flat key >
    # 环境变量 > 默认值。没有独立的 CLI 参数（不是高频切换项，需要时改
    # agent_config.json 或设环境变量即可）。
    _cap_debug_explicit = _debug_dict.get("capability_enabled", file_cfg.get("debug_capability"))
    _env_cap_debug = os.environ.get("CAPABILITY_DEBUG", "").lower() in ("1", "true", "yes")
    _cap_debug_v = bool(_cap_debug_explicit) if _cap_debug_explicit is not None else _env_cap_debug
    debug_cfg = DebugConfig(
        llm_enabled=_debug_llm_v,
        llm_console=_debug_console_v,
        log_dir=_debug_log_dir,
        capability_enabled=_cap_debug_v,
    )
    # capability_debug 是一个模块级开关（跟 configure_tool_executor_log_saving
    # 同一种组织方式）：skill 侧的 impl 代码可以直接调用
    # capability_debug_log()，是否真正落盘由这里统一同步的开关判断，调用方
    # 不需要关心配置从哪里来。
    from mini_agent.skills.generative_capability.capability_debug import configure_capability_debug
    configure_capability_debug(_cap_debug_v)
    # [flat_nested_config_unification_migration_plan.md Stage 2 第 3 类]
    # http：13 字段，flat key 与字段名不一致（http_ring_maxlen →
    # ring_maxlen 等），且没有 CLI 覆盖需求（`load_config()` 签名里没有
    # http_* 参数，CLI 层的 --http/--http-port 等 flag 目前并未接入
    # `load_config()`），纯 nested + flat 兼容层，无需 `apply_overrides`。
    http_cfg = load_nested_block_with_flat_compat(
        file_cfg, "http", HttpConfig,
        flat_key_map={
            "enabled": "http_enabled",
            "host": "http_host",
            "port": "http_port",
            "api_token": "http_api_token",
            "allowed_ips": "http_allowed_ips",
            "cors_origins": "http_cors_origins",
            "fs_readonly": "http_fs_readonly",
            "fs_excludes": "http_fs_excludes",
            "ring_maxlen": "http_ring_maxlen",
            "multi_user_enabled": "http_multi_user_enabled",
        },
    )

    # [flat_nested_config_unification_migration_plan.md Stage 2 第 4 类]
    # retry：9 字段。backoff_mode/step/max_delay 有 CLI 覆盖需求
    # （--retry-backoff 等），其余（max_retries/delay/verbose/
    # network_aware 等）只走 nested + flat 兼容层。
    retry_cfg = apply_overrides(
        load_nested_block_with_flat_compat(
            file_cfg, "retry", RetryConfig,
            flat_key_map={
                "max_retries": "llm_retry_max",
                "delay": "llm_retry_delay",
                "verbose": "llm_retry_verbose",
                "backoff_mode": "llm_retry_backoff_mode",
                "backoff_step": "llm_retry_backoff_step",
                "backoff_max_delay": "llm_retry_backoff_max_delay",
            },
        ),
        backoff_mode=llm_retry_backoff_mode,
        backoff_step=(float(llm_retry_backoff_step) if llm_retry_backoff_step is not None else None),
        backoff_max_delay=(float(llm_retry_backoff_max_delay) if llm_retry_backoff_max_delay is not None else None),
    )

    # [flat_nested_config_unification_migration_plan.md Stage 2 第 3 类]
    # ensemble：10 字段，flat key 与字段名不一致（ensemble_n → n 等），
    # 没有 CLI 覆盖需求（`load_config()` 签名里没有 ensemble_* 参数），
    # 纯 nested + flat 兼容层。
    ensemble_cfg = load_nested_block_with_flat_compat(
        file_cfg, "ensemble", EnsembleConfig,
        flat_key_map={
            "mode": "ensemble_mode",
            "granularity": "ensemble_granularity",
            "n": "ensemble_n",
            "execution": "ensemble_execution",
            "max_concurrency": "ensemble_max_concurrency",
            "judge_strategy": "ensemble_judge_strategy",
            "judge_model": "ensemble_judge_model",
            "judge_provider": "ensemble_judge_provider",
            "early_stop_on_consensus": "ensemble_early_stop_on_consensus",
            "max_extra_cost_ratio": "ensemble_max_extra_cost_ratio",
        },
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
    goal_execution_spec_cfg = _nested_blocks["goal_execution_spec"]
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
    capability_learning_cfg = _nested_blocks["capability_learning"]
    # [next_doc/persona_candidate_autoscan_plan.md] 候选人设/能力自动检测
    # 配置，同样走通用加载——紧跟 capability_learning_cfg 之后取值，避免
    # 分散在文件各处导致后续遗漏。
    persona_candidates_cfg = _nested_blocks["persona_candidates"]
    # [next_doc/generative_capability_three_tier_improvement_plan.md]
    generative_capability_cfg = _nested_blocks["generative_capability"]
    cron_cfg = _nested_blocks["cron"]
    autonomy_cfg = _nested_blocks["autonomy"]
    observability_cfg = _nested_blocks["observability"]
    # [goal_cron_unified_scheduler_improvement_plan.md P5 遗留缺陷修复]
    # `scheduler` block 此前从未被 `_nested_blocks` 提取、也从未传给
    # `AppConfig(...)`（见 param_registry.py 里 `NestedBlockSpec("scheduler",
    # ...)` 新增处的说明），本次一并补齐，其余 block 的加载路径完全不变。
    scheduler_cfg = _nested_blocks["scheduler"]
    # [next_doc/memory_backfill_and_profile_update_plan.md] 记忆回填配置。
    memory_backfill_cfg = _nested_blocks["memory_backfill"]
    # [next_doc/goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md
    # Stage 3] 诊断报告 LLM 摘要 / 调优草案 LLM 自然语言解析两个开关。
    cycle_tuning_cfg = _nested_blocks["cycle_tuning"]
    # [next_doc/goal_stuck_stats_and_llm_progress_judge_plan.md §2 遗留
    # 缺陷修复] 进展趋势判定是否接 LLM 的开关，此前从未被加载（见
    # param_registry.py 注册处的说明）。
    execution_phase_cfg = _nested_blocks["execution_phase"]
    # [cycle_patrol 加载修复] cycle_patrol 在 _nested_blocks 中已加载，但
    # 此前未传入 AppConfig，导致 agent_config.json 中 cycle_patrol.enabled
    # 等字段无论怎么配都不会生效——本次一并补齐。
    cycle_patrol_cfg = _nested_blocks["cycle_patrol"]

    cfg = AppConfig(
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
        # [SYS-BASH-STREAM] bash 工具是否实时打印子进程输出（边跑边看）。
        # [SYS-BASH-HANG-FIX] 默认改为 True，见 config/models.py 同名字段注释。
        # agent_config.json 里显式配置 "bash_stream_output_enabled": false 可关闭。
        bash_stream_output_enabled=_fb("bash_stream_output_enabled", None, True),
        # [next_doc/errors_tool_executor_log_toggle_plan.md] 全局错误日志
        # 是否落盘 tool_executor 来源的记录，默认 True（不改变原行为）。
        save_tool_executor_error_logs=_fb("save_tool_executor_error_logs", None, True),
        # [next_doc/initiative_systems_unification_plan.md §4.6]
        initiative_push_budget_enabled=_fb("initiative_push_budget_enabled", None, False),
        initiative_push_budget_max_per_day=_fn("initiative_push_budget_max_per_day", None, 3),
        # 子配置块
        memory=memory_cfg,
        compress=compress_cfg,
        tool_trim=tool_trim_cfg,
        skill=skill_cfg,
        perception=perception_cfg,
        session=session_cfg,
        profile=profile_cfg,
        memory_backfill=memory_backfill_cfg,
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
        goal_execution_spec=goal_execution_spec_cfg,
        turn_judge=turn_judge_cfg,
        env_info=env_info_cfg,
        workdir_knowledge=workdir_knowledge_cfg,
        global_knowledge=global_knowledge_cfg,
        privacy=privacy_cfg,
        digest_advisor=digest_advisor_cfg,
        growth_advisor=growth_advisor_cfg,
        capability_learning=capability_learning_cfg,
        persona_candidates=persona_candidates_cfg,
        generative_capability=generative_capability_cfg,
        cron=cron_cfg,
        autonomy=autonomy_cfg,
        scheduler=scheduler_cfg,
        observability=observability_cfg,
        cycle_tuning=cycle_tuning_cfg,
        execution_phase=execution_phase_cfg,
        cycle_patrol=cycle_patrol_cfg,
        llm_fallback_chain=_llm_fallback_chain,
        llm_fallback_on=_llm_fallback_on,
    )

    # [next_doc/errors_tool_executor_log_toggle_plan.md] 把
    # `save_tool_executor_error_logs` 同步到 errors.py 的进程级开关——
    # errors.py 不依赖 AppConfig（避免循环导入/被大量非 Agent 场景调用
    # 时的初始化负担），这里是唯一的桥接点。多次调用 load_config()（如
    # daemon 多用户场景每个 session 各自 load 一次）时后来者覆盖前者，
    # 这是当前"进程级单一开关"设计的已知局限——见该 next_doc 的取舍说明。
    from mini_agent.errors import configure_tool_executor_log_saving
    configure_tool_executor_log_saving(cfg.save_tool_executor_error_logs)

    return cfg


# ── 辅助函数（不变）──────────────────────────────────────────────────────────

def _resolve_main_project_root(root: Path) -> Optional[Path]:
    """
    [external_projects_agent_skill_workflow_integration_plan.md 第1.4节]
    外部项目路径可以在磁盘任意位置（不保证挂在主项目目录下，见该文档
    "路径不一定在主项目目录下"的修正记录），不能从 `root` 的目录结构
    反推主项目根，因此改用两条更可靠的信息源，按优先级：

    1. 环境变量 `MINI_AGENT_MAIN_PROJECT_ROOT`——daemon/scheduler 拉起
       外部项目的 entrypoint 子进程时可以显式设置，最直接、无需查表。
    2. `ExternalProjectRegistry`（`~/.agent/external_projects.json`）
       里按 `root` 反查注册记录的 `main_project_root` 字段——这是
       `register()` 时记下来的"注册这个外部项目时所在的主项目"，与
       `root` 实际路径无关，不受目录布局影响。

    `root` 必须是一个外部项目根（`<root>/project.yaml` 存在）才会走这
    两条查找；不是外部项目、或两条都没查到，返回 `None`，调用方据此
    继续走"环境变量兜底 api_key"这条既有路径，不报错。
    """
    try:
        if not (root / "project.yaml").exists():
            return None
    except OSError:
        return None

    import os as _os
    env_root = _os.environ.get("MINI_AGENT_MAIN_PROJECT_ROOT")
    if env_root:
        p = Path(env_root).expanduser()
        if p.is_dir():
            return p

    try:
        from mini_agent.external_projects.registry import ExternalProjectRegistry
        record = ExternalProjectRegistry().find_by_path(root)
        if record and record.main_project_root:
            p = Path(record.main_project_root).expanduser()
            if p.is_dir():
                return p
    except Exception:
        # 注册表不可用/未注册/其它任何异常都不应该拖垮 load_config()
        # 本身——外部项目照样能跑，只是拿不到主项目 LLM 配置的 fallback。
        pass

    return None


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