"""
config/param_registry.py — 统一参数注册与解析机制

对应设计文档：docs/param-system-guide.md（新增参数的唯一权威规范）。

背景（重构动机）：在本模块之前，`agent_config.json` 里的配置项有两类
互相独立、且第二类内部风格也不统一的读取方式：

  1. "嵌套 block" 参数（如 `autonomy.fairness_time_slicing_enabled`、
     `tech_radar.daily_seed_limit`）——概念上应该是"字段名与 dataclass
     字段名一一对应，直接通用遍历展开即可"，但历史上只有少数几个
     block（autonomy/observability）真正用了这种通用遍历
     （`load_nested_block()`），其余十几个 block 都是在
     `config/loader.py` 里手写的一大段 `XxxConfig(field=int(_x.get(...,
     默认值)), ...)`，每加一个字段就要在 dataclass 定义、这段手写构造
     代码里各改一遍——两处默认值还经常不同步（本模块修复前
     `autonomy`/`observability` 两个 block 就长期"写了当没写"，见
     docs/goal-execution-fairness-config.md 的更正说明）。

  2. "扁平 CLI 参数"（如 `--memory`/`--workers`）——CLI 定义
     （cli/parser.py）、优先级合并（loader.py 的 `_f`/`_fb`/`_fn`）、
     字段说明（config_catalog.py 的 `_CORE_FIELDS` 等）三处独立维护，
     同一个参数的 json_key/cli flag/说明文字分散在三个文件里。

本模块的职责：把"嵌套 block"这一类（数量最多、后续新增参数最频繁的
场景）收敛成一份唯一的注册表 `NESTED_CONFIG_BLOCKS`，并提供通用的
`load_nested_block()` / `load_all_nested_blocks()`。`config/loader.py`
和 `config/config_catalog.py` 都从这里 import 同一份表，不再各自维护
一份重复列表。

同时为"扁平 CLI 参数"提供 `ParamSpec` + `resolve_flat_param()` /
`register_cli_argument()`，作为**新增**扁平参数的统一写法（老的
`_f`/`_fb`/`_fn` 手写调用点数量大、逐个改造风险高，本次不做存量迁移，
但新参数一律要求走这里，见 docs/param-system-guide.md）。

════════════════════════════════════════════════════════════════════
如何新增一个"嵌套 block"参数（绝大多数新参数应该走这条路）：
  1. 在 config/models.py 对应的 @dataclass 里加一个字段 + 默认值。
  2. 完事。`load_nested_block()` 会通过 dataclasses.fields() 自动发现
     新字段，`agent_config.json` 里对应 block 下同名 key 会被自动读取
     并按默认值的类型做转换；config_catalog.py（看板配置 UI）也会自动
     展示这个新字段。不需要改本文件、loader.py、config_catalog.py。
  3. 如果这是全新的 block（不是往已有 block 里加字段），才需要：
     a) 在 models.py 定义新的 @dataclass；
     b) 在 AppConfig 里加一个对应字段；
     c) 在本文件的 `NESTED_CONFIG_BLOCKS` 里加一行注册
        `NestedBlockSpec(attr_name, DataclassType)`；
     d) 在 loader.py 的 `load_all_nested_blocks(file_cfg)` 返回值里
        把新 block 传给 AppConfig(...) 构造（一行 kwarg）。

如何新增一个"扁平 CLI 参数"（仅当确实需要命令行覆盖时才用这条路，
纯配置文件参数请优先放进已有的嵌套 block）：
  1. 在本文件的 `FLAT_PARAM_SPECS` 里加一个 `ParamSpec(...)`。
  2. 在 loader.py 里调用 `resolve_flat_param(file_cfg, args, spec)`
     取值，赋给 AppConfig 对应字段。
  3. CLI flag 会通过 `register_cli_argument()` 自动加到 argparse
     parser 里，不需要手写 `p.add_argument(...)`。
════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
from typing import Any, Optional


# ── 嵌套 block 参数机制 ──────────────────────────────────────────────────

@dataclasses.dataclass
class NestedBlockSpec:
    """一个"顶层 dict block"配置的注册项。

    attr_name: `agent_config.json` 顶层 key，同时也是 AppConfig 对应
               字段名（如 "autonomy" → file_cfg["autonomy"] /
               AppConfig(autonomy=...)）。
    dataclass_type: 对应的 dataclass 类型，字段名必须与 JSON key 一一对应。
    """

    attr_name: str
    dataclass_type: type


def load_nested_block(block: dict, cls: type, env_fallback: Optional[dict] = None):
    """通用地把一个 dict block 加载成对应 dataclass 实例。

    规则：只处理 `cls` 自身声明的字段；block 里缺失的字段一律回退到
    dataclass 定义的默认值（不传入 kwargs，交给 `cls()` 自己处理，这样
    default_factory 字段——如 list——也能正确工作）。类型转换按字段默认值
    的 Python 类型做（bool/int/float/str 走显式转换；list 默认值走
    "非 list 就丢弃回退默认"的保护；其余类型（None 默认、dict、Optional
    等）原样透传，交给上层自行校验）。转换失败的脏字段直接跳过，不让一个
    写错类型的字段拖垮整个 block 的加载。

    显式 null 语义：如果字段在 block 里出现且值为 JSON `null`（Python
    `None`），无条件按 `None` 处理，不管该字段的 dataclass 默认值是不是
    `None`——例如 `WorkflowConfig.approval_wait_timeout_seconds` 默认是
    `600.0`（非 None），但语义上"显式写 null"表示"无限等待"，必须能覆盖
    成 `None`，而不是因为"默认值是 float，None 转不了 float"就静默丢弃、
    退回 600.0。这条规则对所有字段统一生效，不需要逐个字段特殊处理。

    env_fallback: 可选的 `{字段名: 环境变量名}` 映射。字段在 block 里缺失
    或值为空（`None`/`""`）时，尝试从对应环境变量读取（仍按字段默认值的
    类型转换）；环境变量也没有则回退到 dataclass 默认值。用于
    `WebSearchConfig` 这类"配置文件 > 环境变量 > 默认值"的字段。
    """
    kwargs: dict[str, Any] = {}
    env_fallback = env_fallback or {}
    for f in dataclasses.fields(cls):
        in_block = f.name in block
        raw_v = block.get(f.name)
        env_name = env_fallback.get(f.name)
        if in_block and raw_v is None:
            # 显式 null：无条件覆盖为 None，不做类型转换（见上方说明）
            kwargs[f.name] = None
            continue
        if (not in_block or raw_v in (None, "")) and env_name and os.environ.get(env_name) is not None:
            raw_v = os.environ[env_name]
        elif not in_block:
            continue
        has_default = f.default is not dataclasses.MISSING
        default_v = f.default if has_default else None
        has_default_factory = f.default_factory is not dataclasses.MISSING
        factory_sample = f.default_factory() if has_default_factory else None
        try:
            if isinstance(default_v, bool):
                kwargs[f.name] = bool(raw_v)
            elif isinstance(default_v, int) and not isinstance(default_v, bool):
                kwargs[f.name] = int(raw_v)
            elif isinstance(default_v, float):
                kwargs[f.name] = float(raw_v)
            elif isinstance(default_v, str):
                kwargs[f.name] = str(raw_v)
            elif isinstance(default_v, list) or (has_default_factory and isinstance(factory_sample, list)):
                kwargs[f.name] = list(raw_v) if isinstance(raw_v, list) else _fallback_field(
                    cls, f.name, raw_v, "list", default_v if has_default else factory_sample
                )
            elif isinstance(default_v, dict) or (has_default_factory and isinstance(factory_sample, dict)):
                # [P5-5] dict 类型字段（如 GrowthAdvisorConfig.category_
                # notification_frequency）此前是"原样透传"，配置文件/编辑器
                # 存进来的错误类型（例如整段被误当字符串保存）会静默流入
                # 下游，某个随机调用点才报错。这里显式校验类型，不匹配时
                # 回退默认值并记一条 warning 日志，不让一个字段的脏数据
                # 拖垮整个加载流程。
                kwargs[f.name] = raw_v if isinstance(raw_v, dict) else _fallback_field(
                    cls, f.name, raw_v, "dict", default_v if has_default else factory_sample
                )
            else:
                # None 默认 / Optional[...] 等：原样透传（合法空值不应被
                # 误判为类型不匹配，例如 `Optional[str] = None` 传入 None）
                kwargs[f.name] = raw_v
        except (TypeError, ValueError):
            # 类型转换失败（脏配置）——忽略该字段，退回默认值
            continue
    return cls(**kwargs)


def _fallback_field(cls: type, field_name: str, raw_v: Any, expected: str, default_v: Any) -> Any:
    """[P5-5] 类型不匹配的字段回退到默认值，并记一条 warning 日志（不
    中断加载流程）。"""
    try:
        from mini_agent.errors import log_exception
        log_exception(
            TypeError(
                f"{cls.__name__}.{field_name} expected {expected}, "
                f"got {type(raw_v).__name__}; falling back to default"
            ),
            where="mini_agent.config.param_registry.load_nested_block",
            extra={"field": field_name, "dataclass": cls.__name__, "expected_type": expected, "actual_type": type(raw_v).__name__},
            level=logging.WARNING,
        )
    except Exception:
        pass
    return default_v


def apply_overrides(instance: Any, **overrides: Any) -> Any:
    """在一个已经通用加载好的 dataclass 实例上，有选择地覆盖少数几个字段
    ——用于 CLI 参数需要覆盖配置文件值、或者字段需要额外类型转换（如
    `str` → `Path`）这类通用机制无法自动处理的场景。`overrides` 里值为
    `None` 的项会被忽略（表示"这次没有更高优先级的值，保持通用加载的
    结果不变"），只有非 `None` 的项才会真正覆盖。"""
    real = {k: v for k, v in overrides.items() if v is not None}
    return dataclasses.replace(instance, **real) if real else instance


