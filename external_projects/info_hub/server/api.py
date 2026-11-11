"""看板 API 的业务逻辑（与 HTTP 解耦，便于测试）。

所有读操作直接读取纯文本文件；所有写操作都通过原子写入完成。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from core.browser_session import BrowserSession
from core.settings import Settings
from core.sources import load_sources
from core.speech_text import digest_to_segments, item_to_segments
from core.store import atomic_write_json, read_json

_RUN_LINE_RE = re.compile(r"^- (\d{2}:\d{2}:\d{2}) `([^`]+)` (成功|失败)(.*)$")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------- 信息源 ----------
def _latest_snapshot(settings: Settings, source_id: str) -> dict[str, Any] | None:
    base = settings.paths.raw_dir / source_id
    if not base.exists():
        return None
    files = sorted(base.glob("*/*.json"))
    if not files:
        return None
    return read_json(files[-1], default=None)


def list_sources(settings: Settings) -> dict[str, Any]:
    loaded = load_sources(settings)
    sched = read_json(settings.paths.state_dir / "_scheduler.json", default={}) or {}
    last_runs = sched.get("sources", {})
    rows = []
    for sid, spec in sorted(loaded.specs.items()):
        snap = _latest_snapshot(settings, sid)
        rows.append({
            "id": sid,
            "name": spec.name,
            "category": spec.category,
            "kind": spec.kind,
            "adapter": spec.adapter,
            "enabled": spec.enabled,
            "every": spec.schedule.get("every", settings.defaults.get("schedule_every", "60m")),
            "browser_site": spec.browser.get("site") if spec.browser.get("required") else None,
            "last_run": last_runs.get(sid),
            "last_fetched_at": snap.get("fetched_at") if snap else None,
            "last_item_count": snap.get("item_count") if snap else None,
            "config_errors": [],
        })
    for sid, errs in sorted(loaded.errors.items()):
        rows.append({"id": sid, "name": sid, "config_errors": errs, "enabled": False})
    return {"sources": rows, "categories": settings.categories}


def set_source_enabled(settings: Settings, source_id: str, enabled: bool) -> dict[str, Any]:
    path = settings.paths.source_index
    index = read_json(path, default={"version": 1, "sources": []}) or {"sources": []}
    found = False
    for entry in index.get("sources", []):
        if entry.get("id") == source_id:
            entry["enabled"] = bool(enabled)
            found = True
    if not found:
        raise ApiError(404, f"未找到信息源 {source_id}")
    atomic_write_json(path, index)
    return {"id": source_id, "enabled": bool(enabled)}


# ---------- 报告 ----------
def list_reports(settings: Settings, limit: int = 50) -> dict[str, Any]:
    rows = []
    for path in sorted(settings.paths.reports_dir.glob("*/*.json"), reverse=True)[:limit]:
        data = read_json(path, default=None)
        if not data:
            continue
        rows.append({
            "plan_id": data.get("plan_id"),
            "generated_at": data.get("generated_at"),
            "window": data.get("window"),
            "summary": data.get("summary"),
            "json": str(path.relative_to(settings.paths.root)),
            "markdown": str(path.with_suffix(".md").relative_to(settings.paths.root)),
        })
    return {"reports": rows, "plans": [p["id"] for p in settings.reports if "id" in p]}


def _safe_report_path(settings: Settings, rel: str) -> Path:
    base = settings.paths.reports_dir.resolve()
    target = (settings.paths.root / rel).resolve()
    if base not in target.parents or target.suffix not in (".md", ".json"):
        raise ApiError(400, "非法的报告路径")
    if not target.exists():
        raise ApiError(404, "报告不存在")
    return target


def read_report(settings: Settings, rel: str) -> dict[str, Any]:
    target = _safe_report_path(settings, rel)
    if target.suffix == ".md":
        return {"markdown": target.read_text(encoding="utf-8")}
    return {"json": read_json(target)}


def report_speech(settings: Settings, rel_json: str) -> dict[str, Any]:
    target = _safe_report_path(settings, rel_json)
    if target.suffix != ".json":
        raise ApiError(400, "朗读需要报告的 json 路径")
    return {"segments": digest_to_segments(read_json(target))}


def item_speech(settings: Settings, source_id: str, item_id: str) -> dict[str, Any]:
    """在最近的条目文件中查找单条内容，用于单条朗读。"""
    from core.store import read_jsonl

    base = settings.paths.items_dir / source_id
    if not base.exists():
        raise ApiError(404, "信息源没有条目")
    for path in sorted(base.glob("*.jsonl"), reverse=True):
        for item in read_jsonl(path):
            if item.get("id") == item_id:
                return {"segments": item_to_segments(item), "item": item}
    raise ApiError(404, "条目不存在")


# ---------- 运行记录 ----------
def list_runs(settings: Settings, day: str | None = None) -> dict[str, Any]:
    if day is None:
        day = datetime.now().astimezone().strftime("%Y-%m-%d")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ApiError(400, "日期格式应为 YYYY-MM-DD")
    path = settings.paths.runs_dir / f"{day}.log.md"
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = _RUN_LINE_RE.match(line)
            if m:
                rows.append({"time": m.group(1), "source_id": m.group(2),
                             "status": m.group(3), "detail": m.group(4).strip(" |")})
    rows.reverse()
    return {"day": day, "runs": rows}


# ---------- 登录 ----------
def login_status(settings: Settings) -> dict[str, Any]:
    from core.settings import load_sites

    session = BrowserSession(settings.paths.browser_profile_dir,
                             settings.paths.root / "browser" / "login_status.json")
    recorded = session.login_status()
    sites = []
    for sid, cfg in sorted(load_sites(settings.paths.root).items()):
        entry = recorded.get(sid, {})
        sites.append({
            "site": sid,
            "name": cfg.get("name", sid),
            "status": entry.get("status", "unknown"),
            "last_login_at": entry.get("last_login_at"),
            "last_checked_at": entry.get("last_checked_at"),
            "last_error": entry.get("last_error"),
            "login_command": f"python cli.py login {sid}",
        })
    return {"sites": sites}


# ---------- 动作 ----------
def run_source_now(settings: Settings, source_id: str) -> dict[str, Any]:
    from core.runner import run_source

    loaded = load_sources(settings)
    if source_id in loaded.errors:
        raise ApiError(400, f"配置有误: {'; '.join(loaded.errors[source_id])}")
    spec = loaded.specs.get(source_id)
    if spec is None:
        raise ApiError(404, f"未找到信息源 {source_id}")
    res = run_source(spec, settings)
    return {"id": source_id, "ok": res.ok, "item_count": res.item_count,
            "new_count": res.new_count, "error": res.error}


def generate_report_now(settings: Settings, plan_id: str) -> dict[str, Any]:
    from core.digest import write_report

    plan = next((p for p in settings.reports if p.get("id") == plan_id), None)
    if plan is None:
        raise ApiError(404, f"未找到报告计划 {plan_id}")
    paths = write_report(settings, plan, datetime.now().astimezone())
    return {"plan_id": plan_id, **paths}


def run_tick_now(settings: Settings) -> dict[str, Any]:
    from core.tick import tick

    rep = tick(settings)
    return {"ran_sources": rep.ran_sources, "failed_sources": rep.failed_sources,
            "generated_reports": rep.generated_reports}
