"""
config/config_catalog.py — 看板配置管理机制的字段目录（Catalog）

对应设计文档：next_doc/kanban_config_management_plan.md

背景：agent_config.json 里的配置项有两种截然不同的落盘方式（历史演进导致，
详见 loader.py 的具体读取代码）：
  1. "flat"   —— 顶层扁平键，如 "memory_enabled"、"auto_compress_threshold"，
                 键名往往不等于 AppConfig 内部字段名（历史遗留命名）。这类
                 字段集中在 memory/compress/tool_trim/skill/perception/
                 session/profile/debug/http/retry/ensemble 这些"较早"的功能
                 域，以及少数核心运行参数。
  2. "nested" —— 顶层是一个 dict block（如 "tech_radar": {...}），block 内部
                 每个字段名与对应 dataclass 字段名完全一致，可以通过
                 `dataclasses.fields()` 自动展开，不需要逐个手写。这类字段
                 集中在较新的功能域。

本模块的职责：把这两种历史遗留的读取方式，统一抽象成一份可供 API/看板 UI
直接消费的"字段目录"（CategorySpec/FieldSpec），并提供：
  - `build_status(cfg, raw_file_cfg)`   —— 生成只读状态视图（分类 + 每个字段
    的当前生效值/是否显式配置过/默认值），供 GET /v1/self/config 使用。
  - `apply_updates(raw_file_cfg, updates)` —— 把一批 {json_key, value} 更新
    合并进原始 JSON dict 副本（不改变未涉及的字段/格式），供
    PATCH /v1/self/config 使用。
  - `KNOWN_JSON_KEYS`                    —— 所有已收录字段的 json_key 集合，
    PATCH 时只接受目录里存在的 json_key，拒绝任意写入（避免看板变成"裸写
    JSON"，保留最基本的白名单校验）。

明确的不做/边界（见设计文档 §设计边界）：
  - 不收录 list/dict 类型的复杂字段（如 mcp_servers、privacy.secrets、
    llm_fallback_chain）——这些字段结构本身是"列表/子对象"，通用的
    单值编辑控件不适用，仍需在 JSON 里手工维护，或留待后续专项 UI。
  - 敏感字段（api_key、http.api_token、privacy.secrets 等）只做"是否已配置"
    的布尔化展示，不回显明文、不支持通过本机制修改。
  - 修改配置后是否需要重启 agent 进程才能生效，本模块不做判断（不同字段
    生效时机不同，多数需要重启），由调用方在 UI 上统一提示。
"""

from __future__ import annotations

import dataclasses
from typing import Any, Optional

from . import models as _models


# ── 数据结构 ──────────────────────────────────────────────────────────────

@dataclasses.dataclass
class FieldSpec:
    json_key: str          # 写入 agent_config.json 的 key："flat_key" 或 "block.field"
    attr_path: str          # 在 AppConfig 实例上取值的路径，如 "memory.enabled"
    label: str              # 中文简短说明
    sensitive: bool = False  # True 时不回显真实值，只展示"已配置/未配置"


@dataclasses.dataclass
class CategorySpec:
    id: str
    label: str
    icon: str
    fields: list  # list[FieldSpec]


def _attr_get(obj, path: str):
    cur = obj
    for part in path.split("."):
        cur = getattr(cur, part, None)
        if cur is None:
            return None
    return cur


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, (list, tuple)):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "other"


