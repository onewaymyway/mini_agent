"""entrypoints/import_library.py — 导入文章（A 导入）。

用法：
  python entrypoints/import_library.py <domain_id> <路径…>            仅预览（不写入）
  python entrypoints/import_library.py <domain_id> <路径…> --apply    确认导入
路径可以是 .md/.txt 文件、zip 包或目录（递归）。源文件只读，不会被修改。
"""

from __future__ import annotations

import json
import sys

from _common import force_utf8_stdio  # noqa: E402

from article_garden import domains  # noqa: E402
from article_garden.library import importer  # noqa: E402


def main(argv=None) -> int:
    force_utf8_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    apply_ = "--apply" in args
    args = [a for a in args if a != "--apply"]
    if len(args) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        ctx = domains.DomainContext(args[0])
    except domains.DomainError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    cands = importer.collect_paths(args[1:])
    if apply_:
        result = importer.apply(ctx, cands)
        print(json.dumps({"ok": True, "imported": result["imported"], "rows": result["rows"]},
                         ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"ok": True, "applied": False, "rows": importer.preview(ctx, cands)},
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
