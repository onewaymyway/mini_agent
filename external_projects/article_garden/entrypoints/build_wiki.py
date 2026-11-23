"""entrypoints/build_wiki.py — 构建概念与关系（P2：归一、概念页、断言账本、关系边、冲突）。需要模型。

用法：
  python entrypoints/build_wiki.py <domain_id>            增量构建（依据未变的概念复用已有定义）
  python entrypoints/build_wiki.py <domain_id> --force    全部概念的定义重写
前提：至少有一篇摘要成功的文章（见 digest.py）。
"""

from __future__ import annotations

import json
import sys

from _common import force_utf8_stdio  # noqa: E402

from article_garden import domains, joblog, settings  # noqa: E402
from article_garden import budget  # noqa: E402
from article_garden.providers.text import MiniAgentTextLLM, text_model_status  # noqa: E402
from article_garden.wiki import build  # noqa: E402


def main(argv=None) -> int:
    force_utf8_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    force = "--force" in args
    args = [a for a in args if a != "--force"]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        ctx = domains.DomainContext(args[0])
    except domains.DomainError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    ready, why = text_model_status()
    if not ready:
        msg = f"模型未就绪：{why}"
        print(msg, file=sys.stderr)
        joblog.log(ctx.root, "wiki", msg, level="error")
        return 1
    eff, _ = settings.effective_settings(ctx)
    llm = budget.guard(ctx, MiniAgentTextLLM(use_system_proxy=eff["llm"]["use_system_proxy"]),
                            timeout_min=eff["budget"]["step_timeout_minutes"])
    try:
        result = build.build(ctx, llm, force=force)
    except ValueError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        joblog.log(ctx.root, "wiki", f"失败：{exc}", level="error")
        return 1
    print(json.dumps({"ok": result["failed_concepts"] == 0, **result}, ensure_ascii=False, indent=2))
    return 0 if result["failed_concepts"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
