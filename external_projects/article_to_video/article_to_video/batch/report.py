"""batch/report.py — 队列报告（R23 第三期）：每次运行结束写 report.json 与 report.md。

位置：<项目集合根目录>/_queue/report.json、report.md（每次运行结束覆盖写入）。

报告内容（每篇一行）：状态、重试次数、耗时（墙钟，从开始处理到结束）、生图次数（读项目的 budget.json）、
成片路径（相对于项目集合根目录）、失败或停止原因。路径只用于展示，不影响任何状态。

纯读取：不调用模型，不修改队列与项目；写文件失败只由调用方决定是否忽略。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..project_store import now_iso, read_json, write_json_atomic, write_text_atomic
from . import store
from .prep import STATE_LABELS

REPORT_JSON = "report.json"
REPORT_MD = "report.md"


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _wall_seconds(item: Dict[str, Any]) -> Optional[float]:
    start, end = _parse_iso(item.get("started_at", "")), _parse_iso(item.get("finished_at", ""))
    if start is None or end is None:
        return None
    return max((end - start).total_seconds(), 0.0)


def _image_calls(project_dir: Path) -> Optional[int]:
    """读项目的 budget.json（Ledger 写入的生图次数）；项目未开始或文件缺失时返回 None。"""
    p = project_dir / "budget.json"
    if not p.is_file():
        return None
    try:
        return int(read_json(p).get("image_calls", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _rel(root: Path, path: str) -> str:
    """成片路径改为相对于项目集合根目录的写法，便于在另一台机器上查看；不在根目录下则原样返回。"""
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return path


def _row(root: Path, item: Dict[str, Any]) -> Dict[str, Any]:
    project_id = item.get("project_id") or ""
    pdir = Path(root) / project_id if project_id else None
    wall = _wall_seconds(item)
    return {
        "id": item["id"],
        "title": item["title"],
        "author": item.get("author", ""),
        "state": item["state"],
        "state_label": STATE_LABELS.get(item["state"], item["state"]),
        "failures": int(item.get("failures", 0)),
        "error": item.get("error", ""),
        "project_id": project_id,
        "image_calls": _image_calls(pdir) if pdir is not None and pdir.is_dir() else None,
        "started_at": item.get("started_at", ""),
        "finished_at": item.get("finished_at", ""),
        "wall_seconds": round(wall, 1) if wall is not None else None,
        "outputs": [_rel(root, o) for o in item.get("outputs", [])],
    }


def build_report(root: Path, *, now: Optional[str] = None) -> Dict[str, Any]:
    """汇总队列当前状态为报告数据（不写盘）。"""
    data = store.load(root)
    rows: List[Dict[str, Any]] = [_row(root, it) for it in data["items"]]
    counts = {s: 0 for s in store.STATES}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    timed = [r["wall_seconds"] for r in rows if r["wall_seconds"] is not None and r["state"] in ("done", "failed")]
    images = [r["image_calls"] for r in rows if r["image_calls"] is not None]
    return {
        "generated_at": now or now_iso(),
        "gear": data["params"]["gear"],
        "until": data["params"]["until"],
        "runner_alive": store.runner_alive(root),
        "stop_requested": store.stop_requested(root),
        "total": len(rows),
        "counts": counts,
        "totals": {
            "image_calls": sum(images),
            "done_wall_seconds": round(sum(timed), 1),
            "failed_items": [r["id"] for r in rows if r["state"] == "failed"],
        },
        "items": rows,
    }


def _flat(text: Any) -> str:
    """压成单行（换行、回车改为空格），用于列表项与表格单元格。"""
    s = "" if text is None else str(text)
    return " ".join(s.split())


def _md_cell(text: Any) -> str:
    """表格单元格转义：管道符、换行不能破坏表格结构。"""
    return _flat(text).replace("|", "\\|") or "—"


def _fmt_seconds(sec: Optional[float]) -> str:
    if sec is None:
        return "—"
    m, s = divmod(int(round(sec)), 60)
    h, m = divmod(m, 60)
    return f"{h}时{m:02d}分{s:02d}秒" if h else f"{m}分{s:02d}秒"


def render_markdown(report: Dict[str, Any]) -> str:
    c = report["counts"]
    lines = [
        "# 批量生成队列报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 挡位：{report['gear']}（运行到 {report['until']}）",
        f"- 队列状态：{'运行中' if report['runner_alive'] else '未运行'}"
        f"{'，已请求停止' if report['stop_requested'] else ''}",
        f"- 条目：共 {report['total']} 篇；完成 {c.get('done', 0)}、失败 {c.get('failed', 0)}、"
        f"已停止 {c.get('stopped', 0)}、停在检查点 {c.get('waiting', 0)}、"
        f"超出预算 {c.get('budget_exceeded', 0)}、排队中 {c.get('queued', 0)}、运行中 {c.get('running', 0)}",
        f"- 生图次数合计：{report['totals']['image_calls']}（只统计已开始的项目）",
        f"- 已完成篇目的耗时合计（墙钟）：{_fmt_seconds(report['totals']['done_wall_seconds'])}",
        "",
    ]
    if not report["items"]:
        lines += ["队列为空。", ""]
        return "\n".join(lines)
    lines += [
        "## 逐篇",
        "",
        "| 编号 | 标题 | 作者 | 状态 | 重试 | 耗时 | 生图 | 成片 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in report["items"]:
        outs = "<br>".join(_md_cell(o) for o in r["outputs"]) if r["outputs"] else "—"
        lines.append("| " + " | ".join([
            _md_cell(r["id"]), _md_cell(r["title"]), _md_cell(r["author"]), _md_cell(r["state_label"]),
            str(r["failures"]), _fmt_seconds(r["wall_seconds"]),
            "—" if r["image_calls"] is None else str(r["image_calls"]), outs,
        ]) + " |")
    problems = [r for r in report["items"] if r["error"] and r["state"] != "done"]
    if problems:
        lines += ["", "## 未完成原因", ""]
        for r in problems:
            lines.append(f"- {r['id']}《{_flat(r['title'])}》（{r['state_label']}）：{_flat(r['error'])}")
    lines.append("")
    return "\n".join(lines)


def write_report(root: Path) -> Dict[str, Any]:
    """写 report.json 与 report.md，返回报告数据。"""
    report = build_report(root)
    qdir = store.queue_dir(root)
    qdir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(qdir / REPORT_JSON, report)
    write_text_atomic(qdir / REPORT_MD, render_markdown(report))
    return report


def write_report_quietly(root: Path) -> None:
    """运行结束时调用：报告写失败不能影响队列本身的状态，只忽略。"""
    try:
        write_report(root)
    except (OSError, ValueError):
        pass
