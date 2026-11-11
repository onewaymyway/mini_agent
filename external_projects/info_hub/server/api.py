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

_RUN_LINE_RE = re.compile(r"^- (\d{2}:\d{2}:\d{2}) `([^`]+)` (成功|失败|降频|告警|暂停|恢复)(.*)$")


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
    from core import health as _health

    hcfg = _health.load_health_config(settings)
    hstate = _health.read_all(settings)
    rows = []
    for sid, spec in sorted(loaded.specs.items()):
        snap = _latest_snapshot(settings, sid)
        he = hstate.get(sid) or {}
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
            "health": _health.status_of(he, hcfg),
            "failures": int(he.get("failures", 0)),
            "last_error": he.get("last_error") if he.get("failures") else None,
        })
    for sid, errs in sorted(loaded.errors.items()):
        rows.append({"id": sid, "name": sid, "config_errors": errs, "enabled": False})
    return {"sources": rows, "categories": settings.categories}


def list_subscriptions(settings: Settings, history: int = 5) -> dict[str, Any]:
    """订阅页数据：每个订阅源的当前游标（来自状态文件）与最近几次更新。"""
    from core.store import read_json as _rj, read_jsonl

    loaded = load_sources(settings)
    sched = _rj(settings.paths.state_dir / "_scheduler.json", default={}) or {}
    last_runs = sched.get("sources", {})
    rows = []
    for sid, spec in sorted(loaded.specs.items()):
        if spec.kind != "subscription":
            continue
        state = _rj(settings.paths.state_dir / f"{sid}.json", default={}) or {}
        cur = state.get("cursor") or {}
        updates: list[dict[str, Any]] = []
        base = settings.paths.items_dir / sid
        if base.exists():
            for path in sorted(base.glob("*.jsonl"), reverse=True):
                for it in reversed(list(read_jsonl(path))):
                    updates.append({"title": it.get("title", ""), "url": it.get("url", ""),
                                    "fetched_at": it.get("fetched_at", ""),
                                    "previous_title": it.get("extra", {}).get("previous_title", "")})
                    if len(updates) >= history:
                        break
                if len(updates) >= history:
                    break
        rows.append({
            "id": sid,
            "name": spec.name,
            "enabled": spec.enabled,
            "checker": (spec.raw.get("subscription") or {}).get("checker"),
            "every": spec.schedule.get("every", settings.defaults.get("schedule_every", "60m")),
            "current_title": cur.get("title"),
            "current_url": cur.get("url"),
            "checked_at": cur.get("checked_at"),
            "updated_at": cur.get("updated_at"),
            "last_run": last_runs.get(sid),
            "recent_updates": updates,
        })
    return {"subscriptions": rows}


def resume_source(settings: Settings, source_id: str) -> dict[str, Any]:
    """手动恢复被暂停/连续失败的信息源（清除失败计数与暂停标记）。"""
    from core import health as _health

    if source_id not in load_sources(settings).specs:
        raise ApiError(404, f"未找到信息源 {source_id}")
    return {"id": source_id, "resumed": _health.resume(settings, source_id)}


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


# ---------- P7：添加信息源（表单 / 对话生成 / 草稿） ----------
def _split_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [t.strip() for t in str(value or "").split(",") if t.strip()]


def form_to_source(form: dict[str, Any]) -> dict[str, Any]:
    """把表单字段转换为 source.yaml 结构。字段含义见 PROJECT.md。"""
    adapter = str(form.get("adapter", ""))
    source: dict[str, Any] = {
        "id": str(form.get("id", "")).strip(),
        "name": str(form.get("name", "")).strip(),
        "category": str(form.get("category", "")).strip(),
        "kind": str(form.get("kind", "feed")).strip(),
        "adapter": adapter,
        "tags": _split_tags(form.get("tags")),
        "enabled": bool(form.get("enabled", True)),
        "request": {"url": str(form.get("url", "")).strip()},
        "policy": {"max_items": int(form.get("max_items") or 30), "dedup_by": "url"},
        "schedule": {"every": str(form.get("every") or "60m").strip()},
    }
    if adapter == "json_api":
        source["parse"] = {"items_path": str(form.get("items_path", "")).strip(),
                           "fields": dict(form.get("fields") or {})}
    elif adapter == "html_list":
        source["parse"] = {"list_selector": str(form.get("list_selector", "")).strip(),
                           "fields": dict(form.get("fields") or {})}
    if form.get("browser_required"):
        source["browser"] = {"required": True, "site": str(form.get("browser_site", "")).strip(),
                             "headless": True, "wait_selector": str(form.get("wait_selector", "")).strip()}
    return source