# ── 核心运行参数（flat，AppConfig 顶层字段）─────────────────────────────────
_CORE_FIELDS = [
    FieldSpec("verbose", "verbose", "详细日志输出"),
    FieldSpec("sandbox", "sandbox", "沙箱模式"),
    FieldSpec("simple_mode", "simple_mode", "极简模式"),
    FieldSpec("raw_output", "raw_output", "原始输出（不做渲染）"),
    FieldSpec("show_reasoning", "show_reasoning", "显示模型推理过程"),
    FieldSpec("yes", "auto_approve", "自动批准工具调用"),
    FieldSpec("model", "model", "模型名称"),
    FieldSpec("provider", "llm_provider", "LLM Provider"),
    FieldSpec("base_url", "llm_base_url", "LLM Base URL"),
    FieldSpec("system_tool_call", "use_system_tool_call", "使用系统原生 tool_call"),
    FieldSpec("system_message_format", "system_message_format", "System 消息格式"),
    FieldSpec("max_llm_calls", "max_llm_calls", "单轮最大 LLM 调用次数"),
    FieldSpec("max_turns", "max_turns", "单次 run_turn 最大轮次"),
    FieldSpec("max_turns_on_limit", "max_turns_on_limit", "达到 max_turns 后的处理策略"),
    FieldSpec("max_turns_hard_limit", "max_turns_hard_limit", "轮次硬上限"),
    FieldSpec("agent_name", "agent_name", "Agent 名称"),
    FieldSpec("notepad_enabled", "notepad_enabled", "记事本功能"),
    FieldSpec("recall_history_enabled", "recall_history_enabled", "历史回溯检索工具"),
    FieldSpec("recall_history_mode", "recall_history_mode", "历史回溯检索模式"),
    FieldSpec("bash_stream_output_enabled", "bash_stream_output_enabled", "bash 实时流式输出"),
]

# ── memory（历史遗留：整体走 flat key，不是 nested block）───────────────────
_MEMORY_FIELDS = [
    FieldSpec("memory_enabled", "memory.enabled", "记忆检索总开关"),
    FieldSpec("memory_backend", "memory.backend", "记忆存储后端"),
    FieldSpec("memory_global_enabled", "memory.global_enabled", "跨项目全局记忆"),
    FieldSpec("memory_global_top_k", "memory.global_top_k", "全局记忆召回条数"),
    FieldSpec("memory_top_k", "memory.top_k", "记忆召回条数"),
    FieldSpec("memory_decay_half_life_days", "memory.decay_half_life_days", "记忆衰减半衰期（天）"),
    FieldSpec("memory_max_entries", "memory.max_entries", "记忆最大条目数"),
    FieldSpec("lesson_rules_enabled", "memory.lesson_rules_enabled", "教训规则提炼"),
    FieldSpec("lesson_fail_threshold", "memory.lesson_fail_threshold", "教训触发失败阈值"),
    FieldSpec("correction_detection_enabled", "memory.correction_detection_enabled", "纠错检测"),
    FieldSpec("memory_per_turn_retrieval_enabled", "memory.per_turn_retrieval_enabled", "每轮主动检索记忆"),
]

