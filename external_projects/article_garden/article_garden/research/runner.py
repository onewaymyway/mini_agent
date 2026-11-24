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
from . import extract, fetch, pack, providers, queries, search, sources

SCOPE = "research"


def _min_cred(ctx, eff) -> str:
    charter = ctx.load_charter()[0]
    return (charter.get("sources", {}) or {}).get("min_credibility") or eff["thresholds"]["min_credibility"]


def _avoid(ctx):
    return (ctx.load_charter()[0].get("sources", {}) or {}).get("avoid") or []


def _bname(b) -> str:
    return getattr(b, "name", "web")


def _blangs(b) -> tuple:
    return tuple(getattr(b, "languages", ("zh", "en")))


def used_queries(used: set) -> set:
    return {q for _, q in used}


def _add_card(ctx, tid, h, title, ex, must, terms, avoid, result, *, source_type, via=""):
    card = sources.make_card(sources.next_id(ctx, tid), tid, url=h.url, title=title, text=ex,
                             credibility=sources.classify(h.url, avoid), source_type=source_type,
                             points=must, terms=terms)
    sources.write(ctx, tid, card)
    result["added"] += 1
    joblog.log(ctx.root, SCOPE, f"调研：已入库 {card['id']}{via}（{'摘要' if source_type == 'abstract' else '网页'}，"
                                f"可信度 {card['credibility']}，摘录 {len(ex)} 字，覆盖要点 "
                                f"{len(card['covers'])}/{len(must)}）")
    return card


