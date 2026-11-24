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


# ---------- 设定补全（交互与自动共用）

def fill_gaps(llm, wid: str, *, gap_ids: Optional[List[str]] = None, limit: int = 5) -> Dict[str, Any]:
    gap_list = gaps.find_gaps(wid)
    if gap_ids:
        gap_list = [g for g in gap_list if g["id"] in gap_ids]
    filled, unfixed = [], []
    ctx_base = build_context(wid)
    for g in gap_list[:limit]:
        if g["kind"] == "missing_summary":
            target = _find_target(wid, g["target"])
            if target is None:
                continue
            draft = generate.gap_fill_draft(llm, context=ctx_base, target_name=target["name"], gap_label=g["label"])
            with _with_lock(wid):
                op = "update_character" if g["entity_type"] == "character" else "update_entity"
                rec = proposals.submit(wid, op, g["target"], {"summary": draft["summary"],
                                                              "attrs": draft.get("attrs") or {}},
                                       source="gap_fill")
            filled.append({"gap": g, "proposal": rec})
        elif g["kind"] == "unlinked_faction_hint":
            hint = repo.load_characters(wid)
            ch = next((c for c in hint if c["id"] == g["target"]), None)
            fid = next((e["id"] for e in repo.load_entities(wid)
                        if ch and e["name"] == ch.get("attrs", {}).get("faction_hint")), None)
            if fid:
                with _with_lock(wid):
                    rec = proposals.submit(wid, "update_character", g["target"], {"faction_id": fid},
                                           source="gap_fill")
                filled.append({"gap": g, "proposal": rec})
            else:
                unfixed.append(g)
        else:
            unfixed.append(g)
    return {"filled": filled, "unfixed": unfixed, "remaining": len(gaps.find_gaps(wid))}


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