# ── compress（历史遗留：flat key，前缀 auto_compress_ / compact_）───────────
_COMPRESS_FIELDS = [
    FieldSpec("auto_compress_enabled", "compress.enabled", "自动压缩总开关"),
    FieldSpec("auto_compress_threshold", "compress.threshold", "触发压缩的 token 占用率"),
    FieldSpec("auto_compress_strategy", "compress.strategy", "压缩策略"),
    FieldSpec("forget_policy_enabled", "compress.forget_orphan_tool_results", "剔除孤立 tool_result"),
    FieldSpec("compact_turn_count_trigger_enabled", "compress.turn_count_trigger_enabled", "按轮数触发压缩"),
    FieldSpec("compact_max_turns", "compress.max_turns_before_compact", "触发压缩的轮数阈值"),
    FieldSpec("compact_tool_call_count_trigger_enabled", "compress.tool_call_count_trigger_enabled", "按工具调用数触发压缩"),
    FieldSpec("compact_max_tool_calls", "compress.max_tool_calls_before_compact", "触发压缩的工具调用数阈值"),
    FieldSpec("compact_topic_shift_detection", "compress.topic_shift_detection", "话题切换检测模式"),
    FieldSpec("compact_topic_shift_keyword_overlap_threshold", "compress.topic_shift_keyword_overlap_threshold", "话题切换关键词重合度阈值"),
    FieldSpec("compact_topic_shift_min_budget_pct", "compress.topic_shift_min_budget_pct", "话题切换最小预算占比"),
    FieldSpec("compact_redundancy_detection_enabled", "compress.redundancy_detection_enabled", "冗余检测"),
    FieldSpec("compact_redundancy_tool_result_ratio", "compress.redundancy_tool_result_ratio", "冗余 tool_result 占比阈值"),
    FieldSpec("compact_cooldown_turns", "compress.compact_cooldown_turns", "两次压缩之间的冷却轮数"),
    FieldSpec("compact_require_confirmation", "compress.require_confirmation", "压缩前需要用户确认"),
    FieldSpec("compact_max_message_chars", "compress.max_message_chars_for_compact", "触发压缩的单消息字符数阈值"),
    FieldSpec("compact_precheck_enabled", "compress.compact_precheck_enabled", "压缩预检"),
    FieldSpec("compact_precheck_threshold", "compress.compact_precheck_threshold", "预检超限比例阈值"),
    FieldSpec("model_context_window", "compress.model_context_window", "模型上下文窗口大小（0=自动）"),
    FieldSpec("compact_goal_aware_weighting_enabled", "compress.goal_aware_weighting_enabled", "目标感知加权保留"),
    FieldSpec("compact_decision_extraction_enabled", "compress.decision_extraction_on_compact_with_skills_enabled", "压缩时顺带提炼决策"),
    FieldSpec("compact_decision_recall_tool_enabled", "compress.decision_recall_tool_enabled", "决策召回只读工具"),
    FieldSpec("compact_safe_point_gating_enabled", "compress.safe_point_gating_enabled", "压缩安全点门控"),
    FieldSpec("compact_composite_intensity_enabled", "compress.composite_intensity_enabled", "复合强度触发"),
    FieldSpec("compact_composite_intensity_threshold", "compress.composite_intensity_threshold", "复合强度阈值"),
    FieldSpec("compact_audit_enabled", "compress.audit_enabled", "压缩审计"),
    FieldSpec("compact_audit_async", "compress.audit_async", "压缩审计异步执行"),
]

_TOOL_TRIM_FIELDS = [
    FieldSpec("tool_result_trim_enabled", "tool_trim.enabled", "工具结果裁剪总开关"),
    FieldSpec("tool_result_trim_threshold", "tool_trim.threshold", "触发裁剪的字符阈值"),
    FieldSpec("tool_trim_bash_tail_ratio", "tool_trim.bash_tail_ratio", "bash 输出保留尾部比例"),
    FieldSpec("tool_trim_read_window_lines", "tool_trim.read_window_lines", "read_file 窗口行数"),
    FieldSpec("tool_trim_grep_max_lines", "tool_trim.grep_max_lines", "grep 结果最大行数"),
    FieldSpec("raw_store_enabled", "tool_trim.raw_store_enabled", "裁剪前原文暂存"),
    FieldSpec("raw_store_max_entries", "tool_trim.raw_store_max_entries", "原文暂存最大条目数"),
    FieldSpec("raw_store_max_total_chars", "tool_trim.raw_store_max_total_chars", "原文暂存总字符上限"),
    FieldSpec("smart_summary_enabled", "tool_trim.smart_summary_enabled", "智能摘要裁剪"),
    FieldSpec("smart_summary_threshold", "tool_trim.smart_summary_threshold", "智能摘要触发阈值"),
    FieldSpec("smart_summary_max_input_chars", "tool_trim.smart_summary_max_input_chars", "智能摘要最大输入字符数"),
    FieldSpec("smart_summary_model", "tool_trim.smart_summary_model", "智能摘要使用的模型"),
]

