"""settings.py — 两层设置：全局默认 + 文章库覆盖（方案 §19.3 设置页、§20.1）。

* 每个设置项在 SPEC 里声明：默认值、类型、取值范围、说明、作用域。
* 覆盖文件是**稀疏**的：只存与上一层不同的项；删除某项即“恢复继承”。
* 作用域：
    "both"   可在全局默认与文章库里设置（库覆盖全局）
    "domain" 只能在文章库里设置（自动化开关）。新建库一律取保守默认（全关、G0），
             **不继承其它库的放权状态**；写进全局文件的 domain 项会被忽略并给出警告。
* 读取永不抛异常：非法值回退默认并产生警告。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import paths, store
from .domains import DomainContext

GEARS = ("G0", "G1", "G2", "G3")
SEARCH_PROVIDERS = ("duckduckgo", "brave", "serper", "tavily")
CREDIBILITY = ("A", "B", "C", "D")


def _s(default, typ, label, help_, scope="both", **kw):
    return {"default": default, "type": typ, "label": label, "help": help_, "scope": scope, **kw}


# 分组 → 项 → 规格。默认值全部取保守值（只做建议、不放权、有上限）。
SPEC: Dict[str, Dict[str, Dict[str, Any]]] = {
    "automation": {
        "gear": _s("G0", "enum", "控制挡位", "G0 全手动 / G1 自动整理与选题（选题需放行）/ G2 自动成文（发布前人审）/ G3 自动到 reviewed",
                   scope="domain", choices=GEARS),
        "open_topics": _s(False, "bool", "开放命题", "允许 LLM 在章程范围内自由提主题（规则缺口之外）", scope="domain"),
        "auto_approve": _s(False, "bool", "自动放行选题", "无需人工放行，选题直接进入调研（仍受预算限制）", scope="domain"),
        "auto_research": _s(False, "bool", "自动调研", "放行后自动搜索、抓取并整理证据包", scope="domain"),
        "auto_write": _s(False, "bool", "自动成文", "证据充足时自动写成文章", scope="domain"),
        "auto_publish": _s(False, "bool", "自动发布", "审核通过后自动标为 published（默认必须人工点击）", scope="domain"),
        "scheduled_cycle": _s(False, "bool", "定时运行一轮", "由定时任务/看板按计划自动运行 cycle", scope="domain"),
    },
    "budget": {
        "max_topics_per_cycle": _s(5, "int", "每轮最多选题数", "单轮 cycle 新增选题的上限", min=0, max=100),
        "max_articles_per_cycle": _s(2, "int", "每轮最多成文数", "单轮 cycle 写成文章的上限", min=0, max=50),
        "max_llm_calls_per_day": _s(200, "int", "每日最多 LLM 调用", "本库当日调用上限，超出即停并记录", min=0, max=100000),
        "global_max_llm_calls_per_day": _s(500, "int", "全局每日最多 LLM 调用",
                                           "所有文章库合计的当日总闸（防多库同时跑放大成本）", scope="global", min=0, max=1000000),
        "max_fetch_pages_per_topic": _s(8, "int", "每选题最多抓取页数", "单个选题调研最多抓取的网页数", min=0, max=100),
        "max_repair_rounds": _s(6, "int", "每篇最多修复轮数", "校验不通过时回喂 LLM 修复的轮数硬上限，到达后标 failed 并保留产物", min=1, max=50),
        "step_timeout_minutes": _s(40, "int", "单步超时（分钟）", "单个步骤最长执行时间，超时终止并重跑", min=1, max=600),
    },
    "llm": {
        "use_system_proxy": _s(False, "bool", "大模型使用系统代理", "默认不使用（与其它外部项目一致）"),
    },
    "search": {
        "provider": _s("duckduckgo", "enum", "搜索后端", "duckduckgo 无需 key；其它需在 mini_agent 配置对应 key", choices=SEARCH_PROVIDERS),
        "max_results": _s(5, "int", "每次搜索结果数", "每条查询取前 N 条", min=1, max=20),
    },
    "thresholds": {
        "overlap_max": _s(0.35, "float", "与库内文章的重复度上限", "段落级相似度高于此值视为同质化（首版默认，待用示例库校准）", min=0.0, max=1.0),
        "copy_chars_max": _s(60, "int", "与来源连续重合字数上限", "防止整段照抄来源", min=10, max=1000),
        "excerpt_chars_max": _s(300, "int", "每来源摘录字数上限", "来源卡中摘录的最大长度", min=50, max=3000),
        "min_credibility": _s("B", "enum", "来源最低可信度", "低于此等级的来源不用（章程里的同名项优先于此）", choices=CREDIBILITY),
    },
}

GLOBAL_ONLY_GROUPS_KEYS = {(g, k) for g, items in SPEC.items() for k, v in items.items() if v["scope"] == "global"}


def defaults() -> Dict[str, Dict[str, Any]]:
    return {g: {k: copy.deepcopy(v["default"]) for k, v in items.items()} for g, items in SPEC.items()}


def _coerce(spec: Dict[str, Any], value: Any) -> Any:
    """按规格转换；非法抛 ValueError。"""
    t = spec["type"]
    if t == "bool":
        if isinstance(value, bool):
            return value
        raise ValueError("应为 true/false")
    if t == "int":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise ValueError("应为整数")
        v = int(value)
    elif t == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("应为数字")
        v = float(value)
    elif t == "enum":
        v = str(value)
        if v not in spec["choices"]:
            raise ValueError(f"应为 {'/'.join(spec['choices'])} 之一")
        return v
    else:  # pragma: no cover
        raise ValueError(f"未知类型 {t}")
    if "min" in spec and v < spec["min"]:
        raise ValueError(f"不能小于 {spec['min']}")
    if "max" in spec and v > spec["max"]:
        raise ValueError(f"不能大于 {spec['max']}")
    return v


def normalize_overrides(raw: Any, layer: str) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """把读到的覆盖文件整理成合法的稀疏结构。layer: "global" | "domain"。"""
    warnings: List[str] = []
    out: Dict[str, Dict[str, Any]] = {}
    if raw is None:
        return out, warnings
    if not isinstance(raw, dict):
        return out, ["设置文件根节点应为键值结构，已忽略"]
    for group, items in raw.items():
        if group not in SPEC:
            warnings.append(f"未知设置分组 {group!r}，已忽略")
            continue
        if not isinstance(items, dict):
            warnings.append(f"设置分组 {group} 应为键值结构，已忽略")
            continue
        for key, value in items.items():
            spec = SPEC[group].get(key)
            if spec is None:
                warnings.append(f"未知设置项 {group}.{key}，已忽略")
                continue
            if layer == "global" and spec["scope"] == "domain":
                warnings.append(f"{group}.{key} 只能在文章库里设置（不随全局继承），已忽略")
                continue
            if layer == "domain" and spec["scope"] == "global":
                warnings.append(f"{group}.{key} 只能在全局默认里设置，已忽略")
                continue
            try:
                out.setdefault(group, {})[key] = _coerce(spec, value)
            except ValueError as exc:
                warnings.append(f"{group}.{key} 的值 {value!r} 非法（{exc}），已使用默认值")
    return out, warnings


def _load_layer(path: Path, layer: str) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    try:
        raw = store.read_yaml(path, default={})
    except Exception as exc:  # noqa: BLE001
        return {}, [f"{path.name} 无法解析（{type(exc).__name__}），已忽略该层设置"]
    return normalize_overrides(raw, layer)


def load_global_overrides() -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    return _load_layer(paths.global_settings_path(), "global")


def load_domain_overrides(ctx: DomainContext) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    return _load_layer(ctx.settings_path, "domain")


def effective_settings(ctx: Optional[DomainContext] = None) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """生效值 = 默认 → 全局覆盖 → 库覆盖。ctx 为 None 时只到全局层。"""
    eff = defaults()
    g, warns = load_global_overrides()
    for grp, items in g.items():
        eff[grp].update(items)
    if ctx is not None:
        d, w2 = load_domain_overrides(ctx)
        warns = warns + w2
        for grp, items in d.items():
            eff[grp].update(items)
    return eff, warns


def override_keys(ctx: Optional[DomainContext]) -> set:
    """当前库覆盖了哪些项（用于设置页高亮“本库覆盖”）。"""
    if ctx is None:
        return set()
    d, _ = load_domain_overrides(ctx)
    return {(g, k) for g, items in d.items() for k in items}


def _layer_path(layer: str, ctx: Optional[DomainContext]) -> Path:
    if layer == "global":
        return paths.global_settings_path()
    if ctx is None:
        raise ValueError("保存文章库设置需要 DomainContext")
    return ctx.settings_path


def save_override(layer: str, group: str, key: str, value: Any, ctx: Optional[DomainContext] = None) -> None:
    """写入一个覆盖项；值与默认/上层相同也照写（显式覆盖）。非法值抛 ValueError。"""
    spec = SPEC.get(group, {}).get(key)
    if spec is None:
        raise ValueError(f"未知设置项 {group}.{key}")
    if layer == "global" and spec["scope"] == "domain":
        raise ValueError(f"{group}.{key} 只能在文章库里设置")
    if layer == "domain" and spec["scope"] == "global":
        raise ValueError(f"{group}.{key} 只能在全局默认里设置")
    v = _coerce(spec, value)
    path = _layer_path(layer, ctx)
    cur, _ = _load_layer(path, layer)
    cur.setdefault(group, {})[key] = v
    store.write_yaml_atomic(path, cur)


def clear_override(layer: str, group: str, key: str, ctx: Optional[DomainContext] = None) -> None:
    """删除一个覆盖项，恢复继承。"""
    path = _layer_path(layer, ctx)
    cur, _ = _load_layer(path, layer)
    if key in cur.get(group, {}):
        del cur[group][key]
        if not cur[group]:
            del cur[group]
        store.write_yaml_atomic(path, cur)
