"""报告生成：按时间窗口汇总条目、热榜变化与异常，输出 markdown 与 json。

输出位置：
  reports/<YYYY-MM-DD>/<HHMM>-<plan_id>.md   报告正文（markdown，人可直接阅读）
  reports/<YYYY-MM-DD>/<HHMM>-<plan_id>.json 结构化数据（看板与听报使用）
  reports/latest.json                        指向最新报告的指针
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from adapters.base import SourceSpec
from core.scheduler import parse_interval
from core.settings import Settings
from core.sources import load_sources
from core.store import atomic_write_json, atomic_write_text, read_json, read_jsonl

MAX_ITEMS_PER_SOURCE = 20
MAX_CHANGES_PER_KIND = 10

_ERROR_LINE_RE = re.compile(r"^- (\d{2}:\d{2}:\d{2}) `([^`]+)` 失败 \| 错误：(.*?) \| 耗时")


def window_bounds(plan: dict[str, Any], now: datetime) -> tuple[datetime, datetime]:
    if "window" in plan:
        return now - parse_interval(plan["window"]), now
    return now - parse_interval(plan.get("every", "24h")), now


def _in_window(ts: str, start: datetime, end: datetime) -> bool:
    try:
        t = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return False
    return start <= t <= end


def _days(start: datetime, end: datetime) -> list[str]:
    days, cur = [], start.date()
    while cur <= end.date():
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


def collect_items(settings: Settings, start: datetime, end: datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not settings.paths.items_dir.exists():
        return out
    for day in _days(start, end):
        for path in settings.paths.items_dir.glob(f"*/{day}.jsonl"):
            for item in read_jsonl(path):
                if _in_window(item.get("fetched_at", ""), start, end):
                    out.append(item)
    return out


def collect_latest_changes(settings: Settings, start: datetime, end: datetime) -> dict[str, dict[str, Any]]:
    """每个源在窗口内最新一次快照的 changes（热榜变化）。"""
    latest: dict[str, dict[str, Any]] = {}
    if not settings.paths.raw_dir.exists():
        return latest
    for day in _days(start, end):
        for path in settings.paths.raw_dir.glob(f"*/{day}/*.json"):
            snap = read_json(path, default=None)
            if not snap or not _in_window(snap.get("fetched_at", ""), start, end):
                continue
            sid = snap.get("source_id", path.parent.parent.name)
            prev = latest.get(sid)
            if prev is None or snap["fetched_at"] > prev["fetched_at"]:
                latest[sid] = snap
    return latest


def collect_errors(settings: Settings, start: datetime, end: datetime) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if not settings.paths.runs_dir.exists():
        return errors
    tz = start.tzinfo
    for day in _days(start, end):
        path = settings.paths.runs_dir / f"{day}.log.md"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            m = _ERROR_LINE_RE.match(line)
            if not m:
                continue
            ts = datetime.fromisoformat(f"{day}T{m.group(1)}").replace(tzinfo=tz)
            if start <= ts <= end:
                errors.append({"time": ts.isoformat(timespec="seconds"), "source_id": m.group(2),
                               "error": m.group(3)})
    return errors


def _selected_categories(plan: dict[str, Any], settings: Settings) -> list[dict[str, str]]:
    wanted = plan.get("categories", "all")
    if wanted == "all":
        return settings.categories
    return [c for c in settings.categories if c["id"] in wanted]


def build_digest(settings: Settings, plan: dict[str, Any], now: datetime) -> dict[str, Any]:
    start, end = window_bounds(plan, now)
    loaded = load_sources(settings)
    specs: dict[str, SourceSpec] = loaded.specs
    items = collect_items(settings, start, end)
    changes = collect_latest_changes(settings, start, end)
    errors = collect_errors(settings, start, end)

    # 订阅更新单独成节、置顶显示，不再出现在分类小节里
    sub_ids = {sid for sid, sp in specs.items() if sp.kind == "subscription"}
    subscriptions = [
        {
            "source_id": it["source_id"],
            "source_name": specs[it["source_id"]].name,
            "title": it["title"],
            "url": it.get("url", ""),
            "current": it.get("extra", {}).get("cursor", ""),
            "previous_title": it.get("extra", {}).get("previous_title", ""),
            "fetched_at": it.get("fetched_at", ""),
        }
        for it in sorted(items, key=lambda x: x.get("fetched_at", ""))
        if it["source_id"] in sub_ids
    ]
    items = [it for it in items if it["source_id"] not in sub_ids]

    by_cat: dict[str, dict[str, list]] = {}
    for it in items:
        by_cat.setdefault(it["category"], {}).setdefault(it["source_id"], []).append(it)

    sections = []
    for cat in _selected_categories(plan, settings):
        src_map = by_cat.get(cat["id"], {})
        hot_ids = [sid for sid, s in specs.items() if s.category == cat["id"] and s.kind == "hot_list"]
        if not src_map and not any(sid in changes for sid in hot_ids):
            continue
        source_blocks = []
        for sid in sorted(set(src_map) | set(hot_ids)):
            spec = specs.get(sid)
            snap = changes.get(sid)
            hot = None
            if snap and snap.get("changes") and spec and spec.kind == "hot_list":
                ch = snap["changes"]
                hot = {
                    "first_snapshot": ch.get("first_snapshot", False),
                    "new": ch.get("new", [])[:MAX_CHANGES_PER_KIND],
                    "rising": ch.get("rising", [])[:MAX_CHANGES_PER_KIND],
                    "falling": ch.get("falling", [])[:MAX_CHANGES_PER_KIND],
                    "dropped": ch.get("dropped", [])[:MAX_CHANGES_PER_KIND],
                }
            source_blocks.append({
                "source_id": sid,
                "source_name": spec.name if spec else sid,
                "kind": spec.kind if spec else "unknown",
                "new_count": len(src_map.get(sid, [])),
                "items": [
                    {"id": i["id"], "title": i["title"], "url": i.get("url", ""),
                     "summary": i.get("summary", ""), "rank": i.get("rank")}
                    for i in src_map.get(sid, [])[:MAX_ITEMS_PER_SOURCE]
                ],
                "hot_changes": hot,
            })
        sections.append({"category": cat["id"], "name": cat["name"], "sources": source_blocks})

    return {
        "plan_id": plan["id"],
        "generated_at": now.isoformat(timespec="seconds"),
        "window": {"start": start.isoformat(timespec="seconds"), "end": end.isoformat(timespec="seconds")},
        "summary": {
            "new_items": len(items),
            "source_count": len({i["source_id"] for i in items} | set(changes)),
            "error_count": len(errors),
            "subscription_updates": len(subscriptions),
        },
        "subscriptions": subscriptions,
        "sections": sections,
        "errors": errors,
    }


def render_markdown(digest: dict[str, Any], title_time: str) -> str:
    lines = [f"# 信息报告 {title_time}（{digest['plan_id']}）", ""]
    w = digest["window"]
    lines.append(f"窗口：{w['start']} 至 {w['end']}")
    lines.append("")
    s = digest["summary"]
    lines.append("## 概览")
    lines.append(f"- 本窗口新增条目：{s['new_items']} 条，涉及信息源 {s['source_count']} 个，异常 {s['error_count']} 次")
    lines.append("")
    subs = digest.get("subscriptions", [])
    if subs:
        lines.append("## 订阅更新")
        for sub in subs:
            link = f"[{sub['title']}]({sub['url']})" if sub["url"] else sub["title"]
            prev = f"（上次：{sub['previous_title']}）" if sub["previous_title"] else ""
            lines.append(f"- 《{sub['source_name']}》更新至 {link}{prev}")
        lines.append("")
    if digest["errors"]:
        lines.append("## 异常")
        for e in digest["errors"]:
            lines.append(f"- {e['time']} `{e['source_id']}`：{e['error']}")
        lines.append("")
    for sec in digest["sections"]:
        lines.append(f"## {sec['name']}")
        lines.append("")
        for src in sec["sources"]:
            lines.append(f"### {src['source_name']}")
            hot = src["hot_changes"]
            if hot:
                if hot["first_snapshot"]:
                    lines.append("（首次抓取，暂无排名变化）")
                for label, key in (("新进", "new"), ("上升", "rising"), ("下降", "falling")):
                    rows = hot[key]
                    if not rows:
                        continue
                    lines.append(f"- {label}：" + "；".join(
                        f"{r['title']}" + (f"（{r['from']}→{r['to']}）" if "from" in r else "")
                        for r in rows))
                if hot["dropped"]:
                    lines.append(f"- 掉出榜单：{len(hot['dropped'])} 条")
            if src["items"]:
                lines.append(f"- 本窗口新增 {src['new_count']} 条：")
                for it in src["items"]:
                    link = f"[{it['title']}]({it['url']})" if it["url"] else it["title"]
                    lines.append(f"  - {link}")
            elif not hot:
                lines.append("- 本窗口无新增")
            lines.append("")
    if not digest["sections"] and not subs:
        lines.append("本窗口没有可展示的内容。")
        lines.append("")
    return "\n".join(lines)


def write_report(settings: Settings, plan: dict[str, Any], now: datetime) -> dict[str, str]:
    digest = build_digest(settings, plan, now)
    day = now.strftime("%Y-%m-%d")
    base = settings.paths.reports_dir / day / f"{now:%H%M}-{plan['id']}"
    md_path, json_path = base.with_suffix(".md"), base.with_suffix(".json")
    atomic_write_text(md_path, render_markdown(digest, now.strftime("%Y-%m-%d %H:%M")))
    atomic_write_json(json_path, digest)
    rel_md = str(md_path.relative_to(settings.paths.root))
    rel_json = str(json_path.relative_to(settings.paths.root))
    atomic_write_json(settings.paths.reports_dir / "latest.json", {
        "plan_id": plan["id"],
        "generated_at": digest["generated_at"],
        "markdown": rel_md,
        "json": rel_json,
    })
    return {"markdown": rel_md, "json": rel_json}