def run(ctx, tid: str, *, llm: Any = None, max_rounds: Optional[int] = None,
        search_backend: Any = None, fetcher: Any = None) -> Dict[str, Any]:
    eff, _ = settings.effective_settings(ctx)
    topic = states.get(ctx, tid)
    if topic["status"] not in (states.APPROVED, states.RESEARCHED):
        raise ValueError(f"只有已放行或已调研的选题可以调研（当前为「{topic['status']}」）")

    prev = topic["status"]
    must = [str(m) for m in (topic.get("must_cover") or []) if str(m).strip()]
    terms = ctx.load_charter()[0].get("terms") or {}
    max_pages = int(eff["budget"]["max_fetch_pages_per_topic"])
    excerpt_max = int(eff["thresholds"]["excerpt_chars_max"])
    backends = [search_backend] if search_backend is not None else providers.default_backends(eff)
    fetcher = fetcher or fetch.Fetcher(ctx)
    rounds = int(max_rounds or eff["budget"]["max_repair_rounds"])
    result: Dict[str, Any] = {"topic_id": tid, "result": None, "sufficient": False, "queries": [],
                              "search_errors": [], "skipped": [], "added": 0, "pages": 0}

    run_id = joblog.run_start(ctx.root, SCOPE, f"调研 {tid}")
    states.begin_research(ctx, tid)
    try:
        domain_name = ctx.info().get("name") or ctx.id
        zh_pending = queries.build(topic, domain_name, llm, rounds)
        en_seed = queries.english(topic, llm, rounds) if any("en" in _blangs(b) for b in backends) else []
        result["queries"] = list(zh_pending)
        result["english_queries"] = list(en_seed)
        en_pending = list(en_seed)
        used: set = set()
        seen_urls: set = set()
        attempts = 0
        round_no = 0
        max_qrounds = int(eff["research"]["max_query_rounds"])
        joblog.log(ctx.root, SCOPE, f"调研：中文查询 {len(zh_pending)} 条，英文查询 {len(en_seed)} 条，"
                                    f"信息源 {'、'.join(_bname(b) for b in backends) or '无'}")
        while zh_pending or en_pending:
            round_no += 1
            cancel.check()
            new_hits = []
            for b in backends:
                langs = set(_blangs(b))
                if langs == {"en"}:
                    qs = en_pending
                elif langs == {"zh"}:
                    qs = zh_pending
                else:
                    qs = zh_pending + en_pending
                for q in qs:
                    key = (_bname(b), q)
                    if key in used:
                        continue
                    used.add(key)
                    try:
                        got = search.cached_search(ctx, b, q, int(eff["search"]["max_results"]))
                    except search.SearchError as exc:
                        result["search_errors"].append(f"{_bname(b)}：{exc}")
                        joblog.log(ctx.root, SCOPE, f"搜索 [{_bname(b)}] 「{q}」失败：{exc}", level="warn")
                        break
                    joblog.log(ctx.root, SCOPE, f"搜索 [{_bname(b)}] 「{q}」：{len(got)} 条结果")
                    for h in got:
                        if h.url and h.url not in seen_urls:
                            seen_urls.add(h.url)
                            new_hits.append(h)
            zh_pending, en_pending = [], []
            joblog.log(ctx.root, SCOPE, f"调研第 {round_no} 轮：新增候选网址 {len(new_hits)} 个")

            for h in new_hits:
                if sources.has_url(ctx, tid, h.url):
                    continue
                # 1) 官方接口已附带摘要且覆盖要点：直接使用摘要，不占抓取页数
                if h.source in ("arxiv", "crossref") and len(h.snippet or "") >= 120:
                    ex = sources.excerpt(h.snippet, must, excerpt_max, terms)
                    if ex:
                        _add_card(ctx, tid, h, h.title, ex, must, terms, _avoid(ctx), result,
                                  source_type="abstract")
                        joblog.log(ctx.root, SCOPE, f"调研：{h.source} 摘要直接入库（不占抓取页数）：{h.title[:40]}")
                        continue
                if attempts >= max_pages:
                    result["skipped"].append(f"已达每题抓取上限 {max_pages} 页")
                    joblog.log(ctx.root, SCOPE, f"调研：已达每题抓取上限 {max_pages} 页，停止抓取", level="warn")
                    break
                attempts += 1
                joblog.log(ctx.root, SCOPE, f"调研：抓取 {attempts}/{max_pages}：{h.url}")
                page = fetcher.fetch(h.url)
                if not page["ok"]:
                    joblog.log(ctx.root, SCOPE, f"调研：未抓取，{page['error']}", level="warn")
                    ex = sources.excerpt(h.snippet, must, excerpt_max, terms) if len(h.snippet or "") >= 120 else ""
                    if ex:
                        _add_card(ctx, tid, h, h.title, ex, must, terms, _avoid(ctx), result,
                                  source_type="abstract")
                        joblog.log(ctx.root, SCOPE, "调研：页面不可用，改用搜索结果中的摘要入库")
                    else:
                        result["skipped"].append(f"{h.url}：{page['error']}")
                    continue
                if page.get("note"):
                    joblog.log(ctx.root, SCOPE, f"调研：{h.url}：{page['note']}", level="warn")
                title, text = extract.extract(page["html"])
                ex = sources.excerpt(text, must, excerpt_max, terms)
                if not ex:
                    result["skipped"].append(f"{h.url}：与选题要点无关")
                    joblog.log(ctx.root, SCOPE, "调研：页面与选题要点无关，不入库")
                    continue
                via = "（浏览器渲染）" if page.get("via") == "browser" else ""
                _add_card(ctx, tid, h, title or h.title or h.url, ex, must, terms, _avoid(ctx), result,
                          source_type="web", via=via)

            ev = pack.evaluate(ctx, tid, topic, _min_cred(ctx, eff))
            if ev["sufficient"]:
                break
            if attempts >= max_pages:
                break
            if round_no >= max_qrounds:
                joblog.log(ctx.root, SCOPE, f"调研：已达最多 {max_qrounds} 轮检索，证据仍不足", level="warn")
                break
            zh_pending, en_pending = queries.expand(topic, used_queries(used), en_seed)
            result["queries"] += zh_pending
            result["english_queries"] += en_pending
            if not zh_pending and not en_pending:
                joblog.log(ctx.root, SCOPE, "调研：已没有新的查询可以尝试", level="warn")
                break
            joblog.log(ctx.root, SCOPE, f"调研：证据不足，补充 {len(zh_pending)} 条中文、{len(en_pending)} 条英文查询，"
                                        f"进入第 {round_no + 1} 轮")
        result["rounds"] = round_no
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
