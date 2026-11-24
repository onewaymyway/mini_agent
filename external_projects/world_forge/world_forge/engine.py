"""world_forge/engine.py — 对外的业务流程：创建世界、补全设定、推进历史、故事与漫画、导出。

约定：模型调用（LLM / 图像）一律在锁外完成；落盘与提案提交在 repo.lock_file 锁内进行。
"""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import generate, gaps, proposals, repo, store, timeline
from .paths import data_root

DEFAULT_SETTINGS = {"auto_minor": True, "years_per_step": 10, "recent_events": 8}


class EngineError(RuntimeError):
    pass


def _with_lock(wid: str):
    return store.locked(repo.lock_file(wid))


# ---------- 世界创建

def create_world(llm, *, concept: str, title: str = "", genre: str = "", tone: str = "") -> Dict[str, Any]:
    concept = (concept or "").strip()
    if not concept:
        raise EngineError("请先输入世界的初始概念")
    draft = generate.charter_draft(llm, concept=concept, title=title, genre=genre, tone=tone)
    wid = store.new_id("wld")
    now = store.now_iso()
    meta = {
        "id": wid, "title": (title or draft.get("title") or "未命名世界").strip(),
        "concept": concept, "genre": draft.get("genre") or genre or "", "tone": draft.get("tone") or tone or "",
        "status": "draft", "created_at": now, "updated_at": now, "charter": None,
        "clock": {"year": 0}, "active_branch": repo.MAIN, "settings": dict(DEFAULT_SETTINGS),
    }
    with _with_lock(wid):
        repo.save_meta(meta)
        repo.save_branches(wid, repo.load_branches(wid))
        for name in ("canon", "cast", "timeline", "proposals", "stories", "exports"):
            (repo.world_dir(wid) / name).mkdir(parents=True, exist_ok=True)
        charter = {k: draft.get(k) for k in ("title", "genre", "tone", "summary", "hard_rules")}
        charter_rec = proposals.submit(wid, "set_charter", payload=charter, source="charter")
        report = _submit_batch(wid, draft, source="charter")
        report["charter"] = charter_rec
    return {"world_id": wid, "charter": charter, "report": report}


def require_charter(wid: str) -> Dict[str, Any]:
    meta = repo.load_meta(wid)
    if not meta.get("charter"):
        raise EngineError("宪章尚未确认：请先在提案队列中批准「set_charter」提案")
    return meta["charter"]


# ---------- 批量提交（模型草稿 → 提案）

def _resolve_map(wid: str) -> Dict[str, str]:
    names: Dict[str, str] = {}
    for c in repo.load_characters(wid):
        names.setdefault(c["name"], c["id"])
    for e in repo.load_entities(wid):
        names[e["name"]] = e["id"]
    return names


def _submit_batch(wid: str, draft: Dict[str, Any], *, source: str) -> Dict[str, Any]:
    ids = _resolve_map(wid)
    records: List[dict] = []
    skipped: List[str] = []

    for e in draft.get("initial_entities") or draft.get("entities") or []:
        if not e.get("name"):
            skipped.append("实体缺少名称")
            continue
        rec = proposals.submit(wid, "create_entity", payload={
            "kind": e.get("kind") or "其它", "name": e["name"], "summary": e.get("summary", ""),
            "attrs": e.get("attrs") or {}}, source=source)
        records.append(rec)
        if rec["status"] == "applied":
            ids[e["name"]] = rec["result"]["id"]

    for c in draft.get("initial_characters") or draft.get("new_characters") or []:
        if not c.get("name"):
            skipped.append("角色缺少名称")
            continue
        payload = {"name": c["name"], "role": c.get("role") or "次要", "motive": c.get("motive", ""),
                   "summary": c.get("summary", ""), "faction_id": None, "attrs": {}}
        fname = c.get("faction_name")
        if fname:
            if fname in ids:
                payload["faction_id"] = ids[fname]
            else:
                payload["attrs"]["faction_hint"] = fname
        rec = proposals.submit(wid, "create_character", payload=payload, source=source)
        records.append(rec)
        if rec["status"] == "applied":
            ids[c["name"]] = rec["result"]["id"]

    for r in draft.get("relations") or []:
        src, dst = ids.get(r.get("src_name")), ids.get(r.get("dst_name"))
        if not src or not dst:
            skipped.append(f"关系 {r.get('src_name')}→{r.get('dst_name')} 的一端尚未落地，已跳过")
            continue
        records.append(proposals.submit(wid, "create_relation", payload={
            "src": src, "dst": dst, "type": r.get("type", "关联"), "note": r.get("note", "")}, source=source))

    for u in draft.get("canon_updates") or []:
        tid = ids.get(u.get("target_name"))
        if not tid:
            skipped.append(f"更新 {u.get('target_name')} 的目标不存在，已跳过")
            continue
        op = "update_character" if u.get("target_type") == "character" else "update_entity"
        records.append(proposals.submit(wid, op, tid, u.get("payload") or {}, source=source))

    for ev in draft.get("events") or []:
        involves = [ids.get(n, n) for n in (ev.get("involves") or [])]
        changes = []
        for ch in ev.get("changes") or []:
            if ch.get("op") not in proposals.CHANGE_OPS:
                continue
            tgt = ids.get(ch.get("target_name"), ch.get("target_name"))
            changes.append({"op": ch["op"], "target": tgt, "payload": ch.get("payload") or {}})
        payload = {"title": ev.get("title", ""), "summary": ev.get("summary", ""), "involves": involves,
                   "caused_by": ev.get("caused_by") or [], "tags": ev.get("tags") or [],
                   "changes": changes, "violates_rules": ev.get("violates_rules") or []}
        records.append(proposals.submit(wid, "add_event", payload=payload, source=source))

    return {"records": records, "skipped": skipped}


