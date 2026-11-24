"""entrypoints/research.py — 资料收集（P5）：对已放行的选题搜索、抓取并整理证据包。

用法：
  python entrypoints/research.py <domain_id> --all                      一键调研全部已放行的选题（逐个进行，一题失败不影响其它）
  python entrypoints/research.py <domain_id> <topic_id>                 调研（需要网络；搜索失败时仅用手动资料）
  python entrypoints/research.py <domain_id> <topic_id> --add-url URL    抓取一个网址，作为手动来源加入证据包
  python entrypoints/research.py <domain_id> <topic_id> --remove-source src-0003
说明：来源可信度按域名启发式分级（A–D），章程 sources.avoid 命中为 D。证据不足的选题会被放弃，并写明原因。
调研结果可重复运行：未过期的搜索与网页缓存不会重复请求。
"""

from __future__ import annotations

import json
import sys

from _common import force_utf8_stdio  # noqa: E402

from article_garden import domains, joblog, settings  # noqa: E402
from article_garden import budget  # noqa: E402
from article_garden.providers.text import MiniAgentTextLLM, text_model_status  # noqa: E402
from article_garden.research import fetch, runner, sources  # noqa: E402


def _opt(args, flag):
    if flag in args:
        i = args.index(flag)
        val = args[i + 1]
        del args[i:i + 2]
        return val
    return None


def _llm_for(ctx):
    ready, why = text_model_status()
    if not ready:
        joblog.log(ctx.root, "research", f"模型未就绪，查询使用模板：{why}", level="warn")
        return None
    eff, _ = settings.effective_settings(ctx)
    return budget.guard(ctx, MiniAgentTextLLM(use_system_proxy=eff["llm"]["use_system_proxy"]),
                        timeout_min=eff["budget"]["step_timeout_minutes"])


def _run_all(ctx) -> int:
    """一键调研：逐个调研全部已放行选题。单题失败只记录，预算用尽或用户停止则整批结束。"""
    from article_garden.planning import states
    approved = [t["id"] for t in states.load_topics(ctx) if t["status"] == states.APPROVED]
    if not approved:
        print(json.dumps({"ok": True, "message": "没有已放行的选题"}, ensure_ascii=False))
        return 0
    eff, _ = settings.effective_settings(ctx)
    llm = _llm_for(ctx)
    results = []
    with joblog.run(ctx.root, "research", f"调研全部已放行选题（{len(approved)} 个）"):
        for i, tid in enumerate(approved, 1):
            joblog.log(ctx.root, "research", f"调研 {i}/{len(approved)}：{tid}「{states.get(ctx, tid)['title']}」")
            try:
                res = runner.run(ctx, tid, llm=llm, max_rounds=eff["budget"]["max_repair_rounds"])
                results.append({"topic": tid, "result": res["result"], "added": res["added"]})
            except ValueError as exc:
                results.append({"topic": tid, "result": "error", "error": str(exc)})
                joblog.log(ctx.root, "research", f"调研 {tid} 未执行：{exc}", level="warn")
    ok = sum(1 for r in results if r["result"] == "researched")
    print(json.dumps({"ok": True, "total": len(approved), "researched": ok, "results": results},
                     ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    force_utf8_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        add_url = _opt(args, "--add-url")
        remove = _opt(args, "--remove-source")
        run_all = "--all" in args
        args = [a for a in args if a != "--all"]
        if len(args) < (1 if run_all else 2):
            print(__doc__, file=sys.stderr)
            return 2
        ctx = domains.DomainContext(args[0])
        tid = None if run_all else args[1]
        if run_all:
            return _run_all(ctx)
        if remove:
            runner.remove_source(ctx, tid, remove)
            print(json.dumps({"ok": True, "removed": remove}, ensure_ascii=False))
            return 0
        if add_url:
            res = fetch.Fetcher(ctx).fetch(add_url)
            if not res.get("ok"):
                print(f"抓取失败：{res.get('error')}", file=sys.stderr)
                return 1
            from article_garden.research import extract
            title, text = extract.extract(res["html"])
            eff, _ = settings.effective_settings(ctx)
            from article_garden.planning import states
            topic = states.get(ctx, tid)
            ex = sources.excerpt(text, list(topic.get("must_cover") or []), eff["thresholds"]["excerpt_chars_max"])
            if not ex:
                print("该网页与选题要点无关，未加入", file=sys.stderr)
                return 1
            cred = sources.classify(add_url, ctx.load_charter()[0].get("sources", {}).get("avoid") or [])
            card = sources.make_card(sources.next_id(ctx, tid), tid, url=add_url, title=title or add_url,
                                     text=ex, credibility=cred, source_type="manual_url",
                                     points=list(topic.get("must_cover") or []))
            sources.write(ctx, tid, card)
            print(json.dumps({"ok": True, "added": card["id"], "credibility": cred}, ensure_ascii=False))
            return 0
        ready, why = text_model_status()
        llm = None
        if ready:
            eff, _ = settings.effective_settings(ctx)
            llm = budget.guard(ctx, MiniAgentTextLLM(use_system_proxy=eff["llm"]["use_system_proxy"]),
                                    timeout_min=eff["budget"]["step_timeout_minutes"])
        else:
            joblog.log(ctx.root, "research", f"模型未就绪，查询使用模板：{why}", level="warn")
        result = runner.run(ctx, tid, llm=llm,
                            max_rounds=settings.effective_settings(ctx)[0]["budget"]["max_repair_rounds"])
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
        return 0 if result["result"] == "researched" else 1
    except (domains.DomainError, ValueError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
