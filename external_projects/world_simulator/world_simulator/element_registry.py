"""world_simulator/element_registry.py — 统一元素模型的注册表与存取层（第二十三轮 E1）。

设计依据：`next_doc/world_simulator_element_causal_lines_plan.md` §4.1（统一元素模型）、
§4.2（技术模型并入）、§5（开关与兼容）、§6 E1。

## 这个模块做什么（E1 范围）

因果线从"领域级"升级为"元素级"。**存储不新开**：仍是 `manifest.settings.causal_lines`
（已是 `dynamic_state.DYNAMIC_KEYS` 成员，分支隔离/快照/未来树/下游全部按它工作），
每个条目在原有字段之上新增**可选**字段（`kind`/`parent`/`element_type`/`aliases`/`origin`/
`born_step`/`profile_status`/`status`/`merged_into`/`tier_pin`/`var_refs`/`lifecycle`）。

E1 只做三件事，**不改任何 LLM 行为、不加 prompt 段落、不加输出协议键**：

1. 元素字段规整（`normalize_element`）与注册表读取/解析（`resolve`，含别名）、
   领域→子元素查询（`children_of`/`group_by_domain`）；
2. **技术节点存取层**：技术节点（`tech_model` 的节点 dict）整体搬进元素的 `lifecycle`
   子对象。`read_tech_nodes_raw()`/`write_tech_nodes()` 是 `tech_model.get_nodes()`/
   `_write_nodes()` 在"元素模式"下的后端——**裁决规则（R1–R7、T1–T10、修复调用）一字不改**，
   只换"节点从哪读、写到哪去"；
3. **兼容折叠**：`fold_legacy_tech_state()` 把旧 `settings.tech_state` 折叠成带 `lifecycle` 的
   元素线（幂等）。

## 开关

`settings.element_modeling_enabled`：**未写入 = 关闭**（旧实例逐字节保持原行为，
`tech_state` 照旧）；新实例由 `materialize_simulation()` 显式写入 True（方案 §5.2）。

## 刻意不做（留给后续阶段）

创建阶段元素展开（E2）、发现/去重/登记/补全（E3）、派生分级与 prompt 预算（E4）、
split/merge/retire/reparent（E5）、领域聚合联动（E6）。因此 E1 里领域线（`kind=domain`）
只是"可被识别"，引擎自己不会创建它。

本模块是纯 Python：不调 LLM、不读写磁盘。依赖 `causal_tree`（建默认未来树）；对
`tech_model` 只做**函数内延迟导入**（避免循环导入）。
"""

from __future__ import annotations

import copy
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Tuple

from world_simulator import causal_tree

# ── 常量 ─────────────────────────────────────────────────────────────

KINDS = ("domain", "element")
ORIGINS = ("seed", "discovered", "split", "merged", "legacy_tech")
PROFILE_STATUSES = ("complete", "pending_enrichment", "fallback")
STATUSES = ("alive", "retired", "merged")
TIER_PINS = ("active",)

# 推荐词表（仅用于展示/过滤，引擎不依赖取值；`technology` 例外：决定是否带生命周期）。
RECOMMENDED_ELEMENT_TYPES = (
    "technology", "project", "organization", "person", "asset",
    "policy", "market", "resource", "event_series",
)
TECHNOLOGY_TYPE = "technology"

# `lifecycle` 里**不**属于技术节点字段的键：节点的 id/name 对应线的 id/label，节点原 `kind`
# （自由文本）改存为 `sub_kind`，避免与线顶层 `kind` 冲突。
_NODE_ONLY_TOP = ("id", "name", "kind")

_TECH_STATE_FLAG = "tech_state"


# ── 开关 ─────────────────────────────────────────────────────────────


def is_enabled(settings: Optional[Dict[str, Any]]) -> bool:
    """元素模式总开关。未写入 = 关闭（旧实例保持原行为）。"""
    return bool((settings or {}).get("element_modeling_enabled"))


# ── 规整 ─────────────────────────────────────────────────────────────


def norm_key(value: Any) -> str:
    """id/label/别名的比对键：NFKC（全半角归一）→ 小写 → 只保留字母数字（含 CJK），
    其余（空白、标点、下划线、连字符）一律去掉。**只做精确的规范化匹配，不做模糊匹配**
    （方案 §4.4 ②：宁可漏合并，不误合并）。"""
    text = unicodedata.normalize("NFKC", str(value if value is not None else "")).lower()
    return "".join(ch for ch in text if ch.isalnum())


