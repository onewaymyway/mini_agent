"""novel_writer/contamination.py — 补救某章后的「上下文污染」检查（纯规则，零 LLM 成本）。

背景：第 N 章是乱码时，它的概述、人物/伏笔状态同步、以及后续章节的上下文包都可能已经吸收了乱码版本。
补救（重新生成第 N 章）之后，需要确认后续章节没有基于旧版本写作。

规则（以第 N 章正文文件的修改时间为「修复时刻」）：
  1. 后续章节正文存在乱码信号 → 污染（正文本身）；
  2. 后续章节概述存在乱码信号 → 污染（概述本身）；
  3. 后续章节的上下文包早于修复时刻 → 写作时读到的是旧版第 N 章信息 → 需要重跑；
  4. 后续章节的概述早于修复时刻 → 概述可能基于旧版第 N 章 → 需要核对；
  5. 人物档案 / 伏笔追踪早于修复时刻 → 全书共享状态可能已吸收乱码版本 → 需要核对；
  6. 找不到上下文包记录 → 无法确认，单独列出（不算污染，但需要人工看）。

注意：依赖文件修改时间。若文件被拷贝/还原导致时间戳改变，结果可能偏差，报告会注明依据。
本模块只检查、不改任何文件；是否重跑由作者决定。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from novel_writer.chapter_ops import chapter_file
from novel_writer.fsutil import atomic_write_json
from novel_writer.garble import is_garbled
from novel_writer.textio import read_text_utf8
from novel_writer.workspace import Workspace

REPORT_NAME = "contamination_check_ch{n:02d}.json"


def _mtime(path: Optional[Path]) -> Optional[float]:
    if path is None or not path.is_file():
        return None
    return path.stat().st_mtime


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return _dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _garble_reasons(path: Optional[Path]) -> Optional[List[str]]:
    if path is None or not path.is_file():
        return None
    report = is_garbled(read_text_utf8(path))
    return report.reasons if report.garbled else []


def scan_contamination(
    ws: Workspace, *, garbled_chapter: int, chapters: Sequence[int],
) -> Dict[str, Any]:
    fixed_path = chapter_file(ws, garbled_chapter)
    fixed_at = _mtime(fixed_path)
    findings: List[Dict[str, Any]] = []
    unknown: List[Dict[str, Any]] = []

    if fixed_at is None:
        return {
            "garbled_chapter": garbled_chapter,
            "error": f"第 {garbled_chapter} 章正文不存在，无法确定修复时刻",
            "contaminated": True,
            "findings": [], "unknown": [],
        }

    def add(kind: str, chapter: int, detail: str, path: Optional[Path] = None) -> None:
        findings.append({"chapter": chapter, "kind": kind, "detail": detail,
                         "file": str(path.relative_to(ws.root)) if path else None})

    # 全书共享状态：只能整体核对
    for key, label in (("characters", "人物档案"), ("plot_tracker", "伏笔追踪")):
        p = ws.path(key)
        if _mtime(p) is not None and _mtime(p) < fixed_at:
            add("shared_state_before_fix", 0,
                f"{label}写于第 {garbled_chapter} 章修复之前，可能已吸收乱码版本，需核对", p)

    for n in chapters:
        text_path = chapter_file(ws, n)
        if text_path is None:
            unknown.append({"chapter": n, "detail": "正文不存在（其余产物仍照常检查）"})
        else:
            reasons = _garble_reasons(text_path)
            if reasons:
                add("garbled_text", n, "正文本身为乱码：" + "；".join(reasons), text_path)

        summary = ws.path("chapter_summary", nn=n)
        s_reasons = _garble_reasons(summary)
        if s_reasons:
            add("garbled_summary", n, "概述为乱码：" + "；".join(s_reasons), summary)
        elif _mtime(summary) is not None and _mtime(summary) < fixed_at:
            add("summary_before_fix", n,
                f"概述早于第 {garbled_chapter} 章修复，可能基于旧版内容，需核对", summary)

        ctx = ws.path("context_pack", n=n)
        ctx_ts = _mtime(ctx)
        if ctx_ts is None:
            unknown.append({"chapter": n, "detail": "找不到上下文包记录，无法确认写作时读到的是哪一版"})
        elif ctx_ts < fixed_at:
            add("stale_context", n,
                f"上下文包（{_iso(ctx_ts)}）早于第 {garbled_chapter} 章修复（{_iso(fixed_at)}），写作时读到的是旧版，需要重跑", ctx)

    return {
        "garbled_chapter": garbled_chapter,
        "fixed_at": _iso(fixed_at),
        "checked_chapters": list(chapters),
        "basis": "文件修改时间与规则匹配；见 novel_writer/contamination.py 模块说明",
        "contaminated": bool(findings),
        "findings": findings,
        "unknown": unknown,
    }


def write_contamination_report(ws: Workspace, report: Dict[str, Any]) -> Path:
    path = ws.studio / "inbox" / REPORT_NAME.format(n=report["garbled_chapter"])
    atomic_write_json(path, report)
    return path
