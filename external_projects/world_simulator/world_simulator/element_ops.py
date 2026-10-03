"""world_simulator/element_ops.py — 元素运维：split / merge / retire / reparent（第二十三轮 E5）。

设计依据：`next_doc/world_simulator_element_causal_lines_plan.md` §4.5、§6 E5。

## 这个模块做什么

推进输出里可选的 `element_ops` 数组，对**已登记**元素做结构调整。引擎只做**结构校验**（id 存在、
不自己并自己、不成环、领域线不参与合并/退场/拆分），"该不该合并/拆分"这类语义判断仍归 LLM。
校验不通过的操作**不生效**，在审计里写明原因（`op_rejected`），不报错、不影响同一步的其它处理。

| 操作 | 效果 |
|---|---|
| `merge` `{from, into}` | `from` 变成 `into` 的别名（id/label/别名并入）；`from` 的关系并入 `into`，其它元素指向 `from` 的关系改指 `into`；`from` 的未来树分支以前缀 id（`<from>__<分支id>`）追加到 `into`，状态原样保留；`from` 标 `status=merged` + `merged_into`，**不删除**。历史里的 `line_updates` key 不改，读取时经 `element_registry.resolve()`/`redirect_merged()` 落到 `into` |
| `split` `{from, into: [元素...]}` | 原元素保留（记 `split_into`）；新元素 `origin=split`、`split_from=原元素`，**默认继承原元素的 `parent`**（条目自己给了合法 parent 则以它为准）；去重/预算与"发现"同一套；技术类 `lifecycle_seed` 走同一套技术裁决 |
| `retire` `{id, reason}` | `status=retired`（+`retired_step`/`retire_reason`），不删除、历史与树保留；不再进 prompt 展示、不再产生新的待兑现因果（`causal_engine.queue_effects` 跳过端点为已退场元素的边）；**已入队的待兑现不撤销** |
| `reparent` `{id, parent}` | 改归属领域；目标必须是已登记且存活的领域线。顺带把"待补全/兜底"且现在信息齐了的元素标回 `complete` |

## 边界（如实记录，不掩盖）

- **带 `lifecycle`（技术发展阶段）的元素不能作为被并入方**：技术节点 id 被其它节点的 `requires` 引用，
  合并会让前置悬空。要淘汰这类元素用 `retire`。（保留方带 lifecycle 没问题。）
- 领域线不参与 merge/split/retire：领域只做分组，动它会让子元素整批失去归属。
- 已退场的元素不会被"再发现"复活（按 id/别名命中仍视为同一个、只补别名）；v1 没有"复活"操作。
- 每步最多处理 `OPS_PER_STEP_MAX` 条操作，多出的记 `op_rejected`（防 LLM 一次改一大片）。
- 每条操作先拷贝工作区、出错整条回滚（审计 `op_error`），不留半截状态。

本模块纯 Python，不调 LLM、不读写磁盘；依赖 `element_registry` 的内部工具（`_Ctx` 等），后者对本模块
只做函数内延迟导入。
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from world_simulator import element_registry as er

OPS_PER_STEP_MAX = 8
OP_NAMES = ("merge", "split", "retire", "reparent")

_OP_ALIASES = {
    "merge": "merge", "merged": "merge", "合并": "merge",
    "split": "split", "拆分": "split", "分化": "split",
    "retire": "retire", "retired": "retire", "退场": "retire", "终止": "retire",
    "reparent": "reparent", "move": "reparent", "改归属": "reparent",
}


def _first(raw: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if raw.get(key) not in (None, ""):
            return raw[key]
    return None


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def normalize_op(raw: Any) -> Optional[Dict[str, Any]]:
    """LLM 给的一项 → 内部形状 `{op, ...}`；不是对象/没写操作名的返回 None。未知操作名保留原值（交给校验记 `op_rejected`）。"""
    if not isinstance(raw, dict):
        return None
    name = _text(_first(raw, "op", "type", "action")).lower()
    if not name:
        return None
    op = _OP_ALIASES.get(name, name)
    out: Dict[str, Any] = {"op": op, "note": _text(_first(raw, "note", "reason", "why"))}
    if op == "merge":
        out["from"] = _text(_first(raw, "from", "source", "id"))
        out["into"] = _text(_first(raw, "into", "target", "to"))
    elif op == "split":
        out["from"] = _text(_first(raw, "from", "source", "id"))
        children = _first(raw, "into", "children", "parts")
        out["into"] = list(children) if isinstance(children, (list, tuple)) else []
    elif op == "retire":
        out["id"] = _text(_first(raw, "id", "from", "element"))
    elif op == "reparent":
        out["id"] = _text(_first(raw, "id", "from", "element"))
        out["parent"] = _text(_first(raw, "parent", "to", "into", "domain"))
    return out


# ── 工具 ─────────────────────────────────────────────────────────────


def _find(ctx: Any, ref: str) -> Optional[Dict[str, Any]]:
    """按 id/规范化 id/名称/别名找线，**不跟随合并重定向**（运维要操作的就是被引用的那条线本身）。"""
    return er.resolve(ctx.lines, ref, follow_merged=False)


def _reject(ctx: Any, op: Dict[str, Any], reason: str, element_id: str = "") -> None:
    ctx.log("op_rejected", element_id, op=op.get("op"), reason=reason, note=op.get("note"))


def _is_default(line: Dict[str, Any]) -> bool:
    return er._is_default_tree(line.get("future_tree"))


def _branch_ids(branches: Any) -> List[str]:
    out: List[str] = []
    for b in branches or []:
        if not isinstance(b, dict):
            continue
        bid = _text(b.get("id"))
        if bid:
            out.append(bid)
        out.extend(_branch_ids(b.get("children")))
        out.extend(_branch_ids(b.get("sub_branches")))
    return out


def _unique(base: str, used: set) -> str:
    new, n = base, 2
    while new in used:
        new = f"{base}_{n}"
        n += 1
    used.add(new)
    return new


def _rename_map(src_id: str, branches: Any, used: set) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for bid in _branch_ids(branches):
        if bid not in mapping:
            mapping[bid] = _unique(f"{src_id}__{bid}", used)
    return mapping


def _moved_branch(branch: Dict[str, Any], mapping: Dict[str, str], src_id: str, drop_targets: set) -> Dict[str, Any]:
    """深拷贝一个分支并改写 id / 同线内前置 / 互斥组 / 指向 `drop_targets` 的自指影响。"""
    b = copy.deepcopy(branch)
    b["id"] = mapping.get(_text(b.get("id")), b.get("id"))
    if b.get("prerequisites"):
        b["prerequisites"] = [mapping.get(_text(p), p) for p in b["prerequisites"]]
    if _text(b.get("exclusive_group")):
        b["exclusive_group"] = f"{src_id}__{_text(b['exclusive_group'])}"
    if isinstance(b.get("effects_if_active"), list):
        kept = [e for e in b["effects_if_active"] if _text((e or {}).get("to_line_id")) not in drop_targets]
        if kept:
            b["effects_if_active"] = kept
        else:
            b.pop("effects_if_active")
    for key in ("children", "sub_branches"):
        if isinstance(b.get(key), list):
            b[key] = [_moved_branch(c, mapping, src_id, drop_targets) for c in b[key] if isinstance(c, dict)]
    return b


def _rewrite_effect_targets(lines: List[Dict[str, Any]], src_id: str, dst_id: str) -> int:
    """其它线分支的 `effects_if_active[].to_line_id == src_id` 改指 `dst_id`（线是分支作用域状态，改它不会漏到别的分支）。
    `dst_id` 自己的分支里指向 `src_id` 的影响会变成自指，直接去掉。返回改写条数。"""
    count = 0

    def walk(line_id: str, branches: Any) -> None:
        nonlocal count
        for b in branches or []:
            if not isinstance(b, dict):
                continue
            effs = b.get("effects_if_active")
            if isinstance(effs, list):
                new_effs: List[Any] = []
                for e in effs:
                    if isinstance(e, dict) and _text(e.get("to_line_id")) == src_id:
                        count += 1
                        if line_id == dst_id:
                            continue
                        e = {**e, "to_line_id": dst_id}
                    new_effs.append(e)
                if new_effs:
                    b["effects_if_active"] = new_effs
                else:
                    b.pop("effects_if_active")
            walk(line_id, b.get("children"))
            walk(line_id, b.get("sub_branches"))

    for line in lines:
        tree = line.get("future_tree")
        if isinstance(tree, dict):
            walk(_text(line.get("id")), tree.get("branches"))
    return count


def _retarget_relations(lines: List[Dict[str, Any]], src_id: str, dst_id: str) -> int:
    """所有线 `relations[].with == src_id` 改指 `dst_id`（去重；落在 `dst` 自己身上的自指关系丢弃）。返回改写条数。"""
    count = 0
    for i, line in enumerate(lines):
        rels = line.get("relations")
        if not isinstance(rels, list) or not rels:
            continue
        lid = _text(line.get("id"))
        new_rels: List[Dict[str, Any]] = []
        seen: set = set()
        changed = False
        for rel in rels:
            if not isinstance(rel, dict):
                continue
            if _text(rel.get("with")) == src_id:
                changed = True
                count += 1
                if lid == dst_id:
                    continue
                rel = {**rel, "with": dst_id}
            key = (er.norm_key(rel.get("with")), rel.get("direction"))
            if key in seen:
                changed = True
                continue
            seen.add(key)
            new_rels.append(rel)
        if changed:
            new = dict(line)
            if new_rels:
                new["relations"] = new_rels
            else:
                new.pop("relations", None)
            lines[i] = new
    return count


# ── 四种操作 ─────────────────────────────────────────────────────────


def _op_merge(ctx: Any, op: Dict[str, Any]) -> None:
    src = _find(ctx, op["from"]) if op["from"] else None
    # 保留方若引用的是已被合并的旧 id，直接并到它最终的归宿上（链式合并不需要 LLM 记得谁并给了谁）
    dst = er.resolve(ctx.lines, op["into"]) if op["into"] else None
    if src is None or dst is None:
        return _reject(ctx, op, "id 未登记：" + "、".join(
            x for x, line in ((op["from"] or "(缺 from)", src), (op["into"] or "(缺 into)", dst)) if line is None))
    sid, did = _text(src["id"]), _text(dst["id"])
    if sid == did:
        return _reject(ctx, op, "不能把元素并入自己", sid)
    if src.get("kind") == "domain" or dst.get("kind") == "domain":
        return _reject(ctx, op, "领域线不参与合并", sid)
    if src.get("status") == "merged":
        return _reject(ctx, op, f"「{sid}」已经被并入「{_text(src.get('merged_into'))}」", sid)
    if not er.is_alive(src) or not er.is_alive(dst):
        return _reject(ctx, op, "已退场/已合并的元素不能参与合并", sid if not er.is_alive(src) else did)
    if isinstance(src.get("lifecycle"), dict):
        return _reject(ctx, op, "被并入方带技术发展阶段（lifecycle），节点 id 被其它节点的前置引用，合并会让前置悬空；请改用 retire", sid)

    # 先在副本上算，最后一次性落到 ctx.lines
    new_dst = dict(dst)
    # ① 名称并入别名
    aliases = er._union_aliases(
        list(new_dst.get("aliases") or []), [sid, _text(src.get("label")), *[str(a) for a in src.get("aliases") or []]],
        [er.norm_key(did), er.norm_key(new_dst.get("label"))],
    )
    added_aliases = [a for a in aliases if a not in (new_dst.get("aliases") or [])]
    if aliases:
        new_dst["aliases"] = aliases
    # ② 补缺（不改写）：类型/归属/变量引用
    for key in ("element_type", "parent"):
        if not _text(new_dst.get(key)) and _text(src.get(key)):
            new_dst[key] = src[key]
    refs = list(dict.fromkeys([*(new_dst.get("var_refs") or []), *(src.get("var_refs") or [])]))
    if refs:
        new_dst["var_refs"] = refs
    # ③ 关系并入（`with` 指向 src/dst 自己的丢掉）
    src_rels = [r for r in src.get("relations") or [] if isinstance(r, dict) and _text(r.get("with")) not in (sid, did)]
    rels = er._merge_relations(list(new_dst.get("relations") or []), src_rels)
    if rels:
        new_dst["relations"] = rels
    # ④ 未来树分支以前缀 id 追加（src 的树还是通用兜底模板就不搬，没有信息量）
    moved = 0
    src_tree = src.get("future_tree")
    if isinstance(src_tree, dict) and src_tree.get("branches") and not _is_default(src):
        dst_tree = copy.deepcopy(new_dst.get("future_tree")) if isinstance(new_dst.get("future_tree"), dict) else {
            "as_of_step": ctx.step, "branches": []}
        dst_tree.setdefault("branches", [])
        used = set(_branch_ids(dst_tree["branches"]))
        mapping = _rename_map(sid, src_tree["branches"], used)
        drop = {sid, did}
        for b in src_tree["branches"]:
            if isinstance(b, dict):
                dst_tree["branches"].append(_moved_branch(b, mapping, sid, drop))
                moved += 1
        dst_tree["as_of_step"] = ctx.step
        new_dst["future_tree"] = dst_tree
    new_dst["merged_from"] = list(dict.fromkeys([*(new_dst.get("merged_from") or []), sid]))

    new_src = dict(src)
    new_src["status"] = "merged"
    new_src["merged_into"] = did
    new_src.pop("tier_pin", None)

    di, si = ctx.index_of(did), ctx.index_of(sid)
    ctx.lines[di], ctx.lines[si] = new_dst, new_src
    retargeted = _retarget_relations(ctx.lines, sid, did)
    effects = _rewrite_effect_targets(ctx.lines, sid, did)
    ctx.log("op_merge", sid, into=did, aliases_added=added_aliases, branches_moved=moved,
            relations_retargeted=retargeted, effects_retargeted=effects, note=op.get("note"))


def _op_retire(ctx: Any, op: Dict[str, Any]) -> None:
    line = _find(ctx, op["id"]) if op["id"] else None
    if line is None:
        return _reject(ctx, op, f"id 未登记：{op['id'] or '(缺 id)'}")
    lid = _text(line["id"])
    if line.get("kind") == "domain":
        return _reject(ctx, op, "领域线不退场（子元素会整批失去归属）", lid)
    if line.get("status") == "merged":
        return _reject(ctx, op, f"「{lid}」已被并入「{_text(line.get('merged_into'))}」，不能再退场", lid)
    if line.get("status") == "retired":
        return ctx.log("op_noop", lid, op="retire", reason="已经是退场状态")
    new = dict(line)
    new["status"] = "retired"
    new["retired_step"] = max(0, int(ctx.step))
    if op.get("note"):
        new["retire_reason"] = op["note"]
    new.pop("tier_pin", None)
    ctx.lines[ctx.index_of(lid)] = new
    ctx.log("op_retire", lid, reason=op.get("note"))


def _op_reparent(ctx: Any, op: Dict[str, Any]) -> None:
    line = _find(ctx, op["id"]) if op["id"] else None
    if line is None:
        return _reject(ctx, op, f"id 未登记：{op['id'] or '(缺 id)'}")
    lid = _text(line["id"])
    if line.get("kind") == "domain":
        return _reject(ctx, op, "领域线没有归属可改", lid)
    if not er.is_alive(line):
        return _reject(ctx, op, "已退场/已合并的元素不能改归属", lid)
    if not op.get("parent"):
        return _reject(ctx, op, "缺少目标领域 parent", lid)
    target = ctx.resolve_parent(op["parent"])
    if target is None:
        return _reject(ctx, op, f"「{op['parent']}」不是已登记且存活的领域线", lid)
    if target == _text(line.get("parent")):
        return ctx.log("op_noop", lid, op="reparent", reason="归属没有变化", parent=target)
    new = dict(line)
    previous = _text(new.get("parent"))
    new["parent"] = target
    if new.get("profile_status") in ("pending_enrichment", "fallback") and er._is_complete(new, True):
        new["profile_status"] = "complete"
    ctx.lines[ctx.index_of(lid)] = new
    ctx.log("op_reparent", lid, parent=target, previous_parent=previous or None, note=op.get("note"))


def _op_split(ctx: Any, op: Dict[str, Any]) -> None:
    src = _find(ctx, op["from"]) if op["from"] else None
    if src is None:
        return _reject(ctx, op, f"id 未登记：{op['from'] or '(缺 from)'}")
    sid = _text(src["id"])
    if src.get("kind") == "domain":
        return _reject(ctx, op, "领域线不拆分", sid)
    if not er.is_alive(src):
        return _reject(ctx, op, "已退场/已合并的元素不能拆分", sid)
    items = [i for i in (er._norm_item(x) for x in op["into"]) if i is not None]
    if not items:
        return _reject(ctx, op, "`into` 里没有可用的新元素（至少要有 id 或 label）", sid)
    created: List[str] = []
    for item in items:
        hit = er._find_by_names(ctx.lines, [item["id"], item["label"], *item["aliases"]])
        if hit is not None:
            ctx.log("op_split_skipped", item["id"], reason=f"与已登记元素「{_text(ctx.lines[hit].get('id'))}」重名/同别名", split_from=sid)
            continue
        if not ctx.budget_ok():
            ctx.log("budget_blocked", item["id"], limit=ctx.params["max_total_elements"], source="element_ops.split")
            continue
        line = er._build_line(ctx, item, origin="split")
        line["split_from"] = sid
        if not line.get("parent") and _text(src.get("parent")):
            line["parent"] = src["parent"]  # 继承原元素的领域归属
            line["profile_status"] = "complete" if er._is_complete(line, bool(ctx.alive_domains())) else "pending_enrichment"
        ctx.lines.append(line)
        created.append(_text(line["id"]))
        er._want_lifecycle(ctx, line, item)
    if not created:
        return _reject(ctx, op, "没有新元素被创建（全部重名或预算已满）", sid)
    idx = ctx.index_of(sid)
    new_src = dict(ctx.lines[idx])
    new_src["split_into"] = list(dict.fromkeys([*(new_src.get("split_into") or []), *created]))
    ctx.lines[idx] = new_src
    ctx.log("op_split", sid, into=created, inherited_parent=_text(src.get("parent")) or None, note=op.get("note"))


_HANDLERS = {"merge": _op_merge, "split": _op_split, "retire": _op_retire, "reparent": _op_reparent}


# ── 入口 ─────────────────────────────────────────────────────────────


def apply_ops(ctx: Any, ops: Any) -> None:
    """依次处理 `element_ops`（就地改 `ctx.lines`、往 `ctx.log` 写审计）。不是数组当作没有；每步最多
    `OPS_PER_STEP_MAX` 条；每条操作出错整条回滚。"""
    if not isinstance(ops, (list, tuple)):
        return
    for n, raw in enumerate(ops):
        op = normalize_op(raw)
        if op is None:
            continue
        if n >= OPS_PER_STEP_MAX:
            ctx.log("op_rejected", "", op=op["op"], reason=f"本步操作超过上限 {OPS_PER_STEP_MAX} 条，其余忽略")
            continue
        handler = _HANDLERS.get(op["op"])
        if handler is None:
            ctx.log("op_rejected", "", op=op["op"], reason=f"不认识的操作（只支持 {'/'.join(OP_NAMES)}）")
            continue
        snapshot_lines = copy.deepcopy(ctx.lines)
        snapshot_audit = len(ctx.audit)
        snapshot_proposals = len(ctx.tech_proposals)
        try:
            handler(ctx, op)
        except Exception as exc:  # noqa: BLE001 — 单条操作出错不拖垮整步
            ctx.lines = snapshot_lines
            del ctx.audit[snapshot_audit:]
            del ctx.tech_proposals[snapshot_proposals:]
            ctx.log("op_error", "", op=op["op"], message=str(exc))
