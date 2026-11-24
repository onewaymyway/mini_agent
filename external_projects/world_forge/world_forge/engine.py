"""world_forge/engine.py — 对外的业务流程：创建世界、补全设定、推进历史、故事与漫画、导出。

约定：模型调用（LLM / 图像）一律在锁外完成；落盘与提案提交在 repo.lock_file 锁内进行。
"""

from __future__ import annotations

import html
import json
import os
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import cancel, comic_layout, comic_render, generate, gaps, perspective, proposals, repo, store, timeline
from .calendar import (clock_minutes, format_time, step_label, step_minutes, validate_step, year_of)
from .paths import data_root

STATUS_LABEL = {"applied": "已写入", "pending": "待确认", "blocked": "已拦截", "rejected": "已拒绝"}
DEFAULT_SETTINGS = {"auto_minor": True, "step_amount": 10, "step_unit": "year", "recent_events": 8}


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
        "clock": {"minutes": 0}, "active_branch": repo.MAIN, "settings": dict(DEFAULT_SETTINGS),
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


_OP_LABEL = {"create_entity": "新增实体", "create_character": "新增角色", "create_relation": "新增关系",
             "update_entity": "更新实体", "update_character": "更新角色", "add_event": "新增事件",
             "set_charter": "宪章"}
_STATUS_LEVEL = {"applied": "ok", "pending": "warn", "blocked": "warn", "rejected": "info"}


def _log_writes(wid: str, log, records: List[dict], skipped: List[str]) -> None:
    if log is None:
        return
    names = {e["id"]: e["name"] for e in repo.load_entities(wid)}
    names.update({c["id"]: c["name"] for c in repo.load_characters(wid)})
    for r in records:
        p = r["payload"]
        if r["op"] == "create_relation":
            head = f"{names.get(p.get('src'), p.get('src'))} —{p.get('type', '')}→ {names.get(p.get('dst'), p.get('dst'))}"
        else:
            head = p.get("title") or p.get("name") or names.get(r.get("target"), r.get("target") or "")
        status_text = STATUS_LABEL.get(r["status"], r["status"])
        msg = f"{_OP_LABEL.get(r['op'], r['op'])}「{head}」→ {status_text}"
        if r.get("reasons"):
            msg += "：" + "；".join(r["reasons"])
        log.log("write", msg, level=_STATUS_LEVEL.get(r["status"], "info"), op=r["op"], status=r["status"])
    for sk in skipped:
        log.log("write", f"跳过：{sk}", level="warn")


def _submit_batch(wid: str, draft: Dict[str, Any], *, source: str, log=None) -> Dict[str, Any]:
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

    _log_writes(wid, log, records, skipped)
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
        "now": format_time(clock_minutes(meta)),
        "charter": {k: charter.get(k) for k in ("title", "genre", "tone", "summary", "hard_rules")},
        "entities": [{"name": e["name"], "kind": e["kind"], "summary": e.get("summary", "")[:120]} for e in ents][:60],
        "characters": [{"name": c["name"], "role": c.get("role"), "status": c.get("status"),
                        "faction": ent_by_id.get(c.get("faction_id")) or c.get("attrs", {}).get("faction_hint"),
                        "summary": c.get("summary", "")[:100]} for c in chars][:60],
        "events": [{"time": ev.get("t"), "title": ev["title"], "summary": ev.get("summary", "")[:160],
                    "involves": [ent_by_id.get(i, i) for i in ev.get("involves", [])]} for ev in recent],
    }


def advance(llm, wid: str, steps: int = 1, log=None, *, amount: Optional[int] = None,
            unit: Optional[str] = None) -> Dict[str, Any]:
    """推进历史 steps 步。每步时长默认取世界设置；amount/unit 可为本次推进临时指定（不改设置）。

    每个阶段写入运行日志（见 runlog.RunLog），失败时记录并重新抛出。
    """
    from .runlog import LoggedLLM, RunLog

    require_charter(wid)
    log = log or RunLog(wid, "advance")
    steps = max(1, int(steps))
    started = time.monotonic()
    meta = repo.load_meta(wid)
    branch_name = repo.load_branches(wid).get(meta["active_branch"], {}).get("name", meta["active_branch"])
    if amount is not None or unit is not None:
        a, u = validate_step(amount if amount is not None else meta["settings"]["step_amount"],
                             unit or meta["settings"]["step_unit"])
        step_settings = {"step_amount": a, "step_unit": u}
    else:
        step_settings = meta["settings"]
    step_min = step_minutes(step_settings)
    step_text = step_label(step_settings["step_amount"], step_settings["step_unit"])
    log.log("start", f"开始推进：{steps} 步，每步 {step_text}", steps=steps, step_minutes=step_min)
    log.log("start", f"起点：{format_time(clock_minutes(meta))}，活动分支「{branch_name}」，"
                     f"运行 ID {log.run_id}")
    results: List[dict] = []
    cancelled = False
    try:
        for i in range(steps):
            cancel.checkpoint()   # 安全点：每步开始前检查停止信号
            step_t = time.monotonic()
            log.log("step", f"第 {i + 1}/{steps} 步：收集世界状态")
            ctx = build_context(wid)
            alive = sum(1 for c in ctx["characters"] if c.get("status") == "alive")
            log.log("context", f"上下文：当前 {format_time(clock_minutes(repo.load_meta(wid)))}，实体 {len(ctx['entities'])}、角色 {len(ctx['characters'])}"
                               f"（存活 {alive}）、近期事件 {len(ctx['events'])}、"
                               f"硬规则 {len((ctx.get('charter') or {}).get('hard_rules') or [])} 条")
            draft = generate.advance_draft(LoggedLLM(llm, log), context=ctx, log=log)   # 锁外调用模型
            log.log("parse", f"模型输出解析通过：新角色 {len(draft.get('new_characters') or [])}、"
                             f"新实体 {len(draft.get('entities') or [])}、关系 {len(draft.get('relations') or [])}、"
                             f"设定更新 {len(draft.get('canon_updates') or [])}、"
                             f"事件 {len(draft.get('events') or [])}", level="ok")
            with _with_lock(wid):
                report = _submit_batch(wid, draft, source="advance", log=log)
                meta = repo.load_meta(wid)
                before = clock_minutes(meta)
                meta["clock"]["minutes"] = before + step_min
                repo.save_meta(meta)
            log.log("clock", f"时钟推进：{format_time(before)} → {format_time(before + step_min)}")
            counts = _status_counts(report["records"])
            summary = "、".join(f"{STATUS_LABEL.get(k, k)} {v}" for k, v in counts.items()) or "无写入"
            log.log("step", f"第 {i + 1} 步完成：{summary}，用时 {time.monotonic() - step_t:.1f} 秒", level="ok")
            results.append({"time": format_time(before), "year": year_of(before), "counts": counts, "skipped": report["skipped"],
                            "records": report["records"]})
    except cancel.Cancelled:
        cancelled = True
        log.log("cancel", f"已停止：完成 {len(results)}/{steps} 步，已写入的内容保留", level="warn")
    except Exception as exc:
        log.log("error", f"推进中断：{type(exc).__name__}：{exc}", level="error")
        raise
    total: Dict[str, int] = {}
    for r in results:
        for k, v in r["counts"].items():
            total[k] = total.get(k, 0) + v
    log.log("done", f"推进结束：共 {steps} 步，用时 {time.monotonic() - started:.1f} 秒，"
                    f"当前 {format_time(clock_minutes(repo.load_meta(wid)))}", level="ok", counts=total)
    return {"steps": results, "clock": repo.load_meta(wid)["clock"], "run_id": log.run_id,
            "log_path": str(log.path), "log": log.entries, "cancelled": cancelled}


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
        cancel.checkpoint()
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
    """返回已保存的世界观简介：{"text", "generated_at", "minutes", "time"}；没有则 None。"""
    path = repo.world_dir(wid) / "overview.md"
    if not path.is_file():
        return None
    info = repo.load_meta(wid).get("overview") or {}
    return {"text": path.read_text(encoding="utf-8"), **info}


def generate_overview(llm, wid: str, log=None) -> Dict[str, Any]:
    """基于当前宪章、设定、角色与近期事件生成世界观背景介绍。旧版本保留在 history/ 中。"""
    log = _log_or_new(log, wid, "overview")
    try:
        log.log("start", "生成世界观简介")
        require_charter(wid)
        cancel.checkpoint()
        text = generate.overview_draft(LoggedLLM(llm, log), context=build_context(wid))   # 锁外调用模型
        with _with_lock(wid):
            path = repo.world_dir(wid) / "overview.md"
            _archive(path, log)
            path.write_text(text + "\n", encoding="utf-8")
            meta = repo.load_meta(wid)
            meta["overview"] = {"generated_at": store.now_iso(), "minutes": clock_minutes(meta),
                                "time": format_time(clock_minutes(meta))}
            repo.save_meta(meta)
        log.log("save", f"已保存世界观简介（{len(text)} 字）", level="ok")
        log.log("done", "完成", level="ok")
        return get_overview(wid)
    except Exception as exc:
        _fail(log, exc)
        raise


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
        cancel.checkpoint()
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

