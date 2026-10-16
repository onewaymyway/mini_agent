#!/usr/bin/env python
"""entrypoints/list_novels.py — 列出书架上所有小说及进度摘要。

用法：
    python entrypoints/list_novels.py
"""

from __future__ import annotations

import _common  # noqa: F401

from novel_writer import tool_api
from novel_writer.config import ensure_dirs


def main() -> int:
    ensure_dirs()
    result = tool_api.list_novels()
    if not result["ok"]:
        print(result["error"]["message"])
        return 1
    novels = result["data"]["novels"]
    if not novels:
        print("（书架为空）")
        return 0
    for n in novels:
        if n.get("error"):
            print(f"{n['workspace']}\tERROR={n['error']}")
            continue
        print(
            f"{n['workspace']}\t{n['title']}\tgear={n['gear']}\tquality={n['quality']}\t"
            f"chapters={n['chapters_written']}/{n['total_chapters_planned'] or '?'}\twords={n['total_words']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("list_novels", main, trigger="manual"))
