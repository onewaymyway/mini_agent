"""entrypoints/list_projects.py — 列出项目集合中的全部项目（JSON）。"""

from __future__ import annotations

import json
import sys

from _common import force_utf8_stdio  # noqa: E402

from article_to_video.paths import projects_root  # noqa: E402
from article_to_video.project_store import list_projects  # noqa: E402


def main() -> int:
    force_utf8_stdio()
    root = projects_root()
    print(json.dumps({"ok": True, "projects_root": str(root), "projects": list_projects(root)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