def _portrait_one(llm, provider, wid: str, cid: str, log) -> Dict[str, Any]:
    """为单个角色生成人设图（核心逻辑，单个与批量共用）。旧图归档后覆盖。"""
    chars = repo.load_characters(wid)
    ch = repo.find_by_id(chars, cid)
    if ch is None:
        raise EngineError(f"角色不存在：{cid}")
    meta = repo.load_meta(wid)
    draft = generate.portrait_draft(LoggedLLM(llm, log), context={
        "name": ch["name"], "summary": ch.get("summary", ""), "role": ch.get("role"),
        "tone": meta.get("tone", ""), "genre": meta.get("genre", "")})
    path = repo.portrait_path(wid, cid)
    _archive(path, log)
    res = provider.generate(f"{draft['visual_en']}, {meta.get('tone', '')} style", path, ratio="3:4")
    with _with_lock(wid):
        chars = repo.load_characters(wid)
        ch = repo.find_by_id(chars, cid)
        ch.setdefault("attrs", {})["visual_en"] = draft["visual_en"]   # 外观锚点，漫画各格保持一致
        if res.ok:
            ch["attrs"]["portrait"] = str(path.relative_to(repo.world_dir(wid)))
        repo.save_characters(wid, chars)
    return {"ok": res.ok, "error": res.error, "path": str(path) if res.ok else None,
            "stub": bool(getattr(provider, "is_stub", False)), "name": ch["name"]}


def make_portrait(llm, provider, wid: str, cid: str, log=None) -> Dict[str, Any]:
    log = _log_or_new(log, wid, "portrait")
    try:
        log.log("start", f"生成人设图（角色 {cid}）")
        cancel.checkpoint()
        out = _portrait_one(llm, provider, wid, cid, log)
        if out["ok"]:
            log.log("done", f"完成：「{out['name']}」的人设图", level="ok")
        else:
            log.log("done", f"失败：{out['error']}", level="warn")
        return out
    except Exception as exc:
        _fail(log, exc)
        raise


def portrait_targets(wid: str, *, overwrite: bool = False) -> List[dict]:
    """需要生成人设图的角色：存活角色；默认只包含还没有人设图的。"""
    out = []
    for c in repo.load_characters(wid):
        if c.get("status") == "dead":
            continue
        if overwrite or repo.resolve_portrait(wid, c) is None:
            out.append(c)
    return out


def make_all_portraits(llm, provider, wid: str, *, overwrite: bool = False, log=None) -> Dict[str, Any]:
    """一键生成所有人设图。逐个角色生成，每个角色之间检查停止信号；停止后已完成的保留。"""
    log = _log_or_new(log, wid, "portrait")
    cancelled = False
    results: List[dict] = []
    try:
        candidates = [c for c in repo.load_characters(wid) if c.get("status") != "dead"]
        targets = portrait_targets(wid, overwrite=overwrite)
        skipped = len(candidates) - len(targets)
        scope = "全部存活角色（覆盖已有）" if overwrite else "尚未有人设图的角色"
        log.log("start", f"一键生成人设图：{scope}，共 {len(targets)} 个"
                         + (f"，跳过 {skipped} 个已有人设图的角色" if skipped else ""),
                targets=len(targets), skipped=skipped,
                stub=bool(getattr(provider, "is_stub", False)))
        if getattr(provider, "is_stub", False):
            log.log("start", "注意：当前为占位图，不会调用图像模型", level="warn")
        try:
            for i, c in enumerate(targets, start=1):
                cancel.checkpoint()
                log.log("panel", f"[{i}/{len(targets)}] 「{c['name']}」（{c.get('role')}）：生成中……")
                t = time.monotonic()
                out = _portrait_one(llm, provider, wid, c["id"], log)
                dur = time.monotonic() - t
                results.append({"id": c["id"], "name": c["name"], "ok": out["ok"], "error": out["error"]})
                if out["ok"]:
                    log.log("panel", f"[{i}/{len(targets)}] 完成：「{c['name']}」，用时 {dur:.1f} 秒", level="ok")
                else:
                    log.log("panel", f"[{i}/{len(targets)}] 失败：「{c['name']}」：{out['error']}", level="warn")
        except cancel.Cancelled:
            cancelled = True
            log.log("cancel", f"已停止：已处理 {len(results)}/{len(targets)} 个，已完成的人设图保留", level="warn")
        ok = sum(1 for r in results if r["ok"])
        failed = sum(1 for r in results if not r["ok"])
        level = "warn" if (failed or cancelled) else "ok"
        log.log("done", f"{'已停止' if cancelled else '完成'}：成功 {ok} 个，失败 {failed} 个，"
                        f"跳过 {skipped} 个", level=level, ok=ok, failed=failed)
        return {"ok": ok, "failed": failed, "skipped": skipped, "total": len(targets),
                "results": results, "cancelled": cancelled,
                "stub": bool(getattr(provider, "is_stub", False))}
    except Exception as exc:
        _fail(log, exc)
        raise


# ---------- 旧版本归档（重新生成前保留）

def _archive(path: Path, log=None) -> None:
    """把现有文件复制到同目录的 history/ 下（带时间戳），再允许覆盖。文件不存在则什么也不做。"""
    if not path.is_file():
        return
    hist = path.parent / "history"
    hist.mkdir(parents=True, exist_ok=True)
    dst = hist / f"{path.stem}.{time.strftime('%Y%m%d-%H%M%S')}-{store.new_id('v')[2:]}{path.suffix}"
    shutil.copy2(path, dst)
    if log is not None:
        log.log("save", f"已保留旧版本：{path.name} → history/{dst.name}")


# ---------- 故事

from .runlog import LoggedLLM, RunLog, format_entry  # noqa: E402  (运行日志，见 runlog.py)


def _log_or_new(log, wid: str, kind: str):
    return log if log is not None else RunLog(wid, kind)


def _fail(log, exc: Exception) -> None:
    log.log("error", f"中断：{type(exc).__name__}：{exc}", level="error")


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
    return [{"time": e.get("t"), "title": e["title"], "summary": e.get("summary", ""),
             "involves": [ent.get(i, i) for i in e.get("involves", [])]} for e in events]


def _focus_of(wid: str, subject_id: Optional[str]) -> Optional[Dict[str, str]]:
    """主体视角：返回 {"id","name","kind"}；主体不存在则报错。"""
    if not subject_id:
        return None
    names = {e["id"]: (e["name"], e["kind"]) for e in repo.load_entities(wid)}
    names.update({c["id"]: (c["name"], "角色") for c in repo.load_characters(wid)})
    if subject_id not in names:
        raise EngineError(f"主体不存在：{subject_id}")
    return {"id": subject_id, "name": names[subject_id][0], "kind": names[subject_id][1]}


def _subject_event_ids(wid: str, subject_id: str) -> List[str]:
    """某个主体参与（或被变更）的事件 id，按时间顺序。没有则报错。"""
    lanes = perspective.subject_lanes(wid)
    lane = next((x for x in lanes["subjects"] if x["id"] == subject_id), None)
    if lane is None or not lane["events"]:
        raise EngineError("该主体还没有参与任何事件，无法以它的时间线生成故事")
    return [ev["id"] for _, ev in lane["events"]]