def load_all_nested_blocks(file_cfg: dict, specs: Optional[list] = None) -> dict:
    """按 `NESTED_CONFIG_BLOCKS`（或调用方传入的 specs）批量加载所有嵌套
    block，返回 `{attr_name: dataclass_instance}`，供 loader.py 直接
    `AppConfig(**load_all_nested_blocks(file_cfg))`（连同其它字段一起）
    使用。
    """
    specs = specs if specs is not None else NESTED_CONFIG_BLOCKS
    result: dict[str, Any] = {}
    for spec in specs:
        raw_block = file_cfg.get(spec.attr_name)
        block = raw_block if isinstance(raw_block, dict) else {}
        result[spec.attr_name] = load_nested_block(block, spec.dataclass_type)
    return result


def _build_nested_blocks() -> list:
    """延迟 import models，避免 param_registry ← models ← param_registry
    的循环 import（models.py 不依赖本模块，这里只是保守写法）。"""
    from . import models as _m

    return [
        NestedBlockSpec("tech_radar", _m.TechRadarConfig),
        NestedBlockSpec("ecosystem_positioning", _m.EcosystemPositioningConfig),
        NestedBlockSpec("goal_mode", _m.GoalModeConfig),
        NestedBlockSpec("goal_execution_spec", _m.GoalExecutionSpecConfig),
        NestedBlockSpec("turn_judge", _m.TurnJudgeConfig),
        NestedBlockSpec("workdir_knowledge", _m.WorkdirKnowledgeConfig),
        NestedBlockSpec("global_knowledge", _m.GlobalKnowledgeConfig),
        NestedBlockSpec("proprioception", _m.ProprioceptionConfig),
        NestedBlockSpec("affordance", _m.AffordanceConfig),
        NestedBlockSpec("digest_advisor", _m.DigestAdvisorConfig),
        NestedBlockSpec("growth_advisor", _m.GrowthAdvisorConfig),
        NestedBlockSpec("cron", _m.CronConfig),
        NestedBlockSpec("autonomy", _m.AutonomyConfig),
        NestedBlockSpec("observability", _m.ObservabilityConfig),
        NestedBlockSpec("workflow", _m.WorkflowConfig),
        NestedBlockSpec("reminder", _m.ReminderConfig),
        NestedBlockSpec("format_correction", _m.FormatCorrectionConfig),
        NestedBlockSpec("privacy", _m.PrivacyConfig),
        NestedBlockSpec("role_agent", _m.RoleAgentConfig),
        NestedBlockSpec("env_info", _m.EnvInfoConfig),
        NestedBlockSpec("web_search", _m.WebSearchConfig),
        # [goal_cron_unified_scheduler_improvement_plan.md P5 第 3/5 步
        # 遗留缺陷修复] `SchedulerConfig`（`unified_arbitration_enabled`/
        # `unified_dispatch_enabled`/`channel_weights` 等）自 P5 第 3 步
        # 引入以来一直只是 `AppConfig` 上的一个 dataclass 字段，从未注册
        # 进本表——`agent_config.json` 里写 `"scheduler": {...}` 此前
        # 完全不起作用（`load_config()` 从未把它传给 `AppConfig(...)`，
        # 实例上永远是 dataclass 默认值），是本文件模块头部注释里提到的
        # 那种"autonomy/observability 两个 block 曾长期'写了当没写'"问题
        # 在 `scheduler` 上的重演。此处补齐注册。
        NestedBlockSpec("scheduler", _m.SchedulerConfig),
        # [next_doc/memory_backfill_and_profile_update_plan.md] 记忆回填配置，
        # 走通用加载机制，避免重演 profile_enabled 那种"手写构造代码里默认值
        # 和 dataclass 默认值不一致"的问题（见 loader.py profile_cfg 处的
        # 说明——那里是历史遗留的手写路径，本 block 直接走新机制不受影响）。
        NestedBlockSpec("memory_backfill", _m.MemoryBackfillConfig),
        # [next_doc/goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md
        # Stage 3] 诊断报告 LLM 摘要 / 调优草案 LLM 自然语言解析两个开关，
        # 均默认 False，走通用加载机制。
        NestedBlockSpec("cycle_tuning", _m.CycleTuningConfig),
    ]


