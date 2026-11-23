"""wiki/build.py — P2 构建：归一 → 定义 → 断言账本 → 关系 → 冲突，写入 wiki/（方案 §5.2、§19.4）。

增量与保护：
  * 概念页记录 source_hash（名称 + 依据断言 id + 版本）；没变则复用已有定义，不调用模型。
  * human_edited: true 的概念页不会被自动改写（方案 §19.4 流程 D）。
  * 单个概念定义失败只标记该概念 status: failed，其它概念照常。
  * 关系：没有依据的边丢弃，丢弃原因计入运行日志。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Set

from .. import cancel, joblog, settings, store
from ..digest import runner as drunner
from ..digest import summarize as sm
from ..library import catalog
from . import content, model, normalize
from . import validate_util as vu

SCOPE = "wiki"
DEF_VERSION = 1
EDGE_VERSION = 1
CONFLICT_VERSION = 1


def _key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ok_digests(ctx) -> List[Dict[str, Any]]:
    out = []
    for row in catalog.list_articles(ctx):
        rec = drunner.load_digest(ctx, row["id"])
        if rec and rec.get("status") == "ok":
            out.append({"row": row, "rec": rec})
    return out


def build(ctx, llm, *, force: bool = False) -> Dict[str, Any]:
    digests = _ok_digests(ctx)
    if not digests:
        raise ValueError("还没有摘要成功的文章，请先完成摘要")
    eff, _ = settings.effective_settings(ctx)
    max_rounds = eff["budget"]["max_repair_rounds"]
    terms = ctx.load_charter()[0].get("terms", {})

    run_id = joblog.run_start(ctx.root, SCOPE, "概念与关系")
    try:
        result = _build(ctx, llm, digests, terms, max_rounds, force)
    except cancel.Cancelled:
        joblog.run_end(ctx.root, SCOPE, run_id, "概念与关系", status="stopped", error="用户请求停止")
        raise
    except BaseException as exc:
        joblog.run_end(ctx.root, SCOPE, run_id, "概念与关系", status="failed",
                       error=f"{type(exc).__name__}: {exc}")
        raise
    failed = result["failed_concepts"]
    joblog.run_end(ctx.root, SCOPE, run_id, "概念与关系", status="failed" if failed else "ok",
                   error=f"{failed} 个概念的定义失败（见上方记录）" if failed else None)
    return result


def _build(ctx, llm, digests, terms, max_rounds, force) -> Dict[str, Any]:
    # 1. 归一 ------------------------------------------------------------
    surfaces: Dict[str, Set[str]] = {}
    for d in digests:
        for name in d["rec"].get("concepts") or []:
            surfaces.setdefault(str(name), set()).add(d["row"]["id"])

    def ask_fn(pairs):
        budget = sm.RepairBudget(max_rounds)
        obj = sm.ask_validated(llm, normalize.ask_prompt(pairs), normalize.ask_validator({p for p, _, _ in pairs}),
                               budget)
        decisions = {str(x["id"]): bool(x["same"]) for x in obj["decisions"]}
        joblog.log(ctx.root, SCOPE, f"归一：本批判定同义 {sum(decisions.values())}/{len(pairs)} 对")
        return decisions

    with joblog.step(ctx.root, SCOPE, "归一") as info:
        def on_batch(k, n, size):
            info["progress"] = f"第 {k}/{n} 批，{size} 对候选"
            joblog.log(ctx.root, SCOPE, f"归一：第 {k}/{n} 批开始，{size} 对候选")

        joblog.log(ctx.root, SCOPE, f"归一：{len(surfaces)} 种写法，{len(terms)} 组已知术语")
        clusters = normalize.cluster(surfaces, terms, ask_fn, progress=on_batch)
        info["detail"] = f"{len(surfaces)} 种写法 → {len(clusters)} 个概念"

    # 2. 概念 id（按写法重叠匹配旧 id，保证稳定） -----------------------------
    idmap = model.load_id_map(ctx)
    old = idmap["concepts"]
    assigned: Dict[str, str] = {}
    for c in clusters:
        best, best_n = None, 0
        for cid, meta in old.items():
            n = len(set(meta.get("surfaces", [])) & set(c["surfaces"]))
            if n > best_n and cid not in assigned.values():
                best, best_n = cid, n
        assigned[c["canonical"]] = best or model.allocate_cid(idmap)
    new_map = {}
    for c in clusters:
        cid = assigned[c["canonical"]]
        new_map[cid] = {"canonical": c["canonical"], "surfaces": c["surfaces"]}
    # 3. 断言账本：每条断言 → 所属概念 -----------------------------------------
    ledger: List[Dict[str, Any]] = []
    origin_of = {r["id"]: r.get("origin", "imported") for r in catalog.list_articles(ctx)}
    for d in digests:
        aid = d["row"]["id"]
        for n, cl in enumerate(d["rec"].get("claims") or [], 1):
            text, quote = str(cl.get("text", "")), str(cl.get("quote", ""))
            concepts = [assigned[c["canonical"]] for c in clusters
                        if any(vu.appears(s, text + quote) for s in c["surfaces"])]
            ledger.append({"id": f"{aid}#{n}", "article_id": aid, "text": text, "quote": quote,
                           "concepts": concepts, "origin": origin_of.get(aid, "imported")})
    claims_by_cid: Dict[str, List[Dict[str, Any]]] = {}
    for row in ledger:
        for cid in row["concepts"]:
            claims_by_cid.setdefault(cid, []).append(row)
    model.write_jsonl(ctx, "claims.jsonl", ledger)

    # 4. 概念页（定义，增量） -----------------------------------------------
    failed_concepts = 0
    with joblog.step(ctx.root, SCOPE, "定义") as info:
        for i, c in enumerate(clusters, 1):
            cid = assigned[c["canonical"]]
            claims = claims_by_cid.get(cid, [])
            info["progress"] = f"概念 {i}/{len(clusters)}：{c['canonical']}"
            _write_concept(ctx, llm, cid, c, claims, max_rounds, force)
            status = (model.read_page(ctx, cid) or ({}, ""))[0].get("status")
            if status == "failed":
                failed_concepts += 1
            label = {"ok": "已生成", "failed": "失败", "no_source": "无依据断言",
                     "generated_only": "仅有生成文章提及，暂不生成"}.get(status or "", status or "未知")
            joblog.log(ctx.root, SCOPE, f"定义 {i}/{len(clusters)}「{c['canonical']}」：{label}"
                                        f"（依据断言 {len(claims)} 条）")
        info["detail"] = f"{len(clusters)} 个概念，{failed_concepts} 个失败"
        if failed_concepts:
            info["status"] = "failed"
    # 清理：不再存在的概念页（未人工编辑的才删除）
    for cid in list(old):
        if cid not in new_map:
            pg = model.read_page(ctx, cid)
            if not (pg and pg[0].get("human_edited")):
                model.delete_page(ctx, cid)
    idmap["concepts"] = new_map
    model.save_id_map(ctx, idmap)

    # 5. 关系边 ---------------------------------------------------------
    edges = _build_edges(ctx, llm, digests, clusters, assigned, max_rounds, force)
    model.write_jsonl(ctx, "edges.jsonl", edges)

    # 6. 冲突 -----------------------------------------------------------
    conflicts = _build_conflicts(ctx, llm, clusters, assigned, claims_by_cid, max_rounds, force)
    model.write_jsonl(ctx, "conflicts.jsonl", conflicts)

    return {"concepts": len(clusters), "claims": len(ledger), "edges": len(edges),
            "conflicts": len(conflicts), "failed_concepts": failed_concepts}


def _source_hash(canon: str, claims: List[Dict[str, Any]]) -> str:
    raw = f"{DEF_VERSION}|{canon}|" + ",".join(sorted(c["id"] for c in claims))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _write_concept(ctx, llm, cid, c, claims, max_rounds, force) -> None:
    existing = model.read_page(ctx, cid)
    if existing and existing[0].get("human_edited"):
        return  # 人工改过的页面不自动覆盖
    # P6：生成文章不能成为定义的唯一出处（方案 §8.1 第 7 步）。定义只依据导入文章的断言。
    imported = [x for x in claims if x.get("origin", "imported") != "generated"]
    sh = _source_hash(c["canonical"], claims)
    meta_base = {"id": cid, "name": c["canonical"],
                 "aliases": [s for s in c["surfaces"] if s != c["canonical"]],
                 "articles": c["articles"], "claims": [x["id"] for x in claims],
                 "source_hash": sh, "human_edited": False}
    if existing and not force and existing[0].get("source_hash") == sh and existing[0].get("status") == "ok":
        return  # 依据未变：复用定义
    if not claims:
        model.write_page(ctx, cid, {**meta_base, "status": "no_source", "generated_at": _now()},
                         f"# {c['canonical']}\n\n暂无可追溯的依据断言。")
        return
    if not imported:
        model.write_page(ctx, cid, {**meta_base, "status": "generated_only", "generated_at": _now()},
                         f"# {c['canonical']}\n\n目前只有系统生成的文章提及该概念，暂不生成定义"
                         "（生成文章不能作为唯一出处）。\n")
        return
    prompt_claims = [{"id": x["id"], "text": x["text"], "quote": x["quote"]} for x in imported]
    try:
        obj = sm.ask_validated(llm, content.definition_prompt(c["canonical"], prompt_claims),
                               content.definition_validator(c["canonical"], [x["id"] for x in imported]),
                               sm.RepairBudget(max_rounds))
    except sm.DigestFailed as exc:
        model.write_page(ctx, cid, {**meta_base, "status": "failed", "issues": exc.issues,
                                    "generated_at": _now()}, f"# {c['canonical']}\n\n定义生成失败。")
        return
    definition = str(obj["definition"]).strip()
    based = [str(b) for b in obj["based_on"]]
    refs = "\n".join(f"- {x['id']}：{x['quote'][:80]}" for x in imported if x["id"] in based)
    model.write_page(ctx, cid, {**meta_base, "status": "ok", "based_on": based, "generated_at": _now()},
                     f"# {c['canonical']}\n\n{definition}\n\n## 依据\n{refs}\n")


def _build_edges(ctx, llm, digests, clusters, assigned, max_rounds, force=False) -> List[Dict[str, Any]]:
    by_art: Dict[str, List[Dict[str, Any]]] = {}
    for c in clusters:
        for aid in c["articles"]:
            by_art.setdefault(aid, []).append(c)
    edges: Dict[tuple, Dict[str, Any]] = {}
    dropped_total = 0
    with joblog.step(ctx.root, SCOPE, "关系") as info:
        for i, d in enumerate(digests, 1):
            row = d["row"]
            head = f"{i}/{len(digests)}「{row.get('title', row['id'])}」"
            info["progress"] = head
            if row.get("origin") == "generated":
                joblog.log(ctx.root, SCOPE, f"关系 {head}：跳过（生成文章不产生关系边）")
                continue  # P6：生成文章不产生关系边（边必须有导入文章的依据）
            present = by_art.get(row["id"], [])
            if len(present) < 2:
                joblog.log(ctx.root, SCOPE, f"关系 {head}：本文只有 {len(present)} 个概念，跳过")
                continue
            body = catalog.read_body(ctx, row)
            if len(body) > content.MAX_BODY_FOR_EDGES:
                joblog.log(ctx.root, SCOPE, f"关系 {head}：正文 {len(body)} 字超过上限，跳过")
                continue
            pairs, display = [], {}
            for i, a in enumerate(present):
                for b in present[i + 1:]:
                    na = _display(a, row["id"], d)
                    nb = _display(b, row["id"], d)
                    pairs.append((na, nb))
                    display[na] = a
                    display[nb] = b
            cache_p = ctx.path("wiki", "cache", "edges", f"{row['id']}.json")
            ck = _key(str(EDGE_VERSION), row["content_hash"], repr(pairs))
            cached = store.read_json(cache_p, default=None)
            cached_hit = bool(not force and cached and cached.get("key") == ck)
            if cached_hit:   # 文章与概念对都没变：复用上次结果
                accepted, dropped_n = cached["accepted"], cached["dropped"]
            else:
                obj = sm.ask_validated(llm, content.edge_prompt(row.get("title", ""), body, pairs),
                                       lambda o: [] if isinstance(o.get("edges", []), list) else ["edges 应为列表"],
                                       sm.RepairBudget(max_rounds))
                accepted, dropped = content.parse_edges(obj, pairs, body)
                dropped_n = len(dropped)
                store.write_json_atomic(cache_p, {"key": ck, "accepted": accepted, "dropped": dropped_n})
            dropped_total += dropped_n
            joblog.log(ctx.root, SCOPE, f"关系 {head}：{len(pairs)} 个概念对，采纳 {len(accepted)} 条，"
                                        f"丢弃 {dropped_n} 条" + ("（沿用缓存）" if cached_hit else ""))
            for e in accepted:
                s, t = display[e["src"]], display[e["dst"]]
                sid, tid = assigned[s["canonical"]], assigned[t["canonical"]]
                if e["type"] in content.UNDIRECTED:
                    sid, tid = sorted((sid, tid))
                key = (sid, tid, e["type"])
                if key not in edges:
                    edges[key] = {"src": sid, "dst": tid, "type": e["type"], "evidence": []}
                edges[key]["evidence"].append({"article_id": row["id"], "quote": e["quote"],
                                               "rationale": e["rationale"]})
        info["detail"] = f"{len(edges)} 条边，丢弃 {dropped_total} 条（无依据或不合规）"
    return list(edges.values())


def _display(cluster, aid, digest) -> str:
    """概念在该文章中的用词（用于提示模型与映射回概念）。"""
    present = set(digest["rec"].get("concepts") or [])
    for s in cluster["surfaces"]:
        if s in present:
            return s
    return cluster["canonical"]


def _build_conflicts(ctx, llm, clusters, assigned, claims_by_cid, max_rounds, force=False) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with joblog.step(ctx.root, SCOPE, "冲突") as info:
        for i, c in enumerate(clusters, 1):
            cid = assigned[c["canonical"]]
            info["progress"] = f"概念 {i}/{len(clusters)}：{c['canonical']}"
            claims = [x for x in claims_by_cid.get(cid, []) if x["text"] and x.get("origin", "imported") != "generated"]
            if len({x["article_id"] for x in claims}) < 2:
                continue
            joblog.log(ctx.root, SCOPE, f"冲突 {i}/{len(clusters)} {c['canonical']}：{len(claims)} 条断言，"
                                        f"来自 {len({x['article_id'] for x in claims})} 篇文章")
            cache_p = ctx.path("wiki", "cache", "conflicts", f"{cid}.json")
            ck = _key(str(CONFLICT_VERSION), *[f"{x['id']}={x['text']}" for x in claims])
            cached = store.read_json(cache_p, default=None)
            if not force and cached and cached.get("key") == ck:
                items = cached["items"]
            else:
                try:
                    obj = sm.ask_validated(llm, content.conflict_prompt(c["canonical"], claims),
                                           content.conflict_validator(claims), sm.RepairBudget(max_rounds))
                except sm.DigestFailed as exc:
                    joblog.log(ctx.root, SCOPE,
                               f"概念「{c['canonical']}」冲突检查失败，已跳过：{'；'.join(exc.issues[:2])}", level="warn")
                    continue
                items = [{"a": str(it["a"]), "b": str(it["b"]), "reason": str(it["reason"]).strip()}
                         for it in obj.get("conflicts") or []]
                store.write_json_atomic(cache_p, {"key": ck, "items": items})
            by_id = {x["id"]: x for x in claims}
            for it in items:
                a, b = by_id[it["a"]], by_id[it["b"]]
                out.append({"concept": cid, "reason": it["reason"],
                            "sides": [{"claim": a["id"], "article_id": a["article_id"], "quote": a["quote"]},
                                      {"claim": b["id"], "article_id": b["article_id"], "quote": b["quote"]}]})
        info["detail"] = f"{len(out)} 处冲突"
    return out

