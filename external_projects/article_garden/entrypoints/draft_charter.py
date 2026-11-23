"""entrypoints/draft_charter.py — 由已摘要文章反推章程草稿（需模型）。

用法：
  python entrypoints/draft_charter.py <domain_id>            生成草稿（写入 drafts/charter_draft.yaml）
  python entrypoints/draft_charter.py <domain_id> --adopt    把草稿采用为正式章程（旧章程备份为 .bak）
"""

from __future__ import annotations

import json
import sys

from _common import force_utf8_stdio  # noqa: E402

from article_garden import domains, joblog, settings  # noqa: E402
from article_garden.digest import charter_draft  # noqa: E402
from article_garden.providers.text import MiniAgentTextLLM, text_model_status  # noqa: E402


def main(argv=None) -> int:
    force_utf8_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    adopt = "--adopt" in args
    args = [a for a in args if a != "--adopt"]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        ctx = domains.DomainContext(args[0])
        if adopt:
            warnings = charter_draft.adopt(ctx)
            print(json.dumps({"ok": True, "adopted": True, "warnings": warnings}, ensure_ascii=False, indent=2))
            return 0
        ready, why = text_model_status()
        if not ready:
            msg = f"模型未就绪：{why}"
            print(msg, file=sys.stderr)
            joblog.log(ctx.root, "charter", msg, level="error")
            return 1
        eff, _ = settings.effective_settings(ctx)
        llm = MiniAgentTextLLM(use_system_proxy=eff["llm"]["use_system_proxy"])
        result = charter_draft.draft(ctx, llm, max_repair_rounds=eff["budget"]["max_repair_rounds"])
    except (domains.DomainError, ValueError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