def _str_list(raw: Any) -> List[str]:
    out: List[str] = []
    seen: set = set()
    for item in raw if isinstance(raw, (list, tuple)) else []:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_element(raw: Dict[str, Any]) -> Dict[str, Any]:
    """规整一条因果线条目上的**元素字段**，返回浅拷贝。

    - 只处理本模块拥有的新字段；`id`/`label`/`future_tree`/`owned_vars`… 等既有字段原样保留，
      未知字段也原样保留（向前兼容）。
    - **只规整出现了的字段，不补缺省**——没有这些字段的旧式线逐字节不变（`kind` 缺省 =
      旧式独立线，方案 §4.1）。
    - 非法取值直接丢弃该字段（回到"缺省"语义），不报错。
    """
    line = dict(raw)
    if "kind" in line and line["kind"] not in KINDS:
        line.pop("kind")
    if "parent" in line:
        parent = str(line["parent"]).strip() if line["parent"] is not None else ""
        if parent and parent != str(line.get("id") or "").strip():
            line["parent"] = parent
        else:
            line.pop("parent")  # None / 空 / 自指 = 未归类
    if "element_type" in line:
        et = str(line["element_type"] or "").strip()
        if et:
            line["element_type"] = et
        else:
            line.pop("element_type")
    if "aliases" in line:
        aliases = [a for a in _str_list(line["aliases"]) if a != str(line.get("id") or "").strip()]
        if aliases:
            line["aliases"] = aliases
        else:
            line.pop("aliases")
    if "origin" in line and line["origin"] not in ORIGINS:
        line.pop("origin")
    if "born_step" in line:
        born = _int_or_none(line["born_step"])
        if born is None or born < 0:
            line.pop("born_step")
        else:
            line["born_step"] = born
    if "profile_status" in line and line["profile_status"] not in PROFILE_STATUSES:
        line.pop("profile_status")
    if "status" in line and line["status"] not in STATUSES:
        line.pop("status")
    if "merged_into" in line:
        target = str(line["merged_into"] or "").strip()
        if target:
            line["merged_into"] = target
        else:
            line.pop("merged_into")
    if "tier_pin" in line and line["tier_pin"] not in TIER_PINS:
        line.pop("tier_pin")
    if "var_refs" in line:
        refs = _str_list(line["var_refs"])
        if refs:
            line["var_refs"] = refs
        else:
            line.pop("var_refs")
    if "lifecycle" in line and not isinstance(line["lifecycle"], dict):
        line.pop("lifecycle")
    return line


# ── 注册表读取 / 解析 ────────────────────────────────────────────────