_SKILL_FIELDS = [
    FieldSpec("skill_semantic_enabled", "skill.semantic_enabled", "语义匹配技能"),
    FieldSpec("skill_semantic_threshold", "skill.semantic_threshold", "语义匹配阈值"),
    FieldSpec("skill_tracking_enabled", "skill.tracking_enabled", "技能使用追踪"),
    FieldSpec("skill_chunking_enabled", "skill.chunking_enabled", "技能分块加载"),
    FieldSpec("skill_compact_budget", "skill.compact_budget", "技能压缩总预算（字符）"),
    FieldSpec("skill_compact_per_skill", "skill.compact_per_skill", "单个技能压缩预算（字符）"),
    FieldSpec("skill_matcher", "skill.matcher", "技能匹配器"),
    FieldSpec("skill_keyword_activation_enabled", "skill.keyword_activation_enabled", "关键词自动激活技能"),
    FieldSpec("skill_auto_unload_enabled", "skill.auto_unload_enabled", "闲置技能自动卸载"),
    FieldSpec("skill_auto_unload_idle_seconds", "skill.auto_unload_idle_seconds", "自动卸载闲置秒数"),
]

_PERCEPTION_FIELDS = [
    FieldSpec("project_scan_enabled", "perception.project_scan_enabled", "项目结构扫描"),
    FieldSpec("file_watch_enabled", "perception.file_watch_enabled", "文件变更监听"),
    FieldSpec("tool_cache_enabled", "perception.tool_cache_enabled", "工具结果缓存"),
    FieldSpec("tool_cache_max_entries", "perception.tool_cache_max_entries", "工具缓存最大条目数"),
    FieldSpec("token_estimate_enabled", "perception.token_estimate_enabled", "token 占用估算"),
    FieldSpec("token_warn_threshold", "perception.token_warn_threshold", "token 占用预警阈值"),
    FieldSpec("tool_stats_enabled", "perception.tool_stats_enabled", "工具调用统计"),
    FieldSpec("artifact_auto_detect_enabled", "perception.artifact_auto_detect_enabled", "产出物自动识别"),
]

_SESSION_FIELDS = [
    FieldSpec("session_fmt", "session.fmt", "会话存储格式"),
    FieldSpec("auto_save_session", "session.auto_save", "自动保存会话"),
    FieldSpec("session_summary_enabled", "session.summary_enabled", "会话摘要"),
    FieldSpec("session_summary_min_turns", "session.summary_min_turns", "生成摘要的最小轮数"),
    FieldSpec("session_search_enabled", "session.search_enabled", "会话搜索"),
    FieldSpec("session_backend", "session.backend", "会话存储后端"),
]

_PROFILE_FIELDS = [
    FieldSpec("profile_enabled", "profile.enabled", "用户画像"),
    FieldSpec("profile_refresh_interval_entries", "profile.refresh_interval_entries", "画像刷新间隔（条目数）"),
    FieldSpec("profile_min_entries", "profile.min_entries", "生成画像所需最小条目数"),
    FieldSpec("profile_max_entries_for_profile", "profile.max_entries_for_profile", "画像参考的最大条目数"),
]

_DEBUG_FIELDS = [
    FieldSpec("debug_llm", "debug.llm_enabled", "LLM 调试日志"),
    FieldSpec("debug_llm_console", "debug.llm_console", "调试日志输出到控制台"),
]

_HTTP_FIELDS = [
    FieldSpec("http_enabled", "http.enabled", "HTTP API 服务"),
    FieldSpec("http_host", "http.host", "监听地址"),
    FieldSpec("http_port", "http.port", "监听端口"),
    FieldSpec("http_api_token", "http.api_token", "API 访问令牌", sensitive=True),
    FieldSpec("http_fs_readonly", "http.fs_readonly", "文件系统接口只读"),
    FieldSpec("http_ring_maxlen", "http.ring_maxlen", "事件环形缓冲区长度"),
    FieldSpec("http_multi_user_enabled", "http.multi_user_enabled", "多用户模式"),
]

_RETRY_FIELDS = [
    FieldSpec("llm_retry_max", "retry.max_retries", "LLM 调用最大重试次数"),
    FieldSpec("llm_retry_delay", "retry.delay", "重试基础延迟（秒）"),
    FieldSpec("llm_retry_verbose", "retry.verbose", "重试日志"),
    FieldSpec("llm_retry_backoff_mode", "retry.backoff_mode", "退避模式"),
    FieldSpec("llm_retry_backoff_step", "retry.backoff_step", "退避步长（秒）"),
    FieldSpec("llm_retry_backoff_max_delay", "retry.backoff_max_delay", "退避最大延迟（秒，0=不限）"),
]