def _generate_arc(llm, wid: str, events: List[dict], log, focus: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    ev_ctx = _event_context(wid, events)
    involved = {n for e in ev_ctx for n in e["involves"]}
    chars = [{"name": c["name"], "role": c.get("role"), "summary": c.get("summary", "")[:100]}
             for c in repo.load_characters(wid) if c["name"] in involved][:6]
    ctx = {"charter": require_charter(wid), "events": ev_ctx, "characters": chars}
    if focus:
        ctx["focus"] = focus["name"]
        log.log("context", f"主体视角：以「{focus['name']}」（{focus['kind']}）的经历展开")
    log.log("context", f"上下文：宪章 1 份、事件 {len(ev_ctx)} 个、出场角色 {len(chars)} 名")
    cancel.checkpoint()
    arc = generate.arc_draft(LoggedLLM(llm, log), context=ctx, log=log)
    log.log("parse", f"故事线结构通过：「{arc['title']}」，{len(arc.get('acts') or [])} 幕", level="ok")
    return arc


def make_arc(llm, wid: str, *, event_ids: Optional[List[str]] = None,
             year_from: Optional[int] = None, year_to: Optional[int] = None,
             subject_id: Optional[str] = None, log=None) -> str:
    """生成小故事线。subject_id 有值时，素材取该主体的时间线，并以它为视角（主角）。"""
    log = _log_or_new(log, wid, "arc")
    try:
        focus = _focus_of(wid, subject_id)
        if focus and not event_ids:
            event_ids = _subject_event_ids(wid, focus["id"])
        log.log("start", "生成小故事线" + (f"（主体视角：{focus['name']}）" if focus else ""),
                event_ids=len(event_ids or []), year_from=year_from, year_to=year_to)
        events = select_events(wid, event_ids=event_ids, year_from=year_from, year_to=year_to)
        if not events:
            raise EngineError("没有可用于成文的事件，请选择事件或调整年份范围")
        log.log("context", f"选中 {len(events)} 个事件：" + "；".join(e["title"] for e in events[:6])
                + ("…" if len(events) > 6 else ""))
        arc = _generate_arc(llm, wid, events, log, focus=focus)
        with _with_lock(wid):
            extra = {"source_event_ids": [e["id"] for e in events], "status": "draft"}
            if focus:
                extra["focus"] = focus
            sid = _new_story(wid, "arc", arc["title"], extra)
            store.write_json(repo.story_dir(wid, sid) / "arc.json", arc)
        log.log("save", f"已保存小故事线 {sid}", level="ok")
        log.log("done", "完成", level="ok", story=sid)
        return sid
    except Exception as exc:
        _fail(log, exc)
        raise


def regenerate_arc(llm, wid: str, sid: str, log=None) -> str:
    """用同一批事件重新生成小故事线（同一个故事 id）。旧版本保留在 history/。"""
    log = _log_or_new(log, wid, "arc")
    try:
        meta = repo.load_story_meta(wid, sid)
        if meta.get("kind") != "arc":
            raise EngineError("只有小故事线可以按此方式重新生成")
        log.log("start", f"重新生成小故事线（故事 {sid}）")
        events = select_events(wid, event_ids=meta.get("source_event_ids") or [])
        if not events:
            raise EngineError("原始事件已不存在，无法重新生成")
        log.log("context", f"沿用原来的 {len(events)} 个事件")
        arc = _generate_arc(llm, wid, events, log, focus=meta.get("focus"))
        with _with_lock(wid):
            path = repo.story_dir(wid, sid) / "arc.json"
            _archive(path, log)
            store.write_json(path, arc)
            meta = repo.load_story_meta(wid, sid)
            meta.update(title=arc["title"], regenerated_at=store.now_iso())
            repo.save_story_meta(wid, sid, meta)
        log.log("save", f"已更新小故事线「{arc['title']}」", level="ok")
        log.log("done", "完成", level="ok", story=sid)
        return sid
    except Exception as exc:
        _fail(log, exc)
        raise


def _generate_short(llm, wid: str, *, arc_sid: Optional[str], brief: str,
                    event_ids: Optional[List[str]], form: str, log,
                    focus: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    charter = require_charter(wid)
    ctx: Dict[str, Any] = {"charter": charter, "brief": brief.strip()}
    if focus:
        ctx["focus"] = focus["name"]
        log.log("context", f"主体视角：以「{focus['name']}」（{focus['kind']}）叙述")
    title = "短篇"
    source_events: List[str] = []
    if arc_sid:
        arc = store.read_json(repo.story_dir(wid, arc_sid) / "arc.json")
        if not arc:
            raise EngineError(f"小故事线不存在或未生成：{arc_sid}")
        ctx["arc"] = arc
        title = arc["title"]
        source_events = load_story_meta_safe(wid, arc_sid).get("source_event_ids", [])
        log.log("context", f"读取小故事线「{arc['title']}」：{len(arc.get('acts') or [])} 幕")
    if event_ids:
        evs = select_events(wid, event_ids=event_ids)
        ctx["events"] = _event_context(wid, evs)
        source_events = list(dict.fromkeys(source_events + [e["id"] for e in evs]))
        if not arc_sid and not ctx["brief"]:
            title = evs[0]["title"]
        log.log("context", f"附带 {len(evs)} 个事件作为素材")
    if not ctx.get("arc") and not ctx.get("brief") and not ctx.get("events"):
        raise EngineError("请提供小故事线、事件或一段创作描述")
    cancel.checkpoint()
    text = generate.short_draft(LoggedLLM(llm, log), context=ctx, form=form)
    log.log("parse", f"正文生成完成：{len(text)} 字", level="ok")
    return {"title": title, "source_events": source_events, "text": text}


def write_short(llm, wid: str, *, arc_sid: Optional[str] = None, brief: str = "",
                event_ids: Optional[List[str]] = None, form: str = "novel",
                subject_id: Optional[str] = None, log=None) -> str:
    """成文短篇。可基于小故事线（arc_sid），也可直接由一段描述（brief）+ 事件创作。
    subject_id 有值时，素材取该主体的时间线并以它为视角；基于小故事线时自动沿用其视角。"""
    log = _log_or_new(log, wid, "short")
    try:
        focus = _focus_of(wid, subject_id)
        if focus is None and arc_sid:
            focus = load_story_meta_safe(wid, arc_sid).get("focus")
        if focus and subject_id and not event_ids:
            event_ids = _subject_event_ids(wid, focus["id"])
        mode = "基于小故事线" if arc_sid else ("直接创作" if brief.strip() else "基于事件")
        log.log("start", f"成文短篇（{mode}，形式：{'短篇小说' if form == 'novel' else '故事说明'}）",
                mode=mode, brief_chars=len(brief.strip()), event_ids=len(event_ids or []))
        out = _generate_short(llm, wid, arc_sid=arc_sid, brief=brief, event_ids=event_ids, form=form, log=log,
                              focus=focus)
        with _with_lock(wid):
            extra = {"parent": arc_sid, "source_event_ids": out["source_events"], "form": form, "status": "draft",
                     "request": {"arc_sid": arc_sid, "brief": brief.strip(), "event_ids": list(event_ids or []),
                                 "form": form, "subject_id": subject_id}}
            if focus:
                extra["focus"] = focus
            sid = _new_story(wid, "short", out["title"], extra)
            (repo.story_dir(wid, sid) / "story.md").write_text(out["text"], encoding="utf-8")
        log.log("save", f"已保存短篇 {sid}「{out['title']}」", level="ok")
        log.log("done", "完成", level="ok", story=sid)
        return sid
    except Exception as exc:
        _fail(log, exc)
        raise


def regenerate_short(llm, wid: str, sid: str, log=None) -> str:
    """用创建时的原始输入重新成文（同一个故事 id）。旧正文保留在 history/。"""
    log = _log_or_new(log, wid, "short")
    try:
        meta = repo.load_story_meta(wid, sid)
        req = meta.get("request")
        if not req:
            raise EngineError("这篇短篇创建于旧版本，缺少原始输入，无法重新生成（可删除后重新创作）")
        log.log("start", f"重新成文（故事 {sid}，沿用原始输入）")
        focus = _focus_of(wid, req.get("subject_id")) if req.get("subject_id") else meta.get("focus")
        out = _generate_short(llm, wid, arc_sid=req.get("arc_sid"), brief=req.get("brief", ""),
                              event_ids=req.get("event_ids"), form=req.get("form", "novel"), log=log,
                              focus=focus)
        with _with_lock(wid):
            d = repo.story_dir(wid, sid)
            _archive(d / "story.md", log)
            (d / "story.md").write_text(out["text"], encoding="utf-8")
            meta = repo.load_story_meta(wid, sid)
            meta["regenerated_at"] = store.now_iso()
            repo.save_story_meta(wid, sid, meta)
        log.log("save", "已更新短篇正文", level="ok")
        log.log("done", "完成", level="ok", story=sid)
        return sid
    except Exception as exc:
        _fail(log, exc)
        raise


def load_story_meta_safe(wid: str, sid: str) -> Dict[str, Any]:
    try:
        return repo.load_story_meta(wid, sid)
    except Exception:  # noqa: BLE001
        return {}


def _ensure_anchors(llm, wid: str, board: Dict[str, Any], log) -> None:
    """为分镜中出场、但还没有外观描述的角色补全英文外观锚点（漫画各格依此保持人物一致）。"""
    names = {n for pg in board["pages"] for pn in pg["panels"] for n in (pn.get("characters") or [])}
    meta = repo.load_meta(wid)
    for ch in repo.load_characters(wid):
        if ch["name"] not in names or (ch.get("attrs") or {}).get("visual_en"):
            continue
        cancel.checkpoint()
        try:
            draft = generate.portrait_draft(LoggedLLM(llm, log), context={
                "name": ch["name"], "summary": ch.get("summary", ""), "role": ch.get("role"),
                "tone": meta.get("tone", ""), "genre": meta.get("genre", "")})
        except generate.GenerationError as exc:
            log.log("parse", f"角色「{ch['name']}」外观描述生成失败：{exc}", level="warn")
            continue
        with _with_lock(wid):
            cur = repo.load_characters(wid)
            item = repo.find_by_id(cur, ch["id"])
            item.setdefault("attrs", {})["visual_en"] = draft["visual_en"]
            repo.save_characters(wid, cur)
        log.log("parse", f"已为角色「{ch['name']}」固定外观描述，供各格保持一致", level="ok")


def make_storyboard(llm, wid: str, sid: str, log=None, *, pages: Any = None, reading: Optional[str] = None,
                    color: Optional[str] = None) -> Dict[str, Any]:
    """两步生成：先节拍表（每页的叙事作用、画面焦点、页末钩子），再按节拍生成分镜格子。

    pages：固定页数（整数）或 "auto"；缺省沿用故事上次的设置，再缺省为 6 页。
    reading / color：阅读方向与色彩模式，记入分镜，漫画渲染时沿用。
    """
    log = _log_or_new(log, wid, "board")
    try:
        log.log("start", f"生成分镜（故事 {sid}）", story=sid)
        require_charter(wid)
        d = repo.story_dir(wid, sid)
        if not (d / "meta.json").is_file():
            raise EngineError(f"故事不存在：{sid}")
        smeta = repo.load_story_meta(wid, sid)
        stored = smeta.get("comic_opts") or {}
        opts = comic_layout.normalize_opts(pages=pages if pages is not None else stored.get("pages"),
                                           reading=reading or stored.get("reading"),
                                           color=color or stored.get("color"))
        fixed = comic_layout.expected_pages(opts)
        log.log("context", f"漫画设置：{'自动页数（按故事长度）' if fixed is None else f'{fixed} 页'}，"
                           f"{comic_layout.READING_LABEL[opts['reading']]}，{comic_layout.COLOR_LABEL[opts['color']]}")
        ctx: Dict[str, Any] = {"charter": repo.load_meta(wid).get("charter") or {}}
        arc = store.read_json(d / "arc.json")
        if arc:
            ctx.update(title=arc["title"], acts=arc.get("acts", []), ending=arc.get("ending", ""))
            log.log("context", f"素材：小故事线「{arc['title']}」，{len(arc.get('acts') or [])} 幕")
        if (d / "story.md").is_file():
            ctx["text"] = (d / "story.md").read_text(encoding="utf-8")[:6000]
            log.log("context", f"素材：短篇正文 {len(ctx['text'])} 字（最多取前 6000 字）")
        if not arc and "text" not in ctx:
            raise EngineError("该故事没有可用于分镜的内容")
        ctx["characters"] = [{"name": c["name"], "summary": c.get("summary", "")[:80]}
                             for c in repo.load_characters(wid)][:12]
        ctx["locations"] = [{"name": e["name"], "summary": e.get("summary", "")[:80]}
                            for e in repo.load_entities(wid) if e.get("kind") == "地理"][:8]
        own_focus = smeta.get("focus")
        if own_focus is None and smeta.get("parent"):
            own_focus = load_story_meta_safe(wid, smeta["parent"]).get("focus")
        if own_focus:
            ctx["focus"] = own_focus["name"]
            log.log("context", f"主体视角：分镜以「{own_focus['name']}」为中心")
        cancel.checkpoint()
        beats_res = generate.beats_draft(LoggedLLM(llm, log), context=ctx, pages=fixed, log=log)
        beats = beats_res["pages"]
        log.log("parse", f"节拍表通过：{len(beats)} 页", level="ok")
        for b in beats:
            log.log("parse", f"第 {b['no']} 页｜{b['purpose']}｜焦点：{b['focus']}｜钩子：{b['hook'] or '（结尾）'}")
        ctx["beats"] = [{"no": b["no"], "purpose": b["purpose"], "focus": b["focus"], "hook": b["hook"],
                         "palette": b.get("palette", "")} for b in beats]
        cancel.checkpoint()
        board = generate.storyboard_draft(LoggedLLM(llm, log), context=ctx, log=log, pages=len(beats))
        for pg, b in zip(board["pages"], beats):
            pg["beat"], pg["hook"], pg["palette"] = b["purpose"], b["hook"], b.get("palette", "")
        n_panels = sum(len(p["panels"]) for p in board["pages"])
        log.log("parse", f"分镜结构通过：{len(board['pages'])} 页，共 {n_panels} 格", level="ok")
        for pg in board["pages"]:
            log.log("parse", f"第 {pg['no']} 页：{len(pg['panels'])} 格，"
                             f"出场角色 {sorted({c for p in pg['panels'] for c in (p.get('characters') or [])}) or '无'}")
        board["version"] = 2
        board["opts"] = opts
        _ensure_anchors(llm, wid, board, log)
        with _with_lock(wid):
            _archive(d / "beats.json", log)
            store.write_json(d / "beats.json", beats_res)
            _archive(d / "storyboard.json", log)
            store.write_json(d / "storyboard.json", board)
            meta = repo.load_story_meta(wid, sid)
            meta["has_storyboard"] = True
            meta["comic_opts"] = opts
            repo.save_story_meta(wid, sid, meta)
        log.log("save", "已保存节拍表 beats.json 与分镜脚本 storyboard.json（漫画图需要重新生成）", level="ok")
        log.log("done", "完成", level="ok", panels=n_panels)
        return board
    except Exception as exc:
        _fail(log, exc)
        raise


COMIC_STYLE = ("comic book illustration, clean ink linework, cel shading, consistent color palette, "
               "same art style and character design across all panels")
COMIC_STYLE_BW = ("black and white manga, clean ink linework, screentone shading, monochrome, "
                  "same art style and character design across all panels")
SHOT_EN = {"远景": "wide establishing shot", "中景": "medium shot", "近景": "close-up shot",
           "特写": "extreme close-up"}
MAX_REFS = 3


def _panel_ratio(span: int) -> str:
    return "16:9" if span == 2 else "1:1"


def _env_budget_default() -> int:
    try:
        return int(os.environ.get("WORLD_FORGE_IMAGE_BUDGET", str(comic_layout.IMAGE_BUDGET_DEFAULT)))
    except ValueError:
        return comic_layout.IMAGE_BUDGET_DEFAULT


def comic_limits(wid: str) -> Dict[str, int]:
    """本世界的漫画上限：图像调用上限与候选数上限。世界设置 > 环境变量（仅图像调用上限）> 默认值。"""
    stored = repo.load_meta(wid).get("comic_limits") or {}
    budget = stored.get("image_budget")
    cand = stored.get("candidate_max")
    return {
        "image_budget": int(budget) if budget is not None else _env_budget_default(),
        "candidate_max": int(cand) if cand is not None else comic_layout.CANDIDATE_MAX,
    }


def set_comic_limits(wid: str, *, image_budget: Optional[int] = None,
                     candidate_max: Optional[int] = None) -> Dict[str, int]:
    """保存本世界的漫画上限。超出允许范围直接报错，不写入任何内容。None 表示不改该项。"""
    lo, hi = comic_layout.IMAGE_BUDGET_RANGE
    if image_budget is not None and not (lo <= int(image_budget) <= hi):
        raise EngineError(f"图像调用上限须在 {lo}–{hi} 之间：{image_budget}")
    clo, chi = comic_layout.CANDIDATE_RANGE
    if candidate_max is not None and not (clo <= int(candidate_max) <= chi):
        raise EngineError(f"候选数上限须在 {clo}–{chi} 之间：{candidate_max}")
    with _with_lock(wid):
        meta = repo.load_meta(wid)
        stored = dict(meta.get("comic_limits") or {})
        if image_budget is not None:
            stored["image_budget"] = int(image_budget)
        if candidate_max is not None:
            stored["candidate_max"] = int(candidate_max)
        meta["comic_limits"] = stored
        repo.save_meta(meta)
    return comic_limits(wid)


class _Budget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def take(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


def _comic_style(meta: Dict[str, Any], color: str) -> str:
    """统一风格提示词：基础风格 + 世界类型/基调 + 风格指南（黑白模式不带配色）。"""
    sg = meta.get("style_guide") or {}
    parts = [COMIC_STYLE_BW if color == "bw" else COMIC_STYLE]
    if meta.get("genre"):
        parts.append(f"{meta['genre']} setting")
    if meta.get("tone"):
        parts.append(f"{meta['tone']} mood")
    for key in ("line", "lighting") + (() if color == "bw" else ("palette",)):
        if sg.get(key):
            parts.append(sg[key])
    return ", ".join(parts)


def _panel_refs(wid: str, pn: Dict[str, Any], prev_img: Optional[Path], present: List[dict],
                ent_by_name: Dict[str, dict]) -> Tuple[List[Path], List[str]]:
    """参考图优先级：上一格 → 主角的表情图（无则立绘）→ 所在地点的场景图 → 其他出场角色立绘；最多 MAX_REFS 张。"""
    refs: List[Path] = []
    names: List[str] = []
    if prev_img is not None:
        refs.append(prev_img)
        names.append("上一格")
    if present:
        main = present[0]
        key = comic_layout.expression_key(pn.get("emotion"))
        sheet = repo.resolve_sheet(wid, main["id"], key) if key else None
        pth = sheet or repo.resolve_portrait(wid, main)
        if pth is not None and len(refs) < MAX_REFS:
            refs.append(pth)
            names.append(f"{main['name']}{'表情图' if sheet else '立绘'}")
    loc = ent_by_name.get(pn.get("location") or "")
    if loc is not None:
        sc = repo.resolve_scene(wid, loc["id"])
        if sc is not None and len(refs) < MAX_REFS:
            refs.append(sc)
            names.append(f"场景「{loc['name']}」")
    for ch in present[1:]:
        pth = repo.resolve_portrait(wid, ch)
        if pth is not None and len(refs) < MAX_REFS:
            refs.append(pth)
            names.append(f"{ch['name']}立绘")
    return refs, names


def _assemble_outputs(wid: str, sid: str, d: Path, board: Dict[str, Any], opts: Dict[str, Any], log) -> Dict[str, Any]:
    """拼装成品：服务端排字导出 PNG 页与 zip，并生成网页预览 comic.html。"""
    fonts = comic_render.Fonts()
    if fonts.path is None:
        log.log("assemble", "未找到中文字体，PNG 中的中文可能显示为方框（可设置环境变量 WORLD_FORGE_FONT 指定字体）",
                level="warn")
    log.log("assemble", "服务端排字，导出 PNG 页（pages/page_NN.png），并打包为 comic_pages.zip")
    plans = [comic_layout.plan_page(pg, opts["reading"]) for pg in board["pages"]]
    pages_dir = d / "pages"
    if pages_dir.exists():
        shutil.rmtree(pages_dir)
    pngs = comic_render.write_pages(plans, base_dir=d, out_dir=pages_dir, color=opts["color"], fonts=fonts)
    log.log("assemble", f"已导出 {len(pngs)} 页 PNG", level="ok")
    log.log("assemble", "拼装网页预览 comic.html（图片嵌入文件，可独立打开）")
    comic = render_comic_html(board, title=load_story_meta_safe(wid, sid).get("title", "漫画"), base_dir=d,
                              reading=opts["reading"])
    (d / "comic.html").write_text(comic, encoding="utf-8")
    page_rel = [p.relative_to(d).as_posix() for p in pngs]
    (d / "reader.html").write_text(render_reader_html(
        load_story_meta_safe(wid, sid).get("title", "漫画"), page_rel, opts["reading"]), encoding="utf-8")
    with _with_lock(wid):
        meta_s = repo.load_story_meta(wid, sid)
        meta_s["has_comic"] = True
        meta_s["comic_opts"] = {**(meta_s.get("comic_opts") or {}), "reading": opts["reading"], "color": opts["color"]}
        repo.save_story_meta(wid, sid, meta_s)
    return {"pages": [str(p) for p in pngs], "pages_zip": str(d / "comic_pages.zip"), "comic": str(d / "comic.html")}


def render_comic(provider, wid: str, sid: str, log=None,
                 only: Optional[List[tuple]] = None, *, reading: Optional[str] = None,
                 color: Optional[str] = None, candidates: Optional[int] = None) -> Dict[str, Any]:
    """逐格生成分镜图并拼装漫画页。only 为 [(页号, 格号), ...] 时只重做这些格，其余保留原图。

    连贯性：统一风格（含风格指南）；每页的配色；主角表情图/立绘、地点场景图与上一格作为参考。
    候选：candidates=N（1–3）时每格生成 N 张，默认采用第一张成功的，其余保存在 candidates 中供人工选用。
    调用上限：单次操作的图像调用总数不超过本世界设置的上限（见 comic_limits）；超限则停止，已完成的保留。
    停止时：已完成的格子保存，并拼装部分漫画，返回 cancelled=True。
    """
    log = _log_or_new(log, wid, "comic")
    cancelled = False
    exhausted = False
    try:
        d = repo.story_dir(wid, sid)
        board = store.read_json(d / "storyboard.json")
        if not board:
            raise EngineError("请先生成分镜")
        meta = repo.load_meta(wid)
        chars = repo.load_characters(wid)
        by_name = {c["name"]: c for c in chars}
        ent_by_name = {e["name"]: e for e in repo.load_entities(wid)}
        stored = board.get("opts") or {}
        opts = comic_layout.normalize_opts(pages=stored.get("pages"), reading=reading or stored.get("reading"),
                                           color=color or stored.get("color"))
        board["opts"] = opts
        limits = comic_limits(wid)
        n_cand = max(1, min(int(candidates or comic_layout.CANDIDATE_DEFAULT), limits["candidate_max"]))
        budget = _Budget(limits["image_budget"])
        provider_name = getattr(provider, "name", "unknown")
        stub = bool(getattr(provider, "is_stub", False))
        total = sum(len(p["panels"]) for p in board["pages"])
        targets = {tuple(x) for x in only} if only else None
        n_todo = total if targets is None else sum(
            1 for pg in board["pages"] for i in range(1, len(pg["panels"]) + 1) if (pg["no"], i) in targets)
        style = _comic_style(meta, opts["color"])
        scope = "全部格子" if targets is None else f"指定的 {n_todo} 格"
        log.log("start", f"生成漫画图（{scope}）：{len(board['pages'])} 页，共 {total} 格，图像来源：{provider_name}"
                         + (f"，每格 {n_cand} 张候选" if n_cand > 1 else "")
                         + ("（占位图，未调用真实模型）" if stub else ""), provider=provider_name, stub=stub)
        if stub:
            log.log("start", "注意：当前为占位图，不会调用图像模型", level="warn")
        log.log("start", f"版式：{comic_layout.READING_LABEL[opts['reading']]}，"
                         f"{comic_layout.COLOR_LABEL[opts['color']]}；图像调用上限 {budget.limit} 次")
        ok = fail = skipped = 0
        idx = 0
        done_now = 0
        prev_img = None
        try:
            for pg in board["pages"]:
                palette = pg.get("palette") or ""
                for i, pn in enumerate(pg["panels"], start=1):
                    idx += 1
                    rel = f"panels/p{pg['no']:02d}_{i:02d}.png"
                    existing = d / rel
                    if targets is not None and (pg["no"], i) not in targets:
                        if existing.is_file():
                            prev_img = existing
                        skipped += 1
                        continue
                    cancel.checkpoint()   # 安全点：每格生成前检查停止信号
                    names = pn.get("characters") or []
                    present = [by_name[n] for n in names if n in by_name]
                    for n in names:
                        if n not in by_name:
                            log.log("panel", f"第 {pg['no']} 页第 {i} 格：角色「{n}」不在世界中，无外观锚点",
                                    level="warn")
                    refs, ref_names = _panel_refs(wid, pn, prev_img, present, ent_by_name)
                    anchors = "; ".join(f"{ch['name']}: {ch['attrs']['visual_en']}"
                                        for ch in present if (ch.get("attrs") or {}).get("visual_en"))
                    shot = pn.get("shot") or "中景"
                    span = int(pn.get("span") or 1)
                    prompt = (f"{style}. {SHOT_EN.get(shot, 'medium shot')}. {pn['visual_en']}."
                              + (f" Characters: {anchors}." if anchors else "")
                              + (f" Color palette: {palette}." if palette and opts["color"] == "color" else "")
                              + " No text, no speech bubbles, no lettering.")
                    done_now += 1
                    log.log("panel", f"[{done_now}/{n_todo}] 第 {pg['no']} 页第 {i} 格（{shot}，"
                                     f"{'整行' if span == 2 else '半行'}）：参考 {('、'.join(ref_names)) or '无'}，"
                                     f"外观锚点 {len(anchors.split('; ')) if anchors else 0} 个，生成中……")
                    if existing.is_file():
                        _archive(existing, log)
                    t = time.monotonic()
                    results = []
                    for k in range(1, n_cand + 1):
                        if not budget.take():
                            exhausted = True
                            break
                        path = existing if n_cand == 1 else d / f"panels/p{pg['no']:02d}_{i:02d}_c{k}.png"
                        if n_cand > 1 and path.is_file():
                            _archive(path, log)
                        res = provider.generate(prompt, path, ratio=_panel_ratio(span),
                                                reference_images=refs[:MAX_REFS])
                        results.append((k, path, res))
                    dur = time.monotonic() - t
                    if not results:
                        break   # 调用上限已用尽：本格及之后的格子保持原样
                    if n_cand == 1:
                        res = results[0][2]
                        pn["image"] = rel if res.ok else None
                        pn["image_error"] = None if res.ok else res.error
                        pn.pop("candidates", None)
                        pn.pop("chosen", None)
                    else:
                        good = [(k, p) for k, p, r in results if r.ok]
                        if good:
                            shutil.copy2(good[0][1], existing)   # 默认采用第一张成功的
                            pn["image"], pn["image_error"] = rel, None
                            pn["candidates"] = [str((p).relative_to(d)) for _, p in good]
                            pn["chosen"] = 1
                        else:
                            pn["image"], pn["image_error"] = None, results[-1][2].error
                            pn.pop("candidates", None)
                            pn.pop("chosen", None)
                    if pn.get("image"):
                        ok += 1
                        prev_img = existing
                        log.log("panel", f"[{done_now}/{n_todo}] 完成：{rel}"
                                         + (f"（候选 {len(pn['candidates'])} 张）" if n_cand > 1 else "")
                                         + f"，用时 {dur:.1f} 秒", level="ok")
                    else:
                        fail += 1
                        log.log("panel", f"[{done_now}/{n_todo}] 失败：{pn['image_error']}，用时 {dur:.1f} 秒",
                                level="warn")
                    if exhausted:
                        break
                if exhausted:
                    break
        except cancel.Cancelled:
            cancelled = True
            log.log("cancel", f"已停止：已处理 {done_now}/{n_todo} 格，已完成的格子保留", level="warn")
        if exhausted:
            log.log("cancel", f"已达本次图像调用上限（{budget.limit} 次），剩余格子未生成。"
                              "可在「上限设置」中调大，或分次生成", level="warn")
        board["provider"], board["stub"] = provider_name, stub
        store.write_json(d / "storyboard.json", board)
        out = _assemble_outputs(wid, sid, d, board, opts, log)
        level = "warn" if (fail or cancelled or exhausted) else "ok"
        log.log("done", f"{'已停止' if cancelled else '完成'}：成功 {ok} 格，失败 {fail} 格，"
                        f"图像调用 {budget.used} 次"
                        + (f"，保留 {skipped} 格原图" if skipped else ""), level=level, ok=ok, failed=fail)
        return {"ok": ok, "failed": fail, "skipped": skipped, "stub": stub, "provider": provider_name,
                **out, "reading": opts["reading"], "color": opts["color"], "image_calls": budget.used,
                "budget_exhausted": exhausted, "cancelled": cancelled}
    except Exception as exc:
        _fail(log, exc)
        raise


def choose_candidate(wid: str, sid: str, page_no: int, panel_no: int, k: int, log=None) -> Dict[str, Any]:
    """人工选用第 k 张候选：复制为该格的正式图，并重新拼装成品（不调用图像模型）。"""
    log = _log_or_new(log, wid, "comic")
    try:
        d = repo.story_dir(wid, sid)
        with _with_lock(wid):
            board = store.read_json(d / "storyboard.json")
            if not board:
                raise EngineError("请先生成分镜")
            pn = next((p for pg in board["pages"] if pg["no"] == page_no
                       for j, p in enumerate(pg["panels"], start=1) if j == panel_no), None)
            if pn is None:
                raise EngineError(f"没有第 {page_no} 页第 {panel_no} 格")
            cands = pn.get("candidates") or []
            if not (1 <= k <= len(cands)):
                raise EngineError(f"该格没有第 {k} 张候选（共 {len(cands)} 张）")
            rel = f"panels/p{page_no:02d}_{panel_no:02d}.png"
            shutil.copy2(d / cands[k - 1], d / rel)
            pn["image"], pn["image_error"], pn["chosen"] = rel, None, k
            store.write_json(d / "storyboard.json", board)
        opts = board.get("opts") or comic_layout.normalize_opts()
        log.log("save", f"第 {page_no} 页第 {panel_no} 格改用第 {k} 张候选，重新拼装成品", level="ok")
        out = _assemble_outputs(wid, sid, d, board, opts, log)
        log.log("done", "完成", level="ok")
        return {"chosen": k, **out}
    except Exception as exc:
        _fail(log, exc)
        raise


def render_reader_html(title: str, page_paths: List[str], reading: str) -> str:
    """网页阅读器：引用同目录 pages/ 下的 PNG 成品页（整个故事目录需一起移动）。翻页、方向键、连续滚动。"""
    pages_js = json.dumps(page_paths, ensure_ascii=False)
    rtl_js = "true" if reading == "rtl" else "false"
    t = html.escape(title)
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>{t}</title>
<style>
body{{margin:0;background:#1d1d1d;color:#eee;font-family:"Microsoft YaHei","PingFang SC",sans-serif}}
header{{display:flex;justify-content:space-between;align-items:center;padding:8px 14px;background:#111}}
main{{display:flex;flex-direction:column;align-items:center;padding:12px}}
#stage img{{display:block;max-width:100%;max-height:calc(100vh - 110px);box-shadow:0 0 18px #000}}
#strip img{{display:block;width:min(100%,900px);margin:0 auto 12px;box-shadow:0 0 12px #000}}
#strip{{display:none}}
nav{{display:flex;gap:8px;padding:10px}}
button{{background:#333;color:#eee;border:1px solid #555;padding:6px 14px;cursor:pointer;border-radius:4px}}
button:disabled{{opacity:.4;cursor:default}}
</style></head><body>
<header><strong>{t}</strong><span id="pos"></span>
<label><input type="checkbox" id="scroll"> 连续滚动</label></header>
<main>
<div id="stage"><img id="pg" alt=""></div>
<div id="strip"></div>
<nav><button id="prev">上一页</button><button id="next">下一页</button></nav>
</main>
<script>
const pages = {pages_js};
const rtl = {rtl_js};
let i = 0;
const pg = document.getElementById('pg'), pos = document.getElementById('pos');
const stage = document.getElementById('stage'), strip = document.getElementById('strip');
const scroll = document.getElementById('scroll');
function show() {{
  i = Math.max(0, Math.min(pages.length - 1, i));
  pg.src = pages[i];
  pos.textContent = (i + 1) + ' / ' + pages.length + (rtl ? '（从右到左）' : '（从左到右）');
  document.getElementById('prev').disabled = i === 0;
  document.getElementById('next').disabled = i === pages.length - 1;
}}
function buildStrip() {{
  strip.innerHTML = '';
  pages.forEach(function (src) {{ const im = document.createElement('img'); im.src = src; strip.appendChild(im); }});
}}
function apply() {{
  const on = scroll.checked;
  stage.style.display = on ? 'none' : 'block';
  strip.style.display = on ? 'block' : 'none';
  document.querySelector('nav').style.display = on ? 'none' : 'flex';
  if (on) buildStrip(); else show();
}}
document.getElementById('prev').onclick = function () {{ i--; show(); }};
document.getElementById('next').onclick = function () {{ i++; show(); }};
scroll.onchange = apply;
document.addEventListener('keydown', function (e) {{
  if (scroll.checked) return;
  const forward = rtl ? e.key === 'ArrowLeft' : e.key === 'ArrowRight';
  const back = rtl ? e.key === 'ArrowRight' : e.key === 'ArrowLeft';
  if (forward) {{ i++; show(); }}
  if (back) {{ i--; show(); }}
}});
show();
</script></body></html>"""


def _load_pages(d: Path) -> List[Path]:
    pages_dir = d / "pages"
    pngs = sorted(pages_dir.glob("page_*.png")) if pages_dir.is_dir() else []
    if not pngs:
        raise EngineError("还没有成品页，请先生成漫画图")
    return pngs


def export_comic_files(wid: str, sid: str, log=None) -> Dict[str, Any]:
    """导出 CBZ（漫画压缩包，含 ComicInfo.xml）与条漫长图（PNG，成品页竖向拼接）。仅使用已生成的成品页，不调用模型。"""
    log = _log_or_new(log, wid, "comic")
    try:
        d = repo.story_dir(wid, sid)
        pngs = _load_pages(d)
        board = store.read_json(d / "storyboard.json") or {}
        reading = (board.get("opts") or {}).get("reading", "ltr")
        title = load_story_meta_safe(wid, sid).get("title", "漫画")
        log.log("start", f"导出 CBZ 与条漫长图（{len(pngs)} 页）")
        cbz = d / "comic.cbz"
        manga = "YesAndRightToLeft" if reading == "rtl" else "No"
        info = (f"<?xml version='1.0' encoding='utf-8'?><ComicInfo><Title>{html.escape(title)}</Title>"
                f"<Manga>{manga}</Manga></ComicInfo>")
        with zipfile.ZipFile(cbz, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in pngs:
                zf.write(p, arcname=p.name)
            zf.writestr("ComicInfo.xml", info)
        log.log("save", f"已写入 comic.cbz（阅读方向标记：{manga}）", level="ok")
        from PIL import Image

        imgs = []
        for p in pngs:
            with Image.open(p) as im:
                imgs.append(im.convert("RGB").copy())
        width = max(im.width for im in imgs)
        height = sum(im.height for im in imgs)
        strip = Image.new("RGB", (width, height), "white")
        y = 0
        for im in imgs:
            strip.paste(im, (0, y))
            y += im.height
        strip_path = d / "comic_strip.png"
        strip.save(strip_path, format="PNG", optimize=True)
        log.log("save", f"已写入 comic_strip.png（{width}×{height} 像素）", level="ok")
        log.log("done", "完成", level="ok")
        return {"cbz": str(cbz), "strip": str(strip_path), "reader": str(d / "reader.html"),
                "pages": len(pngs), "reading": reading}
    except Exception as exc:
        _fail(log, exc)
        raise


def edit_panel_text(wid: str, sid: str, page_no: int, panel_no: int, *, narration: Optional[str] = None,
                    sfx: Optional[str] = None, dialogue: Optional[List[str]] = None, log=None) -> Dict[str, Any]:
    """修改一格的旁白、音效与对白文字（不改说话人与位置），重新拼装成品。不调用图像模型。"""
    log = _log_or_new(log, wid, "comic")
    try:
        d = repo.story_dir(wid, sid)
        with _with_lock(wid):
            board = store.read_json(d / "storyboard.json")
            if not board:
                raise EngineError("请先生成分镜")
            pn = next((p for pg in board["pages"] if pg["no"] == page_no
                       for j, p in enumerate(pg["panels"], start=1) if j == panel_no), None)
            if pn is None:
                raise EngineError(f"没有第 {page_no} 页第 {panel_no} 格")
            if narration is not None:
                pn["narration"] = str(narration).strip()
            if sfx is not None:
                pn["sfx"] = str(sfx).strip()
            if dialogue is not None:
                existing = pn.get("dialogue") or []
                if len(dialogue) != len(existing):
                    raise EngineError(f"对白数量须为 {len(existing)} 句（当前 {len(dialogue)} 句）")
                for dl, text in zip(existing, dialogue):
                    text = str(text).strip()
                    if not text:
                        raise EngineError("对白不能为空")
                    if len(text) > generate.DIALOGUE_MAX_CHARS:
                        raise EngineError(f"对白过长（{len(text)} 字，上限 {generate.DIALOGUE_MAX_CHARS} 字）")
                    dl["text"] = text
            pn["edited"] = True
            store.write_json(d / "storyboard.json", board)
        opts = board.get("opts") or comic_layout.normalize_opts()
        log.log("save", f"已修改第 {page_no} 页第 {panel_no} 格的文字，重新拼装成品（不调用图像模型）", level="ok")
        out = _assemble_outputs(wid, sid, d, board, opts, log)
        return out
    except Exception as exc:
        _fail(log, exc)
        raise


# ---------- P2：风格指南、角色表、场景图

def make_style_guide(llm, wid: str, log=None) -> Dict[str, Any]:
    """全世界一份风格指南（线条、主色、光照）；之后的漫画图都会带上它，保持全书画风一致。"""
    log = _log_or_new(log, wid, "style")
    try:
        log.log("start", "生成风格指南（全书统一画风）")
        require_charter(wid)
        meta = repo.load_meta(wid)
        cancel.checkpoint()
        draft = generate.style_draft(LoggedLLM(llm, log), context={
            "charter": meta.get("charter") or {}, "tone": meta.get("tone", ""), "genre": meta.get("genre", "")})
        sg = {k: str(draft[k]).strip() for k in generate.STYLE_KEYS}
        with _with_lock(wid):
            cur = repo.load_meta(wid)
            cur["style_guide"] = sg
            repo.save_meta(cur)
        log.log("save", f"风格指南：{sg['summary']}", level="ok")
        log.log("done", "完成", level="ok")
        return sg
    except Exception as exc:
        _fail(log, exc)
        raise


def _board_names(d: Path) -> Tuple[Dict[str, Any], set, set]:
    board = store.read_json(d / "storyboard.json")
    if not board:
        raise EngineError("请先生成分镜（角色表与场景图依据分镜中的出场角色与地点）")
    chars = {n for pg in board["pages"] for pn in pg["panels"] for n in (pn.get("characters") or [])}
    locs = {pn.get("location") for pg in board["pages"] for pn in pg["panels"] if pn.get("location")}
    return board, chars, locs


def make_character_sheets(provider, wid: str, sid: str, log=None, *, overwrite: bool = False) -> Dict[str, Any]:
    """为本故事出场、已有立绘的角色生成角色表：侧面 + 愤怒/悲伤/惊讶表情（以立绘为参考）。旧图归档。"""
    log = _log_or_new(log, wid, "sheet")
    made = skipped = failed = 0
    exhausted = cancelled = False
    try:
        d = repo.story_dir(wid, sid)
        _, names, _ = _board_names(d)
        meta = repo.load_meta(wid)
        chars = [c for c in repo.load_characters(wid) if c["name"] in names]
        budget = _Budget(comic_limits(wid)["image_budget"])
        log.log("start", f"生成角色表：{len(chars)} 个出场角色，每人最多 {len(comic_layout.SHEET_KEYS)} 张"
                         f"（已有的跳过，调用上限 {budget.limit} 次）")
        try:
            for ch in chars:
                portrait = repo.resolve_portrait(wid, ch)
                if portrait is None:
                    log.log("sheet", f"「{ch['name']}」还没有立绘，跳过（先生成人设图）", level="warn")
                    skipped += len(comic_layout.SHEET_KEYS)
                    continue
                base = (ch.get("attrs") or {}).get("visual_en") or ch.get("summary", "")
                for key in comic_layout.SHEET_KEYS:
                    path = repo.sheet_path(wid, ch["id"], key)
                    if path.is_file() and not overwrite:
                        skipped += 1
                        continue
                    cancel.checkpoint()
                    if not budget.take():
                        exhausted = True
                        break
                    if path.is_file():
                        _archive(path, log)
                    prompt = (f"{base}, {comic_layout.SHEET_PROMPTS[key]}, {meta.get('tone', '')} style, "
                              "plain neutral background")
                    log.log("sheet", f"「{ch['name']}」{key}：生成中……")
                    res = provider.generate(prompt, path, ratio="3:4", reference_images=[portrait])
                    if res.ok:
                        made += 1
                        log.log("sheet", f"「{ch['name']}」{key}：完成", level="ok")
                    else:
                        failed += 1
                        log.log("sheet", f"「{ch['name']}」{key}：失败：{res.error}", level="warn")
                if exhausted:
                    break
        except cancel.Cancelled:
            cancelled = True
            log.log("cancel", "已停止，已生成的角色表保留", level="warn")
        if exhausted:
            log.log("cancel", f"已达图像调用上限（{budget.limit} 次），其余角色表未生成", level="warn")
        log.log("done", f"{'已停止' if cancelled else '完成'}：新生成 {made} 张，失败 {failed} 张，跳过 {skipped} 张",
                level="warn" if (failed or cancelled or exhausted) else "ok")
        return {"made": made, "failed": failed, "skipped": skipped, "budget_exhausted": exhausted,
                "cancelled": cancelled, "image_calls": budget.used}
    except Exception as exc:
        _fail(log, exc)
        raise


def make_scene_images(llm, provider, wid: str, sid: str, log=None, *, overwrite: bool = False) -> Dict[str, Any]:
    """为分镜中用到的地点生成场景设定图（先由文本模型写英文场景描述，结果保存在设定条目上）。"""
    log = _log_or_new(log, wid, "sheet")
    made = skipped = failed = 0
    exhausted = cancelled = False
    try:
        d = repo.story_dir(wid, sid)
        _, _, loc_names = _board_names(d)
        meta = repo.load_meta(wid)
        ents = [e for e in repo.load_entities(wid) if e["name"] in loc_names]
        budget = _Budget(comic_limits(wid)["image_budget"])
        log.log("start", f"生成场景图：{len(ents)} 个地点（调用上限 {budget.limit} 次）")
        try:
            for e in ents:
                path = repo.scene_path(wid, e["id"])
                if path.is_file() and not overwrite:
                    skipped += 1
                    continue
                cancel.checkpoint()
                if not budget.take():
                    exhausted = True
                    break
                visual = (e.get("attrs") or {}).get("visual_en")
                if not visual:
                    try:
                        draft = generate.scene_draft(LoggedLLM(llm, log), context={
                            "name": e["name"], "summary": e.get("summary", ""), "kind": e.get("kind", ""),
                            "tone": meta.get("tone", ""), "genre": meta.get("genre", "")})
                    except generate.GenerationError as exc:
                        failed += 1
                        log.log("sheet", f"「{e['name']}」场景描述生成失败：{exc}", level="warn")
                        continue
                    visual = draft["visual_en"]
                    with _with_lock(wid):
                        cur = repo.load_entities(wid)
                        item = repo.find_by_id(cur, e["id"])
                        if item is not None:
                            item.setdefault("attrs", {})["visual_en"] = visual
                            repo.save_entities(wid, cur)
                if path.is_file():
                    _archive(path, log)
                res = provider.generate(f"{visual}, {meta.get('tone', '')} style, wide establishing shot, "
                                        "no people, no text", path, ratio="16:9")
                if res.ok:
                    made += 1
                    log.log("sheet", f"场景「{e['name']}」：完成", level="ok")
                else:
                    failed += 1
                    log.log("sheet", f"场景「{e['name']}」：失败：{res.error}", level="warn")
        except cancel.Cancelled:
            cancelled = True
            log.log("cancel", "已停止，已生成的场景图保留", level="warn")
        if exhausted:
            log.log("cancel", f"已达图像调用上限（{budget.limit} 次），其余场景图未生成", level="warn")
        log.log("done", f"{'已停止' if cancelled else '完成'}：新生成 {made} 张，失败 {failed} 张，跳过 {skipped} 张",
                level="warn" if (failed or cancelled or exhausted) else "ok")
        return {"made": made, "failed": failed, "skipped": skipped, "budget_exhausted": exhausted,
                "cancelled": cancelled, "image_calls": budget.used}
    except Exception as exc:
        _fail(log, exc)
        raise


def _data_uri(path: Path) -> str:
    import base64
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


_COMIC_CSS = """
body{margin:0;background:#2b2b2b;font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif;padding:24px 0}
h1{color:#eee;text-align:center;font-size:20px;font-weight:600}
.page{position:relative;background:#fff;border:6px solid #111;padding:0;margin:18px auto 28px;max-width:780px;
      box-shadow:0 4px 18px rgba(0,0,0,.5);box-sizing:border-box}
.page-no{position:absolute;top:-22px;left:0;color:#ddd;font-size:12px}
.grid{position:relative;width:100%;padding-top:141.667%;background:#fff}
.panel{position:absolute;box-sizing:border-box;margin:0;border:3px solid #111;background:#ddd;overflow:hidden}
.panel img{position:absolute;left:0;top:0;width:100%;height:100%;object-fit:cover;display:block}
.missing{position:absolute;left:0;top:0;right:0;bottom:0;display:flex;align-items:center;justify-content:center;
         color:#666;font-size:13px;text-align:center;padding:12px;box-sizing:border-box;background:#e4e4e4}
.bz{position:absolute;display:flex;flex-direction:column;gap:6px;padding:10px;max-width:52%;box-sizing:border-box}
.bz.top{top:0}.bz.bottom{bottom:0;justify-content:flex-end}
.bz.l{left:0;align-items:flex-start}.bz.r{right:0;align-items:flex-end}.bz.c{left:50%;transform:translateX(-50%);align-items:center}
.cap{background:#fff3a3;color:#111;border:2px solid #111;padding:4px 8px;font-size:13px;line-height:1.4;max-width:100%}
.bubble{position:relative;background:#fff;color:#111;border:3px solid #111;border-radius:18px;padding:6px 10px;
        font-size:14px;line-height:1.4;max-width:100%;box-sizing:border-box}
.bubble.thought{border-radius:50%;padding:12px 16px}
.bubble.shout{border-width:5px}
.bubble b{display:block;font-size:12px;color:#555;margin-bottom:2px}
.sfx{font-weight:900;font-size:clamp(24px,5vw,44px);color:#111;text-shadow:2px 2px 0 #fff,-2px -2px 0 #fff,2px -2px 0 #fff,-2px 2px 0 #fff}
.note{color:#bbb;text-align:center;font-size:12px;margin-top:8px}
"""


def render_comic_html(board: Dict[str, Any], title: str, base_dir: Path, reading: Optional[str] = None) -> str:
    """网页预览：格子位置与 PNG 成品共用 comic_layout 的规划；预览不画尾巴，完整排字以 PNG 为准。"""
    reading = reading or (board.get("opts") or {}).get("reading") or "ltr"
    parts = [f"<!doctype html><html lang='zh'><head><meta charset='utf-8'><title>{html.escape(title)}</title>",
             f"<style>{_COMIC_CSS}</style></head><body>", f"<h1>{html.escape(title)}</h1>"]
    for pg in board["pages"]:
        plan = comic_layout.plan_page(pg, reading)
        parts.append(f"<section class='page'><div class='page-no'>第 {pg['no']} 页</div><div class='grid'>")
        for pan in plan["panels"]:
            rx, ry, rw, rh = pan["rect"]
            style = (f"left:calc({rx * 100:.3f}% + 6px);top:calc({ry * 100:.3f}% + 6px);"
                     f"width:calc({rw * 100:.3f}% - 12px);height:calc({rh * 100:.3f}% - 12px)")
            img_path = base_dir / pan["image"] if pan.get("image") else None
            if img_path and img_path.is_file():
                parts.append(f"<figure class='panel' style='{style}'><img src='{_data_uri(img_path)}' alt=''>")
            else:
                err = html.escape(pan.get("image_error") or "未生成")
                parts.append(f"<figure class='panel' style='{style}'><div class='missing'>（画面未生成：{err}）</div>")
            groups: Dict[tuple, List[str]] = {}
            for it in pan["items"]:
                kind = it["kind"]
                text = html.escape(it.get("text", ""))
                if kind == "caption":
                    html_item = f"<div class='cap'>{text}</div>"
                elif kind == "sfx":
                    html_item = f"<div class='sfx'>{text}</div>"
                else:
                    who = f"<b>{html.escape(it['speaker'])}</b>" if it.get("speaker") else ""
                    cls = "bubble" if kind == "speech" else f"bubble {kind}"
                    html_item = f"<div class='{cls}'>{who}{text}</div>"
                groups.setdefault(tuple(it["zone"]), []).append(html_item)
            for (vert, horiz), items in groups.items():
                h = {"left": "l", "right": "r"}.get(horiz, "c")
                parts.append(f"<div class='bz {vert} {h}'>{''.join(items)}</div>")
            parts.append("</figure>")
        parts.append("</div></section>")
    if board.get("stub"):
        parts.append("<p class='note'>注：本页使用占位图，未调用真实图像模型。</p>")
    parts.append("</body></html>")
    return "".join(parts)


# ---------- 导出给 novel_writer

def export_bundle(wid: str, sid: str, log=None) -> Path:
    log = _log_or_new(log, wid, "export")
    try:
        log.log("start", f"导出故事 {sid} 为 novel_writer 输入包", story=sid)
        d = repo.story_dir(wid, sid)
        meta = repo.load_story_meta(wid, sid)
        out = data_root() / wid / "exports" / sid
        if out.exists():
            log.log("save", f"清理旧的导出目录 {out.name}")
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
        ents = repo.load_entities(wid)
        setting_lines += [f"- **{e['name']}**（{e['kind']}）：{e.get('summary', '')}" for e in ents]
        (out / "setting.md").write_text("\n".join(setting_lines) + "\n", encoding="utf-8")
        log.log("save", f"setting.md：宪章规则 {len(charter.get('hard_rules') or [])} 条，设定条目 {len(ents)} 条")

        outline = [f"# {arc.get('title', meta['title'])}", "", f"前提：{arc.get('premise', '')}", ""]
        for i, act in enumerate(arc.get("acts") or [], start=1):
            outline += [f"## {act.get('title', f'第{i}幕')}", act.get("summary", ""), ""]
        if arc.get("ending"):
            outline += ["## 结局", arc["ending"], ""]
        (out / "outline.md").write_text("\n".join(outline), encoding="utf-8")
        log.log("save", f"outline.md：{len(arc.get('acts') or [])} 幕" if arc else "outline.md：该故事没有小故事线，仅含标题")

        names = set(arc.get("protagonist_names") or [])
        chars = [c for c in repo.load_characters(wid) if not names or c["name"] in names or c["role"] in ("主角", "重要配角")]
        (out / "characters.json").write_text(json.dumps(chars, ensure_ascii=False, indent=2), encoding="utf-8")
        log.log("save", f"characters.json：{len(chars)} 名角色")
        if text_path.is_file():
            shutil.copy(text_path, out / "story.md")
            log.log("save", "story.md：已复制短篇正文")
        (out / "bundle.json").write_text(json.dumps({"world_id": wid, "story_id": sid, "kind": meta["kind"],
                                                     "title": meta["title"], "files": sorted(p.name for p in out.iterdir())},
                                                    ensure_ascii=False, indent=2), encoding="utf-8")
        log.log("done", f"完成：{out}", level="ok", files=sorted(p.name for p in out.iterdir()))
        return out
    except Exception as exc:
        _fail(log, exc)
        raise