def get_lines(settings: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`settings.causal_lines` 里 id 合法的条目（不拷贝，调用方不要改）。重复 id 保留第一个。"""
    seen: set = set()
    result: List[Dict[str, Any]] = []
    for line in (settings or {}).get("causal_lines") or []:
        if not isinstance(line, dict):
            continue
        lid = str(line.get("id") or "").strip()
        if not lid or lid in seen:
            continue
        seen.add(lid)
        result.append(line)
    return result


def line_kind(line: Dict[str, Any]) -> str:
    """`domain` / `element`；缺省（旧式独立线）返回 `legacy`。"""
    kind = line.get("kind")
    return kind if kind in KINDS else "legacy"


def is_alive(line: Dict[str, Any]) -> bool:
    """`status` 缺省视为 alive。"""
    return line.get("status", "alive") not in ("retired", "merged")


def resolve(lines_or_settings: Any, ref: Any) -> Optional[Dict[str, Any]]:
    """把一个引用（id / label / 别名）解析到已登记的线；解析不到返回 None。

    优先级：① 精确 id；② 规范化后的 id；③ 规范化后的 label / aliases（按线的登记顺序，
    先到先得）。只做精确的规范化匹配，不做模糊匹配。接受 `settings` 或线列表。
    """
    lines = get_lines(lines_or_settings) if isinstance(lines_or_settings, dict) else [
        x for x in (lines_or_settings or []) if isinstance(x, dict) and str(x.get("id") or "").strip()
    ]
    text = str(ref if ref is not None else "").strip()
    if not text:
        return None
    for line in lines:
        if str(line.get("id")).strip() == text:
            return line
    key = norm_key(text)
    if not key:
        return None
    for line in lines:
        if norm_key(line.get("id")) == key:
            return line
    for line in lines:
        names = [line.get("label")] + list(line.get("aliases") or [])
        if any(norm_key(n) == key for n in names if n is not None and str(n).strip()):
            return line
    return None


def domains(lines: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [x for x in lines if isinstance(x, dict) and x.get("kind") == "domain"]


def children_of(lines: Iterable[Dict[str, Any]], domain_id: str) -> List[Dict[str, Any]]:
    """某领域线下的元素线（`parent == domain_id`，按登记顺序）。"""
    did = str(domain_id or "").strip()
    return [
        x for x in lines
        if isinstance(x, dict) and x.get("kind") != "domain" and str(x.get("parent") or "").strip() == did
    ]


def group_by_domain(
    lines: Iterable[Dict[str, Any]],
) -> List[Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]]:
    """展示用分组：`[(领域线, [子元素...]), ..., (None, [未归类线...])]`。

    - 每个领域线一组，组内是 `parent` 指向它的元素；
    - `parent` 缺省 / 指向不存在的领域的线，以及旧式独立线，归入末尾的 `None` 组（"未归类"，
      不猜领域）；
    - 没有任何领域线时返回单个 `(None, 全部线)` —— 与现在的平铺展示等价；
    - 保持各自的登记顺序。
    """
    all_lines = [x for x in lines if isinstance(x, dict) and str(x.get("id") or "").strip()]
    dom = domains(all_lines)
    if not dom:
        return [(None, all_lines)] if all_lines else []
    dom_ids = {str(d["id"]).strip() for d in dom}
    groups: List[Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]] = [
        (d, children_of(all_lines, str(d["id"]))) for d in dom
    ]
    loose = [
        x for x in all_lines
        if x.get("kind") != "domain" and str(x.get("parent") or "").strip() not in dom_ids
    ]
    if loose:
        groups.append((None, loose))
    return groups


# ── 技术节点 ↔ lifecycle ─────────────────────────────────────────────


def node_to_lifecycle(node: Dict[str, Any]) -> Dict[str, Any]:
    """技术节点 dict（`tech_model.normalize_node()` 的输出）→ `lifecycle` 子对象。
    `id`/`name` 不存（对应线的 id/label）；节点原 `kind` 改名 `sub_kind`。深拷贝。"""
    lifecycle = {k: copy.deepcopy(v) for k, v in node.items() if k not in _NODE_ONLY_TOP}
    lifecycle["sub_kind"] = str(node.get("kind") or "")
    return lifecycle


def line_to_node_raw(line: Dict[str, Any]) -> Dict[str, Any]:
    """带 `lifecycle` 的线 → 原始节点 dict（交给 `tech_model.normalize_node()` 规整）。"""
    lifecycle = copy.deepcopy(line.get("lifecycle") or {})
    raw = {k: v for k, v in lifecycle.items() if k != "sub_kind"}
    raw["id"] = str(line.get("id")).strip()
    raw["name"] = str(line.get("label") or "").strip()
    raw["kind"] = str(lifecycle.get("sub_kind") or "")
    return raw


def lines_with_lifecycle(settings: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [x for x in get_lines(settings) if isinstance(x.get("lifecycle"), dict)]


def has_lifecycle(settings: Optional[Dict[str, Any]]) -> bool:
    return bool(lines_with_lifecycle(settings))


def snapshot_has_lifecycle(snapshot: Optional[Dict[str, Any]]) -> bool:
    """动态快照（`SimState.dynamic_snapshot`）里是否有带 `lifecycle` 的线。"""
    if not isinstance(snapshot, dict):
        return False
    return has_lifecycle({"causal_lines": snapshot.get("causal_lines")})


def _legacy_nodes_raw(settings: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    state = (settings or {}).get(_TECH_STATE_FLAG)
    raw_nodes = state.get("nodes") if isinstance(state, dict) else None
    return [x for x in raw_nodes or [] if isinstance(x, dict)]


def read_tech_nodes_raw(settings: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """元素模式下读技术节点（原始 dict，未规整）：线上的 `lifecycle` 优先；万一还有
    没折叠完的旧 `tech_state` 节点（id 不重复的部分）也并入，保证任何读路径都不丢数据。
    只读，不改 settings。"""
    raw = [line_to_node_raw(x) for x in lines_with_lifecycle(settings)]
    have = {str(r["id"]) for r in raw}
    for legacy in _legacy_nodes_raw(settings):
        lid = str(legacy.get("id") or "").strip() or str(legacy.get("name") or "").strip()
        if lid and lid not in have:
            raw.append(legacy)
            have.add(lid)
    return raw


# ── 新建元素线（技术类） ─────────────────────────────────────────────


def new_tech_line(node: Dict[str, Any], *, origin: str, step: int) -> Dict[str, Any]:
    """为一个技术节点建一条最小元素线。带默认未来树（"因果线一定有未来树"这个既有承诺
    不因为是技术元素就降级）。`parent` 不猜，留空 = 未归类。

    `profile_status`：创建期（`origin=seed`/`legacy_tech`）视为 `complete`；推进中新登记
    （`discovered`）标 `pending_enrichment`，等 E3 的补全流程。"""
    label = str(node.get("name") or node.get("id") or "").strip()
    line: Dict[str, Any] = {
        "id": str(node["id"]).strip(),
        "label": label,
        "time_granularity": "",
        "kind": "element",
        "element_type": TECHNOLOGY_TYPE,
        "origin": origin,
        "born_step": max(0, int(step)),
        "profile_status": "pending_enrichment" if origin == "discovered" else "complete",
        "future_tree": causal_tree.build_default_future_tree(label, max(0, int(step))),
        "lifecycle": node_to_lifecycle(node),
    }
    if origin == "discovered":
        line["auto_discovered"] = True
    return line


def write_tech_nodes(settings: Dict[str, Any], nodes: List[Dict[str, Any]]) -> None:
    """元素模式下把规整后的技术节点列表写回 `settings.causal_lines[].lifecycle`（就地改
    `settings`；`causal_lines` 用新列表、新线 dict 替换，不改动传入的旧对象）。

    - 已有同 id 线（精确匹配）→ 更新其 `lifecycle`；技术元素线同步 `label`=节点名；
      旧式无 `kind` 的线补 `element_type`（"是同一个东西就合并"，方案 §4.2）；
    - 没有同 id 线 → 新建一条技术元素线（`origin`：创建期节点 `seed`，推进中登记 `discovered`）；
    - 线上有 `lifecycle` 但不在 `nodes` 里 → 去掉该线的 `lifecycle`（线本身保留）；
    - 写完后移除旧 `settings.tech_state`（所有节点此时都在线上了）。
    """
    if settings.get(_TECH_STATE_FLAG):
        fold_legacy_tech_state(settings)
    lines = [dict(x) for x in (settings.get("causal_lines") or []) if isinstance(x, dict)]
    by_id: Dict[str, Dict[str, Any]] = {}
    for line in lines:
        lid = str(line.get("id") or "").strip()
        if lid and lid not in by_id:
            by_id[lid] = line
    written: set = set()
    for node in nodes:
        nid = str(node["id"]).strip()
        written.add(nid)
        line = by_id.get(nid)
        if line is None:
            created = int(node.get("created_step") or 0)
            line = new_tech_line(node, origin="seed" if created <= 0 else "discovered", step=created)
            lines.append(line)
            by_id[nid] = line
            continue
        was_tech = line.get("element_type") == TECHNOLOGY_TYPE
        had_lifecycle = isinstance(line.get("lifecycle"), dict)
        line["lifecycle"] = node_to_lifecycle(node)
        if was_tech and node.get("name"):
            line["label"] = str(node["name"])  # 技术元素线：label 跟随节点名
        if not was_tech and not had_lifecycle and line.get("kind") != "domain":
            # 旧式同 id 线被并入：补类型，但不覆盖用户给它起的 label
            line.setdefault("element_type", TECHNOLOGY_TYPE)
    for lid, line in by_id.items():
        if lid not in written and "lifecycle" in line:
            line.pop("lifecycle")
    if lines or "causal_lines" in settings:
        settings["causal_lines"] = lines
    settings.pop(_TECH_STATE_FLAG, None)


def fold_legacy_tech_state(settings: Dict[str, Any]) -> bool:
    """把旧 `settings.tech_state.nodes` 折叠成带 `lifecycle` 的元素线（`origin=legacy_tech`），
    然后移除 `tech_state`。**幂等**：没有旧 `tech_state` 时什么都不做，返回 False。就地改
    `settings`。调用方负责先判断 `is_enabled(settings)`。

    id 冲突（方案 §4.2）：
    - 旧节点 id 与已有**非领域**线相同 → 视为同一个东西，把 `lifecycle` 挂到该线上；
    - 与已有**领域**线相同 → 节点改名 `<id>_tech`（重名再加序号），旧 id 写入 `aliases`，
      其它节点 `requires[].tech_id` 里对旧 id 的引用同步改写。
    """
    from world_simulator import tech_model  # 延迟导入：避免与 tech_model 循环依赖

    legacy_raw = _legacy_nodes_raw(settings)
    if not legacy_raw and _TECH_STATE_FLAG not in settings:
        return False
    nodes: List[Dict[str, Any]] = []
    seen: set = set()
    for raw in legacy_raw:
        node = tech_model.normalize_node(raw, step=0)
        if node is None or node["id"] in seen:
            continue
        seen.add(node["id"])
        nodes.append(node)

    lines = [dict(x) for x in (settings.get("causal_lines") or []) if isinstance(x, dict)]
    by_id = {str(x.get("id") or "").strip(): x for x in lines if str(x.get("id") or "").strip()}
    taken = set(by_id) | {n["id"] for n in nodes}

    renamed: Dict[str, str] = {}
    for node in nodes:
        existing = by_id.get(node["id"])
        if existing is not None and existing.get("kind") == "domain":
            base = f"{node['id']}_tech"
            new_id, n = base, 2
            while new_id in taken:
                new_id, n = f"{base}{n}", n + 1
            taken.add(new_id)
            renamed[node["id"]] = new_id
    if renamed:
        for node in nodes:
            for req in node.get("requires") or []:
                if req.get("tech_id") in renamed:
                    req["tech_id"] = renamed[req["tech_id"]]

    for node in nodes:
        old_id = node["id"]
        if old_id in renamed:
            node = dict(node)
            node["id"] = renamed[old_id]
            line = new_tech_line(node, origin="legacy_tech", step=int(node.get("created_step") or 0))
            line["aliases"] = [old_id]
            lines.append(line)
            by_id[node["id"]] = line
            continue
        existing = by_id.get(old_id)
        if existing is not None:
            existing["lifecycle"] = node_to_lifecycle(node)
            existing.setdefault("element_type", TECHNOLOGY_TYPE)
            continue
        line = new_tech_line(node, origin="legacy_tech", step=int(node.get("created_step") or 0))
        lines.append(line)
        by_id[old_id] = line

    if lines or "causal_lines" in settings:
        settings["causal_lines"] = lines
    settings.pop(_TECH_STATE_FLAG, None)
    return True


# ── 预算参数（第二十三轮 E2；其余键由 E4 追加） ──────────────────────

DEFAULT_PARAMS: Dict[str, Optional[int]] = {
    "create_max_elements": 20,     # 创建时元素总数上限（不含领域线）；None = 不限
    "create_max_per_domain": 6,    # 创建时每个领域下的元素上限；None = 不限
}


def _valid_limit(value: Any) -> Tuple[bool, Optional[int]]:
    """`(是否合法, 规整值)`。`None` = 不限（合法）；非负整数合法；其余（负数、非整数、布尔、文本）非法。
    0 是合法的"0 个"，不表示不限（避免混淆，方案 §5.1）。"""
    if value is None:
        return True, None
    if isinstance(value, bool):
        return False, None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and value >= 0:
        return True, value
    return False, None


def get_params(settings: Optional[Dict[str, Any]]) -> Dict[str, Optional[int]]:
    """`DEFAULT_PARAMS` 叠加 `settings.element_params`；非法值回退默认。"""
    params = dict(DEFAULT_PARAMS)
    override = (settings or {}).get("element_params")
    if isinstance(override, dict):
        for key in DEFAULT_PARAMS:
            if key in override:
                ok, val = _valid_limit(override[key])
                if ok:
                    params[key] = val
    return params


def invalid_param_keys(settings: Optional[Dict[str, Any]]) -> List[str]:
    """`element_params` 里取值非法（已回退默认）的 key，给设置页提示。"""
    override = (settings or {}).get("element_params")
    if not isinstance(override, dict):
        return []
    return [k for k in DEFAULT_PARAMS if k in override and not _valid_limit(override[k])[0]]


def creation_enabled(settings: Optional[Dict[str, Any]]) -> bool:
    """创建阶段是否走"领域→元素"展开。创建时 manifest 还没写开关，所以**缺省视为开启**
    （新实例默认开，方案 §9.1）；只有显式 False 才关。"""
    return (settings or {}).get("element_modeling_enabled", True) is not False


# ── 创建阶段：规整 + 预算裁剪 ────────────────────────────────────────


def clip_to_budget(
    lines: List[Dict[str, Any]],
    edges: Optional[List[Dict[str, Any]]],
    params: Dict[str, Optional[int]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """按创建预算裁剪元素，返回 `(保留, 被裁掉的候选)`，各自保持原顺序。

    - 领域线不计数、永不裁；其余线都算"元素"（含旧式无 `kind` 的线）。
    - 总数上限 `create_max_elements`、每领域上限 `create_max_per_domain`（`None` = 不限）。
    - 优先级：先验边端点 > `technology` 类型 > LLM 给的顺序。被裁掉的**不丢**，作为候选返回，
      由向导展示让用户手动加回。
    - `declared_causal_graph` 的边不随裁剪过滤（用户加回候选后边仍然有效）。
    """
    endpoints: set = set()
    for edge in edges or []:
        if isinstance(edge, dict):
            for key in ("from_line_id", "to_line_id"):
                if str(edge.get(key) or "").strip():
                    endpoints.add(str(edge[key]).strip())
    dom_ids = {str(x["id"]).strip() for x in lines if x.get("kind") == "domain"}
    ranked = sorted(
        (i for i, x in enumerate(lines) if x.get("kind") != "domain"),
        key=lambda i: (
            0 if str(lines[i]["id"]).strip() in endpoints else 1,
            0 if lines[i].get("element_type") == TECHNOLOGY_TYPE else 1,
            i,
        ),
    )
    total_cap = params.get("create_max_elements")
    dom_cap = params.get("create_max_per_domain")
    kept_idx: set = set()
    per_domain: Dict[str, int] = {}
    for i in ranked:
        parent = str(lines[i].get("parent") or "").strip()
        in_domain = parent in dom_ids
        if total_cap is not None and len(kept_idx) >= total_cap:
            continue
        if in_domain and dom_cap is not None and per_domain.get(parent, 0) >= dom_cap:
            continue
        kept_idx.add(i)
        if in_domain:
            per_domain[parent] = per_domain.get(parent, 0) + 1
    kept = [x for i, x in enumerate(lines) if x.get("kind") == "domain" or i in kept_idx]
    cut = [x for i, x in enumerate(lines) if x.get("kind") != "domain" and i not in kept_idx]
    return kept, cut


def prepare_created_lines(
    raw_lines: Optional[List[Any]],
    edges: Optional[List[Dict[str, Any]]],
    settings: Optional[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """创建草稿里的 `causal_lines` → `(落盘用的线, 被预算裁掉的候选)`。

    规整元素字段；按规范化 id/label/别名去重（先到先得，不模糊）；`parent` 能解析到领域线
    就改写成领域 id，解析不到就清空（未归类，不猜）；有 `parent` 无 `kind` 视为元素；盖创建期
    戳（`origin=seed`/`born_step=0`/`profile_status=complete`，只补缺省）；技术元素的
    `lifecycle_seed` 经 `tech_model.normalize_node` 落成 `lifecycle`；最后按预算裁剪。
    只处理 dict 条目，其余忽略。
    """
    from world_simulator import tech_model  # 延迟导入，避免循环依赖

    normalized: List[Dict[str, Any]] = []
    for raw in raw_lines or []:
        if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
            continue
        line = normalize_element(raw)
        line["id"] = str(line["id"]).strip()
        # 重复判定：新线的 id 或任一别名命中已有线的 id/label/别名 → 先到先得。只看 id 和别名、
        # 不看新线自己的 label（两个不同元素碰巧同名比误合并更常见，宁可漏合并）。
        if any(resolve(normalized, ref) is not None for ref in [line["id"], *(line.get("aliases") or [])]):
            continue
        normalized.append(line)

    domain_lines = [x for x in normalized if x.get("kind") == "domain"]
    for line in normalized:
        if line.get("kind") == "domain":
            line.pop("parent", None)
            continue
        parent = line.get("parent")
        if parent:
            target = resolve(domain_lines, parent)
            if target is not None:
                line["parent"] = str(target["id"]).strip()
                line.setdefault("kind", "element")
            else:
                line.pop("parent")
        seed = line.pop("lifecycle_seed", None)
        if isinstance(seed, dict) and not isinstance(line.get("lifecycle"), dict):
            node = tech_model.normalize_node(
                {**seed, "id": line["id"], "name": str(line.get("label") or line["id"])}, step=0
            )
            if node is not None:
                line["lifecycle"] = node_to_lifecycle(node)
                line.setdefault("element_type", TECHNOLOGY_TYPE)
    for line in normalized:
        if line.get("kind") in KINDS:
            line.setdefault("origin", "seed")
            line.setdefault("born_step", 0)
            line.setdefault("profile_status", "complete")
    return clip_to_budget(normalized, edges, get_params(settings))
