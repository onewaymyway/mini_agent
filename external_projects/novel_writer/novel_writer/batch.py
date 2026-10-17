"""novel_writer/batch.py — 批量推进所有自动挡小说（M5，方案 6.5「批量/定时（可选、默认关）」）。

规则：
  * 只处理挡位在 `AUTO_GEARS`（默认 G2 / G3）内的小说；G0 / G1 始终需要人逐步确认，不纳入批量；
  * 每本书最多推进 `max_chapters` 章（默认 1）——批量是「小步快跑」，不是无限续写；
  * 已有存活 worker 的书跳过（同一本书同一时刻只允许一个 worker）；
  * 预算、审批、待答复等任何需要人的地方都会让该书停下，结果写进摘要，不视为失败；
  * `dry_run=True` 只列出将要执行的书，不调用执行器，用于预览与 cron 前的核对。

默认不接入任何定时任务：`project.yaml` 只登记入口，cron 行需要你手动添加（见 PROJECT.md）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence

from novel_writer import runner as R
from novel_writer import shelf
from novel_writer import state as st
from novel_writer.worker import run_worker
from novel_writer.workspace import Workspace

AUTO_GEARS = ("G2", "G3")
DEFAULT_MAX_CHAPTERS = 1


def advance_all_auto(
    *,
    novels_dir: Optional[Path | str] = None,
    index_file: Optional[Path | str] = None,
    gears: Iterable[str] = AUTO_GEARS,
    max_chapters: int = DEFAULT_MAX_CHAPTERS,
    dry_run: bool = False,
    invoker: Optional[R.Invoker] = None,
    handlers: Optional[Dict[str, R.PyHandler]] = None,
    only: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """依次推进每本自动挡小说。返回逐本摘要与汇总（不抛异常）。"""
    if not isinstance(max_chapters, int) or isinstance(max_chapters, bool) or max_chapters < 1:
        return {"ok": False, "error": "max_chapters 必须是正整数"}
    allowed = set(gears)
    wanted = {str(p) for p in only} if only else None
    rows = []
    for root in shelf.discover(
        Path(novels_dir) if novels_dir else None, Path(index_file) if index_file else None,
    ):
        ws = Workspace(root)
        settings = ws.settings()
        title = _title(ws)
        row: Dict[str, Any] = {"workspace": str(ws.root), "title": title, "gear": settings.get("gear")}
        if wanted is not None and str(ws.root) not in wanted:
            continue
        if settings.get("gear") not in allowed:
            row.update(action="skipped", reason=f"挡位 {settings.get('gear')} 不在批量范围内")
        elif st.worker_alive(ws):
            row.update(action="skipped", reason="该小说已有存活的 worker")
        elif dry_run:
            row.update(action="would_run", reason=f"将推进至多 {max_chapters} 章")
        elif invoker is None:
            row.update(action="error", reason="未提供执行器（invoker）")
        else:
            res = run_worker(ws, mode="until_gate", invoker=invoker, handlers=handlers,
                             max_chapters=max_chapters)
            row.update(
                action="ran" if res.get("ok") else "error",
                reason=res.get("reason"),
                executed=len(res.get("executed") or []),
                error=res.get("error"),
            )
        rows.append(row)

    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["action"]] = counts.get(row["action"], 0) + 1
    return {"ok": True, "dry_run": dry_run, "max_chapters": max_chapters, "counts": counts, "novels": rows}


def _title(ws: Workspace) -> str:
    from novel_writer.fsutil import read_json

    manifest = read_json(ws.path("studio_manifest"), default=None)
    if isinstance(manifest, dict) and manifest.get("title"):
        return str(manifest["title"])
    return ws.root.name
