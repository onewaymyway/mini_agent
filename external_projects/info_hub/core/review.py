"""阶段 P18：已接受信息源的周期复查、替代入口、退役。

范围（保守）：只复查「由自动探索发现、且用户已确认启用」的信息源；用户手工配置的源不动。
默认关闭（config/discovery.yaml 的 review.enabled），开启后由 tick 每天最多触发一轮。

流程：
  到期（默认 30 天；上次复查失败的源 7 天）→ 小样本预览（条目数 + 字段完整度，门槛与自动探索一致）
    ├─ 通过：失败计数清零
    └─ 失败：失败计数 +1，并尝试同站替代入口（订阅入口，可选模型重建同页草稿）
         → 通过质量/查重门槛的替代方案进入候选收件箱（默认停用，带 replaces 字段，需用户确认）
         → 连续失败达到 retire_after 次：标记「建议退役」；仅当 review.auto_retire 开启时才自动停用
  用户确认替代候选时，原信息源随之退役（停用并记录）。

护栏：
- 需要浏览器的源不复查（不使用共享登录态，也不在复查里渲染），记为 skipped；
- 替代入口只访问同站公开页面，遵守 robots.txt、禁止名单与每站点每日页数上限；暂停开关同样生效；
- 退役只做「停用 + 记录」，不删除任何文件；已退役的源不再复查，也不再为它生成替代候选。

文件（纯文本）：data/discovery/review_state.json（每源复查状态）、retired.jsonl（退役记录）、
review.jsonl（每轮摘要）；每步另写入 explore_log.jsonl 以便审计。
"""
from __future__ import annotations

import hashlib
import re
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml

from core import autoexplore, discovery, explore_params
from core.store import append_jsonl, atomic_write_json, file_lock, read_json, read_jsonl

DEFAULTS = {"enabled": False, "every_days": 30, "failing_recheck_days": 7, "retire_after": 3,
            "auto_retire": False, "max_alternatives": 3, "daily": 10, "redraft": True}
BOOL_KEYS = ("enabled", "auto_retire", "redraft")
COMMON_FEED_PATHS = ("/feed", "/rss", "/atom.xml")
ID_PREFIX = "review:"


def state_path(data_dir: Path) -> Path:
    return Path(data_dir) / "discovery" / "review_state.json"


def retired_path(data_dir: Path) -> Path:
    return Path(data_dir) / "discovery" / "retired.jsonl"


def summary_path(data_dir: Path) -> Path:
    return Path(data_dir) / "discovery" / "review.jsonl"


def load_config(root: Path) -> dict[str, Any]:
    # P19：yaml 为基线，看板保存的覆盖值优先（见 core/explore_params.py）
    raw = explore_params.raw_section(Path(root), "review")
    out: dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        if key in BOOL_KEYS:
            out[key] = str(raw.get(key, default)).strip().lower() in ("1", "true", "yes", "on")
            continue
        try:
            out[key] = type(default)(raw.get(key, default))
        except (TypeError, ValueError):
            out[key] = default
    out["every_days"] = max(1, int(out["every_days"]))
    out["failing_recheck_days"] = max(1, int(out["failing_recheck_days"]))
    out["retire_after"] = max(1, int(out["retire_after"]))
    out["max_alternatives"] = max(0, min(10, int(out["max_alternatives"])))
    out["daily"] = max(0, min(100, int(out["daily"])))
    return out


# ---------- 状态与退役记录 ----------
def load_state(data_dir: Path) -> dict[str, Any]:
    data = read_json(state_path(data_dir), default=None)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("sources", {})
    return data


def _save_state(data_dir: Path, state: dict[str, Any]) -> None:
    path = state_path(data_dir)
    with file_lock(path):
        atomic_write_json(path, state)


def retired_ids(data_dir: Path) -> set[str]:
    return {str(r.get("source_id")) for r in read_jsonl(retired_path(data_dir)) if r.get("source_id")}


