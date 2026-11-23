"""entrypoints/digest.py — 单篇摘要（增量）。

用法：
  python entrypoints/digest.py <domain_id>                    摘要所有未处理/已变更的文章
  python entrypoints/digest.py <domain_id> --article-id a-0003 只处理指定文章（可重复指定）
  python entrypoints/digest.py <domain_id> --force            忽略缓存，全部重做
需要 mini_agent 的文本模型配置（见看板「设置 → 模型与搜索」的就绪检查）。
"""

from __future__ import annotations

import json
import sys

from _common import force_utf8_stdio  # noqa: E402

from article_garden import domains, joblog, settings  # noqa: E402
from article_garden.digest import runner  # noqa: E402
from article_garden.providers.text import MiniAgentTextLLM, text_model_status  # noqa: E402


def main(argv=None) -> int:
    force_utf8_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    only = []
    while "--article-id" in args:
        i = args.index("--article-id")
        try:
            only.append(args[i + 1])
        except IndexError:
            print("--article-id 后需要文章 id", file=sys.stderr)
            return 2
        del args[i:i + 2]
    force = "--force" in args
    args = [a for a in args if a != "--force"]
    try:
        ctx = domains.DomainContext(args[0])
    except domains.DomainError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    ready, why = text_model_status()
    if not ready:
        msg = f"模型未就绪，无法摘要：{why}"
        print(msg, file=sys.stderr)
        joblog.log(ctx.root, "digest", msg, level="error")
        return 1
    eff, _ = settings.effective_settings(ctx)
    llm = MiniAgentTextLLM(use_system_proxy=eff["llm"]["use_system_proxy"])
    counts = runner.run_digest(ctx, llm, only=only or None, force=force)
    print(json.dumps({"ok": counts["failed"] == 0, "counts": counts}, ensure_ascii=False, indent=2))
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
