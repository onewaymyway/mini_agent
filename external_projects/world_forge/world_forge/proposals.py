"""world_forge/proposals.py — 所有写入的唯一入口：风险分级、一致性检查、应用、待确认队列。

流程：submit → 一致性检查（有问题即 blocked，不写入）→ 风险分级：
  minor 且 auto_minor 开启 → 直接应用（applied）
  其余 → 进入待确认（pending），由用户 approve / reject

调用方必须持有世界写锁（repo.lock_file）。本模块不加锁，避免嵌套。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import repo, store

KINDS = ["地理", "势力", "物种", "技术/魔法", "历史事件", "物品", "组织", "其它"]
# 这些类别会塑造世界走向，新增即视为重大（需确认）
MAJOR_ENTITY_KINDS = {"势力", "历史事件", "技术/魔法"}
# 这些属性涉及权力归属，修改即视为重大
RULER_ATTRS = {"leader", "ruler", "founder"}
# 修改这些核心字段即视为重大
CORE_ENTITY_FIELDS = {"name", "kind", "status"}
# 次要角色（role 为其一即视为次要，可自动写入）
MINOR_ROLES = {"次要", "路人"}
# 事件中允许的变更操作
CHANGE_OPS = {"update_entity", "update_character", "create_relation"}

ALL_OPS = {
    "create_entity", "update_entity", "delete_entity", "create_relation",
    "create_character", "update_character", "delete_character",
    "add_event", "set_charter",
}


def _norm_name(name: Any) -> str:
    return str(name or "").strip().casefold()


# ---------- 输入规范化
# 模型输出不可控：attrs 可能是数组、字符串或对象。统一在提案入口转换为字典，
# 之后的分级、检查与写入都只需处理字典。

def coerce_attrs(value: Any) -> Dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    if isinstance(value, list):
        out: Dict[str, Any] = {}
        for i, item in enumerate(value, start=1):
            if isinstance(item, dict):
                out.update({str(k): v for k, v in item.items()})
            else:
                out[f"要点{i}"] = str(item)
        return out
    return {"说明": str(value)}


def normalize_payload(op: str, payload: Any) -> Dict[str, Any]:
    """返回规范化后的 payload 副本：attrs 为字典；事件中的变更递归规范化。"""
    if not isinstance(payload, dict):
        payload = {}
    out = dict(payload)
    if "attrs" in out:
        out["attrs"] = coerce_attrs(out["attrs"])
    if op == "add_event" and isinstance(out.get("changes"), list):
        out["changes"] = [
            {**c, "payload": normalize_payload(c.get("op", ""), c.get("payload"))}
            for c in out["changes"] if isinstance(c, dict)
        ]
    return out


# ---------- 风险分级

def classify(op: str, payload: Dict[str, Any]) -> str:
    """返回 'minor' 或 'major'。"""
    if op in ("set_charter", "delete_entity", "delete_character"):
        return "major"
    if op == "create_entity":
        if payload.get("kind") in MAJOR_ENTITY_KINDS or set((payload.get("attrs") or {}).keys()) & RULER_ATTRS:
            return "major"
        return "minor"
    if op == "update_entity":
        if set(payload.keys()) & CORE_ENTITY_FIELDS or set((payload.get("attrs") or {}).keys()) & RULER_ATTRS:
            return "major"
        return "minor"
    if op == "create_relation":
        return "minor"
    if op == "create_character":
        return "minor" if payload.get("role") in MINOR_ROLES else "major"
    if op == "update_character":
        return "major" if "status" in payload else "minor"
    if op == "add_event":
        changes = payload.get("changes") or []
        return "major" if any(classify(c["op"], c.get("payload") or {}) == "major" for c in changes) else "minor"
    return "major"


# ---------- 状态与一致性检查

def load_state(wid: str) -> Dict[str, Any]:
    meta = repo.load_meta(wid)
    return {
        "meta": meta,
        "entities": {e["id"]: e for e in repo.load_entities(wid)},
        "relations": repo.load_relations(wid),
        "characters": {c["id"]: c for c in repo.load_characters(wid)},
        "events": repo.load_events(wid, meta["active_branch"]),
    }


def check(state: Dict[str, Any], op: str, target: Optional[str], payload: Dict[str, Any]) -> List[str]:
    """返回阻止写入的原因列表；空列表表示可以写入。"""
    issues: List[str] = []
    ents, chars = state["entities"], state["characters"]

    if op not in ALL_OPS:
        return [f"未知操作：{op}"]

    if op == "create_entity":
        if not _norm_name(payload.get("name")):
            issues.append("实体名称不能为空")
        elif any(_norm_name(e["name"]) == _norm_name(payload["name"]) for e in ents.values()):
            issues.append(f"已存在同名实体：{payload['name']}")

    elif op == "update_entity":
        if target not in ents:
            issues.append(f"实体不存在：{target}")
        elif "name" in payload and any(
            e["id"] != target and _norm_name(e["name"]) == _norm_name(payload["name"]) for e in ents.values()
        ):
            issues.append(f"改名后与已有实体重名：{payload['name']}")

    elif op == "delete_entity":
        if target not in ents:
            issues.append(f"实体不存在：{target}")
        else:
            if any(target in (r["src"], r["dst"]) for r in state["relations"]):
                issues.append("仍有关系引用该实体，请先删除相关关系")
            if any(c.get("faction_id") == target for c in chars.values()):
                issues.append("仍有角色归属该实体，请先调整角色归属")

    elif op == "create_relation":
        src, dst = payload.get("src"), payload.get("dst")
        if src not in ents or dst not in ents:
            issues.append("关系的两端实体必须都存在")
        elif src == dst:
            issues.append("关系不能指向自身")
        if not str(payload.get("type") or "").strip():
            issues.append("关系类型不能为空")

    elif op == "create_character":
        if not _norm_name(payload.get("name")):
            issues.append("角色名不能为空")
        elif any(_norm_name(c["name"]) == _norm_name(payload["name"]) for c in chars.values()):
            issues.append(f"已存在同名角色：{payload['name']}")
        if payload.get("faction_id") and payload["faction_id"] not in ents:
            issues.append(f"所属势力不存在：{payload['faction_id']}")

    elif op == "update_character":
        if target not in chars:
            issues.append(f"角色不存在：{target}")
        else:
            if payload.get("status") == "alive" and chars[target].get("status") == "dead":
                issues.append("已死亡角色不能复活")
            if payload.get("faction_id") and payload["faction_id"] not in ents:
                issues.append(f"所属势力不存在：{payload['faction_id']}")

    elif op == "delete_character":
        if target not in chars:
            issues.append(f"角色不存在：{target}")

    elif op == "add_event":
        if not _norm_name(payload.get("title")):
            issues.append("事件标题不能为空")
        for ref in payload.get("involves") or []:
            if ref not in ents and ref not in chars:
                issues.append(f"事件引用了不存在的实体或角色：{ref}")
            elif ref in chars and chars[ref].get("status") == "dead":
                issues.append(f"已死亡角色不能参与事件：{chars[ref]['name']}")
        if payload.get("violates_rules"):
            issues.append("违反世界规则：" + "；".join(str(x) for x in payload["violates_rules"]))
        for ch in payload.get("changes") or []:
            if ch.get("op") not in CHANGE_OPS:
                issues.append(f"事件中不允许的变更：{ch.get('op')}")
                continue
            issues.extend(check(state, ch["op"], ch.get("target"), ch.get("payload") or {}))

    elif op == "set_charter":
        if state["events"]:
            issues.append("已有历史事件，宪章不可再修改（请用新分支或补充设定）")
        if not isinstance(payload.get("hard_rules"), list) or not payload["hard_rules"]:
            issues.append("宪章必须包含至少一条硬规则")

    return issues


# ---------- 应用

def apply_op(wid: str, op: str, target: Optional[str], payload: Dict[str, Any], source: str) -> Dict[str, Any]:
    now = store.now_iso()
    if op == "create_entity":
        items = repo.load_entities(wid)
        eid = store.new_id("ent")
        items.append({"id": eid, "kind": payload.get("kind") or "其它", "name": payload["name"].strip(),
                      "summary": payload.get("summary", ""), "attrs": payload.get("attrs") or {},
                      "status": "active", "source": source, "created_at": now, "updated_at": now})
        repo.save_entities(wid, items)
        return {"id": eid}

    if op == "update_entity":
        items = repo.load_entities(wid)
        it = repo.find_by_id(items, target)
        for k, v in payload.items():
            if k == "attrs":
                it["attrs"] = {**(it.get("attrs") or {}), **(v or {})}
            else:
                it[k] = v
        it["updated_at"] = now
        repo.save_entities(wid, items)
        return {"id": target}

    if op == "delete_entity":
        repo.save_entities(wid, [e for e in repo.load_entities(wid) if e["id"] != target])
        return {"id": target}

    if op == "create_relation":
        rels = repo.load_relations(wid)
        rid = store.new_id("rel")
        rels.append({"id": rid, "src": payload["src"], "dst": payload["dst"],
                     "type": payload["type"].strip(), "note": payload.get("note", "")})
        repo.save_relations(wid, rels)
        return {"id": rid}

    if op == "create_character":
        chars = repo.load_characters(wid)
        cid = store.new_id("chr")
        chars.append({"id": cid, "name": payload["name"].strip(), "role": payload.get("role", "次要"),
                      "faction_id": payload.get("faction_id"), "motive": payload.get("motive", ""),
                      "status": "alive", "summary": payload.get("summary", ""),
                      "attrs": payload.get("attrs") or {}, "source": source, "created_at": now})
        repo.save_characters(wid, chars)
        return {"id": cid}

    if op == "update_character":
        chars = repo.load_characters(wid)
        it = repo.find_by_id(chars, target)
        it.update({k: v for k, v in payload.items() if k != "attrs"})
        if "attrs" in payload:
            it["attrs"] = {**(it.get("attrs") or {}), **(payload["attrs"] or {})}
        repo.save_characters(wid, chars)
        return {"id": target}

    if op == "delete_character":
        repo.save_characters(wid, [c for c in repo.load_characters(wid) if c["id"] != target])
        return {"id": target}

    if op == "add_event":
        meta = repo.load_meta(wid)
        year = meta["clock"]["year"]
        eid = store.new_id("evt")
        applied = []
        for ch in payload.get("changes") or []:
            apply_op(wid, ch["op"], ch.get("target"), ch.get("payload") or {}, source)
            applied.append({"op": ch["op"], "target": ch.get("target")})
        repo.append_event(wid, meta["active_branch"], {
            "id": eid, "year": year, "t": f"第{year}年", "title": payload["title"].strip(),
            "summary": payload.get("summary", ""), "involves": payload.get("involves") or [],
            "caused_by": payload.get("caused_by") or [], "tags": payload.get("tags") or [],
            "changes": applied, "branch": meta["active_branch"], "source": source, "created_at": now,
        })
        return {"id": eid}

    if op == "set_charter":
        meta = repo.load_meta(wid)
        meta["charter"] = payload
        if payload.get("title"):
            meta["title"] = payload["title"]
        repo.save_meta(meta)
        return {"id": "charter"}

    raise ValueError(f"不支持的操作：{op}")


# ---------- 提案生命周期

def submit(wid: str, op: str, target: Optional[str] = None, payload: Optional[Dict[str, Any]] = None, *,
           source: str = "user", note: str = "") -> Dict[str, Any]:
    payload = normalize_payload(op, payload)
    state = load_state(wid)
    auto_minor = bool(state["meta"].get("settings", {}).get("auto_minor", True))
    rec: Dict[str, Any] = {
        "id": store.new_id("prp"), "op": op, "target": target, "payload": payload,
        "source": source, "note": note, "created_at": store.now_iso(), "reviewed_at": None,
        "risk": None, "status": None, "reasons": [], "result": None,
    }
    issues = check(state, op, target, payload)
    if issues:
        rec.update(status="blocked", reasons=issues, risk=classify(op, payload))
    else:
        risk = classify(op, payload)
        rec["risk"] = risk
        if risk == "minor" and auto_minor:
            rec["result"] = apply_op(wid, op, target, payload, source)
            rec["status"] = "applied"
            rec["reviewed_at"] = store.now_iso()
        else:
            rec["status"] = "pending"
    queue = repo.load_proposals(wid)
    queue.append(rec)
    repo.save_proposals(wid, queue)
    return rec


def _find_pending(queue: List[dict], pid: str) -> Tuple[int, dict]:
    for i, rec in enumerate(queue):
        if rec["id"] == pid:
            return i, rec
    raise KeyError(f"提案不存在：{pid}")


def approve(wid: str, pid: str) -> Dict[str, Any]:
    queue = repo.load_proposals(wid)
    idx, rec = _find_pending(queue, pid)
    if rec["status"] != "pending":
        raise ValueError(f"提案当前状态为 {rec['status']}，不能批准")
    state = load_state(wid)
    issues = check(state, rec["op"], rec["target"], rec["payload"])
    rec["reviewed_at"] = store.now_iso()
    if issues:
        rec.update(status="blocked", reasons=issues)
    else:
        rec["result"] = apply_op(wid, rec["op"], rec["target"], rec["payload"], rec["source"])
        rec.update(status="applied", reasons=[])
    queue[idx] = rec
    repo.save_proposals(wid, queue)
    return rec


def reject(wid: str, pid: str, note: str = "") -> Dict[str, Any]:
    queue = repo.load_proposals(wid)
    idx, rec = _find_pending(queue, pid)
    if rec["status"] not in ("pending", "blocked"):
        raise ValueError(f"提案当前状态为 {rec['status']}，不能拒绝")
    rec.update(status="rejected", reviewed_at=store.now_iso(), reasons=rec.get("reasons") or [])
    if note:
        rec["note"] = note
    queue[idx] = rec
    repo.save_proposals(wid, queue)
    return rec
