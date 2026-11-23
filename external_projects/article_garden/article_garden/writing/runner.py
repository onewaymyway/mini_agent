"""writing/runner.py — 成文流程（方案 §8.1）与审阅动作（方案 §8.1 第 8 步、§19.3「✍ 审阅」）。

成文：brief → outline → 逐节初稿（代码校验 + 修复循环）→ 组装 → 全文校验 → 独立评审（不通过则按小节回喂修复）
      → 入库 library/generated（status=draft, origin=generated）→ 摘要（回灌 A 步）→ 选题标记为 drafted。
* 修复预算按“每篇”计（max_repair_rounds），与摘要一致；用尽、或评审连续不通过 → failed，并保留全部中间产物。
* 停止或异常：选题退回成文前的状态，并记录原因。
* 重写（退回意见）复用同一文章 id，旧版本保存在 drafts/<tid>/revisions/。
审阅：通过（drafted → reviewed）、发布（reviewed → published，需明确执行）、放弃（→ dropped）。
生成文章不会自动发布；wiki 构建会忽略只出自生成文章的定义与冲突（P6 的“不能成为唯一出处”规则）。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .. import cancel, joblog, settings, store
from ..digest import runner as drunner
from ..digest import summarize as sm
from ..library import catalog, front_matter, hashing
from ..planning import states
from ..research import pack
from . import brief as brief_mod
from . import checks, critic, draft, outline

SCOPE = "writing"
MAX_DRAFT_ATTEMPTS = 3      # 整篇评审/全文问题的最多重写轮数（每轮消耗一次修复预算）
LIB_PARA_LIMIT = 400


class WriteFailed(Exception):
    def __init__(self, reasons: List[str], last: str = ""):
        super().__init__("；".join(reasons[:3]))
        self.reasons = reasons
        self.last = last


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _library_paragraphs(ctx, exclude_aid: Optional[str]) -> List[str]:
    out: List[str] = []
    for row in catalog.list_articles(ctx):
        if row["id"] == exclude_aid:
            continue
        for p in checks.paragraphs(catalog.read_body(ctx, row)):
            if len(p) >= checks.MIN_PARA_FOR_OVERLAP:
                out.append(p)
            if len(out) >= LIB_PARA_LIMIT:
                return out
    return out


def _rules(ctx, brief: Dict[str, Any], exclude_aid: Optional[str]) -> Dict[str, Any]:
    eff, _ = settings.effective_settings(ctx)
    th = eff["thresholds"]
    return {"copy_max": int(th["copy_chars_max"]), "overlap_max": float(th["overlap_max"]),
            "forbidden": brief["risk"]["forbidden_claims"], "out": brief["scope"]["out"],
            "terms": brief["terms"],
            "library": [checks.shingles(p) for p in _library_paragraphs(ctx, exclude_aid)]}


def _topic_dir(ctx, tid: str):
    return ctx.path("drafts", tid)


def _doc_issues(body: str, brief: Dict[str, Any]) -> List[str]:
    return checks.check_document(body, length=brief["style"]["length"], must_have=brief["style"]["must_have"],
                                 kind=brief["kind"], disclaimer=brief["risk"]["disclaimer"],
                                 library_titles=[x["title"] for x in brief["library_links"]])


def _map_critic(issues: List[Dict[str, str]], headings: List[str]) -> Dict[int, List[str]]:
    """把评审问题归属到小节下标；找不到归属的问题作用于全部小节（保守）。"""
    out: Dict[int, List[str]] = {}
    for it in issues:
        target = [i for i, h in enumerate(headings) if it["section"] == h or it["section"] in h or h in it["section"]]
        note = f"[{it['kind']}] {it['note']}"
        for i in (target or range(len(headings))):
            out.setdefault(i, []).append(note)
    return out


def _write_article(ctx, tid: str, topic: Dict[str, Any], llm, rounds: int, feedback: str) -> Dict[str, Any]:
    """成文主体。模型输出耗尽修复预算时统一转为 WriteFailed（保留产物），其它异常原样抛出。"""
    try:
        return _write_article_inner(ctx, tid, topic, llm, rounds, feedback)
    except sm.DigestFailed as exc:
        raise WriteFailed(exc.issues, exc.last_output) from exc


def _write_article_inner(ctx, tid: str, topic: Dict[str, Any], llm, rounds: int, feedback: str) -> Dict[str, Any]:
    budget = sm.RepairBudget(rounds)
    d = _topic_dir(ctx, tid)
    brief = brief_mod.build(ctx, tid, [feedback] if feedback else [])
    by_id = {s["id"]: s for s in brief["sources"]}
    rules_for_check = _rules(ctx, brief, topic.get("article_id"))
    lib_snippets = [f"{a['title']}：{'、'.join(a['concepts'])}" for a in brief["existing_articles"]
                    if a.get("origin") != "generated"]

    secs_all = outline.make(llm, brief, budget)
    store.write_yaml_atomic(d / "outline.yaml", {"sections": secs_all})
    body_secs = [s for s in secs_all if not outline.is_refs(s["heading"])]
    headings = [s["heading"] for s in body_secs]

    texts: Dict[int, str] = {}
    for i, sec in enumerate(body_secs):
        texts[i] = draft.draft_section(llm, brief, sec, by_id, budget, headings[:i], [], rules_for_check)
        store.write_text_atomic(d / "sections" / f"{i + 1:02d}.md", f"## {sec['heading']}\n\n{texts[i]}\n")

    library_links = brief["library_links"]
    attempts = 0
    critic_log: List[Dict[str, Any]] = []
    while True:
        parts = [(headings[i], texts[i]) for i in range(len(body_secs))]
        body = draft.assemble(brief, parts, by_id, library_links)
        doc = _doc_issues(body, brief)
        if doc:
            per_sec = {i: list(doc) for i in range(len(body_secs))}
            pending = doc
        else:
            cr = critic.review(llm, body, brief["sources"], lib_snippets)
            critic_log.append({"round": attempts, "issues": cr})
            if not cr:
                store.write_text_atomic(d / "draft.md", body)
                return {"body": body, "brief": brief, "by_id": by_id, "budget": budget,
                        "critic_rounds": len(critic_log), "outline": secs_all}
            per_sec = _map_critic(cr, headings)
            pending = [f"{it['section']}：{it['note']}" for it in cr]
        attempts += 1
        if attempts > MAX_DRAFT_ATTEMPTS:
            raise WriteFailed(pending, body)
        if not budget.take():
            raise WriteFailed(pending + ["修复轮数已用尽"], body)
        for i, hints in per_sec.items():
            cancel.check()
            texts[i] = draft.draft_section(llm, brief, body_secs[i], by_id, budget, headings[:i], hints, rules_for_check)
        store.write_text_atomic(d / "draft.md", body)


def _register(ctx, tid: str, topic: Dict[str, Any], result: Dict[str, Any], status: str = "draft") -> str:
    brief, body = result["brief"], result["body"]
    data = catalog.load(ctx)
    aid = topic.get("article_id")
    if not aid:
        aid = catalog.allocate_id(data)
    used = []
    for sid in re.findall(r"\[\^(src-\d{4})\]", body):
        if sid not in used:
            used.append(sid)
    rel = f"library/generated/{aid}.md"
    meta = {"id": aid, "title": brief["title"], "origin": "generated", "status": status,
            "kind": brief["kind"], "level": brief["level"], "derived_from": tid,
            "evidence": used, "concepts": brief["must_cover"], "content_hash": hashing.content_hash(body),
            "generated_at": _now(), "tree_node": topic.get("node")}
    store.write_text_atomic(ctx.path(rel), front_matter.dump(meta, body))
    row = {"id": aid, "title": brief["title"], "origin": "generated", "status": status,
           "content_hash": meta["content_hash"], "path": rel, "source_name": tid,
           "chars": len(re.sub(r"\s+", "", body)), "imported_at": ""}
    data["articles"] = [r for r in data["articles"] if r["id"] != aid] + [row]
    catalog.save(ctx, data)
    return aid


def _set_article_status(ctx, aid: str, status: str) -> None:
    data = catalog.load(ctx)
    row = next((r for r in data["articles"] if r["id"] == aid), None)
    if row is None:
        raise ValueError(f"文章不存在：{aid}")
    p = ctx.path(row["path"])
    meta, body = front_matter.split(p.read_text(encoding="utf-8"))
    meta["status"] = status
    store.write_text_atomic(p, front_matter.dump(meta, body))
    row["status"] = status
    catalog.save(ctx, data)


def write(ctx, tid: str, llm, *, max_rounds: Optional[int] = None, feedback: str = "") -> Dict[str, Any]:
    if llm is None:
        raise ValueError("成文需要文本模型，请先确认模型配置可用")
    eff, _ = settings.effective_settings(ctx)
    topic = states.get(ctx, tid)
    allowed = (states.RESEARCHED, states.FAILED, states.DRAFTED, states.REVIEWED)
    if topic["status"] not in allowed:
        raise ValueError(f"只有已调研、成文失败或已成稿的选题可以成文（当前为「{topic['status']}」）")
    pk = pack.read(ctx, tid)
    if not pk or not pk.get("sufficient"):
        raise ValueError("证据不足或尚未调研，不能成文（证据不足的选题请补充资料或放弃）")
    rounds = int(max_rounds or eff["budget"]["max_repair_rounds"])
    prev = topic["status"]
    d = _topic_dir(ctx, tid)
    if topic["status"] in (states.DRAFTED, states.REVIEWED) and topic.get("article_id"):
        prior = d / "draft.md"
        if prior.is_file():
            n = len(list((d / "revisions").glob("*.md")) if (d / "revisions").is_dir() else []) + 1
            store.write_text_atomic(d / "revisions" / f"r{n:02d}.md", prior.read_text(encoding="utf-8"))

    run_id = joblog.run_start(ctx.root, SCOPE, f"成文 {tid}")
    note = "开始成文" + (f"（编辑意见：{feedback[:40]}）" if feedback else "")
    states.begin_draft(ctx, tid, note)
    try:
        res = _write_article(ctx, tid, topic, llm, rounds, feedback)
        aid = _register(ctx, tid, topic, res)
        states.link_article(ctx, tid, aid)
        ck = {"article_id": aid, "critic_rounds": res["critic_rounds"], "budget_used": res["budget"].used,
              "budget_max": rounds, "checked_at": _now(), "passed": True}
        store.write_json_atomic(d / "checks.json", ck)
        (d / "failed.json").unlink(missing_ok=True)
        counts = drunner.run_digest(ctx, llm, only=[aid], force=True)
        states.end_draft(ctx, tid, states.DRAFTED, f"已成稿 {aid}（评审 {res['critic_rounds']} 轮，修复 {res['budget'].used}/{rounds} 轮）")
        joblog.run_end(ctx.root, SCOPE, run_id, f"成文 {tid}", status="ok")
        return {"topic_id": tid, "result": "drafted", "article_id": aid, "checks": ck, "digest": counts}
    except WriteFailed as exc:
        store.write_json_atomic(d / "failed.json", {"reasons": exc.reasons, "failed_at": _now()})
        if exc.last:
            store.write_text_atomic(d / "last_output.md", exc.last)
        states.end_draft(ctx, tid, states.FAILED, "成文未通过：" + "；".join(exc.reasons[:3]))
        joblog.run_end(ctx.root, SCOPE, run_id, f"成文 {tid}", status="failed", error="；".join(exc.reasons[:3]))
        return {"topic_id": tid, "result": "failed", "reasons": exc.reasons}
    except cancel.Cancelled:
        states.revert_to(ctx, tid, prev, "用户停止，退回成文前状态")
        joblog.run_end(ctx.root, SCOPE, run_id, f"成文 {tid}", status="stopped", error="用户请求停止")
        raise
    except BaseException as exc:
        states.revert_to(ctx, tid, prev, f"成文中断，退回成文前状态（{type(exc).__name__}）")
        joblog.run_end(ctx.root, SCOPE, run_id, f"成文 {tid}", status="failed", error=f"{type(exc).__name__}: {exc}")
        raise


# ------------------------------------------------------------------ 审阅动作（不调用模型）

def approve(ctx, tid: str) -> Dict[str, Any]:
    t = states.move(ctx, tid, states.REVIEWED, "审阅通过")
    _set_article_status(ctx, t["article_id"], "reviewed")
    return t


def publish(ctx, tid: str) -> Dict[str, Any]:
    t = states.move(ctx, tid, states.PUBLISHED, "发布")
    _set_article_status(ctx, t["article_id"], "published")
    return t


def abandon(ctx, tid: str, reason: str) -> Dict[str, Any]:
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("放弃选题必须给出原因")
    t = states.move(ctx, tid, states.DROPPED, f"放弃：{reason}")
    if t.get("article_id"):
        _set_article_status(ctx, t["article_id"], "archived")
    return t
