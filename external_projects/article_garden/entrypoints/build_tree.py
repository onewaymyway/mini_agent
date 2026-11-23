"""entrypoints/build_tree.py — 知识树（P3）：生成挂载提案、应用提案、计算覆盖度。

用法：
  python entrypoints/build_tree.py <domain_id>            生成挂载提案（需模型；无变化则不调用模型）并计算覆盖度
  python entrypoints/build_tree.py <domain_id> --apply    确认应用待定提案（不调用模型）
  python entrypoints/build_tree.py <domain_id> --discard  放弃待定提案
  python entrypoints/build_tree.py <domain_id> --coverage 只重算覆盖度（不调用模型）
自动变更只以提案形式出现，必须经 --apply 或看板确认后才写入知识树。
"""

from __future__ import annotations

import json
import sys

from _common import force_utf8_stdio  # noqa: E402

from article_garden import domains, joblog, settings  # noqa: E402
from article_garden.providers.text import MiniAgentTextLLM, text_model_status  # noqa: E402
from article_garden.wiki import coverage, placement  # noqa: E402


def main(argv=None) -> int:
    force_utf8_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    mode = "propose"
    for flag, m in (("--apply", "apply"), ("--discard", "discard"), ("--coverage", "coverage")):
        if flag in args:
            mode = m
            args = [a for a in args if a != flag]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        ctx = domains.DomainContext(args[0])
        if mode == "discard":
            placement.discard_proposal(ctx)
            print(json.dumps({"ok": True, "discarded": True}, ensure_ascii=False))
            return 0
        if mode == "apply":
            summary = placement.apply_proposal(ctx)
            cov = coverage.compute(ctx)
            print(json.dumps({"ok": True, "applied": summary, "coverage_nodes": len(cov["nodes"])},
                             ensure_ascii=False, indent=2))
            return 0
        if mode == "coverage":
            cov = coverage.compute(ctx)
            print(json.dumps({"ok": True, "nodes": len(cov["nodes"])}, ensure_ascii=False))
            return 0
        ready, why = text_model_status()
        if not ready:
            msg = f"模型未就绪：{why}"
            print(msg, file=sys.stderr)
            joblog.log(ctx.root, "tree", msg, level="error")
            return 1
        eff, _ = settings.effective_settings(ctx)
        llm = MiniAgentTextLLM(use_system_proxy=eff["llm"]["use_system_proxy"])
        proposal = placement.make_proposal(ctx, llm, max_repair_rounds=eff["budget"]["max_repair_rounds"])
        coverage.compute(ctx)
        joblog.log(ctx.root, "tree", "挂载提案：" + ("已生成，待确认" if proposal else "无变化"), level="ok")
        print(json.dumps({"ok": True, "proposal": proposal}, ensure_ascii=False, indent=2))
        return 0
    except ValueError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