# 唯一权威列表：loader.py 与 config_catalog.py 都从这里 import，不再各自
# 维护重复的 block 清单。
#
# 说明：`reminder`/`format_correction`/`privacy`/`role_agent` 这 4 个
# block 里，多数字段走本文件的通用加载，但各自有少数几个字段（如
# `enabled`/`verbose`）额外支持 CLI 覆盖或需要 `str → Path`/合并去重
# 这类通用机制无法自动处理的转换——这部分仍在 loader.py 里用
# `apply_overrides()` 显式处理（见 loader.py 对应位置的注释），不是
# "没接入通用机制"，而是"通用加载 + 小范围手工覆盖"两段式组合。
# `env_info` 的 `provider_kwargs` 字段由 `include_hostname`/
# `include_username` 派生，通用加载后在 loader.py 里做一次派生填充。
#
# 真正保留独立解析逻辑、未纳入本列表的只剩 `mcp`（`servers` 是一个独立
# dataclass 组成的列表，不是"字段名直接映射"的扁平 block，天然不适合这
# 套机制）。新增字段如果落在 `mcp` block 里，直接在 loader.py 里
# `mcp_server_list` 那段手写代码里加一行即可。
NESTED_CONFIG_BLOCKS: list = _build_nested_blocks()


# ── 扁平 CLI 参数机制（供新增参数使用，见模块头部说明）──────────────────

