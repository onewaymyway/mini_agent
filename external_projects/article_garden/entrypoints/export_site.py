"""entrypoints/export_site.py — 导出（P7）：静态 wiki 站与已发布文章的 zip。只包含 status=published 的文章。

用法：
  python entrypoints/export_site.py <domain_id>            导出静态站到 exports/site/（index.html 为入口）
  python entrypoints/export_site.py <domain_id> --zip      打包已发布文章到 exports/<id>_published_<时间>.zip
"""

from __future__ import annotations

import json
import sys

from _common import force_utf8_stdio  # noqa: E402

from article_garden import domains  # noqa: E402
from article_garden import export  # noqa: E402


def main(argv=None) -> int:
    force_utf8_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    as_zip = "--zip" in args
    args = [a for a in args if a != "--zip"]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        ctx = domains.DomainContext(args[0])
        if as_zip:
            out = export.export_published_zip(ctx)
        else:
            out = export.export_site(ctx) / "index.html"
        print(json.dumps({"ok": True, "path": str(out)}, ensure_ascii=False))
        return 0
    except domains.DomainError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
