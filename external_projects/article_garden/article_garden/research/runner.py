"""research/runner.py — 调研流程（方案 §7）：查询 → 站内已有内容标注 → 搜索（缓存）→ 抓取（robots/节流/缓存）
→ 摘录与来源卡 → 证据包 → 充分则 researched，不足则 dropped 并写明原因。

* 只有已放行（approved）或已调研（researched）的选题可以调研。
* 停止或异常：选题退回调研前的状态，并记录原因（方案 §16）。
* 手动来源（粘贴文本）在重新调研时保留；可以单独删除。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .. import cancel, joblog, settings
from ..planning import states
from . import extract, fetch, pack, queries, search, sources

SCOPE = "research"


def _min_cred(ctx, eff) -> str:
    charter = ctx.load_charter()[0]
    return (charter.get("sources", {}) or {}).get("min_credibility") or eff["thresholds"]["min_credibility"]


def _avoid(ctx):
    return (ctx.load_charter()[0].get("sources", {}) or {}).get("avoid") or []


def run(ctx, tid: str, *, llm: Any = None, max_rounds: Optional[int] = None,
        search_backend: Any = None, fetcher: Any = None) -> Dict[str, Any]:
    eff, _ = settings.effective_settings(ctx)
    topic = states.get(ctx, tid)
    if topic["status"] not in (states.APPROVED, states.RESEARCHED):
        raise ValueError(f"只有已放行或已调研的选题可以调研（当前为「{topic['status']}」）")

    prev = topic["status"]
    must = [str(m) for m in (topic.get("must_cover") or []) if str(m).strip()]
    max_pages = int(eff["budget"]["max_fetch_pages_per_topic"])
    excerpt_max = int(eff["thresholds"]["excerpt_chars_max"])
    backend = search_backend or search.MiniAgentSearch(eff["search"]["provider"])
    fetcher = fetcher or fetch.Fetcher(ctx)
    rounds = int(max_rounds or eff["budget"]["max_repair_rounds"])
    result: Dict[str, Any] = {"topic_id": tid, "result": None, "sufficient": False, "queries": [],
                              "search_errors": [], "skipped": [], "added": 0, "pages": 0}

    run_id = joblog.run_start(ctx.root, SCOPE, f"调研 {tid}")
    states.begin_research(ctx, tid)
    try:
        qs = queries.build(topic, ctx.info().get("name") or ctx.id, llm, rounds)
        result["queries"] = qs
        hits, seen = [], set()
        for q in qs:
            try:
                got = search.cached_search(ctx, backend, q, int(eff["search"]["max_results"]))
            except search.SearchError as exc:
                result["search_errors"].append(str(exc))
                joblog.log(ctx.root, SCOPE, f"搜索失败，仅使用已有资料：{exc}", level="warn")
                break
            for h in got:
                if h.url and h.url not in seen:
                    seen.add(h.url)
                    hits.append(h)

        attempts = 0
        for h in hits:
            if sources.has_url(ctx, tid, h.url):
                continue
            if attempts >= max_pages:
                result["skipped"].append(f"已达每题抓取上限 {max_pages} 页")
                break
            attempts += 1
            page = fetcher.fetch(h.url)
            if not page["ok"]:
                result["skipped"].append(f"{h.url}：{page['error']}")
                continue
            title, text = extract.extract(page["html"])
            ex = sources.excerpt(text, must, excerpt_max)
            if not ex:
                result["skipped"].append(f"{h.url}：与选题要点无关")
                continue
            card = sources.make_card(sources.next_id(ctx, tid), tid, url=h.url,
                                     title=title or h.title or h.url, text=ex,
                                     credibility=sources.classify(h.url, _avoid(ctx)),
                                     source_type="web", points=must)
            sources.write(ctx, tid, card)
            result["added"] += 1
        result["pages"] = attempts

        ev = pack.evaluate(ctx, tid, topic, _min_cred(ctx, eff))
        result["sufficient"] = ev["sufficient"]
        if ev["sufficient"]:
            states.end_research(ctx, tid, states.RESEARCHED, f"证据充分（要点覆盖 {ev['coverage']:.0%}）")
            result["result"] = "researched"
        else:
            states.end_research(ctx, tid, states.DROPPED, "证据不足：" + "；".join(ev["reasons"]))
            result["result"] = "dropped"
        joblog.run_end(ctx.root, SCOPE, run_id, f"调研 {tid}", status="ok")
        return result
    except cancel.Cancelled:
        states.revert_research(ctx, tid, prev, "用户停止，退回调研前状态")
        joblog.run_end(ctx.root, SCOPE, run_id, f"调研 {tid}", status="stopped", error="用户请求停止")
        raise
    except BaseException as exc:
        states.revert_research(ctx, tid, prev, f"调研中断，退回调研前状态（{type(exc).__name__}）")
        joblog.run_end(ctx.root, SCOPE, run_id, f"调研 {tid}", status="failed",
                       error=f"{type(exc).__name__}: {exc}")
        raise


def add_manual_text(ctx, tid: str, text: str, *, title: str = "", url: str = "",
                    credibility: str = "B") -> Dict[str, Any]:
    body = (text or "").strip()
    if not body:
        raise ValueError("资料正文不能为空")
    if credibility not in ("A", "B", "C", "D"):
        raise ValueError("可信度应为 A/B/C/D")
    eff, _ = settings.effective_settings(ctx)
    topic = states.get(ctx, tid)
    stored = body[: int(eff["thresholds"]["excerpt_chars_max"]) * 4]   # 手动资料保留更多正文，仍设上限
    card = sources.make_card(sources.next_id(ctx, tid), tid, url=(url or "").strip(),
                             title=(title or "").strip() or "手动资料", text=stored,
                             credibility=credibility, source_type="manual",
                             points=list(topic.get("must_cover") or []))
    sources.write(ctx, tid, card)
    return card


def remove_source(ctx, tid: str, sid: str) -> None:
    if not sources.delete(ctx, tid, sid):
        raise ValueError(f"来源不存在：{sid}")
    eff, _ = settings.effective_settings(ctx)
    pack.evaluate(ctx, tid, states.get(ctx, tid), _min_cred(ctx, eff))