def retired_rows(data_dir: Path) -> list[dict[str, Any]]:
    return [r for r in read_jsonl(retired_path(data_dir)) if isinstance(r, dict)]


def retire(settings: Any, source_id: str, reason: str, *, by: str = "user",
           now: Optional[datetime] = None) -> dict[str, Any]:
    """退役：停用信息源并记录原因。不删除任何文件；重复退役是幂等的。"""
    from core.source_store import SourceError, update_source_file
    from core.sources import load_sources
    now = now or datetime.now().astimezone()
    data_dir = settings.paths.data_dir
    stamp = now.isoformat(timespec="seconds")
    if source_id in retired_ids(data_dir):
        return {"source_id": source_id, "already": True}
    spec = load_sources(settings).specs.get(source_id)
    if spec is None:
        raise SourceError(f"未找到信息源 {source_id}")
    update_source_file(settings, source_id, dict(spec.raw, enabled=False))
    row = {"time": stamp, "source_id": source_id, "reason": reason, "by": by}
    with file_lock(retired_path(data_dir)):
        append_jsonl(retired_path(data_dir), row)
    state = load_state(data_dir)
    entry = state["sources"].setdefault(source_id, {})
    entry.update(status="retired", retired_at=stamp, retired_reason=reason)
    _save_state(data_dir, state)
    autoexplore._log(data_dir, {"time": stamp, "host": autoexplore.host_of(str(spec.request.get("url") or "")),
                                "step": "retire", "result": "retired", "source": source_id, "by": by})
    return row


# ---------- 到期判断 ----------
def reviewable_ids(settings: Any) -> list[str]:
    """已接受的自动探索源 id（去重、保持稳定顺序）。"""
    return sorted(discovery.accepted_auto_sources(settings.paths.data_dir))


def is_due(entry: Optional[dict[str, Any]], now: datetime, cfg: dict[str, Any]) -> bool:
    entry = entry or {}
    if entry.get("status") == "retired":
        return False
    last = entry.get("last_review")
    if not last:
        return True
    days = cfg["failing_recheck_days"] if int(entry.get("failures", 0)) > 0 else cfg["every_days"]
    try:
        return now - datetime.fromisoformat(str(last)) >= timedelta(days=days)
    except ValueError:
        return True


# ---------- 判定 ----------
def judge(settings: Any, spec: Any, ctx: Any, acfg: dict[str, Any]) -> dict[str, Any]:
    """小样本预览。返回 {ok, total, completeness, error}。门槛与自动探索入箱一致。"""
    from core.source_store import preview_spec
    try:
        preview = preview_spec(spec, ctx)
    except Exception as exc:  # noqa: BLE001 - 适配器异常也视为一次失败，不中断整轮
        return {"ok": False, "total": 0, "completeness": 0.0, "error": f"{type(exc).__name__}: {exc}"}
    total, complete = autoexplore._quality(preview)
    ok = bool(preview.get("ok")) and total >= acfg["min_items"] and complete >= acfg["min_completeness"]
    error = "" if ok else (str(preview.get("error") or "") or
                           f"条目 {total} 条、字段完整度 {round(complete * 100)}%，低于门槛")
    return {"ok": ok, "total": total, "completeness": round(complete, 3), "error": error}


# ---------- 替代入口 ----------
def _alt_id(source_id: str, url: str) -> str:
    return f"{ID_PREFIX}{source_id}#{hashlib.sha1(url.encode('utf-8')).hexdigest()[:6]}"