_ENSEMBLE_FIELDS = [
    FieldSpec("ensemble_mode", "ensemble.mode", "Ensemble 模式"),
    FieldSpec("ensemble_granularity", "ensemble.granularity", "Ensemble 粒度"),
    FieldSpec("ensemble_n", "ensemble.n", "并行方案数 N"),
    FieldSpec("ensemble_execution", "ensemble.execution", "执行方式"),
    FieldSpec("ensemble_max_concurrency", "ensemble.max_concurrency", "最大并发数"),
    FieldSpec("ensemble_judge_strategy", "ensemble.judge_strategy", "评审策略"),
    FieldSpec("ensemble_judge_model", "ensemble.judge_model", "评审模型"),
    FieldSpec("ensemble_judge_provider", "ensemble.judge_provider", "评审 Provider"),
    FieldSpec("ensemble_early_stop_on_consensus", "ensemble.early_stop_on_consensus", "共识后提前终止"),
    FieldSpec("ensemble_max_extra_cost_ratio", "ensemble.max_extra_cost_ratio", "允许的额外成本倍率"),
]

_FLAT_CATEGORIES: list[CategorySpec] = [
    CategorySpec("core", "核心运行参数", "⚙️", _CORE_FIELDS),
    CategorySpec("memory", "记忆", "🧠", _MEMORY_FIELDS),
    CategorySpec("compress", "历史压缩 Compact", "🗜️", _COMPRESS_FIELDS),
    CategorySpec("tool_trim", "工具结果裁剪", "✂️", _TOOL_TRIM_FIELDS),
    CategorySpec("skill", "技能 Skill", "🧩", _SKILL_FIELDS),
    CategorySpec("perception", "感知", "👀", _PERCEPTION_FIELDS),
    CategorySpec("session", "会话", "🗂️", _SESSION_FIELDS),
    CategorySpec("profile", "用户画像", "🪪", _PROFILE_FIELDS),
    CategorySpec("debug", "调试", "🐞", _DEBUG_FIELDS),
    CategorySpec("http", "HTTP API", "🌐", _HTTP_FIELDS),
    CategorySpec("retry", "LLM 重试", "🔁", _RETRY_FIELDS),
    CategorySpec("ensemble", "Ensemble 多方案", "🎛️", _ENSEMBLE_FIELDS),
]

# ── nested block 分类：字段名与 dataclass 字段名一一对应，自动展开 ──────────
# (category_id, label, icon, AppConfig 属性名, dataclass 类, {字段名: 是否敏感})
_NESTED_BLOCKS = [
    ("tech_radar", "技术雷达", "🛰️", "tech_radar", _models.TechRadarConfig, {}),
    ("web_search", "网络搜索", "🔍", "web_search", _models.WebSearchConfig, {"api_key": True}),
    ("ecosystem_positioning", "生态定位", "🌐", "ecosystem_positioning", _models.EcosystemPositioningConfig, {}),
    ("reminder", "主动提醒", "🔔", "reminder", _models.ReminderConfig, {}),
    ("format_correction", "工具调用格式纠错", "🛠️", "format_correction", _models.FormatCorrectionConfig, {}),
    ("privacy", "隐私保护", "🔒", "privacy", _models.PrivacyConfig, {"secrets": True, "auto_env_patterns": False}),
    ("role_agent", "角色代理 RoleAgent", "🎭", "role_agent", _models.RoleAgentConfig, {}),
    ("goal_mode", "目标模式 GoalMode", "🎯", "goal_mode", _models.GoalModeConfig, {}),
    ("turn_judge", "单轮裁判 TurnJudge", "⚖️", "turn_judge", _models.TurnJudgeConfig, {}),
    ("env_info", "环境信息注入", "🖥️", "env_info", _models.EnvInfoConfig, {}),
    ("workdir_knowledge", "工作目录知识层", "📂", "workdir_knowledge", _models.WorkdirKnowledgeConfig, {}),
    ("global_knowledge", "全局知识层", "🌍", "global_knowledge", _models.GlobalKnowledgeConfig, {}),
    ("proprioception", "本体感知", "🧭", "proprioception", _models.ProprioceptionConfig, {}),
    ("affordance", "余裕感知 Affordance", "🪄", "affordance", _models.AffordanceConfig, {}),
    ("workflow", "工作流执行", "🔄", "workflow", _models.WorkflowConfig, {}),
    ("digest_advisor", "日报与主动推荐", "📰", "digest_advisor", _models.DigestAdvisorConfig, {}),
    ("growth_advisor", "🌱 成长顾问", "🌱", "growth_advisor", _models.GrowthAdvisorConfig, {}),
    ("cron", "Cron 任务执行", "⏰", "cron", _models.CronConfig, {}),
    ("autonomy", "自主性调度 Autonomy", "🤖", "autonomy", _models.AutonomyConfig, {}),
    ("observability", "可观测性", "📊", "observability", _models.ObservabilityConfig, {}),
]

