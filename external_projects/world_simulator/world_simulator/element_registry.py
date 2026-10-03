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
    # 第二十三轮 E5：元素运维留下的链路字段（拆分来源/去向、合并来源、退场信息）。
    for key in ("split_from", "retire_reason"):
        if key in line:
            text = str(line[key] or "").strip()
            if text:
                line[key] = text
            else:
                line.pop(key)
    for key in ("split_into", "merged_from"):
        if key in line:
            ids = [a for a in _str_list(line[key]) if a != str(line.get("id") or "").strip()]
            if ids:
                line[key] = ids
            else:
                line.pop(key)
    if "retired_step" in line:
        rstep = _int_or_none(line["retired_step"])
        if rstep is None or rstep < 0:
            line.pop("retired_step")
        else:
            line["retired_step"] = rstep
    if "var_refs" in line:
        refs = _str_list(line["var_refs"])
        if refs:
            line["var_refs"] = refs
        else:
            line.pop("var_refs")
    if "relations" in line:
        rels = _norm_relations(line["relations"])
        if rels:
            line["relations"] = rels
        else:
            line.pop("relations")
    if "lifecycle" in line and not isinstance(line["lifecycle"], dict):
        line.pop("lifecycle")
    return line


_DIRECTION_ALIASES = {
    "affects": "affects", "->": "affects", "to": "affects", "influences": "affects",
    "affected_by": "affected_by", "<-": "affected_by", "from": "affected_by", "influenced_by": "affected_by",
}
_SIGNS = ("positive", "negative", "mixed")


def _norm_relations(raw: Any) -> List[Dict[str, Any]]:
    """元素的关系声明 `[{with, direction, sign?, note?}]`：`with` 非空、`direction` 能识别才保留
    （方向是引擎把它翻成因果边的唯一依据，认不出来就丢，不猜）；同 `(with, direction)` 去重。
    `sign` 只接受 positive/negative/mixed（经 `causal_engine` 同一套别名），其余丢掉该字段。"""
    from world_simulator import causal_engine  # 函数内延迟导入：causal_engine 反过来依赖本模块

    out: List[Dict[str, Any]] = []
    seen: set = set()
    for item in raw if isinstance(raw, (list, tuple)) else []:
        if not isinstance(item, dict):
            continue
        target = str(item.get("with") or "").strip()
        direction = _DIRECTION_ALIASES.get(str(item.get("direction") or "").strip().lower())
        if not target or direction is None or (target, direction) in seen:
            continue
        seen.add((target, direction))
        rel: Dict[str, Any] = {"with": target, "direction": direction}
        sign = causal_engine._SIGN_ALIASES.get(str(item.get("sign") or "").strip().lower())
        if sign in _SIGNS:
            rel["sign"] = sign
        note = str(item.get("note") or "").strip()
        if note:
            rel["note"] = note
        out.append(rel)
    return out


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


def resolve(lines_or_settings: Any, ref: Any, *, follow_merged: bool = True) -> Optional[Dict[str, Any]]:
    """把一个引用（id / label / 别名）解析到已登记的线；解析不到返回 None。

    优先级：① 精确 id；② 规范化后的 id；③ 规范化后的 label / aliases（按线的登记顺序，
    先到先得）。只做精确的规范化匹配，不做模糊匹配。接受 `settings` 或线列表。

    第二十三轮 E5：命中的线若已被合并（`status=merged` + `merged_into`），默认**跟随到合并目标**
    （`follow_merged=True`）——历史里的旧 id、别名视图层都经这里落到存活的那条线上（历史不可变，
    只在读取时改写）。元素运维自己要找\"被合并的那条线本身\"时传 `follow_merged=False`。
    """
    lines = get_lines(lines_or_settings) if isinstance(lines_or_settings, dict) else [
        x for x in (lines_or_settings or []) if isinstance(x, dict) and str(x.get("id") or "").strip()
    ]
    found = _lookup(lines, ref)
    if found is not None and follow_merged:
        found = follow_merge(lines, found)
    return found


def _lookup(lines: List[Dict[str, Any]], ref: Any) -> Optional[Dict[str, Any]]:
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


MERGE_FOLLOW_MAX = 16


def follow_merge(lines: List[Dict[str, Any]], line: Dict[str, Any]) -> Dict[str, Any]:
    """沿 `merged_into` 链走到存活的那条线（有环/断链/超过 16 跳时停在最后一个能走到的线上，不报错）。"""
    seen = {str(line.get("id") or "").strip()}
    cur = line
    for _ in range(MERGE_FOLLOW_MAX):
        if cur.get("status") != "merged":
            break
        target_id = str(cur.get("merged_into") or "").strip()
        if not target_id or target_id in seen:
            break
        nxt = next((x for x in lines if str(x.get("id") or "").strip() == target_id), None)
        if nxt is None:
            break
        seen.add(target_id)
        cur = nxt
    return cur


def redirect_merged(lines_or_settings: Any, ref: Any) -> str:
    """**只按精确 id** 把被合并元素的 id 重定向到合并目标的 id；其它情况（存活元素、解析不到、只靠别名/名称
    才能命中）原样返回。给因果边端点这类\"读取时改写\"的地方用——不改变任何非合并引用的既有行为。"""
    text = str(ref if ref is not None else "").strip()
    if not text:
        return text
    lines = get_lines(lines_or_settings) if isinstance(lines_or_settings, dict) else [
        x for x in (lines_or_settings or []) if isinstance(x, dict) and str(x.get("id") or "").strip()
    ]
    line = next((x for x in lines if str(x.get("id")).strip() == text), None)
    if line is None or line.get("status") != "merged":
        return text
    return str(follow_merge(lines, line).get("id") or text).strip()


def retired_ids(settings: Optional[Dict[str, Any]]) -> set:
    """已退场（`status=retired`）元素的 id 集合；元素模式未开启返回空集合（旧实例不受影响）。"""
    if not is_enabled(settings):
        return set()
    return {str(x["id"]).strip() for x in get_lines(settings) if x.get("status") == "retired"}


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


# ═════════════════════════════════════════════════════════════════════
# 第二十三轮 E3：推进阶段的发现 / 去重 / 登记 / 补全 / 引用即登记
#
# 设计依据：`next_doc/world_simulator_element_causal_lines_plan.md` §4.4、§5.1、§6 E3。
# 纯 Python、不调 LLM。入口是 `process_step()`（由 `engine/causal_lines.py` 在
# `_apply_tree_updates` 之前调用）与 `build_hint()`（拼进下一步 prompt 的 `{element_hint}`）。
# ═════════════════════════════════════════════════════════════════════

