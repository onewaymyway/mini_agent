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

_FLAT_CATEGORIES: list[CategorySpec] = [
    CategorySpec("core", "核心运行参数", "⚙️", _CORE_FIELDS),
]

# ── nested block 分类：字段名与 dataclass 字段名一一对应，自动展开 ──────────
# (category_id, label, icon, AppConfig 属性名, dataclass 类, {字段名: 是否敏感})
#
# [flat_nested_config_unification_migration_plan.md Stage 2 验收标准第 3 条]
# memory/compress/tool_trim/skill/perception/session/profile/debug/http/
# retry/ensemble 这 11 个 block 原先在上面 `_FLAT_CATEGORIES` 里各自手写一份
# `_XXX_FIELDS`（flat json_key，如 `"memory_top_k"`），随着 `loader.py`
# 迁移成 nested-first（见 `config/loader.py`、
# `param_registry.load_nested_block_with_flat_compat()`），看板这边也一并
# 迁移成这里的自动展开写法，json_key 从 `"memory_top_k"` 变成
# `"memory.top_k"`——PATCH 写回时统一写 nested 形式，与 loader 的
# "nested 优先"保持一致。注意：`_is_customized()` 判断"是否显式配置过"时
# 也是按 json_key（即 nested 路径）去查 `raw_file_cfg`，所以存量
# `agent_config.json` 里只写了旧 flat key（如 `"memory_top_k"`）、没有
# nested `"memory": {...}` 的用户，看板上会显示这些字段"未customized"——
# 这只影响这一个"是否显式配置过"的展示态，不影响 `load_config()` 实际读到
# 的值（flat key 依然通过兼容层生效，见 `loader.py` 对应注释）。
_NESTED_BLOCKS = [
    ("memory", "记忆", "🧠", "memory", _models.MemoryConfig, {}),
    ("compress", "历史压缩 Compact", "🗜️", "compress", _models.CompressConfig, {}),
    ("tool_trim", "工具结果裁剪", "✂️", "tool_trim", _models.ToolTrimConfig, {}),
    ("skill", "技能 Skill", "🧩", "skill", _models.SkillConfig, {}),
    ("perception", "感知", "👀", "perception", _models.PerceptionConfig, {}),
    ("session", "会话", "🗂️", "session", _models.SessionConfig, {}),
    ("profile", "用户画像", "🪪", "profile", _models.ProfileConfig, {}),
    ("debug", "调试", "🐞", "debug", _models.DebugConfig, {}),
    ("http", "HTTP API", "🌐", "http", _models.HttpConfig, {"api_token": True}),
    ("retry", "LLM 重试", "🔁", "retry", _models.RetryConfig, {}),
    ("ensemble", "Ensemble 多方案", "🎛️", "ensemble", _models.EnsembleConfig, {}),
    ("tech_radar", "技术雷达", "🛰️", "tech_radar", _models.TechRadarConfig, {}),
    ("web_search", "网络搜索", "🔍", "web_search", _models.WebSearchConfig, {"api_key": True}),
    ("ecosystem_positioning", "生态定位", "🌐", "ecosystem_positioning", _models.EcosystemPositioningConfig, {}),
    ("reminder", "主动提醒", "🔔", "reminder", _models.ReminderConfig, {}),
    ("format_correction", "工具调用格式纠错", "🛠️", "format_correction", _models.FormatCorrectionConfig, {}),
    ("privacy", "隐私保护", "🔒", "privacy", _models.PrivacyConfig, {"secrets": True, "auto_env_patterns": False}),
    ("role_agent", "角色代理 RoleAgent", "🎭", "role_agent", _models.RoleAgentConfig, {}),
    ("goal_mode", "目标模式 GoalMode", "🎯", "goal_mode", _models.GoalModeConfig, {}),
    ("goal_execution_spec", "Goal执行规范 GoalExecutionSpec", "📐", "goal_execution_spec", _models.GoalExecutionSpecConfig, {}),
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
    ("cycle_tuning", "周期任务诊断与调优 LLM 增强", "🩺", "cycle_tuning", _models.CycleTuningConfig, {}),
    ("cycle_patrol", "周期任务主动巡检推送", "🚨", "cycle_patrol", _models.CyclePatrolConfig, {}),
    ("execution_phase", "执行阶段进展趋势判定", "🧭", "execution_phase", _models.ExecutionPhaseConfig, {}),
    # [看板字段补齐] scheduler/memory_backfill 两个 block 已在
    # param_registry.NESTED_CONFIG_BLOCKS 里注册、也已被 loader.py 正确
    # 加载，但此前从未加进本文件的 _NESTED_BLOCKS，导致看板既看不到也
    # 无法编辑这两个 block 下的字段。
    ("scheduler", "统一调度器 Scheduler", "🗓️", "scheduler", _models.SchedulerConfig, {}),
    ("memory_backfill", "记忆回填", "🧬", "memory_backfill", _models.MemoryBackfillConfig, {}),
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


def apply_list_seed_merge(raw_file_cfg: dict, block: str, field_name: str, new_items: list) -> tuple:
    """[growth_advisor_improvement_plan_v4.md 方向二 2.2 节] 把一批字符串
    合并进 `raw_file_cfg[block][field_name]`（一个 list 字段）的深拷贝，
    返回 `(new_cfg, added_count)`。

    跟 `apply_updates()` 是平行但独立的写路径——`apply_updates()` 只处理
    `KNOWN_FIELDS` 里收录的标量字段（模块头部说明里明确写了"不收录
    list/dict 类型的复杂字段"），`TechRadarConfig.keywords` 这类 list
    字段走这里。两者共享同一套"深拷贝 + 不修改传入对象"约定，写盘统一
    通过 `write_config_file()`（与 `PATCH /v1/self/config` 完全一致的
    临时文件 + `os.replace` 原子写入），不允许调用方自己拼 JSON 直接
    写文件——这是 2.5 节风险项 1 明确要求的"必须走跟看板保存配置一致的
    路径"。

    幂等：已存在的项（大小写不敏感比较）不重复添加，只追加新增的。
    """
    import copy
    new_cfg = copy.deepcopy(raw_file_cfg)
    block_dict = new_cfg.get(block)
    if not isinstance(block_dict, dict):
        block_dict = {}
    existing = block_dict.get(field_name)
    if not isinstance(existing, list):
        existing = []
    existing_lower = {str(x).strip().lower() for x in existing}
    merged = list(existing)
    added = 0
    for item in new_items or []:
        item = str(item).strip()
        if not item:
            continue
        key = item.lower()
        if key in existing_lower:
            continue
        existing_lower.add(key)
        merged.append(item)
        added += 1
    block_dict[field_name] = merged
    new_cfg[block] = block_dict
    return new_cfg, added


def write_config_file(config_path, raw_cfg: dict) -> None:
    """原子写入 `agent_config.json`：临时文件 + `os.replace`，与
    `PATCH /v1/self/config`（`api/routes.py::patch_self_config`）使用的
    写入方式完全一致。所有需要修改配置文件的调用方（无论走的是
    `apply_updates()` 还是 `apply_list_seed_merge()`）都应该收敛到这一个
    函数完成落盘，避免同一份文件出现两套不一致的写入实现。
    """
    import json as _json
    import os as _os
    from pathlib import Path as _Path

    config_path = _Path(config_path)
    tmp_path = config_path.with_suffix(".json.tmp")
    tmp_path.write_text(_json.dumps(raw_cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    _os.replace(tmp_path, config_path)