# ── 与 param_registry.NESTED_CONFIG_BLOCKS 的一致性校验 ────────────────────
# 本文件的 _NESTED_BLOCKS 比 param_registry.NESTED_CONFIG_BLOCKS 多带了
# label/icon/敏感字段映射（纯 UI 展示用），职责不同不能直接合并成一份，
# 但两边 (attr_name, dataclass 类) 这一核心对应关系必须一致——否则看板
# 展示的字段和 loader.py 实际加载的字段就会对不上。这里在模块加载期做一次
# 断言：本文件收录的、且同时也在 param_registry 通用注册表里的 block，
# dataclass 类必须完全一致。新增/修改 nested block 时如果两边配置类型
# 对不上，import 这个模块就会立刻报错，而不是留到运行时才发现看板显示的
# 是旧字段。
from . import param_registry as _param_registry  # noqa: E402

_registry_by_attr = {s.attr_name: s.dataclass_type for s in _param_registry.NESTED_CONFIG_BLOCKS}
for _cat_id, _label, _icon, _attr_name, _cls, _sensitive_map in _NESTED_BLOCKS:
    _expected_cls = _registry_by_attr.get(_attr_name)
    if _expected_cls is not None and _expected_cls is not _cls:
        raise AssertionError(
            f"config_catalog._NESTED_BLOCKS['{_attr_name}'] 用的是 {_cls!r}，"
            f"与 param_registry.NESTED_CONFIG_BLOCKS 里注册的 {_expected_cls!r} 不一致——"
            "两处对同一个 block 的 dataclass 类型必须保持同步。"
        )
del _registry_by_attr

# 简单类型才纳入通用编辑目录；list/dict 类型的字段（如 keywords/seeds/
# judge_allowed_tools）只读展示，不生成可编辑 FieldSpec——通用单值控件不
# 适合编辑列表，见模块头部说明。
_EDITABLE_PY_TYPES = (bool, int, float, str, type(None))


def _build_nested_category(cat_id, label, icon, attr_name, cls, sensitive_map) -> CategorySpec:
    fields = []
    for f in dataclasses.fields(cls):
        default = f.default if f.default is not dataclasses.MISSING else None
        if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            try:
                default = f.default_factory()  # type: ignore[misc]
            except Exception:
                default = None
        if not isinstance(default, _EDITABLE_PY_TYPES):
            continue  # list/dict 等复杂类型跳过，只读部分由前端直接展示原始 JSON
        fields.append(FieldSpec(
            json_key=f"{attr_name}.{f.name}",
            attr_path=f"{attr_name}.{f.name}",
            label=f.name,
            sensitive=bool(sensitive_map.get(f.name, False)),
        ))
    return CategorySpec(cat_id, label, icon, fields)


