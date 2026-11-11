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
from core import health as health_mod
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


def collect_health_problems(settings: Settings, loaded) -> list[dict[str, Any]]:
    """当前仍存在的问题：已暂停、连续失败（标红）、配置错误。与时间窗口无关——被暂停/配错的源不会再产生
    运行失败记录，不在这里列出就会从报告里悄悄消失。"""
    cfg = health_mod.load_health_config(settings)
    state = health_mod.read_all(settings)
    out: list[dict[str, Any]] = []
    for sid, spec in sorted(loaded.specs.items()):
        e = state.get(sid)
        st = health_mod.status_of(e, cfg)
        if st == "ok":
            continue
        out.append({"source_id": sid, "source_name": spec.name, "kind": st,
                    "failures": int(e.get("failures", 0)), "last_error": e.get("last_error", "")})
    for sid, errs in sorted(loaded.errors.items()):
        out.append({"source_id": sid, "source_name": sid, "kind": "config_error",
                    "failures": 0, "last_error": "；".join(errs)})
    return out


def _selected_categories(plan: dict[str, Any], settings: Settings) -> list[dict[str, str]]:
    wanted = plan.get("categories", "all")
    if wanted == "all":
        return settings.categories
    return [c for c in settings.categories if c["id"] in wanted]


def _source_status(paused: bool, new_count: int, errs: list[dict[str, Any]], ever_fetched: bool) -> tuple[str, str]:
    """源在本窗口的状态：paused | new | failed | no_new | never。"""
    if paused:
        return "paused", "已自动暂停，需手动恢复"
    if new_count:
        return "new", f"本窗口新增 {new_count} 条"
    if errs:
        return "failed", f"本窗口抓取失败 {len(errs)} 次：{errs[-1]['error']}"
    if not ever_fetched:
        return "never", "尚未抓取过"
    return "no_new", "本窗口无新增"