# 运行期参数。与 E2 的创建期参数（`DEFAULT_PARAMS`/`get_params`）分开存放：E2 已有测试把
# `get_params(None)` 钉死为两个键，所以 E3 的键不并进去；它们同样写在 `settings.element_params`
# 里（E4 追加分级参数时再统一）。
RUNTIME_DEFAULT_PARAMS: Dict[str, Optional[int]] = {
    "max_total_elements": None,       # 运行中元素总数上限；None = 不限（方案 §9.2 第 3 条）
    "candidate_promote_mentions": 2,  # 候选池转正所需被提次数（跨步骤，同一步多次只算一次），>= 1
    "enrich_grace_steps": 2,          # 补全宽限步数，>= 0；0 = 不向 LLM 索要补全，登记当步即按兜底
}
CANDIDATE_POOL_MAX = 50   # 候选池条数上限（防无限增长）；超出时丢"提次最少、最久没被提到"的
INDEX_MAX = 60            # prompt 里已登记元素索引的条数上限（不传 history 的旧调用方；E4 起引擎路径由分级/休眠索引接管）


def _valid_int_at_least(value: Any, minimum: int) -> Tuple[bool, Optional[int]]:
    if value is None or isinstance(value, bool):
        return False, None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and value >= minimum:
        return True, value
    return False, None


_RUNTIME_VALIDATORS = {
    "max_total_elements": _valid_limit,
    "candidate_promote_mentions": lambda v: _valid_int_at_least(v, 1),
    "enrich_grace_steps": lambda v: _valid_int_at_least(v, 0),
}


def get_runtime_params(settings: Optional[Dict[str, Any]]) -> Dict[str, Optional[int]]:
    """`RUNTIME_DEFAULT_PARAMS` 叠加 `settings.element_params`；非法值回退默认。"""
    params = dict(RUNTIME_DEFAULT_PARAMS)
    override = (settings or {}).get("element_params")
    if isinstance(override, dict):
        for key, check in _RUNTIME_VALIDATORS.items():
            if key in override:
                ok, val = check(override[key])
                if ok:
                    params[key] = val
    return params


def invalid_runtime_param_keys(settings: Optional[Dict[str, Any]]) -> List[str]:
    """`element_params` 里 E3 运行期参数取值非法（已回退默认）的 key，给设置页提示。"""
    override = (settings or {}).get("element_params")
    if not isinstance(override, dict):
        return []
    return [k for k, check in _RUNTIME_VALIDATORS.items() if k in override and not check(override[k])[0]]


# ── 候选池 ───────────────────────────────────────────────────────────


