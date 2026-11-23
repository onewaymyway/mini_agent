"""entrypoints/list_domains.py — 列出文章库（JSON）。用法：python entrypoints/list_domains.py [--all]（含已归档）。"""

from __future__ import annotations

import json
import sys

from _common import force_utf8_stdio  # noqa: E402

from article_garden import domains  # noqa: E402


def main(argv=None) -> int:
    force_utf8_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    rows = []
    for d in domains.list_domains(include_archived="--all" in args):
        try:
            d = dict(d, articles=domains.DomainContext(d["id"]).article_count())
        except domains.DomainError:
            d = dict(d, articles=None)
        rows.append(d)
    print(json.dumps({"domains": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