def find_alternatives(settings: Any, llm: Any, spec: Any, failure: str, ctx: Any, *, rcfg: dict[str, Any],
                      acfg: dict[str, Any], now: datetime, provider_label: str = "") -> dict[str, Any]:
    """为失效的源寻找同站替代入口并入箱。返回 {added: [候选], skipped: {原因: 数}}。"""
    from adapters.base import detect_block
    from core import source_generator
    from core.explore_forms import feed_links, rss_source
    from core.fingerprint import find_similar
    from core.frontier import is_denied, load_denylist
    from core.source_store import SourceError, discard_draft, preview_spec, validate_candidate
    from core.sources import load_sources

    root, data_dir = settings.paths.root, settings.paths.data_dir
    stamp = now.isoformat(timespec="seconds")
    day = now.date().isoformat()
    sid = spec.id
    url0 = str(spec.request.get("url") or "")
    host = autoexplore.host_of(url0)
    added: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    if rcfg["max_alternatives"] <= 0 or not host:
        return {"added": added, "skipped": skipped}
    if is_denied(host, load_denylist(root)):
        skip("denylisted")
        return {"added": added, "skipped": skipped}
    cands = discovery.list_candidates(data_dir)
    if any(c.get("replaces") == sid and c.get("status") == "pending" for c in cands):
        skip("alternative_pending")                       # 已有待确认的替代方案，不重复生成
        return {"added": added, "skipped": skipped}
    known_ids = {c["id"] for c in cands}
    pending = sum(1 for c in cands if c.get("status") == "pending")
    pending_cap = discovery.load_config(root)["pending_cap"]
    others = {k: v for k, v in load_sources(settings).specs.items() if k != sid}   # 失效源自身不算重复

    state = autoexplore._load_state(data_dir)
    host_pages = state.setdefault("host_pages", {}).setdefault(day, {})

    def budget_ok() -> bool:
        return int(host_pages.get(host, 0)) < int(acfg["host_daily_pages"])

    def get(url: str) -> Optional[bytes]:
        ok, why, _ = autoexplore.robots_allows(url, ctx)
        if not ok:
            skip(why)
            return None
        if not budget_ok():
            skip("host_budget")
            return None
        host_pages[host] = int(host_pages.get(host, 0)) + 1
        try:
            body = ctx.http_get(url)
        except Exception:  # noqa: BLE001
            skip("fetch_error")
            return None
        if detect_block(body):
            skip("blocked_or_login")
            return None
        return body

    def gate(source: dict[str, Any], cid: str, url: str, how: str) -> None:
        nonlocal pending
        if len(added) >= rcfg["max_alternatives"]:
            return
        if cid in known_ids:
            skip("already_known")                         # 含此前被忽略的同一方案
            return
        if pending >= pending_cap:
            skip("pending_cap")
            return
        new_spec, errors, _ = validate_candidate(settings, source)
        if new_spec is None or errors:
            skip("invalid")
            return
        if find_similar(others, source):
            skip("duplicate")
            return
        total, complete = autoexplore._quality(preview_spec(new_spec, ctx))
        if total < acfg["min_items"] or complete < acfg["min_completeness"]:
            skip("low_quality")
            return
        cand = {"id": cid, "template_id": "auto_review", "template_name": "复查·替代入口", "query": host,
                "reason": f"原信息源 {sid} 复查失败（{failure[:80]}）；{how}：预览 {total} 条，"
                          f"字段完整度 {round(complete * 100)}%",
                "url": url, "status": "pending", "created_at": stamp, "decided_at": None,
                "source": dict(source, enabled=False), "origin": "auto_review", "replaces": sid}
        discovery.add_candidate(data_dir, cand)
        known_ids.add(cid)
        pending += 1
        added.append(cand)
        autoexplore._log(data_dir, {"time": stamp, "host": host, "step": "review_alt", "result": "added",
                                    "candidate": cid, "replaces": sid, "total": total})

    # 1) 订阅入口：首页声明的；没有声明时才试常见路径（不需要模型）
    home = f"https://{urllib.parse.urlsplit(url0).netloc}/"
    body = get(home)
    feeds: list[str] = []
    if body is not None:
        feeds = [u for u in feed_links(body.decode("utf-8", errors="replace"), home)
                 if autoexplore.host_of(u) == host]
        if not feeds:
            feeds = [f"https://{urllib.parse.urlsplit(url0).netloc}{p}" for p in COMMON_FEED_PATHS]
    for n, feed_url in enumerate(feeds, start=1):
        if len(added) >= rcfg["max_alternatives"] or not budget_ok():
            break
        if feed_url.rstrip("/") == url0.rstrip("/"):
            continue                                       # 就是失效的那个入口
        short = re.sub(r"[^a-z0-9]+", "_", f"{sid}_alt{n}")[:36].strip("_")
        src = rss_source(f"auto_{short}", f"{spec.name}（替代订阅 {n}）", feed_url)
        src["category"] = spec.category
        gate(src, _alt_id(sid, feed_url), feed_url, "同站订阅入口")

    # 2) 模型重建同一页面的草稿（选择器失效是最常见原因）；需要浏览器的源不处理
    if (rcfg["redraft"] and llm is not None and spec.adapter == "html_list" and len(added) < rcfg["max_alternatives"]
            and not (spec.browser or {}).get("required") and url0.startswith("https://") and budget_ok()):
        ok, why, _ = autoexplore.robots_allows(url0, ctx)
        if not ok:
            skip(why)
        else:
            host_pages[host] = int(host_pages.get(host, 0)) + 1
            try:
                draft = source_generator.generate_draft(
                    settings, llm, url0, name=host, hint=f"复查重建：原选择器已失效（{failure[:60]}）",
                    category=spec.category, provider_label=provider_label, ctx=ctx)
            except SourceError:
                skip("generate_error")
            else:
                try:
                    source = draft["source"]
                    if source.get("adapter") not in autoexplore.AUTO_ADAPTERS:
                        skip("script_not_auto")
                    else:
                        gate(source, _alt_id(sid, url0 + "#redraft"), url0, "模型重建同页面")
                finally:
                    discard_draft(settings, draft["id"])

    state.setdefault("host_pages", {})[day] = host_pages
    autoexplore._save_state(data_dir, state)
    return {"added": added, "skipped": skipped}