def _form_source(body: dict[str, Any]) -> dict[str, Any]:
    src = body.get("source")
    if not isinstance(src, dict):
        raise ApiError(400, "缺少 source 对象")
    return form_to_source(src)


def provider_status(settings: Settings) -> dict[str, Any]:
    from core.llm import provider_status as _ps
    return _ps(settings)


def preview_form(settings: Settings, body: dict[str, Any]) -> dict[str, Any]:
    from core.source_store import preview_spec, validate_candidate
    from core.runner import default_context
    source = _form_source(body)
    spec, errors, dups = validate_candidate(settings, source)
    if spec is None or errors:
        return {"ok": False, "errors": errors, "duplicates": dups, "preview": None}
    if spec.kind not in ("hot_list", "feed", "recommend", "subscription", "custom"):
        return {"ok": False, "errors": ["kind 不合法"], "duplicates": dups, "preview": None}
    return {"ok": True, "errors": [], "duplicates": dups,
            "preview": preview_spec(spec, default_context(settings))}


def create_form(settings: Settings, body: dict[str, Any]) -> dict[str, Any]:
    from core.source_store import SourceError, validate_candidate, write_source
    source = _form_source(body)
    spec, errors, dups = validate_candidate(settings, source)
    if errors:
        raise ApiError(400, "；".join(errors))
    if dups and not body.get("force"):
        raise ApiError(409, f"与已有信息源相似：{', '.join(dups)}。如仍要添加，请勾选强制添加")
    try:
        target = write_source(settings, source)
    except SourceError as exc:
        raise ApiError(400, str(exc))
    return {"id": source["id"], "path": str(target.relative_to(settings.paths.root))}


def _llm(settings: Settings):
    from core.llm import LLMUnavailable, build_llm
    try:
        return build_llm(settings)
    except LLMUnavailable as exc:
        raise ApiError(503, str(exc))


def generate_source(settings: Settings, body: dict[str, Any]) -> dict[str, Any]:
    from core.source_generator import generate_draft
    from core.source_store import SourceError
    from core.llm import resolve_provider
    llm = _llm(settings)
    label = resolve_provider(settings).label
    try:
        draft = generate_draft(
            settings, llm,
            url=str(body.get("url", "")).strip(),
            name=str(body.get("name", "")).strip(),
            hint=str(body.get("hint", "")).strip(),
            category=body.get("category") or None,
            site=body.get("site") or None,
            use_browser=bool(body.get("use_browser")),
            provider_label=label,
        )
    except SourceError as exc:
        raise ApiError(400, str(exc))
    return draft


def _draft_call(fn):
    from core.source_store import SourceError
    try:
        return fn()
    except SourceError as exc:
        raise ApiError(400, str(exc))
    except FileNotFoundError as exc:
        raise ApiError(404, str(exc))


def list_drafts(settings: Settings) -> dict[str, Any]:
    from core.source_store import list_drafts as _list
    return {"drafts": _list(settings)}


def get_draft(settings: Settings, draft_id: str) -> dict[str, Any]:
    from core.source_store import load_draft
    return _draft_call(lambda: load_draft(settings, draft_id))


def revise_draft(settings: Settings, draft_id: str, body: dict[str, Any]) -> dict[str, Any]:
    from core.source_generator import revise_draft as _revise
    from core.llm import resolve_provider
    llm = _llm(settings)
    return _draft_call(lambda: _revise(settings, llm, draft_id, str(body.get("feedback", "")),
                                       provider_label=resolve_provider(settings).label))


def preview_draft(settings: Settings, draft_id: str) -> dict[str, Any]:
    from core.source_generator import preview_draft as _preview
    return _draft_call(lambda: _preview(settings, draft_id))


def confirm_draft(settings: Settings, draft_id: str, body: dict[str, Any]) -> dict[str, Any]:
    from core.source_generator import confirm_draft as _confirm
    return _draft_call(lambda: _confirm(settings, draft_id, force=bool(body.get("force"))))


def discard_draft(settings: Settings, draft_id: str) -> dict[str, Any]:
    from core.source_store import discard_draft as _discard
    _discard(settings, draft_id)
    return {"discarded": draft_id}