def _status_counts(records: List[dict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in records:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out


# ---------- 推进

def build_context(wid: str) -> Dict[str, Any]:
    meta = repo.load_meta(wid)
    charter = meta.get("charter") or {}
    ents = repo.load_entities(wid)
    chars = repo.load_characters(wid)
    ent_by_id = {e["id"]: e["name"] for e in ents}
    ent_by_id.update({c["id"]: c["name"] for c in chars})
    events = repo.load_events(wid, meta["active_branch"])
    recent = events[-int(meta["settings"].get("recent_events", 8)):]
    return {
        "year": meta["clock"]["year"],
        "charter": {k: charter.get(k) for k in ("title", "genre", "tone", "summary", "hard_rules")},
        "entities": [{"name": e["name"], "kind": e["kind"], "summary": e.get("summary", "")[:120]} for e in ents][:60],
        "characters": [{"name": c["name"], "role": c.get("role"), "status": c.get("status"),
                        "faction": ent_by_id.get(c.get("faction_id")) or c.get("attrs", {}).get("faction_hint"),
                        "summary": c.get("summary", "")[:100]} for c in chars][:60],
        "events": [{"year": ev.get("year"), "title": ev["title"], "summary": ev.get("summary", "")[:160],
                    "involves": [ent_by_id.get(i, i) for i in ev.get("involves", [])]} for ev in recent],
    }


def advance(llm, wid: str, steps: int = 1) -> Dict[str, Any]:
    require_charter(wid)
    results = []
    for _ in range(max(1, int(steps))):
        ctx = build_context(wid)
        draft = generate.advance_draft(llm, context=ctx)   # 锁外调用模型
        with _with_lock(wid):
            report = _submit_batch(wid, draft, source="advance")
            meta = repo.load_meta(wid)
            year = meta["clock"]["year"]
            meta["clock"]["year"] = year + int(meta["settings"].get("years_per_step", 10))
            repo.save_meta(meta)
        results.append({"year": year, "counts": _status_counts(report["records"]),
                        "skipped": report["skipped"], "records": report["records"]})
    return {"steps": results, "clock": repo.load_meta(wid)["clock"]}


# ---------- 扩展世界观

MAX_EXPAND_COUNT = 5


def expand_world(llm, wid: str, *, request: str = "", count: int = 3) -> Dict[str, Any]:
    """扩展世界观。request 为空：自动完善；否则按用户要求扩展。

    新内容经提案流程写入：次要内容自动写入，重大内容进入待确认，冲突则被拦截并给出原因。
    返回 records（每条提案及状态）、skipped、source。
    """
    require_charter(wid)
    count = max(1, min(MAX_EXPAND_COUNT, int(count)))
    ctx = build_context(wid)
    ctx["gaps"] = [g["label"] for g in gaps.find_gaps(wid)][:15]
    draft = generate.expansion_draft(llm, context=ctx, request=request, count=count)   # 锁外调用模型
    source = "request" if request.strip() else "expand"
    with _with_lock(wid):
        report = _submit_batch(wid, draft, source=source)
    return {"source": source, "records": report["records"], "skipped": report["skipped"],
            "counts": _status_counts(report["records"])}


def enrich_world(llm, wid: str) -> Dict[str, Any]:
    """一键完善：先补全可处理的缺口，再自动扩展少量内容。"""
    filled = fill_gaps(llm, wid, limit=5)
    expanded = expand_world(llm, wid, count=2)
    related = complete_relations(llm, wid, limit=3)
    return {"fill": filled, "expand": expanded, "relations": related}


# ---------- 实体关系补全

def _linked_pairs(relations: List[dict]) -> set:
    return {frozenset((r["src"], r["dst"])) for r in relations}


def _degrees(ents: List[dict], rels: List[dict]) -> Dict[str, int]:
    deg = {e["id"]: 0 for e in ents}
    for r in rels:
        for eid in (r["src"], r["dst"]):
            if eid in deg:
                deg[eid] += 1
    return deg


def complete_relations(llm, wid: str, *, limit: int = 5, min_degree: int = 2) -> Dict[str, Any]:
    """为关系稀少的实体补全关系。

    目标：关系数少于 min_degree 的实体（关系最少的优先），每次最多处理 limit 个。
    每个目标实体让模型从尚未连接的其它实体中选择对象，最多建立 3 条。
    remaining 只统计仍低于阈值、且仍有可连接对象的实体，即真正还缺关系的数量。
    """
    require_charter(wid)
    min_degree = max(1, int(min_degree))
    ents = repo.load_entities(wid)
    by_id = {e["id"]: e for e in ents}
    ctx = build_context(wid)
    records: List[dict] = []
    skipped: List[str] = []
    processed = 0

    def targets(rels):
        linked = _linked_pairs(rels)
        deg = _degrees(ents, rels)
        out = []
        for e in ents:
            if deg[e["id"]] >= min_degree:
                continue
            opts = [o for o in ents if o["id"] != e["id"] and frozenset((e["id"], o["id"])) not in linked]
            if opts:
                out.append((deg[e["id"]], e, opts))
        out.sort(key=lambda t: (t[0], t[1]["name"]))
        return out

    for _, e, _opts in targets(repo.load_relations(wid))[: max(1, int(limit))]:
        rels_now = repo.load_relations(wid)
        options = [o for o in ents if o["id"] != e["id"]
                   and frozenset((e["id"], o["id"])) not in _linked_pairs(rels_now)][:40]
        if not options:
            continue
        processed += 1
        try:
            draft = generate.relation_draft(llm, context=ctx, target_name=e["name"], target_kind=e["kind"],
                                            options=[o["name"] for o in options])
        except generate.GenerationError as exc:
            skipped.append(f"「{e['name']}」：模型输出不合格（{exc}）")
            continue
        by_name = {o["name"]: o["id"] for o in options}
        got = 0
        with _with_lock(wid):
            current = _linked_pairs(repo.load_relations(wid))
            for r in (draft.get("relations") or [])[:3]:
                oid = by_name.get(r.get("name"))
                if not oid or frozenset((e["id"], oid)) in current:
                    continue
                rec = proposals.submit(wid, "create_relation", payload={
                    "src": e["id"], "dst": oid, "type": r.get("type") or "关联",
                    "note": r.get("note", "")}, source="relation_fill")
                records.append(rec)
                if rec["status"] == "applied":
                    current.add(frozenset((e["id"], oid)))
                    got += 1
        if got == 0:
            skipped.append(f"「{e['name']}」：模型没有给出可用的新关系，下次会再尝试")

    rels_after = repo.load_relations(wid)
    remaining = len(targets(rels_after))
    return {"records": records, "skipped": skipped, "counts": _status_counts(records),
            "processed": processed, "remaining": remaining, "min_degree": min_degree}


# ---------- 按要求修改设定

MAX_MODIFY_CHANGES = 10
ENTITY_FIELDS = {"name", "kind", "summary", "attrs"}
CHARACTER_FIELDS = {"name", "role", "motive", "summary", "attrs", "status", "faction_name"}


def _details(wid: str, ids: List[str]) -> List[dict]:
    """选中对象的完整信息（不截断），供模型修改时参考。"""
    ents = {e["id"]: e for e in repo.load_entities(wid)}
    chars = {c["id"]: c for c in repo.load_characters(wid)}
    rels = repo.load_relations(wid)
    names = {**{k: v["name"] for k, v in ents.items()}, **{k: v["name"] for k, v in chars.items()}}
    out = []
    for i in ids:
        if i in ents:
            e = ents[i]
            related = [f"{names.get(r['dst'] if r['src'] == i else r['src'], '')}（{r['type']}）"
                       for r in rels if i in (r["src"], r["dst"])]
            out.append({"name": e["name"], "kind": e["kind"], "summary": e.get("summary", ""),
                        "attrs": e.get("attrs") or {}, "relations": related})
        elif i in chars:
            c = chars[i]
            out.append({"name": c["name"], "kind": "角色", "role": c.get("role"), "status": c.get("status"),
                        "summary": c.get("summary", ""), "motive": c.get("motive", ""),
                        "attrs": c.get("attrs") or {}})
    return out


def _current_values(wid: str, op: str, tid: str, keys: List[str]) -> Dict[str, Any]:
    item = (repo.find_by_id(repo.load_entities(wid), tid) if op == "update_entity"
            else repo.find_by_id(repo.load_characters(wid), tid)) or {}
    out = {}
    for k in keys:
        if k == "faction_name":
            fid = item.get("faction_id")
            out[k] = next((e["name"] for e in repo.load_entities(wid) if e["id"] == fid), None)
        else:
            out[k] = item.get(k)
    return out


def _clean_payload(wid: str, ttype: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    allowed = ENTITY_FIELDS if ttype == "entity" else CHARACTER_FIELDS
    out = {k: v for k, v in payload.items() if k in allowed and v not in (None, "")}
    if "faction_name" in out:
        fname = out.pop("faction_name")
        fid = next((e["id"] for e in repo.load_entities(wid) if e["name"] == fname), None)
        if fid:
            out["faction_id"] = fid
    if "attrs" in out:
        out["attrs"] = proposals.coerce_attrs(out["attrs"])
    return out


def modify_world(llm, wid: str, request: str, target_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """根据用户要求修改设定。target_ids 为空时由模型自动判断需要修改的对象。

    修改经提案流程写入：次要字段自动写入，重大字段（名称、类别、角色状态、权力归属等）进入待确认。
    返回每条修改的 before / after 与状态，便于核对。
    """
    require_charter(wid)
    request = (request or "").strip()
    if not request:
        raise EngineError("请描述要修改的内容")
    ctx = build_context(wid)
    if target_ids:
        ctx["targets"] = _details(wid, target_ids)
    draft = generate.modification_draft(llm, context=ctx, request=request, scoped=bool(target_ids))  # 锁外

    ents = repo.load_entities(wid)
    chars = repo.load_characters(wid)
    index: Dict[str, List[tuple]] = {}
    for e in ents:
        index.setdefault(e["name"], []).append(("entity", e["id"]))
    for c in chars:
        index.setdefault(c["name"], []).append(("character", c["id"]))
    allowed_ids = set(target_ids) if target_ids else None

    results: List[dict] = []
    skipped: List[dict] = []
    for ch in (draft.get("changes") or [])[:MAX_MODIFY_CHANGES]:
        name = ch.get("target_name")
        cands = index.get(name, [])
        ref = next((c for c in cands if c[0] == ch.get("target_type")), cands[0] if cands else None)
        if ref is None:
            skipped.append({"target": name, "reason": "世界中找不到该对象"})
            continue
        ttype, tid = ref
        if allowed_ids is not None and tid not in allowed_ids:
            skipped.append({"target": name, "reason": "不在所选对象范围内，已忽略"})
            continue
        payload = _clean_payload(wid, ttype, ch.get("payload") or {})
        if not payload:
            skipped.append({"target": name, "reason": "没有可修改的有效字段"})
            continue
        op = "update_entity" if ttype == "entity" else "update_character"
        with _with_lock(wid):
            before = _current_values(wid, op, tid, list(payload.keys()))
            rec = proposals.submit(wid, op, tid, payload, source="modify", note=ch.get("reason", ""))
        results.append({"target": name, "reason": ch.get("reason", ""), "before": before,
                        "after": payload, "proposal": rec})
    return {"results": results, "skipped": skipped, "counts": _status_counts([r["proposal"] for r in results])}


# ---------- 世界观简介

def get_overview(wid: str) -> Optional[Dict[str, Any]]:
    """返回已保存的世界观简介：{"text", "generated_at", "year"}；没有则 None。"""
    path = repo.world_dir(wid) / "overview.md"
    if not path.is_file():
        return None
    info = repo.load_meta(wid).get("overview") or {}
    return {"text": path.read_text(encoding="utf-8"), **info}


def generate_overview(llm, wid: str) -> Dict[str, Any]:
    """基于当前宪章、设定、角色与近期事件生成世界观背景介绍，保存为 overview.md（覆盖旧版本）。"""
    require_charter(wid)
    text = generate.overview_draft(llm, context=build_context(wid))   # 锁外调用模型
    with _with_lock(wid):
        (repo.world_dir(wid) / "overview.md").write_text(text + "\n", encoding="utf-8")
        meta = repo.load_meta(wid)
        meta["overview"] = {"generated_at": store.now_iso(), "year": meta["clock"]["year"]}
        repo.save_meta(meta)
    return get_overview(wid)


# ---------- 删除世界

def delete_world(wid: str) -> Dict[str, Any]:
    """永久删除整个世界目录（设定、角色、时间线、分支、提案、故事、导出）。不可恢复。

    不在锁内删除：锁文件位于世界目录内，Windows 下打开中的文件无法删除。
    调用方应先确认用户意图（看板要求输入世界 id）。
    """
    meta = repo.load_meta(wid)   # 不存在则抛 WorldNotFound
    d = repo.world_dir(wid)
    shutil.rmtree(d)
    return {"deleted": wid, "title": meta.get("title", "")}


# ---------- 设定补全（交互与自动共用）

FIXABLE_GAP_KINDS = {"missing_summary", "unlinked_faction_hint", "faction_without_relation", "isolated_entity"}


def fill_gaps(llm, wid: str, *, gap_ids: Optional[List[str]] = None, limit: int = 5) -> Dict[str, Any]:
    """补全设定缺口。只对可补全的缺口计数，limit 作用于可补全项，不被无法处理的项占用。

    返回 filled（已提交的提案）、skipped（跳过的缺口及原因）、remaining（补全后仍存在的缺口数）。
    """
    all_gaps = gaps.find_gaps(wid)
    targets = [g for g in all_gaps if (not gap_ids or g["id"] in gap_ids)]
    fixable = [g for g in targets if g["kind"] in FIXABLE_GAP_KINDS]
    skipped = [{"gap": g, "reason": "需要手动处理（如角色所属势力不存在，请在角色页修改归属）"}
               for g in targets if g["kind"] not in FIXABLE_GAP_KINDS]
    ctx_base = build_context(wid)
    filled: List[dict] = []
    done = 0
    for g in fixable:
        if done >= limit:
            break
        done += 1
        try:
            if g["kind"] == "missing_summary":
                target = _find_target(wid, g["target"])
                if target is None:
                    skipped.append({"gap": g, "reason": "目标已不存在"})
                    continue
                draft = generate.gap_fill_draft(llm, context=ctx_base, target_name=target["name"], gap_label=g["label"])
                op = "update_character" if g["entity_type"] == "character" else "update_entity"
                with _with_lock(wid):
                    rec = proposals.submit(wid, op, g["target"], {"summary": draft["summary"],
                                                                  "attrs": draft.get("attrs") or {}},
                                           source="gap_fill")
                filled.append({"gap": g, "proposals": [rec]})

            elif g["kind"] == "unlinked_faction_hint":
                ch = repo.find_by_id(repo.load_characters(wid), g["target"])
                hint = (ch or {}).get("attrs", {}).get("faction_hint")
                fid = next((e["id"] for e in repo.load_entities(wid) if e["name"] == hint), None)
                if not fid:
                    skipped.append({"gap": g, "reason": f"世界中还没有名为「{hint}」的实体，请先创建它"})
                    continue
                with _with_lock(wid):
                    rec = proposals.submit(wid, "update_character", g["target"], {"faction_id": fid},
                                           source="gap_fill")
                filled.append({"gap": g, "proposals": [rec]})

            else:  # faction_without_relation / isolated_entity：让模型建议关系
                target = _find_target(wid, g["target"])
                if target is None:
                    skipped.append({"gap": g, "reason": "目标已不存在"})
                    continue
                others = [e for e in repo.load_entities(wid) if e["id"] != g["target"]]
                if not others:
                    skipped.append({"gap": g, "reason": "世界中没有其它实体可供建立关系"})
                    continue
                draft = generate.relation_draft(llm, context=ctx_base, target_name=target["name"],
                                                target_kind=target["kind"],
                                                options=[e["name"] for e in others])
                by_name = {e["name"]: e["id"] for e in others}
                recs = []
                with _with_lock(wid):
                    for r in draft.get("relations") or []:
                        other_id = by_name.get(r.get("name"))
                        if not other_id:
                            continue
                        recs.append(proposals.submit(wid, "create_relation", payload={
                            "src": g["target"], "dst": other_id, "type": r.get("type") or "关联",
                            "note": r.get("note", "")}, source="gap_fill"))
                if recs and any(x["status"] == "applied" for x in recs):
                    filled.append({"gap": g, "proposals": recs})
                else:
                    skipped.append({"gap": g, "reason": "模型未给出可用的关系"})
        except generate.GenerationError as exc:
            skipped.append({"gap": g, "reason": f"模型输出不合格：{exc}"})

    remaining = len(gaps.find_gaps(wid))
    return {"filled": filled, "skipped": skipped, "remaining": remaining,
            "fixable_total": len(fixable)}


def _find_target(wid: str, tid: str) -> Optional[dict]:
    return repo.find_by_id(repo.load_entities(wid), tid) or repo.find_by_id(repo.load_characters(wid), tid)


# ---------- 角色立绘

def make_portrait(llm, provider, wid: str, cid: str) -> Dict[str, Any]:
    chars = repo.load_characters(wid)
    ch = repo.find_by_id(chars, cid)
    if ch is None:
        raise EngineError(f"角色不存在：{cid}")
    meta = repo.load_meta(wid)
    draft = generate.portrait_draft(llm, context={
        "name": ch["name"], "summary": ch.get("summary", ""), "role": ch.get("role"),
        "tone": meta.get("tone", ""), "genre": meta.get("genre", "")})
    path = repo.portrait_path(wid, cid)
    res = provider.generate(f"{draft['visual_en']}, {meta.get('tone', '')} style", path, ratio="3:4")
    with _with_lock(wid):
        chars = repo.load_characters(wid)
        ch = repo.find_by_id(chars, cid)
        if res.ok:
            ch.setdefault("attrs", {})["portrait"] = str(path.relative_to(repo.world_dir(wid)))
            repo.save_characters(wid, chars)
    return {"ok": res.ok, "error": res.error, "path": str(path) if res.ok else None,
            "stub": bool(getattr(provider, "is_stub", False))}


# ---------- 故事

def select_events(wid: str, *, event_ids: Optional[List[str]] = None,
                  year_from: Optional[int] = None, year_to: Optional[int] = None) -> List[dict]:
    meta = repo.load_meta(wid)
    evs = repo.load_events(wid, meta["active_branch"])
    if event_ids:
        evs = [e for e in evs if e["id"] in event_ids]
    if year_from is not None:
        evs = [e for e in evs if e.get("year", 0) >= year_from]
    if year_to is not None:
        evs = [e for e in evs if e.get("year", 0) <= year_to]
    return evs


def _new_story(wid: str, kind: str, title: str, extra: Dict[str, Any]) -> str:
    sid = store.new_id("sty")
    meta = {"id": sid, "kind": kind, "title": title, "created_at": store.now_iso(),
            "branch": repo.load_meta(wid)["active_branch"], **extra}
    (repo.story_dir(wid, sid)).mkdir(parents=True, exist_ok=True)
    repo.save_story_meta(wid, sid, meta)
    return sid


def _event_context(wid: str, events: List[dict]) -> List[dict]:
    ent = {e["id"]: e["name"] for e in repo.load_entities(wid)}
    ent.update({c["id"]: c["name"] for c in repo.load_characters(wid)})
    return [{"year": e.get("year"), "title": e["title"], "summary": e.get("summary", ""),
             "involves": [ent.get(i, i) for i in e.get("involves", [])]} for e in events]


def make_arc(llm, wid: str, *, event_ids: Optional[List[str]] = None,
             year_from: Optional[int] = None, year_to: Optional[int] = None) -> str:
    events = select_events(wid, event_ids=event_ids, year_from=year_from, year_to=year_to)
    if not events:
        raise EngineError("没有可用于成文的事件，请选择事件或调整年份范围")
    ctx = {"charter": require_charter(wid), "events": _event_context(wid, events),
           "characters": [c for c in repo.load_characters(wid)
                          if any(c["name"] in e["involves"] for e in _event_context(wid, events))][:6]}
    ctx["characters"] = [{"name": c["name"], "role": c.get("role"), "summary": c.get("summary", "")[:100]}
                         for c in ctx["characters"]]
    arc = generate.arc_draft(llm, context=ctx)
    with _with_lock(wid):
        sid = _new_story(wid, "arc", arc["title"], {"source_event_ids": [e["id"] for e in events],
                                                    "status": "draft"})
        store.write_json(repo.story_dir(wid, sid) / "arc.json", arc)
    return sid


def write_short(llm, wid: str, *, arc_sid: Optional[str] = None, brief: str = "",
                event_ids: Optional[List[str]] = None, form: str = "novel") -> str:
    """成文短篇。可基于小故事线（arc_sid），也可直接由一段事件描述（brief）+ 指定事件创作。"""
    charter = require_charter(wid)
    ctx: Dict[str, Any] = {"charter": charter, "brief": brief.strip()}
    title = "短篇"
    source_events: List[str] = []
    if arc_sid:
        arc = store.read_json(repo.story_dir(wid, arc_sid) / "arc.json")
        if not arc:
            raise EngineError(f"小故事线不存在或未生成：{arc_sid}")
        ctx["arc"] = arc
        title = arc["title"]
        source_events = load_story_meta_safe(wid, arc_sid).get("source_event_ids", [])
    if event_ids:
        evs = select_events(wid, event_ids=event_ids)
        ctx["events"] = _event_context(wid, evs)
        source_events = list(dict.fromkeys(source_events + [e["id"] for e in evs]))
        if not arc_sid and not ctx["brief"]:
            title = evs[0]["title"]
    if not ctx.get("arc") and not ctx.get("brief") and not ctx.get("events"):
        raise EngineError("请提供小故事线、事件或一段创作描述")
    text = generate.short_draft(llm, context=ctx, form=form)
    with _with_lock(wid):
        sid = _new_story(wid, "short", title, {"parent": arc_sid, "source_event_ids": source_events,
                                               "form": form, "status": "draft"})
        (repo.story_dir(wid, sid) / "story.md").write_text(text, encoding="utf-8")
    return sid


def load_story_meta_safe(wid: str, sid: str) -> Dict[str, Any]:
    try:
        return repo.load_story_meta(wid, sid)
    except Exception:  # noqa: BLE001
        return {}


def make_storyboard(llm, wid: str, sid: str) -> Dict[str, Any]:
    require_charter(wid)
    d = repo.story_dir(wid, sid)
    if not (d / "meta.json").is_file():
        raise EngineError(f"故事不存在：{sid}")
    ctx: Dict[str, Any] = {"charter": repo.load_meta(wid).get("charter") or {}}
    arc = store.read_json(d / "arc.json")
    if arc:
        ctx.update(title=arc["title"], acts=arc.get("acts", []), ending=arc.get("ending", ""))
    if (d / "story.md").is_file():
        ctx["text"] = (d / "story.md").read_text(encoding="utf-8")[:6000]
    if not arc and "text" not in ctx:
        raise EngineError("该故事没有可用于分镜的内容")
    ctx["characters"] = [{"name": c["name"], "summary": c.get("summary", "")[:80]}
                         for c in repo.load_characters(wid)][:12]
    board = generate.storyboard_draft(llm, context=ctx)
    with _with_lock(wid):
        store.write_json(d / "storyboard.json", board)
        meta = repo.load_story_meta(wid, sid)
        meta["has_storyboard"] = True
        repo.save_story_meta(wid, sid, meta)
    return board


def render_comic(provider, wid: str, sid: str) -> Dict[str, Any]:
    d = repo.story_dir(wid, sid)
    board = store.read_json(d / "storyboard.json")
    if not board:
        raise EngineError("请先生成分镜")
    chars = repo.load_characters(wid)
    provider_name = getattr(provider, "name", "unknown")
    stub = bool(getattr(provider, "is_stub", False))
    ok = fail = 0
    for pg in board["pages"]:
        for i, pn in enumerate(pg["panels"], start=1):
            refs = []
            for nm in pn.get("characters") or []:
                ch = next((c for c in chars if c["name"] == nm), None)
                if ch:
                    pth = repo.portrait_path(wid, ch["id"])
                    if pth.is_file():
                        refs.append(pth)
            rel = f"panels/p{pg['no']:02d}_{i:02d}.png"
            res = provider.generate(
                f"{pn['visual_en']}, comic panel illustration, consistent character design, no text, no speech bubbles",
                d / rel, ratio="3:4", reference_images=refs[:3])
            pn["image"] = rel if res.ok else None
            pn["image_error"] = None if res.ok else res.error
            ok += int(res.ok)
            fail += int(not res.ok)
    board["provider"], board["stub"] = provider_name, stub
    store.write_json(d / "storyboard.json", board)
    comic = _render_html(board, title=load_story_meta_safe(wid, sid).get("title", "漫画"))
    (d / "comic.html").write_text(comic, encoding="utf-8")
    with _with_lock(wid):
        meta = repo.load_story_meta(wid, sid)
        meta["has_comic"] = True
        repo.save_story_meta(wid, sid, meta)
    return {"ok": ok, "failed": fail, "stub": stub, "provider": provider_name,
            "comic": str(d / "comic.html")}


def _render_html(board: Dict[str, Any], title: str) -> str:
    parts = [f"<!doctype html><html lang='zh'><meta charset='utf-8'><title>{html.escape(title)}</title>",
             "<style>body{background:#111;color:#eee;font-family:sans-serif;max-width:720px;margin:auto}"
             ".panel{position:relative;margin:12px 0}.panel img{width:100%;display:block}"
             ".nar{background:#222c;padding:6px 10px;font-size:14px}"
             ".bub{background:#fff;color:#111;border-radius:12px;padding:6px 10px;margin:6px 0;font-size:14px}"
             ".missing{background:#333;padding:40px;text-align:center}</style>",
             f"<h1>{html.escape(title)}</h1>"]
    for pg in board["pages"]:
        parts.append(f"<h2>第 {pg['no']} 页</h2>")
        for pn in pg["panels"]:
            if pn.get("image"):
                parts.append(f"<div class='panel'><img src='{html.escape(pn['image'])}'>")
            else:
                parts.append(f"<div class='panel missing'>（画面生成失败：{html.escape(pn.get('image_error') or '')}）")
            if pn.get("narration"):
                parts.append(f"<div class='nar'>{html.escape(pn['narration'])}</div>")
            for dl in pn.get("dialogue") or []:
                parts.append(f"<div class='bub'><b>{html.escape(dl.get('speaker', ''))}</b>："
                             f"{html.escape(dl.get('text', ''))}</div>")
            parts.append("</div>")
    if board.get("stub"):
        parts.append("<p>注：本页使用占位图，未调用真实图像模型。</p>")
    parts.append("</html>")
    return "".join(parts)


# ---------- 导出给 novel_writer

def export_bundle(wid: str, sid: str) -> Path:
    d = repo.story_dir(wid, sid)
    meta = repo.load_story_meta(wid, sid)
    out = data_root() / wid / "exports" / sid
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    world = repo.load_meta(wid)
    charter = world.get("charter") or {}
    arc = store.read_json(d / "arc.json") or {}
    text_path = d / "story.md"

    setting_lines = [f"# {world['title']} · 设定", "", f"题材：{charter.get('genre', '')}　基调：{charter.get('tone', '')}",
                     "", "## 世界规则"]
    setting_lines += [f"- {r}" for r in charter.get("hard_rules") or []]
    setting_lines += ["", "## 设定条目"]
    setting_lines += [f"- **{e['name']}**（{e['kind']}）：{e.get('summary', '')}" for e in repo.load_entities(wid)]
    (out / "setting.md").write_text("\n".join(setting_lines) + "\n", encoding="utf-8")

    outline = [f"# {arc.get('title', meta['title'])}", "", f"前提：{arc.get('premise', '')}", ""]
    for i, act in enumerate(arc.get("acts") or [], start=1):
        outline += [f"## {act.get('title', f'第{i}幕')}", act.get("summary", ""), ""]
    if arc.get("ending"):
        outline += ["## 结局", arc["ending"], ""]
    (out / "outline.md").write_text("\n".join(outline), encoding="utf-8")

    names = set(arc.get("protagonist_names") or [])
    chars = [c for c in repo.load_characters(wid) if not names or c["name"] in names or c["role"] in ("主角", "重要配角")]
    (out / "characters.json").write_text(json.dumps(chars, ensure_ascii=False, indent=2), encoding="utf-8")
    if text_path.is_file():
        shutil.copy(text_path, out / "story.md")
    (out / "bundle.json").write_text(json.dumps({"world_id": wid, "story_id": sid, "kind": meta["kind"],
                                                 "title": meta["title"], "files": sorted(p.name for p in out.iterdir())},
                                                ensure_ascii=False, indent=2), encoding="utf-8")
    return out