# ---------- 一轮复查 ----------
def run(settings: Any, llm: Any, *, ctx: Any, now: Optional[datetime] = None, provider_label: str = "",
        force: bool = False) -> dict[str, Any]:
    """复查到期的源。force=True 时忽略到期时间与 review.enabled（用于看板/CLI 手动触发）。

    返回 {reviewed, ok, failing, alternatives, retire_suggested, retired, skipped: {原因: 数}, paused}。
    """
    from core.sources import load_sources
    now = now or datetime.now().astimezone()
    data_dir = settings.paths.data_dir
    stamp = now.isoformat(timespec="seconds")
    rcfg = load_config(settings.paths.root)
    acfg = autoexplore.load_config(settings.paths.root)
    out: dict[str, Any] = {"reviewed": 0, "ok": 0, "failing": 0, "alternatives": 0, "retire_suggested": 0,
                           "retired": 0, "skipped": {}, "paused": False}

    def skip(reason: str) -> None:
        out["skipped"][reason] = out["skipped"].get(reason, 0) + 1

    if autoexplore.is_paused(data_dir):
        out["paused"] = True
        return out
    specs = load_sources(settings).specs
    state = load_state(data_dir)
    gone = retired_ids(data_dir)
    for sid in reviewable_ids(settings):
        if autoexplore.is_paused(data_dir):               # 一键暂停：下一个源之前立即停止
            out["paused"] = True
            break
        spec = specs.get(sid)
        entry = state["sources"].setdefault(sid, {})
        if sid in gone or entry.get("status") == "retired":
            skip("retired")
            continue
        if spec is None:
            skip("source_missing")
            continue
        if not spec.enabled:
            skip("disabled")                              # 用户已停用的源不复查
            continue
        if (spec.browser or {}).get("required"):
            skip("browser_required")                      # 不用共享登录态，也不在复查里渲染
            continue
        if not force and not is_due(entry, now, rcfg):
            skip("not_due")
            continue
        if out["reviewed"] >= rcfg["daily"] and not force:
            skip("daily_cap")
            break
        verdict = judge(settings, spec, ctx, acfg)
        out["reviewed"] += 1
        entry.update(last_review=stamp, last_total=verdict["total"], last_completeness=verdict["completeness"])
        autoexplore._log(data_dir, {"time": stamp, "host": autoexplore.host_of(str(spec.request.get("url") or "")),
                                    "step": "review", "result": "ok" if verdict["ok"] else "failed",
                                    "source": sid, "total": verdict["total"]})
        if verdict["ok"]:
            entry.update(failures=0, status="ok", last_error="")
            entry.pop("retire_suggested_at", None)
            out["ok"] += 1
            continue
        entry.update(failures=int(entry.get("failures", 0)) + 1, status="failing", last_error=verdict["error"])
        out["failing"] += 1
        alt = find_alternatives(settings, llm, spec, verdict["error"], ctx, rcfg=rcfg, acfg=acfg, now=now,
                                provider_label=provider_label)
        out["alternatives"] += len(alt["added"])
        entry["alternatives"] = sorted(set(entry.get("alternatives", [])) | {c["id"] for c in alt["added"]})
        for reason, n in alt["skipped"].items():
            out["skipped"]["alt_" + reason] = out["skipped"].get("alt_" + reason, 0) + n
        if entry["failures"] >= rcfg["retire_after"]:
            if rcfg["auto_retire"]:
                _save_state(data_dir, state)              # retire() 会重新读取并写回状态
                retire(settings, sid, f"复查连续失败 {entry['failures']} 次：{verdict['error'][:100]}",
                       by="auto", now=now)
                state = load_state(data_dir)
                out["retired"] += 1
            else:
                entry.setdefault("retire_suggested_at", stamp)
                entry["status"] = "retire_suggested"
                out["retire_suggested"] += 1
    state["last_run"] = stamp
    _save_state(data_dir, state)
    summary = dict(out, time=stamp)
    with file_lock(summary_path(data_dir)):
        append_jsonl(summary_path(data_dir), summary)
    return out