def build_digest(settings: Settings, plan: dict[str, Any], now: datetime) -> dict[str, Any]:
    start, end = window_bounds(plan, now)
    loaded = load_sources(settings)
    specs: dict[str, SourceSpec] = loaded.specs
    items = collect_items(settings, start, end)
    changes = collect_latest_changes(settings, start, end)
    errors = collect_errors(settings, start, end)
    problems = collect_health_problems(settings, loaded)

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

    health_state = health_mod.read_all(settings)
    cap = int(settings.defaults.get("report_max_items_per_source", MAX_ITEMS_PER_SOURCE))
    errors_by_src: dict[str, list[dict[str, Any]]] = {}
    for e in errors:
        errors_by_src.setdefault(e["source_id"], []).append(e)

    counts = {"enabled": 0, "new": 0, "no_new": 0, "failed": 0, "paused": 0, "never": 0, "omitted_items": 0}
    sections = []
    for cat in _selected_categories(plan, settings):
        src_map = by_cat.get(cat["id"], {})
        # 每个启用的源都要出现（即使窗口内无新增）；窗口内有条目的已停用源也保留，避免悄悄丢数据
        cat_sids = sorted({sid for sid, sp in specs.items()
                           if sp.category == cat["id"] and sp.enabled and sp.kind != "subscription"}
                          | set(src_map))
        if not cat_sids:
            continue
        source_blocks = []
        for sid in cat_sids:
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
                hot["omitted"] = {k: max(0, len(ch.get(k, [])) - MAX_CHANGES_PER_KIND)
                                  for k in ("new", "rising", "falling", "dropped")}
            new_items = src_map.get(sid, [])
            errs = errors_by_src.get(sid, [])
            paused = bool((health_state.get(sid) or {}).get("paused"))
            ever_fetched = sid in health_state or (settings.paths.raw_dir / sid).exists()
            status, status_text = _source_status(paused, len(new_items), errs, ever_fetched)
            counts["enabled"] += 1 if (spec and spec.enabled) else 0
            counts[status] += 1
            omitted = max(0, len(new_items) - cap)
            counts["omitted_items"] += omitted
            source_blocks.append({
                "source_id": sid,
                "source_name": spec.name if spec else sid,
                "kind": spec.kind if spec else "unknown",
                "enabled": bool(spec and spec.enabled),
                "status": status,
                "status_text": status_text,
                "new_count": len(new_items),
                "omitted": omitted,
                "errors_in_window": len(errs),
                "last_error": errs[-1]["error"] if errs else "",
                "items": [
                    {"id": i["id"], "title": i["title"], "url": i.get("url", ""),
                     "summary": i.get("summary", ""), "rank": i.get("rank"),
                     "published_at": i.get("published_at")}
                    for i in new_items[:cap]
                ],
                "hot_changes": hot,
            })
        sections.append({"category": cat["id"], "name": cat["name"], "sources": source_blocks})

    active = {b["source_id"] for sec in sections for b in sec["sources"]
              if b["status"] == "new" or b["hot_changes"]}
    summary = {
        "source_count": counts["enabled"],
        "active_source_count": len(active),
        "new_items": len(items),
        "no_new_count": counts["no_new"],
        "failed_count": counts["failed"],
        "paused_count": counts["paused"],
        "never_count": counts["never"],
        "omitted_items": counts["omitted_items"],
        "error_count": len(errors),
        "subscription_updates": len(subscriptions),
        "problem_count": len(problems),
    }

    return {
        "plan_id": plan["id"],
        "generated_at": now.isoformat(timespec="seconds"),
        "window": {"start": start.isoformat(timespec="seconds"), "end": end.isoformat(timespec="seconds")},
        "summary": summary,
        "health": problems,
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
    lines.append(f"- 本窗口新增条目：{s['new_items']} 条，启用信息源 {s['source_count']} 个（有更新 {s.get('active_source_count', 0)} 个）")
    lines.append(f"- 无新增 {s.get('no_new_count', 0)} 个，抓取失败 {s.get('failed_count', 0)} 个，"
                 f"已暂停 {s.get('paused_count', 0)} 个，尚未抓取 {s.get('never_count', 0)} 个")
    if s.get("omitted_items"):
        lines.append(f"- 另有 {s['omitted_items']} 条新增因单源展示上限未列出，完整数据见 JSON 报告")
    lines.append("")
    subs = digest.get("subscriptions", [])
    if subs:
        lines.append("## 订阅更新")
        for sub in subs:
            link = f"[{sub['title']}]({sub['url']})" if sub["url"] else sub["title"]
            prev = f"（上次：{sub['previous_title']}）" if sub["previous_title"] else ""
            lines.append(f"- 《{sub['source_name']}》更新至 {link}{prev}")
        lines.append("")
    problems = digest.get("health", [])
    if digest["errors"] or problems:
        lines.append("## 异常")
        for pr in problems:
            label = {"paused": "已自动暂停，需手动恢复", "degraded": "连续失败",
                     "config_error": "配置错误"}.get(pr["kind"], pr["kind"])
            cnt = f"（连续失败 {pr['failures']} 次）" if pr["failures"] else ""
            lines.append(f"- `{pr['source_id']}` {label}{cnt}：{pr['last_error']}")
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
            status = src.get("status", "new" if src["items"] else "no_new")
            if status == "new" or src["items"]:
                lines.append(f"- 本窗口新增 {src['new_count']} 条：")
                for it in src["items"]:
                    link = f"[{it['title']}]({it['url']})" if it["url"] else it["title"]
                    lines.append(f"  - {link}")
                if src.get("omitted"):
                    lines.append(f"  - （另有 {src['omitted']} 条未展示）")
            elif status == "failed":
                lines.append(f"- ⚠ {src.get('status_text', '抓取失败')}")
            elif status == "paused":
                lines.append("- ⛔ 已自动暂停，需手动恢复")
            elif status == "never":
                lines.append("- 尚未抓取过")
            elif not hot:
                lines.append("- 本窗口无新增")
            if hot and hot.get("omitted") and any(hot["omitted"].values()):
                lines.append("- 榜单变化较多，部分未展示")
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