@dataclasses.dataclass
class ParamSpec:
    """一个"CLI 可覆盖的扁平参数"的完整定义：CLI flag + json_key + env
    var + 类型 + 默认值 + 帮助文本，一处定义，各处通用消费。
    """

    name: str                       # 内部标识 / argparse dest
    json_key: str                   # agent_config.json 顶层 key
    cli_flags: tuple = ()           # argparse flag，如 ("--foo",)；留空表示不提供 CLI 覆盖
    env_var: Optional[str] = None   # 环境变量名（优先级低于配置文件，高于硬编码默认值）
    default: Any = None
    value_type: type = str          # str / int / float / bool
    help: str = ""
    cli_action: Optional[str] = None  # 传给 argparse 的 action，如 "store_true"


def register_cli_argument(parser: argparse.ArgumentParser, spec: ParamSpec) -> None:
    """按 ParamSpec 自动往 argparse parser 上加一条 `add_argument`，不需要
    在 cli/parser.py 里手写。"""
    if not spec.cli_flags:
        return
    kwargs: dict[str, Any] = {"dest": spec.name, "default": None, "help": spec.help}
    if spec.cli_action:
        kwargs["action"] = spec.cli_action
    elif spec.value_type is not str:
        kwargs["type"] = spec.value_type
    parser.add_argument(*spec.cli_flags, **kwargs)


def resolve_flat_param(file_cfg: dict, args: Any, spec: ParamSpec) -> Any:
    """通用版 CLI > agent_config.json > 环境变量 > 默认值 解析，替代原来
    逐个手写的 `_f`/`_fb`/`_fn` 闭包。`args` 是 argparse Namespace（或任意
    带 getattr 的对象）；没有对应 CLI flag 的 spec 直接跳过 CLI 层。
    """
    cli_val = getattr(args, spec.name, None) if spec.cli_flags and args is not None else None
    if cli_val is not None:
        return spec.value_type(cli_val) if spec.value_type is not bool else bool(cli_val)
    if spec.json_key in file_cfg:
        raw_v = file_cfg[spec.json_key]
        try:
            return spec.value_type(raw_v) if spec.value_type is not bool else bool(raw_v)
        except (TypeError, ValueError):
            pass
    if spec.env_var and os.environ.get(spec.env_var) is not None:
        raw_v = os.environ[spec.env_var]
        try:
            return spec.value_type(raw_v) if spec.value_type is not bool else bool(raw_v)
        except (TypeError, ValueError):
            pass
    return spec.default


# 新增扁平 CLI 参数从这里加一行即可（当前为空——存量参数仍走 loader.py 里
# 的 `_f`/`_fb`/`_fn`，见模块头部"存量迁移"说明）。
FLAT_PARAM_SPECS: list = []