def maybe_run(settings: Any, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    """由 tick 调用。默认关闭；开启后每天最多一轮，暂停时不执行。"""
    from adapters.base import FetchContext
    now = now or datetime.now().astimezone()
    data_dir = settings.paths.data_dir
    if not load_config(settings.paths.root)["enabled"] or autoexplore.is_paused(data_dir):
        return None
    state = load_state(data_dir)
    last = state.get("last_run")
    if last:
        try:
            if datetime.fromisoformat(str(last)).date() == now.date():
                return None
        except ValueError:
            pass
    state["last_run"] = now.isoformat(timespec="seconds")     # 先记录：失败也不会在同一天反复尝试
    _save_state(data_dir, state)
    llm, label = autoexplore._default_llm(settings)
    return run(settings, llm, ctx=FetchContext(), now=now, provider_label=label)


# ---------- 展示 ----------
def status_rows(settings: Any) -> list[dict[str, Any]]:
    """看板/CLI 用：每个被复查的源一行（含状态、连续失败、最近错误、待确认替代数）。"""
    from core.sources import load_sources
    data_dir = settings.paths.data_dir
    state = load_state(data_dir)["sources"]
    specs = load_sources(settings).specs
    cands = discovery.list_candidates(data_dir)
    gone = retired_ids(data_dir)
    rows = []
    for sid in sorted(set(reviewable_ids(settings)) | set(state)):
        e = state.get(sid, {})
        spec = specs.get(sid)
        pending = [c["id"] for c in cands if c.get("replaces") == sid and c.get("status") == "pending"]
        rows.append({"source_id": sid, "name": spec.name if spec else sid,
                     "status": "retired" if sid in gone else e.get("status", "unreviewed"),
                     "failures": int(e.get("failures", 0)), "last_review": e.get("last_review"),
                     "last_error": e.get("last_error", ""), "pending_alternatives": pending,
                     "retired_reason": e.get("retired_reason", "")})
    return rows


def last_summary(data_dir: Path) -> Optional[dict[str, Any]]:
    rows = list(read_jsonl(summary_path(data_dir)))
    return rows[-1] if rows else None