def _all_categories() -> list:
    cats = list(_FLAT_CATEGORIES)
    for cat_id, label, icon, attr_name, cls, sensitive_map in _NESTED_BLOCKS:
        cats.append(_build_nested_category(cat_id, label, icon, attr_name, cls, sensitive_map))
    return cats


CATEGORIES: list = _all_categories()

# json_key → FieldSpec，供 PATCH 时白名单校验 + 定位写入路径
KNOWN_FIELDS: dict = {
    field.json_key: field
    for cat in CATEGORIES
    for field in cat.fields
}


# ── 只读状态视图（GET）───────────────────────────────────────────────────────

def _is_customized(raw_file_cfg: dict, json_key: str) -> bool:
    """判断某个 json_key 是否在 agent_config.json 里被显式设置过（区别于
    "当前生效值恰好等于默认值"——两者语义不同：前者是"用户主动配置过"，
    后者可能只是默认值恰好和用户想要的一致）。"""
    if "." in json_key:
        block, _, field_name = json_key.partition(".")
        block_dict = raw_file_cfg.get(block)
        return isinstance(block_dict, dict) and field_name in block_dict
    return json_key in raw_file_cfg


def build_status(cfg, raw_file_cfg: dict) -> list:
    """返回分类状态列表，供 GET /v1/self/config 使用。

    每个分类：{"id", "label", "icon", "fields": [...]}；
    每个字段：{"json_key", "label", "type", "value", "default",
              "customized", "sensitive"}。
    敏感字段：value/default 不回显真实内容，只给 "***"（已配置）或 ""（未配置）。
    """
    default_cfg = _models.AppConfig(project_root=cfg.project_root if hasattr(cfg, "project_root") else None)
    out = []
    for cat in CATEGORIES:
        field_rows = []
        for f in cat.fields:
            live_value = _attr_get(cfg, f.attr_path)
            default_value = _attr_get(default_cfg, f.attr_path)
            customized = _is_customized(raw_file_cfg, f.json_key)
            if f.sensitive:
                display_value = "***" if live_value else ""
                display_default = ""
            else:
                display_value = live_value
                display_default = default_value
            field_rows.append({
                "json_key": f.json_key,
                "label": f.label,
                "type": _type_name(default_value),
                "value": display_value,
                "default": display_default,
                "customized": customized,
                "sensitive": f.sensitive,
            })
        out.append({
            "id": cat.id,
            "label": cat.label,
            "icon": cat.icon,
            "fields": field_rows,
        })
    return out


# ── 写回（PATCH）─────────────────────────────────────────────────────────────

class ConfigUpdateError(Exception):
    pass


def apply_updates(raw_file_cfg: dict, updates: list) -> dict:
    """把一批 {"json_key": str, "value": Any} 更新合并进 raw_file_cfg 的
    深拷贝并返回新 dict（不修改传入的原始 dict）。

    只接受 KNOWN_FIELDS 里存在、且非 sensitive 的 json_key；未知字段或
    敏感字段一律拒绝（敏感字段的修改需要用户手工编辑 JSON 文件，见模块
    头部说明），拒绝时抛 ConfigUpdateError，整批更新不生效（要么全部
    应用，要么都不应用，避免部分生效造成难以理解的中间状态）。
    """
    import copy
    new_cfg = copy.deepcopy(raw_file_cfg)

    for upd in updates:
        json_key = upd.get("json_key")
        if json_key not in KNOWN_FIELDS:
            raise ConfigUpdateError(f"未知配置项：{json_key}")
        field = KNOWN_FIELDS[json_key]
        if field.sensitive:
            raise ConfigUpdateError(f"敏感配置项不支持通过看板修改：{json_key}")

    for upd in updates:
        json_key = upd["json_key"]
        value = upd.get("value")
        if "." in json_key:
            block, _, field_name = json_key.partition(".")
            block_dict = new_cfg.get(block)
            if not isinstance(block_dict, dict):
                block_dict = {}
            block_dict[field_name] = value
            new_cfg[block] = block_dict
        else:
            new_cfg[json_key] = value

    return new_cfg
