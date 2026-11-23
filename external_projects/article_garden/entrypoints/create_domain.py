"""entrypoints/create_domain.py — 新建文章库。

用法：python entrypoints/create_domain.py <domain_id> <名称> [模板] [--copy-charter-from <id>]
模板：blank（默认）/ general_tech / ai。新库的自动化开关一律保守默认（全关、G0）。
"""

from __future__ import annotations

import json
import sys

from _common import force_utf8_stdio  # noqa: E402

from article_garden import domains  # noqa: E402


def main(argv=None) -> int:
    force_utf8_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    copy_from = None
    if "--copy-charter-from" in args:
        i = args.index("--copy-charter-from")
        try:
            copy_from = args[i + 1]
        except IndexError:
            print("--copy-charter-from 后需要库 id", file=sys.stderr)
            return 2
        del args[i:i + 2]
    if len(args) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    domain_id, name = args[0], args[1]
    template = args[2] if len(args) > 2 else "blank"
    try:
        ctx = domains.create_domain(domain_id, name, template, copy_charter_from=copy_from)
    except (domains.DomainError, KeyError) as exc:
        print(f"创建失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "id": ctx.id, "root": str(ctx.root)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