def get_candidates(settings: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`settings.element_candidates`（分支作用域）里 id 合法的条目。"""
    return [
        x for x in (settings or {}).get("element_candidates") or []
        if isinstance(x, dict) and str(x.get("id") or "").strip()
    ]


def _names_of(entry: Dict[str, Any]) -> List[str]:
    return [str(entry.get("id") or ""), str(entry.get("label") or ""), *[str(a) for a in entry.get("aliases") or []]]


def _find_by_names(entries: List[Dict[str, Any]], refs: Iterable[str], *, skip_blank_label: bool = True) -> Optional[int]:
    """在 `entries` 里找第一个 id/label/别名与 `refs` 任一规范化后相等的条目下标。"""
    keys = {norm_key(r) for r in refs if str(r or "").strip()}
    keys.discard("")
    if not keys:
        return None
    for i, entry in enumerate(entries):
        if any(norm_key(n) in keys for n in _names_of(entry) if str(n or "").strip()):
            return i
    return None


# ── 条目规整（`discovered_elements` / `element_enrichments` 的单项） ──


def _norm_item(raw: Any) -> Optional[Dict[str, Any]]:
    """LLM 给的一项发现/补全 → 内部统一形状；没有 id 也没有 label 的丢弃。无 id 有 label 时用 label
    （空白换成下划线）当 id。不认识的字段忽略，不报错。"""
    if not isinstance(raw, dict):
        return None
    eid = str(raw.get("id") or "").strip()
    label = str(raw.get("label") or raw.get("name") or "").strip()
    if not eid and label:
        eid = re.sub(r"\s+", "_", label)
    if not eid:
        return None
    tree = raw.get("future_tree")
    seed = raw.get("lifecycle_seed")
    return {
        "id": eid,
        "label": label,
        "element_type": str(raw.get("element_type") or "").strip(),
        "parent": str(raw.get("parent") or "").strip(),
        "aliases": _str_list(raw.get("aliases")),
        "why_key": str(raw.get("why_key") or "").strip(),
        "relations": _norm_relations(raw.get("relations")),
        "future_tree": tree if isinstance(tree, dict) else None,
        "lifecycle_seed": seed if isinstance(seed, dict) and seed else None,
    }


def _union_aliases(existing: List[str], extra: Iterable[str], exclude_keys: Iterable[str]) -> List[str]:
    """别名并集。**已有别名原样保留**（只按规范化键去重，不因为与 id/label 规范化后相等而删掉——
    那是用户/LLM 已经写下的东西）；新增项按规范化键去重，并排除 `exclude_keys`（该元素自己的 id/label）。"""
    seen: set = set()
    out: List[str] = []
    for text in existing:
        text = str(text or "").strip()
        key = norm_key(text)
        if text and key and key not in seen:
            seen.add(key)
            out.append(text)
    blocked = {k for k in exclude_keys if k}
    for text in extra:
        text = str(text or "").strip()
        key = norm_key(text)
        if text and key and key not in seen and key not in blocked:
            seen.add(key)
            out.append(text)
    return out


def _merge_relations(existing: List[Dict[str, Any]], extra: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = list(existing)
    seen = {(norm_key(r.get("with")), r.get("direction")) for r in out}
    for rel in extra:
        key = (norm_key(rel.get("with")), rel.get("direction"))
        if key not in seen:
            seen.add(key)
            out.append(rel)
    return out


# ── 工作上下文 ───────────────────────────────────────────────────────


class _Ctx:
    """一步里所有登记动作共用的可变工作区。`lines`/`cands` 是新建的列表（里面的 dict 在被改之前
    先拷贝），`process_step` 结束时才一次性写回 `settings`——中途出错不留半截状态。"""

    def __init__(self, settings: Dict[str, Any], step: int) -> None:
        self.settings = settings
        self.step = int(step)
        self.params = get_runtime_params(settings)
        self.lines: List[Dict[str, Any]] = [dict(x) for x in (settings.get("causal_lines") or []) if isinstance(x, dict)]
        self.cands: List[Dict[str, Any]] = [dict(x) for x in get_candidates(settings)]
        self.audit: List[Dict[str, Any]] = []
        self.tech_proposals: List[Dict[str, Any]] = []
        self._alias_logged: set = set()

    # 注册表视图 --------------------------------------------------------
    def alive(self) -> List[Dict[str, Any]]:
        return [x for x in self.lines if str(x.get("id") or "").strip() and is_alive(x)]

    def alive_domains(self) -> List[Dict[str, Any]]:
        return [x for x in self.alive() if x.get("kind") == "domain"]

    def element_count(self) -> int:
        return sum(1 for x in self.alive() if x.get("kind") != "domain")

    def budget_ok(self) -> bool:
        cap = self.params.get("max_total_elements")
        return cap is None or self.element_count() < cap

    def resolve(self, ref: Any) -> Optional[Dict[str, Any]]:
        return resolve(self.lines, ref)

    def index_of(self, line_id: str) -> int:
        for i, x in enumerate(self.lines):
            if str(x.get("id") or "").strip() == line_id:
                return i
        return -1

    def log(self, action: str, element_id: str = "", **detail: Any) -> None:
        entry: Dict[str, Any] = {"action": action}
        if element_id:
            entry["element_id"] = element_id
        entry.update({k: v for k, v in detail.items() if v not in (None, "", [], {})})
        self.audit.append(entry)

    def resolve_parent(self, parent: str) -> Optional[str]:
        if not str(parent or "").strip():
            return None
        target = resolve(self.alive_domains(), parent)
        return str(target["id"]).strip() if target is not None else None


def _is_default_tree(tree: Any) -> bool:
    """树仍是引擎的通用兜底模板（分支 id 恰为模板那几个、且都没动过）。"""
    if not isinstance(tree, dict):
        return True
    default_ids = {b["id"] for b in causal_tree.build_default_future_tree("", 0)["branches"]}
    branches = [b for b in tree.get("branches") or [] if isinstance(b, dict)]
    if not branches:
        return True
    return {b.get("id") for b in branches} == default_ids and all(
        str(b.get("status") or "dormant") == "dormant" for b in branches
    )


def _seed_tree(raw: Optional[Dict[str, Any]], step: int) -> Optional[Dict[str, Any]]:
    """LLM 给的种子树规整；没有合法分支返回 None。分支没标 `first_seen_step` 的补上当前步。"""
    if raw is None:
        return None
    tree = causal_tree.normalize_future_tree(raw, as_of_step=step)
    if tree is None:
        return None
    for branch in tree["branches"]:
        if branch.get("first_seen_step") is None:
            branch["first_seen_step"] = step
    return tree


def _is_complete(line: Dict[str, Any], has_domains: bool) -> bool:
    """补全是否到位：有类型，且（有归属领域，或本实例根本没有领域线）。label 不作为判据——LLM 给不给
    label 都不应该让一个元素永远卡在\"待补全\"。"""
    return bool(str(line.get("element_type") or "").strip()) and (bool(line.get("parent")) or not has_domains)


def _missing_fields(line: Dict[str, Any], has_domains: bool) -> List[str]:
    missing: List[str] = []
    if not str(line.get("element_type") or "").strip():
        missing.append("element_type")
    if has_domains and not line.get("parent"):
        missing.append("parent")
    return missing


def _resolve_relations(ctx: _Ctx, rels: List[Dict[str, Any]], self_id: str) -> List[Dict[str, Any]]:
    """把 `with` 解析到已登记的 alive 线（领域线也算），改写成它的规范 id；解析不到/指向自己的丢掉。"""
    out: List[Dict[str, Any]] = []
    alive_ids = {str(x["id"]).strip() for x in ctx.alive()}
    for rel in rels:
        target = ctx.resolve(rel.get("with"))
        if target is None:
            continue
        tid = str(target["id"]).strip()
        if tid == self_id or tid not in alive_ids:
            continue
        out.append({**rel, "with": tid})
    return out


def _want_lifecycle(ctx: _Ctx, line: Dict[str, Any], item: Dict[str, Any]) -> None:
    """技术类元素带 `lifecycle_seed` 时，转成一条合成的 `tech_updates` 登记提议，交给同一套技术裁决
    （T5 阶段夹值、`preexisting` 声明等规则只有一个出口）。技术模型没开则只记审计，不生效。"""
    seed = item.get("lifecycle_seed")
    if not seed or line.get("element_type") != TECHNOLOGY_TYPE or isinstance(line.get("lifecycle"), dict):
        return
    from world_simulator import tech_model  # 延迟导入

    lid = str(line["id"]).strip()
    if not tech_model.is_enabled(ctx.settings):
        ctx.log("lifecycle_seed_ignored", lid, reason="技术生命周期规则未开启（tech_model_enabled）")
        return
    proposal = {k: v for k, v in seed.items() if k not in ("id", "tech_id", "name")}
    proposal["id"] = lid
    proposal["name"] = str(line.get("label") or lid)
    ctx.tech_proposals.append(proposal)
    ctx.log("lifecycle_seed_queued", lid)


# ── 建线 / 并入 / 补全 ───────────────────────────────────────────────


def _build_line(ctx: _Ctx, item: Dict[str, Any], *, origin: str = "discovered") -> Dict[str, Any]:
    label = item["label"] or item["id"]
    line: Dict[str, Any] = {
        "id": item["id"], "label": label, "time_granularity": "", "kind": "element",
        "origin": origin, "born_step": max(0, ctx.step), "auto_discovered": True,
    }
    if item["element_type"]:
        line["element_type"] = item["element_type"]
    parent = ctx.resolve_parent(item["parent"])
    if parent:
        line["parent"] = parent
    aliases = _union_aliases([], item["aliases"], [norm_key(item["id"]), norm_key(label)])
    if aliases:
        line["aliases"] = aliases
    rels = _resolve_relations(ctx, item["relations"], item["id"])
    if rels:
        line["relations"] = rels
    line["future_tree"] = _seed_tree(item["future_tree"], ctx.step) or causal_tree.build_default_future_tree(label, ctx.step)
    line["profile_status"] = "complete" if _is_complete(line, bool(ctx.alive_domains())) else "pending_enrichment"
    return line


def _enrich_line(ctx: _Ctx, line: Dict[str, Any], item: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """用 `item` 给一条**待补全/兜底**的线补缺：补缺不改写（类型/归属只在缺省时填；label 在待补全期以补全为准；
    树只在仍是通用兜底模板时替换）。返回 `(新线, 实际补了哪些字段)`。"""
    new = dict(line)
    changed: List[str] = []
    if item["label"] and item["label"] != new.get("label"):
        new["label"] = item["label"]
        changed.append("label")
    if item["element_type"] and not str(new.get("element_type") or "").strip():
        new["element_type"] = item["element_type"]
        changed.append("element_type")
    parent = ctx.resolve_parent(item["parent"])
    if parent and not new.get("parent") and parent != new["id"]:
        new["parent"] = parent
        changed.append("parent")
    aliases = _union_aliases(
        list(new.get("aliases") or []), [item["id"], *item["aliases"]],
        [norm_key(new["id"]), norm_key(new.get("label"))],
    )
    if aliases != list(new.get("aliases") or []):
        new["aliases"] = aliases
        changed.append("aliases")
    seeded = _seed_tree(item["future_tree"], ctx.step)
    if seeded is not None and _is_default_tree(new.get("future_tree")):
        new["future_tree"] = seeded
        changed.append("future_tree")
    rels = _merge_relations(list(new.get("relations") or []), _resolve_relations(ctx, item["relations"], new["id"]))
    if rels != list(new.get("relations") or []):
        new["relations"] = rels
        changed.append("relations")
    if _is_complete(new, bool(ctx.alive_domains())):
        if new.get("profile_status") != "complete":
            new["profile_status"] = "complete"
            changed.append("profile_status")
    return new, changed


def _follow_idx(ctx: _Ctx, idx: int) -> int:
    """`ctx.lines[idx]` 若已被合并，沿 `merged_into` 走到存活那条线的下标（找不到/有环就停在原地）。"""
    seen = {idx}
    for _ in range(MERGE_FOLLOW_MAX):
        line = ctx.lines[idx]
        if line.get("status") != "merged":
            break
        nxt = ctx.index_of(str(line.get("merged_into") or "").strip())
        if nxt < 0 or nxt in seen:
            break
        seen.add(nxt)
        idx = nxt
    return idx


def _merge_hit(ctx: _Ctx, idx: int, item: Dict[str, Any], source: str) -> None:
    """发现项命中了已登记元素（id/label/别名规范化后相等）：视为同一个——补别名、补关系；
    若它还在待补全/兜底，同时按补全处理。不新建线。"""
    line = ctx.lines[idx]
    lid = str(line["id"]).strip()
    if str(line.get("profile_status") or "complete") in ("pending_enrichment", "fallback"):
        new, changed = _enrich_line(ctx, line, item)
        if changed:
            ctx.lines[idx] = new
            ctx.log("enriched", lid, fields=changed, via="rediscovered")
        _want_lifecycle(ctx, ctx.lines[idx], item)
    else:
        new = dict(line)
        aliases = _union_aliases(
            list(new.get("aliases") or []), [item["id"], item["label"], *item["aliases"]],
            [norm_key(new["id"]), norm_key(new.get("label"))],
        )
        rels = _merge_relations(list(new.get("relations") or []), _resolve_relations(ctx, item["relations"], lid))
        added = [a for a in aliases if a not in list(new.get("aliases") or [])]
        if aliases != list(new.get("aliases") or []):
            new["aliases"] = aliases
        if rels != list(new.get("relations") or []):
            new["relations"] = rels
        if new != line:
            ctx.lines[idx] = new
        ctx.log("merged_alias", lid, matched=item["id"], added_aliases=added, source=source)


def _upsert_candidate(ctx: _Ctx, item: Dict[str, Any], *, reason: str) -> Dict[str, Any]:
    """候选池写入/更新。同一步多次提到只算一次提次（`mentions` 按不同步骤累计）。"""
    idx = _find_by_names(ctx.cands, [item["id"], item["label"], *item["aliases"]])
    if idx is None:
        cand: Dict[str, Any] = {
            "id": item["id"], "label": item["label"], "element_type": item["element_type"],
            "parent": item["parent"], "aliases": list(item["aliases"]), "why_key": item["why_key"],
            "relations": list(item["relations"]), "mentions": 1,
            "first_step": ctx.step, "last_step": ctx.step, "reason": reason,
        }
        if item["future_tree"] is not None:
            cand["future_tree"] = item["future_tree"]
        if item["lifecycle_seed"] is not None:
            cand["lifecycle_seed"] = item["lifecycle_seed"]
        ctx.cands.append(cand)
        if len(ctx.cands) > CANDIDATE_POOL_MAX:
            drop = min(range(len(ctx.cands) - 1), key=lambda i: (ctx.cands[i]["mentions"], ctx.cands[i]["last_step"]))
            ctx.cands.pop(drop)
        return cand
    cand = dict(ctx.cands[idx])
    if ctx.step > int(cand.get("last_step", -1)):
        cand["mentions"] = int(cand.get("mentions", 1)) + 1
        cand["last_step"] = ctx.step
    for key in ("label", "element_type", "parent", "why_key"):
        if item[key] and not cand.get(key):
            cand[key] = item[key]
    cand["aliases"] = _union_aliases(
        list(cand.get("aliases") or []), [item["id"], *item["aliases"]],
        [norm_key(cand["id"]), norm_key(cand.get("label"))],
    )
    cand["relations"] = _merge_relations(list(cand.get("relations") or []), item["relations"])
    if item["future_tree"] is not None and "future_tree" not in cand:
        cand["future_tree"] = item["future_tree"]
    if item["lifecycle_seed"] is not None and "lifecycle_seed" not in cand:
        cand["lifecycle_seed"] = item["lifecycle_seed"]
    cand["reason"] = reason
    ctx.cands[idx] = cand
    return cand


def _cand_to_item(cand: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": cand["id"], "label": str(cand.get("label") or ""), "element_type": str(cand.get("element_type") or ""),
        "parent": str(cand.get("parent") or ""), "aliases": list(cand.get("aliases") or []),
        "why_key": str(cand.get("why_key") or ""), "relations": list(cand.get("relations") or []),
        "future_tree": cand.get("future_tree") if isinstance(cand.get("future_tree"), dict) else None,
        "lifecycle_seed": cand.get("lifecycle_seed") if isinstance(cand.get("lifecycle_seed"), dict) else None,
    }


def _register(
    ctx: _Ctx, item: Dict[str, Any], *, via: str, cand_idx: Optional[int] = None, promoted: Optional[bool] = None
) -> Dict[str, Any]:
    line = _build_line(ctx, item)
    ctx.lines.append(line)
    if cand_idx is not None:
        ctx.cands.pop(cand_idx)
    ctx.log(
        "promoted" if (cand_idx is not None if promoted is None else promoted) else "registered", line["id"],
        via=via, profile_status=line["profile_status"], parent=line.get("parent"),
        element_type=line.get("element_type"), why_key=item.get("why_key"),
        relations=[f"{r['direction']}:{r['with']}" for r in line.get("relations") or []],
    )
    _want_lifecycle(ctx, line, item)
    return line


def _qualifies(ctx: _Ctx, item: Dict[str, Any], mentions: int) -> Tuple[bool, str]:
    """关键性门槛（方案 §4.4 ③）：关系能连到已登记的 alive 元素 → 通过；否则有 `why_key` 且被不同步骤
    提到够次数 → 通过；都不满足留在候选池。"""
    if _resolve_relations(ctx, item["relations"], item["id"]):
        return True, "relation"
    if item["why_key"] and mentions >= int(ctx.params["candidate_promote_mentions"] or 1):
        return True, "mentions"
    return False, ""


def _discover_one(ctx: _Ctx, item: Dict[str, Any], source: str = "discovered_elements", *, scan: bool = False) -> None:
    """`scan=True`（E5 周期扫描）：扫描本身只列\"反复出现但未建模\"的对象，视为已满足提次门槛——
    有 `relations` 或有 `why_key` 就登记；两者都没有仍进候选池。"""
    idx = _find_by_names(ctx.lines, [item["id"], item["label"], *item["aliases"]])
    if idx is not None:
        idx = _follow_idx(ctx, idx)  # 命中已被合并的元素 → 落到合并目标上
        _merge_hit(ctx, idx, item, source)
        return
    was_pooled = _find_by_names(ctx.cands, [item["id"], item["label"], *item["aliases"]]) is not None
    cand = _upsert_candidate(ctx, item, reason="below_threshold")
    cidx = _find_by_names(ctx.cands, [cand["id"]])
    if scan:
        credit = int(ctx.params["candidate_promote_mentions"] or 1)
        if int(cand.get("mentions", 1)) < credit:
            cand = dict(cand)
            cand["mentions"] = credit
            ctx.cands[cidx] = cand
    merged = _cand_to_item(cand)  # 合并了候选池里此前攒下的信息（别名/关系/why_key）
    ok, why = _qualifies(ctx, merged, int(cand.get("mentions", 1)))
    if not ok:
        ctx.log("candidate", cand["id"], mentions=cand["mentions"], why_key=merged["why_key"])
        return
    if not ctx.budget_ok():
        cand["reason"] = "over_budget"
        ctx.cands[cidx] = cand
        ctx.log("budget_blocked", cand["id"], limit=ctx.params["max_total_elements"], qualified_by=why)
        return
    # 首次提到就达标（关系明确）→ registered；之前已在候选池里攒过 → promoted
    _register(ctx, merged, via=why, cand_idx=cidx, promoted=was_pooled)


def _sweep_candidates(ctx: _Ctx) -> None:
    """每步末尾扫一遍候选池：之前关系指向的元素现在登记了、或预算空出来了 → 转正。"""
    for cand in list(ctx.cands):
        cidx = _find_by_names(ctx.cands, [cand["id"]])
        if cidx is None or _find_by_names(ctx.lines, _names_of(cand)) is not None:
            continue  # 已经有同名线了（由别处登记）——候选条目随后清理
        item = _cand_to_item(ctx.cands[cidx])
        ok, why = _qualifies(ctx, item, int(ctx.cands[cidx].get("mentions", 1)))
        if ok and ctx.budget_ok():
            _register(ctx, item, via=f"sweep:{why}", cand_idx=cidx)
    ctx.cands = [c for c in ctx.cands if _find_by_names(ctx.lines, _names_of(c)) is None]


# ── 引用即登记 ───────────────────────────────────────────────────────


def _canon(ctx: _Ctx, ref: Any, source: str, *, element_type: str = "") -> Optional[str]:
    """把一个 id 引用规范成已登记元素的 id。命中已登记 → 返回其 id（经别名/规范化命中时记审计）；
    命中候选池 → 视为被引用，转正；都没有 → 登记成待补全的桩元素。预算已满等原因登记不了返回 None
    （调用方保留原样的 key，不改写）。"""
    text = str(ref if ref is not None else "").strip()
    if not text:
        return None
    line = ctx.resolve(text)
    if line is not None:
        lid = str(line["id"]).strip()
        if text != lid and (text, lid) not in ctx._alias_logged:
            ctx._alias_logged.add((text, lid))
            ctx.log("alias_resolved", lid, ref=text, source=source)
        return lid
    cidx = _find_by_names(ctx.cands, [text])
    if cidx is not None:
        if ctx.budget_ok():
            item = _cand_to_item(ctx.cands[cidx])
            if element_type and not item["element_type"]:
                item["element_type"] = element_type
            return str(_register(ctx, item, via=f"referenced:{source}", cand_idx=cidx)["id"])
        ctx.log("budget_blocked", str(ctx.cands[cidx]["id"]), limit=ctx.params["max_total_elements"], source=source)
        return None
    item = {
        "id": text, "label": "", "element_type": element_type, "parent": "", "aliases": [], "why_key": "",
        "relations": [], "future_tree": None, "lifecycle_seed": None,
    }
    if not ctx.budget_ok():
        _upsert_candidate(ctx, item, reason="over_budget")
        ctx.log("budget_blocked", text, limit=ctx.params["max_total_elements"], source=source)
        return None
    line = _build_line(ctx, item)
    line["profile_status"] = "pending_enrichment"  # 桩元素：没有任何补全信息
    ctx.lines.append(line)
    ctx.log("ref_registered", text, source=source)
    return text


def _canon_condition(ctx: _Ctx, cond: Any, source: str) -> Any:
    """结构化条件里的 `{"tech": id}` / `{"element": id}` 引用规范化（dict 或 dict 列表）。"""
    if isinstance(cond, list):
        return [_canon_condition(ctx, c, source) for c in cond]
    if not isinstance(cond, dict):
        return cond
    out = dict(cond)
    for key in ("tech", "element"):
        if key in out and str(out[key] or "").strip():
            cid = _canon(ctx, out[key], source, element_type=TECHNOLOGY_TYPE if key == "tech" else "")
            if cid:
                out[key] = cid
    return out


def _canon_branch(ctx: _Ctx, branch: Any, source: str) -> Any:
    if not isinstance(branch, dict):
        return branch
    out = dict(branch)
    for ckey in ("trigger_condition",):
        if ckey in out:
            out[ckey] = _canon_condition(ctx, out[ckey], f"{source}.{ckey}")
    effects = out.get("effects_if_active")
    if isinstance(effects, list):
        new_effects = []
        for eff in effects:
            if isinstance(eff, dict):
                eff = dict(eff)
                if str(eff.get("to_line_id") or "").strip():
                    cid = _canon(ctx, eff["to_line_id"], f"{source}.effects_if_active")
                    if cid:
                        eff["to_line_id"] = cid
                if "condition" in eff:
                    eff["condition"] = _canon_condition(ctx, eff["condition"], f"{source}.effects_if_active.condition")
            new_effects.append(eff)
        out["effects_if_active"] = new_effects
    return out


def _canon_tree_updates(ctx: _Ctx, updates: Any) -> Any:
    if not isinstance(updates, list):
        return updates
    out: List[Any] = []
    for upd in updates:
        if not isinstance(upd, dict):
            out.append(upd)
            continue
        upd = dict(upd)
        if str(upd.get("line_id") or "").strip():
            cid = _canon(ctx, upd["line_id"], "tree_updates")
            if cid:
                upd["line_id"] = cid
        if isinstance(upd.get("new_branches"), list):
            upd["new_branches"] = [_canon_branch(ctx, b, "tree_updates.new_branches") for b in upd["new_branches"]]
        out.append(upd)
    return out


def _canon_line_updates(ctx: _Ctx, line_updates: Dict[str, Any]) -> Dict[str, Any]:
    """`line_updates` 的 key 规范成已登记 id（保持顺序）。两个 key 规范到同一个元素时，先到的字段优先、
    后到的补缺，并记一条 `alias_collision` 审计——不静默丢数据。"""
    out: Dict[str, Any] = {}
    for key, value in (line_updates or {}).items():
        cid = _canon(ctx, key, "line_updates") or str(key)
        if cid in out and isinstance(out[cid], dict) and isinstance(value, dict):
            out[cid] = {**value, **out[cid]}
            ctx.log("alias_collision", cid, keys=[str(key)])
        else:
            out[cid] = value
    return out


def _canon_tech_updates(ctx: _Ctx, tech_updates: Any) -> Any:
    """`tech_updates` 的 `id`/`tech_id` 只做别名解析、不登记：新技术仍由技术裁决登记（同时建元素线），
    这里只保证 `gpt_5` 这种漂移的写法落到已登记的 `gpt5` 上。"""
    if not isinstance(tech_updates, list):
        return tech_updates
    out: List[Any] = []
    for prop in tech_updates:
        if isinstance(prop, dict):
            prop = dict(prop)
            for key in ("id", "tech_id"):
                text = str(prop.get(key) or "").strip()
                if text:
                    line = ctx.resolve(text)
                    if line is not None and str(line["id"]).strip() != text:
                        ctx.log("alias_resolved", str(line["id"]).strip(), ref=text, source="tech_updates")
                        prop[key] = str(line["id"]).strip()
        out.append(prop)
    return out


# ── 补全与兜底 ───────────────────────────────────────────────────────


def _apply_enrichments(ctx: _Ctx, items: List[Dict[str, Any]]) -> None:
    for item in items:
        idx = _find_by_names(ctx.lines, [item["id"], item["label"], *item["aliases"]])
        if idx is None:
            ctx.log("enrichment_unknown", item["id"])
            continue
        idx = _follow_idx(ctx, idx)
        line = ctx.lines[idx]
        lid = str(line["id"]).strip()
        if str(line.get("profile_status") or "complete") not in ("pending_enrichment", "fallback"):
            ctx.log("enrichment_ignored", lid, reason="该元素不在待补全状态")
            continue
        new, changed = _enrich_line(ctx, line, item)
        ctx.lines[idx] = new
        if changed:
            ctx.log("enriched", lid, fields=changed)
        _want_lifecycle(ctx, new, item)


def _expire_pending(ctx: _Ctx) -> None:
    """宽限期过了仍未补全 → `fallback`：保留兜底树、`parent` 保持空（显示为\"未归类\"），不由引擎猜。"""
    grace = int(ctx.params["enrich_grace_steps"] or 0)
    has_domains = bool(ctx.alive_domains())
    for i, line in enumerate(ctx.lines):
        if line.get("profile_status") != "pending_enrichment":
            continue
        born = _int_or_none(line.get("born_step"))
        if born is None or ctx.step - born < grace:
            continue
        new = dict(line)
        new["profile_status"] = "fallback"
        ctx.lines[i] = new
        ctx.log("fallback", str(line["id"]).strip(), missing=_missing_fields(line, has_domains), grace_steps=grace)


# ── 入口 ─────────────────────────────────────────────────────────────


def process_step(
    settings: Dict[str, Any],
    *,
    step: int,
    line_updates: Optional[Dict[str, Any]] = None,
    causal_links: Optional[List[Any]] = None,
    tree_updates: Any = None,
    tech_updates: Any = None,
    discovered: Any = None,
    enrichments: Any = None,
    ops: Any = None,
) -> Dict[str, Any]:
    """元素模式下一步推进输出的\"元素登记\"处理（替换旧 `_auto_register_causal_lines`）。

    顺序：补全（`element_enrichments`）→ 发现（`discovered_elements`）→ 元素运维（`element_ops`，E5：
    split/merge/retire/reparent）→ 引用即登记（规范化
    `line_updates`/`causal_links`/`tree_updates`/`tech_updates` 里的 id，登记桩元素）→ 候选池转正 →
    宽限期过后落 `fallback`。就地写回 `settings["causal_lines"]` / `settings["element_candidates"]`
    （处理中途出错则不写回，由调用方兜底）。

    返回 `{"line_updates", "causal_links", "tree_updates", "tech_updates", "audit"}`：前四项是 id 规范化
    之后的副本（`tech_updates` 可能追加了 `lifecycle_seed` 转成的合成登记提议），`audit` 落
    `SimState.element_audit`。**不判断\"这个对象重不重要\"**——只做结构校验、去重、预算。
    """
    ctx = _Ctx(settings, step)
    _apply_enrichments(ctx, [i for i in (_norm_item(x) for x in discovered_list(enrichments)) if i])
    for item in (_norm_item(x) for x in discovered_list(discovered)):
        if item is not None:
            _discover_one(ctx, item)
    if ops:
        from world_simulator import element_ops  # 延迟导入（element_ops 反过来用本模块的内部工具）

        element_ops.apply_ops(ctx, ops)  # 先于引用规范化：同一步里写旧 id 的 line_updates 会落到合并目标上
    new_line_updates = _canon_line_updates(ctx, dict(line_updates or {}))
    new_links: List[Any] = []
    for link in causal_links or []:
        if isinstance(link, dict) and str(link.get("line_id") or "").strip():
            cid = _canon(ctx, link["line_id"], "causal_links")
            link = {**link, "line_id": cid} if cid else link
        new_links.append(link)
    new_tree_updates = _canon_tree_updates(ctx, tree_updates)
    new_tech_updates = _canon_tech_updates(ctx, tech_updates)
    _sweep_candidates(ctx)
    _expire_pending(ctx)

    if ctx.tech_proposals:
        have = {
            str(p.get("id") or p.get("tech_id") or "").strip()
            for p in (new_tech_updates if isinstance(new_tech_updates, list) else []) if isinstance(p, dict)
        }
        extra = [p for p in ctx.tech_proposals if p["id"] not in have]  # LLM 自己给了提议的，以它为准
        new_tech_updates = [*(new_tech_updates if isinstance(new_tech_updates, list) else []), *extra]

    settings["causal_lines"] = ctx.lines
    if ctx.cands or "element_candidates" in settings:
        settings["element_candidates"] = ctx.cands
    return {
        "line_updates": new_line_updates,
        "causal_links": new_links,
        "tree_updates": new_tree_updates,
        "tech_updates": new_tech_updates,
        "audit": ctx.audit,
    }


def register_scan_items(settings: Dict[str, Any], items: Any, *, step: int) -> List[Dict[str, Any]]:
    """E5 周期扫描/手动扫描的建议项 → 与 `discovered_elements` **同一套**校验与登记（别名去重、关键性门槛、
    预算、桩元素待补全）。就地写回 `settings[\"causal_lines\"]`/`[\"element_candidates\"]`，返回审计。

    与 `process_step` 的差别：扫描是事后回看，不处理 `line_updates` 等引用，也不产生 `lifecycle_seed`
    （技术裁决只在推进那一步发生，事后没有出口，所以丢弃）；元素模式未开启时什么都不做。
    """
    if not is_enabled(settings):
        return []
    ctx = _Ctx(settings, step)
    for raw in discovered_list(items):
        item = _norm_item(raw)
        if item is None:
            continue
        item["lifecycle_seed"] = None
        _discover_one(ctx, item, "element_scan", scan=True)
    _sweep_candidates(ctx)
    settings["causal_lines"] = ctx.lines
    if ctx.cands or "element_candidates" in settings:
        settings["element_candidates"] = ctx.cands
    return ctx.audit


def discovered_list(raw: Any) -> List[Any]:
    """`discovered_elements`/`element_enrichments` 不是数组时当作没有（不认识就忽略，不报错）。"""
    return list(raw) if isinstance(raw, (list, tuple)) else []


# ── 派生因果边（关系存在元素线上，随分支） ───────────────────────────


def derived_edges(settings: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把元素线上的 `relations` 翻成因果边（`{id, from_line_id, to_line_id, note, sign?, origin}`）。

    **为什么不写进 `settings.declared_causal_graph`**：它不是分支作用域动态状态（不在
    `dynamic_state.DYNAMIC_KEYS`），分支 A 里发现的元素关系会漏到分支 B。元素线（`causal_lines`）
    随分支快照，所以关系存在线上，需要边的地方（`causal_engine.get_edges`、先验提示）经本函数合并。
    只在元素模式下、且两端都是已登记的 alive 线时产出；与 `declared_causal_graph` 同 id 的边以后者为准
    （由调用方去重）。
    """
    if not is_enabled(settings):
        return []
    lines = [x for x in get_lines(settings) if is_alive(x)]
    alive_ids = {str(x["id"]).strip() for x in lines}
    edges: List[Dict[str, Any]] = []
    seen: set = set()
    for line in lines:
        lid = str(line["id"]).strip()
        for rel in line.get("relations") or []:
            if not isinstance(rel, dict):
                continue
            other = str(rel.get("with") or "").strip()
            if not other or other == lid or other not in alive_ids:
                continue
            src, dst = (lid, other) if rel.get("direction") == "affects" else (other, lid)
            eid = f"{src}->{dst}"
            if eid in seen:
                continue
            seen.add(eid)
            edge: Dict[str, Any] = {"id": eid, "from_line_id": src, "to_line_id": dst, "note": str(rel.get("note") or ""), "origin": "discovered"}
            if rel.get("sign") in _SIGNS:
                edge["sign"] = rel["sign"]
            edges.append(edge)
    return edges


# ── 提示词（`{element_hint}`） ───────────────────────────────────────

_PROTOCOL = (
    "【元素建模已开启】因果线分两层：领域线（只做分组）和元素线（每个具体的技术/项目/公司/政策/资产/市场/人物都"
    "可以是一条独立的线，有自己的未来树）。下面三个输出键（`discovered_elements`/`element_enrichments`/`element_ops`）都是**可选**的，没有就不要输出：\n"
    "1. `discovered_elements`（数组）：这一步出现了**值得单独建模的新关键对象**，且它不在下面的\"已登记元素\"里时才列。"
    "每项 `{\"id\": 简短英文/拼音 id, \"label\": 中文名, \"element_type\": technology/project/organization/person/"
    "asset/policy/market/resource/event_series 之一, \"parent\": 所属领域线 id（没有合适的就省略，不要编）, "
    "\"aliases\": [别名/曾用名], \"why_key\": 一句话说明为什么值得单独建模, \"relations\": [{\"with\": 已登记元素或领域的 id, "
    "\"direction\": \"affects\"（它影响对方）或 \"affected_by\"（对方影响它）, \"sign\": \"positive\"/\"negative\"/\"mixed\", "
    "\"note\": 一句话}], \"future_tree\": 与因果线相同的 {\"branches\": [...]}（可选）}`；技术类元素如果一出现就已经处于某个"
    "发展阶段，可再给 `lifecycle_seed`（`{\"stage\": ..., \"preexisting\": true}`，格式同技术发展模型）。\n"
    "   - 已登记的对象不要重复列出，直接沿用它的 id（写别名也认）；引擎按 id/名称/别名去重，别名命中的会被合并而不是新建。\n"
    "   - 与已登记元素有明确因果关系（填 `relations`）的新对象会直接登记；关系说不清的先进入候选池，之后被再次提到才转正。"
    "不要把普通名词、一次性提到的对象、同一件事的不同说法列进来。\n"
    "2. `element_enrichments`（数组）：给下面\"待补全元素\"补信息，每项 `{\"id\": 已登记 id, 以及上面同样的 label/element_type/"
    "parent/aliases/relations/future_tree}`；只补缺的字段，补不出来就不要编。\n"
    "3. `element_ops`（数组，可选）：对**已登记**元素做结构调整，每项 `{\"op\": ..., ...}`，只在确有必要时才写：\n"
    "   - `{\"op\": \"merge\", \"from\": 被并入的 id, \"into\": 保留的 id, \"note\": 一句话原因}`：确认两个 id 其实是同一个对象\n"
    "（名字不同但指同一件事）时用；被并入的会变成保留者的别名，它的未来树分支会以前缀 id 追加到保留者上。"
    "带发展阶段（lifecycle）的元素不能作为被并入方。\n"
    "   - `{\"op\": \"split\", \"from\": id, \"into\": [{\"id\":..., \"label\":..., \"element_type\":..., \"future_tree\":...}, ...]}`："
    "一个对象分化成多个时用；原元素保留，新元素默认继承它的领域归属。\n"
    "   - `{\"op\": \"retire\", \"id\": id, \"reason\": 一句话}`：对象已终止（技术被淘汰、项目取消）；不删除，只是不再产生新的待兑现因果。\n"
    "   - `{\"op\": \"reparent\", \"id\": id, \"parent\": 领域线 id}`：改所属领域（只能指向已登记的领域线）。\n"
    "   引擎只做结构校验（id 存在、不自己并自己、领域线不参与合并/退场），不通过的会在审计里写明原因；\"是不是该合并/拆分\"由你判断。\n"
    "4. 其余因果线输出（`line_updates`/`tree_updates`/`tech_updates`）照常，只是现在它们寻址的是同一套 id。"
)


def _fmt_element(line: Dict[str, Any]) -> str:
    lid = str(line.get("id")).strip()
    label = str(line.get("label") or lid).strip()
    piece = lid if label == lid else f"{lid}（{label}"
    extra = []
    aliases = [str(a) for a in line.get("aliases") or []]
    if aliases:
        extra.append("别名：" + "/".join(aliases[:4]))
    if label == lid:
        return piece + (f"（{'；'.join(extra)}）" if extra else "")
    return piece + ("；" + "；".join(extra) if extra else "") + "）"


_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")
_LATIN_TOKEN = re.compile(r"[a-z0-9]+")


def _name_tokens(text: Any) -> set:
    t = unicodedata.normalize("NFKC", str(text or "")).lower()
    tokens = {w for w in _LATIN_TOKEN.findall(t) if len(w) >= 2}
    for run in _CJK_RUN.findall(t):
        if len(run) == 1:
            tokens.add(run)
        else:
            tokens.update(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def suspected_duplicates(lines: List[Dict[str, Any]], recent_ids: Iterable[str], limit: int = 3) -> List[Tuple[str, str]]:
    """近期新登记的元素与其它 alive 元素\"名称相近\"的配对（只提示，不合并）。相近 = 名称词元 Jaccard >= 0.5，
    或较短名称（规范化后 >= 4 字符）被较长名称包含。规范化后完全相等的不在这里（那是别名命中，已被合并）。"""
    by_id = {str(x["id"]).strip(): x for x in lines}
    pairs: List[Tuple[str, str]] = []
    for rid in recent_ids:
        a = by_id.get(rid)
        if a is None:
            continue
        a_names = [n for n in (a.get("id"), a.get("label")) if str(n or "").strip()]
        for oid, b in by_id.items():
            if oid == rid or (oid, rid) in pairs or (rid, oid) in pairs or b.get("kind") == "domain" or a.get("kind") == "domain":
                continue
            b_names = [n for n in (b.get("id"), b.get("label")) if str(n or "").strip()]
            hit = False
            for x in a_names:
                for y in b_names:
                    kx, ky = norm_key(x), norm_key(y)
                    if not kx or not ky or kx == ky:
                        continue
                    short, long_ = sorted((kx, ky), key=len)
                    if len(short) >= 4 and short in long_:
                        hit = True
                    else:
                        tx, ty = _name_tokens(x), _name_tokens(y)
                        if tx and ty and len(tx & ty) / len(tx | ty) >= 0.5:
                            hit = True
                    if hit:
                        break
                if hit:
                    break
            if hit:
                pairs.append((rid, oid))
                if len(pairs) >= limit:
                    return pairs
    return pairs


def build_hint(settings: Optional[Dict[str, Any]], *, step: int, history: Optional[List[Any]] = None) -> str:
    """喂给 `advance_step`/`world_evolve` 的 `{element_hint}`：输出协议 + 已登记元素索引 + 待补全请求 +
    候选池 + 疑似重复提示。`step` 是**正在生成**的那一步。元素模式未开启返回空串（prompt 与之前等价）。

    `history`（第二十三轮 E4，可选）：传了（哪怕是空列表）表示调用方同时把分级展示接进了
    `{causal_lines_hint}`——那里已经列出所有 active 元素、watch 摘要和休眠索引，所以这里**不再重复列
    "已登记元素"索引**，只留一句指引；不传（旧调用方/单测）保持 E3 的行为。"""
    if not is_enabled(settings):
        return ""
    params = get_runtime_params(settings)
    grace = int(params["enrich_grace_steps"] or 0)
    lines = [x for x in get_lines(settings) if is_alive(x)]
    domain_lines = [x for x in lines if x.get("kind") == "domain"]
    elements = [x for x in lines if x.get("kind") != "domain"]
    parts = [_PROTOCOL]
    if domain_lines:
        parts.append("可用的领域线 id（`parent` 只能填这些）：" + "、".join(
            f"{x['id']}（{x.get('label') or x['id']}）" for x in domain_lines
        ) + "。")
    else:
        parts.append("当前没有领域线，`parent` 一律省略。")
    if elements and history is not None:
        parts.append(
            "已登记元素的 id 都列在上面的\"因果线设置\"里（活跃的完整展开，其余是摘要/休眠索引）：复用这些 id，"
            "不要重复发现；写别名也认。"
        )
    elif elements:
        shown = elements[-INDEX_MAX:]
        text = "已登记元素（复用这些 id，不要重复发现）：" + "；".join(_fmt_element(x) for x in shown)
        if len(elements) > len(shown):
            text += f"；……另有 {len(elements) - len(shown)} 个较早登记的元素，见上方因果线设置"
        parts.append(text + "。")
    has_domains = bool(domain_lines)
    pending = []
    for x in elements:
        born = _int_or_none(x.get("born_step"))
        if x.get("profile_status") == "pending_enrichment" and born is not None and 1 <= step - born <= grace:
            miss = _missing_fields(x, has_domains) or ["（只缺更准确的 label/别名/关系/未来树）"]
            pending.append(f"{_fmt_element(x)} 缺：{'、'.join(miss)}")
    if pending:
        parts.append(
            "待补全元素（上一步登记时信息不全，请在本步 `element_enrichments` 里补；本步仍未补全将按兜底处理——保留通用未来树、"
            "不归属任何领域）：" + "；".join(pending) + "。"
        )
    cands = sorted(get_candidates(settings), key=lambda c: (-int(c.get("mentions", 1)), -int(c.get("last_step", 0))))[:5]
    if cands:
        rows = []
        for c in cands:
            why = str(c.get("why_key") or "").strip()
            rows.append(f"{c['id']}（{c.get('label') or c['id']}；已提到 {int(c.get('mentions', 1))} 次" + (f"；{why}" if why else "") + "）")
        parts.append(
            "候选池里这些对象之前提到过、还没正式登记（关系不明确或预算已满）：" + "；".join(rows)
            + "。如果它们确实重要，请在 `discovered_elements` 里补上与已登记元素的 `relations`，或在后续推进里再次提到。"
        )
    recent = [
        str(x["id"]).strip() for x in elements
        if x.get("origin") == "discovered" and (_int_or_none(x.get("born_step")) is not None)
        and 1 <= step - int(x["born_step"]) <= grace + 1
    ]
    dups = suspected_duplicates(elements, recent)
    if dups:
        parts.append(
            "名称相近、可能是同一个对象的元素（仅供提示，引擎不会自动合并）：" + "；".join(f"{a} ≈ {b}" for a, b in dups)
            + "。如果确实是同一个，请此后只使用其中一个 id，并在 `element_enrichments` 里给保留的那个补上另一个的名字作别名。"
        )
    return "\n".join(parts)


def safe_build_hint(settings: Optional[Dict[str, Any]], *, step: int, history: Optional[List[Any]] = None) -> str:
    """`build_hint()` 的兜底版：任何异常返回空串，不拖垮推进。"""
    try:
        return build_hint(settings, step=step, history=history)
    except Exception:  # noqa: BLE001 — 提示词是旁路信息
        return ""
