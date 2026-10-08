"""world_simulator/element_view.py — 元素模型的展示分组、手动编辑与预算参数校验（第二十三轮 E6）。

设计依据：`next_doc/world_simulator_element_causal_lines_plan.md` §4.7 “界面与导出”、§5.1（预算可调、
`null`=不限）。纯 Python、不调 LLM、不读写磁盘；Streamlit 界面（`app.py`）与静态 HTML 导出
（`html_export.py`）共用这里的分组/徽标，避免两处各写一套。

## 三件事

1. **展示**：`build_overview()` 把 `causal_lines` 按领域分组（领域线做标题，子元素在下；`parent` 缺省/指向不存在
   领域的线与旧式独立线归入“未归类”），每行带徽标——类型、当前分级（派生值，不存储）、待补全/兜底、已退场/
   已合并、钉住；`filter_overview()` 按元素类型过滤。元素模式未开启返回 `enabled=False`，调用方照旧平铺展示。
2. **手动编辑**：`apply_element_edit()` 修改一条元素线的 `parent`/`aliases`/`tier_pin`/`status`。只做**结构校验**
   （领域必须存在、别名不与其它线的 id/名称/别名冲突、状态只允许 alive↔retired），返回新列表和错误，不改入参。
3. **预算参数**：`param_specs()`/`current_params()`/`build_params()` 把散在 `element_registry`/`element_tiers`
   里的九个参数统一成“设置页表单”需要的形态；上限类参数 `null`=不限（界面“不限”勾选），**不用 0 表示不限**。

## 刻意不做

- 不改任何引擎行为；手动退场的元素和 LLM 退场的元素等价（`status=retired`），不会触发已入队因果的撤销。
- 不提供“合并/拆分”的手动编辑（那是 `element_ops` 的事，需要改写未来树分支，由 LLM 判断语义后经引擎校验执行）。
- 手动把 `retired` 改回 `alive` 是用户的显式决定，允许（引擎自己不会“复活”已退场元素）；`merged` 的元素不允许改状态
  （要撤销合并需要拆回分支，不在本阶段范围）。
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from world_simulator import anatomy as an
from world_simulator import anatomy_templates as at
from world_simulator import element_registry as er
from world_simulator import element_tiers as et
from world_simulator import evidence as evm

_UNSET: Any = object()

TYPE_LABELS: Dict[str, str] = {
    "technology": "技术", "project": "项目", "organization": "组织", "person": "人物", "asset": "资产",
    "policy": "政策", "market": "市场", "resource": "资源", "event_series": "事件序列",
}
UNTYPED_LABEL = "未标注类型"
TIER_LABELS: Dict[str, str] = {"active": "🔥 活跃", "watch": "👀 关注", "dormant": "💤 休眠"}
UNGROUPED_LABEL = "未归类"
EDITABLE_STATUSES = ("alive", "retired")


def type_label(element_type: Any) -> str:
    text = str(element_type or "").strip()
    return TYPE_LABELS.get(text, text) if text else UNTYPED_LABEL


# ── 展示 ─────────────────────────────────────────────────────────────


def _row(line: Dict[str, Any], tier: Optional[str], lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    lid = str(line["id"]).strip()
    kind = er.line_kind(line)
    status = str(line.get("status") or "alive")
    badges: List[str] = []
    if kind == "domain":
        badges.append("领域")
    else:
        badges.append(type_label(line.get("element_type")))
        if tier in TIER_LABELS and status == "alive":
            badges.append(TIER_LABELS[tier])
        if line.get("tier_pin") == "active":
            badges.append("📌 固定活跃")
    profile = str(line.get("profile_status") or "")
    if profile == "pending_enrichment":
        badges.append("待补全")
    elif profile == "fallback":
        badges.append("兜底（未补全）")
    if status == "retired":
        badges.append("已退场")
    elif status == "merged":
        badges.append(f"已合并→{line.get('merged_into') or '?'}")
    return {
        "id": lid,
        "label": str(line.get("label") or lid),
        "kind": kind,
        "element_type": str(line.get("element_type") or "").strip(),
        "type_label": type_label(line.get("element_type")) if kind != "domain" else "领域",
        "tier": tier,
        "status": status,
        "profile_status": profile,
        "tier_pin": str(line.get("tier_pin") or ""),
        "parent": str(line.get("parent") or ""),
        "aliases": [str(a) for a in (line.get("aliases") or [])],
        "badges": badges,
    }


def _virtual_type_groups(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """没有任何领域线时的展示层分组：按 `element_type` 归组（保持首次出现的顺序与组内登记顺序）；
    没有类型的行归“未归类”（放最后）。`virtual=True` 标记它不是真实领域线（没有 `domain_row`，不可当 `parent` 用）。"""
    typed: Dict[str, List[Dict[str, Any]]] = {}
    untyped: List[Dict[str, Any]] = []
    for r in rows:
        key = str(r.get("element_type") or "").strip()
        (typed.setdefault(key, []) if key else untyped).append(r)
    out = [
        {"domain_id": "", "domain_label": f"{type_label(k)}（按类型）", "domain_row": None, "rows": v, "virtual": True}
        for k, v in typed.items()
    ]
    if untyped:
        out.append({"domain_id": "", "domain_label": UNGROUPED_LABEL, "domain_row": None, "rows": untyped})
    return out


def build_overview(
    settings: Optional[Dict[str, Any]], history: Sequence[Any] = (), *, extra_ids: Iterable[str] = ()
) -> Dict[str, Any]:
    """总览用的分组数据。

    返回 `{"enabled", "groups": [{"domain_id", "domain_label", "domain_row", "rows"}], "types": [...], "counts": {...}}`。
    `enabled=False`（元素模式未开启）时 `groups` 为空，调用方沿用原来的平铺展示。
    `extra_ids`：声明列表里没有、但历史里出现过的 id（总览的退化路径）——归入“未归类”，没有元素字段。

    分级是派生值（`element_tiers.derive_tiers`），按“下一步（历史最后一步 +1）”计算；算失败就不显示分级徽标，
    不影响分组。
    """
    if not er.is_enabled(settings):
        return {"enabled": False, "groups": [], "types": [], "counts": {}}
    lines = er.get_lines(settings)
    tiers: Dict[str, str] = {}
    try:
        last = max([int(getattr(s, "step", 0) or 0) for s in history] or [0])
        tiers = et.derive_tiers(list(history), settings, step=last + 1)["tiers"]
    except Exception:  # noqa: BLE001 — 分级只是徽标，算不出来就不显示
        tiers = {}
    groups: List[Dict[str, Any]] = []
    types: Dict[str, str] = {}
    counts = {"domains": 0, "elements": 0, "alive": 0, "pending_enrichment": 0, "fallback": 0, "retired": 0,
              "merged": 0}

    def _count(line: Dict[str, Any]) -> None:
        if er.line_kind(line) == "domain":
            counts["domains"] += 1
            return
        counts["elements"] += 1
        status = line.get("status", "alive")
        if status == "retired":
            counts["retired"] += 1
        elif status == "merged":
            counts["merged"] += 1
            counts["elements"] -= 1  # 已合并的不算独立元素（它的历史属于合并目标）
            return
        else:
            counts["alive"] += 1
        if line.get("profile_status") == "pending_enrichment":
            counts["pending_enrichment"] += 1
        elif line.get("profile_status") == "fallback":
            counts["fallback"] += 1

    sources = merged_sources(settings)
    has_real_domains = bool(er.domains(lines))
    for dom, kids in er.group_by_domain(lines):
        # 已合并的元素不再单独占行：它的历史按“读取时重定向”并入合并目标那一行（见 `merged_sources`），
        # 目标行用徽标注明并入了谁。
        shown = [k for k in kids if k.get("status") != "merged"]
        rows = [_row(k, tiers.get(str(k["id"]).strip()), lines) for k in shown]
        for r in rows:
            if sources.get(r["id"]):
                r["badges"].append("并入：" + "、".join(sources[r["id"]]))
        if dom is None:
            if not has_real_domains:
                # 没有任何领域线（LLM 创建时没划领域 / 旧实例 / 推进中发现的元素）：不再把所有元素堆进“未归类”，
                # 改按 `element_type` 做**展示层**虚拟分组（不写盘、不改 prompt、不创建领域线、不影响预算）。
                # 没有 `element_type` 的仍归“未归类”。有真实领域线时这里保持原样（散线仍是“未归类”，不猜）。
                groups.extend(_virtual_type_groups(rows))
            else:
                groups.append({"domain_id": "", "domain_label": UNGROUPED_LABEL, "domain_row": None, "rows": rows})
        else:
            groups.append({
                "domain_id": str(dom["id"]).strip(), "domain_label": str(dom.get("label") or dom["id"]),
                "domain_row": _row(dom, None, lines), "rows": rows,
            })
        for k in kids:
            _count(k)
            if er.line_kind(k) != "domain" and k.get("status") != "merged":
                types.setdefault(str(k.get("element_type") or "").strip(), type_label(k.get("element_type")))
        if dom is not None:
            _count(dom)
    known = {str(x["id"]).strip() for x in lines}
    extras = [str(x).strip() for x in extra_ids if str(x).strip() and str(x).strip() not in known]
    if extras:
        rows = [{
            "id": x, "label": x, "kind": "legacy", "element_type": "", "type_label": UNTYPED_LABEL, "tier": None,
            "status": "alive", "profile_status": "", "tier_pin": "", "parent": "", "aliases": [], "badges": [UNTYPED_LABEL],
        } for x in extras]
        for g in groups:
            if g["domain_id"] == "" and not g.get("virtual"):
                g["rows"].extend(rows)
                break
        else:
            groups.append({"domain_id": "", "domain_label": UNGROUPED_LABEL, "domain_row": None, "rows": rows})
        types.setdefault("", UNTYPED_LABEL)
    counts["virtual_groups"] = sum(1 for g in groups if g.get("virtual"))
    return {
        "enabled": True, "groups": groups, "counts": counts,
        "types": sorted(({"value": k, "label": v} for k, v in types.items()), key=lambda d: d["label"]),
    }


def merged_sources(settings: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    """`{合并目标 id: [被并入的元素 id...]}`（沿 `merged_into` 链走到存活的那条线，按登记顺序）。
    元素模式未开启返回 `{}`。历史里的 `line_updates`/`causal_links` 仍用被并入元素的旧 id 写着（历史不可变），
    展示时用它把旧 id 的记录并回目标那一行——这是 E5 遗留的“视图层重定向”。"""
    if not er.is_enabled(settings):
        return {}
    lines = er.get_lines(settings)
    out: Dict[str, List[str]] = {}
    for line in lines:
        if line.get("status") != "merged":
            continue
        target = er.follow_merge(lines, line)
        tid, sid = str(target.get("id") or "").strip(), str(line.get("id") or "").strip()
        if tid and sid and tid != sid:
            out.setdefault(tid, []).append(sid)
    return out


def update_for_line(line_updates: Any, line_id: str, sources: Optional[Sequence[str]] = None) -> Any:
    """某一步里 `line_id` 这一行该展示的 `line_updates` 条目：先看它自己的 id，没有再依次看被并入它的元素的旧 id。
    `sources` 缺省/为空时等价于 `line_updates.get(line_id)`（旧实例行为不变）。"""
    updates = line_updates or {}
    if line_id in updates:
        return updates.get(line_id)
    for sid in sources or ():
        if sid in updates:
            return updates.get(sid)
    return None


def link_ids(line_id: str, sources: Optional[Sequence[str]] = None) -> set:
    """`causal_links` 里哪些 `line_id` 算这一行的：自己 + 被并入它的元素的旧 id。"""
    return {line_id, *(sources or ())}


def filter_overview(
    overview: Dict[str, Any], types: Optional[Iterable[str]], *, hide_inactive: bool = False
) -> Dict[str, Any]:
    """按元素类型过滤（`types` 是 `element_type` 原值的集合，空=不过滤），可选隐藏已退场/已合并的元素。
    只要有任何一个过滤条件生效，过滤后没有子元素的领域组整组隐藏；领域线本身不受过滤影响（它只是标题）。
    两个条件都不生效时原样返回同一个对象。返回新对象，不改入参。"""
    wanted = {str(t) for t in (types or [])}
    if (not wanted and not hide_inactive) or not overview.get("enabled"):
        return overview
    groups = []
    for g in overview["groups"]:
        rows = [
            r for r in g["rows"]
            if (not wanted or r["element_type"] in wanted)
            and not (hide_inactive and r["status"] in ("retired", "merged"))
        ]
        if rows:
            groups.append({**g, "rows": rows})
    return {**overview, "groups": groups}


def ordered_ids(overview: Dict[str, Any]) -> List[str]:
    """分组展示的先后顺序（领域线在其子元素之前）。"""
    out: List[str] = []
    for g in overview.get("groups", []):
        if g["domain_row"] is not None:
            out.append(g["domain_row"]["id"])
        out.extend(r["id"] for r in g["rows"])
    return out


# ── 手动编辑 ─────────────────────────────────────────────────────────


def _clean_aliases(raw: Any) -> List[str]:
    if isinstance(raw, str):
        raw = raw.replace("，", ",").replace("、", ",").split(",")
    out: List[str] = []
    seen: set = set()
    for a in raw or []:
        text = str(a).strip()
        key = er.norm_key(text)
        if text and key and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def apply_element_edit(
    lines: Sequence[Dict[str, Any]],
    line_id: str,
    *,
    parent: Any = _UNSET,
    aliases: Any = _UNSET,
    tier_pin: Any = _UNSET,
    status: Any = _UNSET,
    step: int = 0,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """改一条元素线的 `parent`/`aliases`/`tier_pin`/`status`（没传的字段不动）。返回 `(新的 lines, 错误)`；
    有错误时返回**原样的深拷贝**（整次编辑作废，不做半截修改）。

    校验：
    - 线必须存在；领域线不能设 `parent`/`tier_pin`/`status`（领域线只做分组，E5 起也不参与退场）；
    - `parent` 必须是空串（=未归类）或一条存活的领域线，且不是自己；
    - `aliases` 去掉空白/重复，并且不能与**其它**线的 id/label/别名规范化后相同（否则解析会二义）；与自己的
      id/label 相同的别名会被丢掉（没有意义）；
    - `tier_pin` 只能是空串（取消钉住）或 `active`；
    - `status` 只能在 `alive` 与 `retired` 之间切换，`merged` 的线不允许改。退场写 `retired_step=step`、
      `retire_reason="手动"`；恢复为 alive 时清掉这两个字段。
    """
    new_lines = copy.deepcopy([dict(x) for x in lines])
    errors: List[str] = []
    target = next((x for x in new_lines if str(x.get("id") or "").strip() == str(line_id).strip()), None)
    if target is None:
        return new_lines, [f"找不到元素「{line_id}」"]
    kind = er.line_kind(target)
    domain_ids = {str(x["id"]).strip() for x in new_lines if x.get("kind") == "domain" and er.is_alive(x)}

    if parent is not _UNSET:
        pid = str(parent or "").strip()
        if kind == "domain":
            errors.append("领域线没有所属领域")
        elif pid and pid not in domain_ids:
            errors.append(f"所属领域「{pid}」不是已登记且存活的领域线")
        elif pid == str(target["id"]).strip():
            errors.append("不能把自己设为所属领域")
        else:
            target["parent"] = pid
    if aliases is not _UNSET:
        cleaned = _clean_aliases(aliases)
        own = {er.norm_key(target["id"]), er.norm_key(target.get("label"))}
        cleaned = [a for a in cleaned if er.norm_key(a) not in own]
        taken: Dict[str, str] = {}
        for other in new_lines:
            if other is target:
                continue
            oid = str(other.get("id") or "").strip()
            for name in [other.get("id"), other.get("label"), *(other.get("aliases") or [])]:
                key = er.norm_key(name)
                if key:
                    taken.setdefault(key, oid)
        for a in cleaned:
            owner = taken.get(er.norm_key(a))
            if owner is not None:
                errors.append(f"别名「{a}」与元素「{owner}」的 id/名称/别名冲突")
        if not errors:
            target["aliases"] = cleaned
    if tier_pin is not _UNSET:
        pin = str(tier_pin or "").strip()
        if kind == "domain":
            errors.append("领域线不参与分级，不能钉住")
        elif pin not in ("",) + tuple(er.TIER_PINS):
            errors.append(f"tier_pin 只能是空或 {'/'.join(er.TIER_PINS)}")
        elif pin:
            target["tier_pin"] = pin
        else:
            target.pop("tier_pin", None)
    if status is not _UNSET:
        st_new = str(status or "").strip()
        cur = str(target.get("status") or "alive")
        if kind == "domain":
            errors.append("领域线不能退场")
        elif cur == "merged":
            errors.append("已合并的元素不能改状态")
        elif st_new not in EDITABLE_STATUSES:
            errors.append(f"status 只能是 {'/'.join(EDITABLE_STATUSES)}")
        elif st_new != cur:
            if st_new == "retired":
                target["status"] = "retired"
                target["retired_step"] = int(step)
                target["retire_reason"] = "手动"
            else:
                target["status"] = "alive"
                target.pop("retired_step", None)
                target.pop("retire_reason", None)
    if errors:
        return copy.deepcopy([dict(x) for x in lines]), errors
    return new_lines, []


# ── 预算参数 ─────────────────────────────────────────────────────────

_PARAM_ORDER = (
    "create_max_elements", "create_max_per_domain", "max_active_in_prompt", "max_total_elements",
    "active_window_steps", "watch_window_steps", "dormant_index_max", "candidate_promote_mentions",
    "enrich_grace_steps",
)
_PARAM_META: Dict[str, Dict[str, Any]] = {
    "create_max_elements": {"label": "创建时元素总数上限", "limit": True, "min": 0,
                            "help": "只在创建模拟时生效；超出的元素不会在创建时登记。"},
    "create_max_per_domain": {"label": "创建时每个领域的元素上限", "limit": True, "min": 0,
                              "help": "只在创建模拟时生效。"},
    "max_active_in_prompt": {"label": "同时完整进 prompt 的活跃元素数", "limit": True, "min": 0,
                             "help": "超出的活跃元素降为“关注”（一行摘要）；钉住的不受裁剪。"},
    "max_total_elements": {"label": "运行中元素总数上限", "limit": True, "min": 0,
                           "help": "达到后新发现的元素只进候选池，不再登记。默认不限（长模拟里历史文件会增长）。"},
    "active_window_steps": {"label": "活跃窗口（步）", "limit": False, "min": 1,
                            "help": "最近这么多步内有动静算活跃（按线自身节奏缩放）。"},
    "watch_window_steps": {"label": "关注窗口（步）", "limit": False, "min": 1,
                           "help": "不满足活跃、但这么多步内有过动静算关注；小于活跃窗口时按活跃窗口处理。"},
    "dormant_index_max": {"label": "休眠索引条数上限", "limit": False, "min": 0,
                          "help": "休眠元素只在 prompt 里留 id+label 的精简索引；0 = 不列索引。"},
    "candidate_promote_mentions": {"label": "候选转正所需提及次数", "limit": False, "min": 1,
                                   "help": "候选池里的对象被不同步骤再提到这么多次才转正登记。"},
    "enrich_grace_steps": {"label": "补全宽限步数", "limit": False, "min": 0,
                           "help": "登记后给 AI 这么多步补全信息；0 = 不索要补全，登记当步即按兜底。"},
}


def param_specs() -> List[Dict[str, Any]]:
    """设置页表单的参数说明（顺序固定）：`key/label/limit(是否可“不限”)/min/help`。"""
    return [{"key": k, **_PARAM_META[k]} for k in _PARAM_ORDER]


def current_params(settings: Optional[Dict[str, Any]]) -> Dict[str, Optional[int]]:
    """九个参数的当前生效值（非法值已回退默认）。`None` = 不限。"""
    out: Dict[str, Optional[int]] = {}
    out.update(er.get_params(settings))
    out.update(er.get_runtime_params(settings))
    out.update(et.get_tier_params(settings))
    return {k: out[k] for k in _PARAM_ORDER}


def build_params(
    existing: Optional[Dict[str, Any]], form: Dict[str, Dict[str, Any]]
) -> Tuple[Dict[str, Any], List[str]]:
    """把表单值并进 `element_params`。`form[key] = {"unlimited": bool, "value": number}`。

    返回 `(新的 element_params, 错误)`。**只改表单里出现的 key，`existing` 里其它键原样保留**；有任何错误时返回
    `existing` 的拷贝（整次保存作废）。上限类参数 `unlimited=True` 写 `None`；`value` 必须是满足下限的整数
    （布尔、小数、负数、文本都算错误，不静默取整）。非上限类参数忽略 `unlimited`。
    """
    result = dict(existing) if isinstance(existing, dict) else {}
    updates: Dict[str, Any] = {}
    errors: List[str] = []
    for key, item in (form or {}).items():
        meta = _PARAM_META.get(key)
        if meta is None:
            errors.append(f"未知参数「{key}」")
            continue
        item = item if isinstance(item, dict) else {"value": item}
        if meta["limit"] and item.get("unlimited"):
            updates[key] = None
            continue
        value = item.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (
                isinstance(value, float) and not value.is_integer()):
            errors.append(f"「{meta['label']}」需要整数")
            continue
        value = int(value)
        if value < meta["min"]:
            errors.append(f"「{meta['label']}」不能小于 {meta['min']}")
            continue
        updates[key] = value
    if errors:
        return (dict(existing) if isinstance(existing, dict) else {}), errors
    result.update(updates)
    return result, []


# ── 元素档案（第二十四轮 A1：只读骨架版） ────────────────────────────

BASIS_LABELS: Dict[str, str] = {
    "sourced": "有出处", "llm_prior": "LLM 先验", "user_confirmed": "已确认", "user_edited": "已编辑",
}
STATUS_LABELS: Dict[str, str] = {
    "none": "无", "draft": "未审阅", "reviewed": "已审阅", "verified": "已核实",
}
BOTTLENECK_STATUS_LABELS = {"open": "未解决", "resolved": "已解决", "exhausted": "路径耗尽"}


def _fmt_num(v: Any) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def _badge(item: Dict[str, Any]) -> str:
    return BASIS_LABELS.get(an.basis_of(item)["state"], "LLM 先验")


def _criteria_text(node: Any) -> str:
    """条件树的一行可读形式（只展示，不求值）。"""
    if not isinstance(node, dict):
        return ""
    for key, join in (("all", " 且 "), ("any", " 或 ")):
        if key in node:
            return "（" + join.join(_criteria_text(c) for c in node[key]) + "）"
    if "not" in node:
        return "非" + _criteria_text(node["not"])
    if "metric" in node:
        return f"{node['metric']} {node['op']} {_fmt_num(node['value'])}"
    if "component" in node:
        return f"{node['component']} 就绪度 ≥ {_fmt_num(node['min_readiness'])}"
    if "bottleneck" in node:
        return f"{node['bottleneck']} 状态={BOTTLENECK_STATUS_LABELS.get(node['status'], node['status'])}"
    return "，".join(f"{k}={v}" for k, v in node.items())


def _rows_for(part: str, items: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for it in items:
        name = it.get("name") or it["id"]
        detail = ""
        if part == "components":
            if "readiness" in it:
                detail = f"就绪度 {_fmt_num(it['readiness'])}"
                if isinstance(it.get("engine"), dict) and it["engine"]["v"] != it["readiness"]:
                    detail += f"（引擎推进到 {_fmt_num(round(it['engine']['v'], 4))}）"
            if it.get("requires"):
                detail += ("；" if detail else "") + "依赖 " + "、".join(it["requires"])
        elif part == "metrics":
            cur = it.get("current") or {}
            if "value" in cur:
                detail = f"现值 {_fmt_num(cur['value'])}{it.get('unit', '')}"
                if cur.get("as_of"):
                    detail += f"（{cur['as_of']}，{_badge(cur)}）"
            if it.get("target"):
                detail += f"；目标 {it['target']['op']} {_fmt_num(it['target']['value'])}"
            trend = it.get("trend") or {}
            if trend:
                detail += f"；趋势 {trend['kind']}" + ("" if trend['kind'] in an.RECOMMENDED_TRENDS else "（非推荐库）")
            eng = it.get("engine")
            if isinstance(eng, dict):  # A4：引擎当前值；趋势没有引擎模型时明说
                detail += f"；**引擎当前值 {_fmt_num(round(eng['v'], 4))}{it.get('unit', '')}**"
                if eng.get("na"):
                    detail += "（无引擎模型，只记录 LLM 报告值）"
        elif part == "bottlenecks":
            detail = BOTTLENECK_STATUS_LABELS.get(it.get("status", "open"), "未解决")
            if it.get("severity"):
                detail += f"；严重度 {it['severity']}"
            paths = it.get("resolution_paths") or []
            if paths:
                detail += f"；{len(paths)} 条解决路径"
            run = next((p for p in paths if p.get("status") == "running"), None)
            if run:  # A4：进行中的路径与预计到期日（引擎模拟日；不显示预先抽好的成败）
                detail += f"；路径「{run['id']}」进行中，预计模拟第 {_fmt_num(round(run['resolve_at_day']))} 天到期"
            if it.get("resolved_step") is not None:
                detail += f"；第 {it['resolved_step']} 步由路径「{it.get('resolved_by', '?')}」解决"
        elif part in ("approaches", "signals", "assumptions"):
            detail = it.get("desc") or it.get("statement") or it.get("watch") or ""
            if part == "assumptions" and "prior_p_true" in it:
                detail += f"（先验成立概率 {_fmt_num(it['prior_p_true'])}）"
        elif part in ("milestones", "adoption_gates"):
            detail = it.get("desc") or it.get("market") or ""
            crit = _criteria_text(it.get("criteria"))
            if crit:
                detail += ("；" if detail else "") + "判据 " + crit
            if it.get("reached_step") is not None:
                detail += f"；第 {it['reached_step']} 步达成"
            if part == "adoption_gates" and isinstance(it.get("open"), bool):
                detail += "；当前已打开" if it["open"] else "；当前未打开"
        rows.append({"id": it["id"], "name": str(name), "detail": detail, "basis": _badge(it)})
    return rows


def build_profile(
    settings: Optional[Dict[str, Any]], element_ref: Any, *, evidence: Optional[List[Dict[str, Any]]] = None,
    now: Any = None,
) -> Dict[str, Any]:
    """元素档案视图（只读）。纯函数，界面与后续导出共用。

    - 剖面总开关未开启（含旧实例）→ `{"enabled": False}`，调用方什么都不画；
    - 元素不存在或没有剖面 → `{"enabled": True, "has_anatomy": False}`；
    - 否则返回表头（名称/类型/模板/状态/重点）、来源统计、按模板顺序排好的各部分行，以及体检提示。
    """
    if not an.is_enabled(settings):
        return {"enabled": False}
    line = er.resolve(settings or {}, element_ref)
    anatomy = an.get_anatomy(line)
    if line is None or anatomy is None:
        return {"enabled": True, "has_anatomy": False}
    meta = anatomy.get("meta") or {}
    tpl = at.get_template(line.get("element_type"))
    stats = an.basis_stats(anatomy)
    sections = [
        {"part": p, "label": at.SLOT_LABELS[p], "rows": _rows_for(p, anatomy[p])}
        for p in an.PARTS if anatomy.get(p)
    ]
    result = {
        "enabled": True, "has_anatomy": True,
        "id": str(line["id"]).strip(), "label": str(line.get("label") or line["id"]),
        "type_label": type_label(line.get("element_type")), "template": tpl["name"], "template_label": tpl["label"],
        "status": meta.get("anatomy_status", "draft"),
        "status_label": STATUS_LABELS.get(meta.get("anatomy_status", "draft"), "未审阅"),
        "key": bool(meta.get("key")),
        "stats": stats, "counts": an.counts(anatomy), "sections": sections,
        "problems": an.validate_anatomy(anatomy),
    }
    if evidence is not None:  # A3：传了证据库才多出证据视图与证据 id 体检；不传则与 A1 输出完全一致
        result["evidence"] = build_evidence_view(settings, line, evidence, now=now)
        result["problems"] = an.validate_anatomy(anatomy, known_evidence_ids=evm.known_ids(evidence))
    return result


def build_progress(settings: Optional[Dict[str, Any]], history: Any, element_ref: Any) -> Dict[str, Any]:
    """元素档案的\"引擎推进\"视图（A4，只读）：已发生的指标/组件取值序列、瓶颈与里程碑当前状态、逐步变化及原因。

    - 剖面总开关未开 / 元素没有剖面 / 引擎从未结算过（没有 `meta.last_step`）→ `{"has_progress": False}`；
    - `series` 是 `{展示名: [(步号, 值), ...]}`（只含有变化的步，界面按步画折线）；`rows` 是逐步流水的可读形式。
    """
    if not an.is_enabled(settings):
        return {"has_progress": False}
    line = er.resolve(settings or {}, element_ref)
    anatomy = an.get_anatomy(line)
    if line is None or anatomy is None or (anatomy.get("meta") or {}).get("last_step") is None:
        return {"has_progress": False}
    from world_simulator import anatomy_engine as ae

    lid = str(line["id"]).strip()
    traj = ae.trajectory(history, lid)
    names = {f"metric:{m['id']}": str(m.get("name") or m["id"]) for m in anatomy.get("metrics") or []}
    names.update({f"component:{c['id']}": f"{c.get('name') or c['id']}（就绪度）" for c in anatomy.get("components") or []})
    meta = anatomy.get("meta") or {}
    bottlenecks = []
    for b in anatomy.get("bottlenecks") or []:
        run = next((p for p in b.get("resolution_paths") or [] if p.get("status") == "running"), None)
        bottlenecks.append({
            "id": b["id"], "name": str(b.get("name") or b["id"]),
            "status": BOTTLENECK_STATUS_LABELS.get(b.get("status", "open"), "未解决"),
            "running_path": run["id"] if run else "", "due_day": round(run["resolve_at_day"]) if run else None,
            "paths": [{"id": p["id"], "status": p.get("status") or "pending"} for p in b.get("resolution_paths") or []],
        })
    milestones = [
        {"id": m["id"], "name": str(m.get("name") or m["id"]), "reached_step": m.get("reached_step"),
         "stage": m.get("maps_to_stage") or ""}
        for m in anatomy.get("milestones") or []
    ]
    return {
        "has_progress": True, "clock_day": meta.get("clock_day", 0), "last_step": meta.get("last_step"),
        "series": {names.get(ref, ref): pts for ref, pts in traj["series"].items()},
        "rows": traj["rows"], "bottlenecks": bottlenecks, "milestones": milestones,
    }


DEEP_STATUS_LABELS = {"ok": "已深度分析", "failed": "深度调用失败（已降级为轻量）", "deferred": "顺延到下一步", "skipped": "已达总上限，跳过"}


def build_deep_view(settings: Optional[Dict[str, Any]], history: Any, element_ref: Any = None) -> Dict[str, Any]:
    """第二十四轮 A5：深度模式的只读展示数据。

    - L1（每步一行）：`steps = [{step, calls, ok, failed, deferred, skipped, cap}]`，只含有深度记录的步；
    - L2（该元素的变化卡片）：`cards = [{step, status, status_label, reasons, narrative_short, accepted}]`；
    - L3（完整叙述）：`cards[i]["narrative"]`（界面折叠展示，**一律按文本处理、由界面转义**）。
    没有任何深度记录 → `{"has_deep": False}`。`element_ref=None` 时只给 L1。"""
    from world_simulator import anatomy_deepen as ad

    steps: List[Dict[str, Any]] = []
    cards: List[Dict[str, Any]] = []
    lid = ""
    if element_ref is not None:
        line = er.resolve(settings or {}, element_ref)
        lid = str(line["id"]).strip() if line else ""
    for state in history or []:
        recs = getattr(state, "anatomy_deep", None) or []
        if not recs:
            continue
        step = getattr(state, "step", None)
        steps.append({"step": step, **ad.step_summary(recs, settings)})
        for r in recs:
            if not isinstance(r, dict) or (lid and r.get("element") != lid):
                continue
            nar = str(r.get("narrative") or "")
            cards.append({
                "step": step, "element": r.get("element"), "status": r.get("status"),
                "status_label": DEEP_STATUS_LABELS.get(r.get("status"), str(r.get("status") or "")),
                "reasons": list(r.get("reasons") or []), "narrative": nar,
                "narrative_short": nar if len(nar) <= 120 else nar[:120] + "…",
                "accepted": r.get("accepted") or {},
            })
    if not steps:
        return {"has_deep": False}
    return {"has_deep": True, "steps": steps, "cards": cards if element_ref is not None else []}


CONFIDENCE_LABELS = {"high": "高", "medium": "中", "low": "低"}
FLAG_LABELS = {"out_of_bounds": "数值超出声明边界，待审", "unit_mismatch": "单位不一致", "value_mismatch": "数值不一致"}


def _field_label(anatomy: Dict[str, Any], field_ref: str) -> str:
    """`metrics.cost.current` → `关键指标「单位成本」现值`；找不到条目就原样返回。"""
    bits = str(field_ref or "").split(".")
    if len(bits) < 2 or bits[0] not in at.SLOT_LABELS:
        return str(field_ref or "")
    item = next((x for x in anatomy.get(bits[0]) or [] if x["id"] == bits[1]), None)
    name = (item or {}).get("name") or bits[1]
    return f"{at.SLOT_LABELS[bits[0]]}「{name}」" + ("现值" if len(bits) > 2 and bits[2] == "current" else "")


def build_evidence_view(
    settings: Optional[Dict[str, Any]], element_ref: Any, records: Iterable[Dict[str, Any]], *, now: Any = None,
) -> Dict[str, Any]:
    """一个元素的证据视图（纯函数）。`element_ref` 可传线 dict 或引用。

    - `items`：有效（active）证据，每条带人读字段、`stale`（按 `research_ttl_days` 现实天数）、待审标记、被哪些字段引用；
    - `prior`：仍是 LLM 先验（没有外部出处）的条目，与有出处的**分开列**；
    - 另给取代/驳回/过期的计数。没有剖面 → 空视图。
    """
    line = element_ref if isinstance(element_ref, dict) else er.resolve(settings or {}, element_ref)
    anatomy = an.get_anatomy(line) or {}
    lid = str(line["id"]).strip() if isinstance(line, dict) and line.get("id") else ""
    ttl = an.get_params(settings)["research_ttl_days"]
    records = list(records or [])
    mine = [r for r in records if r.get("element_id") == lid]
    used: Dict[str, List[str]] = {}
    for part in an.PARTS:
        for it in anatomy.get(part) or []:
            for ref, carrier in ((f"{part}.{it['id']}", it), (f"{part}.{it['id']}.current", it.get("current"))):
                for e in (an.basis_of(carrier).get("evidence_ids") or []) if isinstance(carrier, dict) else []:
                    used.setdefault(e, []).append(_field_label(anatomy, ref))
    items = []
    for r in evm.annotate_stale([x for x in mine if x.get("status") == "active"], now=now, ttl=ttl):
        host = r["source_url"].split("/")[2] if r["source_url"].count("/") >= 2 else r["source_url"]
        value = f"{_fmt_num(r['value'])}{r.get('unit', '')}" if "value" in r else ""
        items.append({
            "ev_id": r["ev_id"], "claim": r["claim"], "value_text": value, "url": r["source_url"],
            "title": r.get("source_title") or host, "publisher": r.get("publisher", ""),
            "published_at": r.get("published_at", ""), "retrieved_at": r.get("retrieved_at", ""),
            "confidence": r["confidence"], "confidence_label": CONFIDENCE_LABELS[r["confidence"]],
            "stale": r["stale"], "flags": [FLAG_LABELS[f] for f in r.get("flags") or [] if f in FLAG_LABELS],
            "field_label": _field_label(anatomy, r.get("field_ref", "")), "used_by": used.get(r["ev_id"], []),
        })
    prior = []
    for part in an.PARTS:
        for it in anatomy.get(part) or []:
            if an.basis_of(it)["state"] == "llm_prior":
                prior.append({"part": part, "label": at.SLOT_LABELS[part], "id": it["id"], "name": it.get("name") or it["id"]})
            cur = it.get("current")
            if part == "metrics" and isinstance(cur, dict) and an.basis_of(cur)["state"] == "llm_prior":
                prior.append({"part": part, "label": at.SLOT_LABELS[part], "id": it["id"], "name": f"{it.get('name') or it['id']}（现值）"})
    meta = anatomy.get("meta") or {}
    return {
        "items": items, "prior": prior, "ttl_days": ttl,
        "stale_count": sum(1 for i in items if i["stale"]),
        "superseded_count": sum(1 for r in mine if r.get("status") == "superseded"),
        "rejected_count": sum(1 for r in mine if r.get("status") == "rejected"),
        "researched_at": meta.get("researched_at", ""), "researched": bool(meta.get("researched_at")),
    }


# ── 第二十四轮 A6：预测视图 ──────────────────────────────────────────


def build_forecast_view(
    forecast: Optional[Dict[str, Any]], element_id: str, sens: Optional[Dict[str, Any]] = None,
    watch: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """把 `forecast.run_forecast` / `sensitivity` / `build_watchlist` 的结果切成**一个元素**的展示数据（纯函数，不重算）。
    预测没跑 / 失败 → `{"has_forecast": False, "reason": ...}`。里程碑/瓶颈/门槛/分支/阶段/指标只留属于该元素的行；
    假设条件化只留该元素的假设；监测清单留该元素的项（含\"全局\"项之外的所有 anatomy 信号与预测生成项）。"""
    if not forecast or not forecast.get("ok"):
        return {"has_forecast": False, "reason": (forecast or {}).get("reason") or "还没有运行预测"}
    mine = lambda rows: [r for r in rows or [] if r.get("element") == element_id]  # noqa: E731
    meta = forecast["meta"]
    out: Dict[str, Any] = {
        "has_forecast": True, "meta": meta, "honesty": meta["honesty"],
        "milestones": mine(forecast.get("milestones")), "bottlenecks": mine(forecast.get("bottlenecks")),
        "gates": mine(forecast.get("gates")), "branches": mine(forecast.get("branches")),
        "stages": mine(forecast.get("stages")), "conditional": mine(forecast.get("conditional")),
        "metrics": [m for m in mine(forecast.get("metrics")) if m.get("p50") and any(v is not None for v in m["p50"])],
        "watch": mine(watch),
        "banners": [],
    }
    if meta.get("truncated"):
        out["banners"].append(f"时间预算用尽，只跑了 {meta['runs_done']}/{meta['runs_requested']} 次——区间噪声更大。")
    if meta.get("time_precision_degraded"):
        out["banners"].append(meta.get("time_precision_note") or "时间精度降级")
    share = (meta.get("params") or {}).get("llm_prior_share")
    if share:
        out["banners"].append(f"参数里有 {share:.0%} 仍是 LLM 先验（没有出处/未经你确认）。")
    sens_rows: List[Dict[str, Any]] = []
    if sens and sens.get("ok"):
        for t in sens.get("targets") or []:
            if t.get("element") == element_id:
                sens_rows.append({"milestone": t["name"], "baseline": t["baseline"], "rows": t["rows"][:10]})
    out["sensitivity"] = sens_rows
    return out
