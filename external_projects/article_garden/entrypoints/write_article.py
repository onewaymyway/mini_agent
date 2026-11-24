"""entrypoints/write_article.py — 成文与审阅（P6）。

用法：
  python entrypoints/write_article.py <domain_id> <topic_id>                      成文（需模型）
  python entrypoints/write_article.py <domain_id> <topic_id> --feedback "意见"      退回重写并附意见（需模型）
  python entrypoints/write_article.py <domain_id> --batch t-0001,t-0002          批量成文（逐个进行，一题失败不影响其它）
  python entrypoints/write_article.py <domain_id> --batch-all                    批量成文：全部「已调研」且证据充分的选题
  python entrypoints/write_article.py <domain_id> <topic_id> --approve            审阅通过（drafted → reviewed）
  python entrypoints/write_article.py <domain_id> <topic_id> --publish            发布（reviewed → published，需明确执行）
  python entrypoints/write_article.py <domain_id> <topic_id> --abandon "原因"      放弃选题（生成文章归档）
说明：生成文章默认 status=draft、origin=generated，不会自动发布；只出自生成文章的定义与冲突不进入 wiki。
"""

from __future__ import annotations

import json
import sys

from _common import force_utf8_stdio  # noqa: E402

from article_garden import domains, settings  # noqa: E402
from article_garden import budget  # noqa: E402
from article_garden.providers.text import MiniAgentTextLLM, text_model_status  # noqa: E402
from article_garden.writing import runner  # noqa: E402


def _opt(args, flag):
    if flag in args:
        i = args.index(flag)
        val = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        return val
    return None


def _batch(args, batch, batch_all) -> int:
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    ctx = domains.DomainContext(args[0])
    from article_garden.planning import states
    if batch_all:
        tids = [t["id"] for t in states.load_topics(ctx) if t["status"] == states.RESEARCHED and runner.writable(ctx, t["id"])]
        if not tids:
            print(json.dumps({"ok": True, "message": "没有可成文的已调研选题"}, ensure_ascii=False))
            return 0
    else:
        tids = [x.strip() for x in (batch or "").split(",") if x.strip()]
    ready, why = text_model_status()
    if not ready:
        print(f"失败：成文需要文本模型（{why}）", file=sys.stderr)
        return 1
    eff, _ = settings.effective_settings(ctx)
    llm = budget.guard(ctx, MiniAgentTextLLM(use_system_proxy=eff["llm"]["use_system_proxy"]),
                       timeout_min=eff["budget"]["step_timeout_minutes"])
    result = runner.write_batch(ctx, tids, llm, max_rounds=eff["budget"]["max_repair_rounds"])
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    force_utf8_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        feedback = _opt(args, "--feedback")
        abandon = _opt(args, "--abandon")
        batch = _opt(args, "--batch")
        batch_all = "--batch-all" in args
        approve = "--approve" in args
        publish = "--publish" in args
        args = [a for a in args if a not in ("--approve", "--publish", "--batch-all")]
        if batch is not None or batch_all:
            return _batch(args, batch, batch_all)
        if len(args) < 2:
            print(__doc__, file=sys.stderr)
            return 2
        ctx = domains.DomainContext(args[0])
        tid = args[1]
        if approve:
            print(json.dumps({"ok": True, "status": runner.approve(ctx, tid)["status"]}, ensure_ascii=False))
            return 0
        if publish:
            print(json.dumps({"ok": True, "status": runner.publish(ctx, tid)["status"]}, ensure_ascii=False))
            return 0
        if abandon is not None:
            print(json.dumps({"ok": True, "status": runner.abandon(ctx, tid, abandon)["status"]}, ensure_ascii=False))
            return 0
        ready, why = text_model_status()
        if not ready:
            print(f"失败：成文需要文本模型（{why}）", file=sys.stderr)
            return 1
        eff, _ = settings.effective_settings(ctx)
        llm = budget.guard(ctx, MiniAgentTextLLM(use_system_proxy=eff["llm"]["use_system_proxy"]),
                                timeout_min=eff["budget"]["step_timeout_minutes"])
        result = runner.write(ctx, tid, llm, max_rounds=eff["budget"]["max_repair_rounds"],
                              feedback=feedback or "")
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
        return 0 if result["result"] == "drafted" else 1
    except (domains.DomainError, ValueError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
