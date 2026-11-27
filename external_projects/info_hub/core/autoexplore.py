"""阶段 P12：自动站点探索。

站点由系统自动探索，用户不配置站点。流程：
  种子目录（config/seed_sites.yaml，项目维护）+ 已抓取条目的外链线索
  → 逐站点检查（robots.txt、拦截/验证页、页面结构）
  → 复用 source_generator 生成草稿（只接受 rss / json_api / html_list，script 草稿不自动运行）
  → 预览评分（条目数、字段完整度、与已有源的相似度）
  → 候选收件箱（默认停用，用户确认后才启用）。

护栏：只读取公开页面；不登录、不携带共享 profile；遵守 robots.txt（无法获取则保守不访问）；
每站点最小间隔、全局每日上限；每一步写入 data/discovery/explore_log.jsonl 以便审计。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.robotparser
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml

from core import discovery
from core.store import append_jsonl, atomic_write_json, file_lock, read_json, read_jsonl

SEED_FILE = "config/seed_sites.yaml"
AUTO_ADAPTERS = ("rss", "json_api", "html_list")   # script 草稿不会被自动探索生成后运行
DEFAULTS = {"daily": 20, "min_interval_hours": 1, "min_items": 3, "min_completeness": 0.8,
            "harvest_limit": 30}
PREFIX = "auto:"


class AutoExploreError(ValueError):
    pass


def state_path(data_dir: Path) -> Path:
    return Path(data_dir) / "discovery" / "explore_state.json"


def log_path(data_dir: Path) -> Path:
    return Path(data_dir) / "discovery" / "explore_log.jsonl"


def load_config(root: Path) -> dict[str, float]:
    try:
        cfg = yaml.safe_load((Path(root) / "config" / "discovery.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        cfg = {}
    raw = cfg.get("autoexplore") or {}
    out: dict[str, float] = {}
    for k, default in DEFAULTS.items():
        try:
            out[k] = type(default)(raw.get(k, default))
        except (TypeError, ValueError):
            out[k] = default
    out["daily"] = max(0, min(200, int(out["daily"])))
    out["min_interval_hours"] = max(0.0, float(out["min_interval_hours"]))
    out["min_completeness"] = min(1.0, max(0.0, float(out["min_completeness"])))
    return out


def load_seeds(root: Path) -> list[dict[str, str]]:
    """种子站点：只接受 https 首页地址，忽略格式不对的条目。"""
    try:
        data = yaml.safe_load((Path(root) / SEED_FILE).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    out = []
    for row in data.get("sites") or []:
        url = str((row or {}).get("url") or "").strip()
        if url.startswith("https://") and urllib.parse.urlsplit(url).netloc:
            out.append({"url": url, "name": str(row.get("name") or "")})
    return out


def host_of(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")


def harvest_hosts(data_dir: Path, known_hosts: set[str], limit: int, now: datetime) -> list[dict[str, str]]:
    """从最近三天的抓取条目中提取外部站点首页作为线索（不额外访问任何页面）。"""
    seen: dict[str, str] = {}
    items_root = Path(data_dir) / "items"
    if not items_root.exists():
        return []
    days = {(now - timedelta(days=i)).date().isoformat() for i in range(3)}
    for path in items_root.glob("*/*.jsonl"):
        if path.stem not in days:
            continue
        for row in read_jsonl(path):
            url = str((row or {}).get("url") or "")
            if not url.startswith("https://"):
                continue
            host = host_of(url)
            if host and host not in known_hosts and host not in seen:
                seen[host] = f"https://{urllib.parse.urlsplit(url).netloc}/"
    return [{"url": u, "name": h} for h, u in list(seen.items())[:limit]]


def _log(data_dir: Path, row: dict[str, Any]) -> None:
    path = log_path(data_dir)
    with file_lock(path):
        append_jsonl(path, row)


def _load_state(data_dir: Path) -> dict[str, Any]:
    state = read_json(state_path(data_dir), default=None)
    return state if isinstance(state, dict) else {"hosts": {}, "days": {}}


def _save_state(data_dir: Path, state: dict[str, Any]) -> None:
    path = state_path(data_dir)
    with file_lock(path):
        atomic_write_json(path, state)


def robots_allows(url: str, ctx: Any) -> tuple[bool, str]:
    """robots.txt 不存在（404/410）视为允许；其它错误保守地视为不允许。"""
    parts = urllib.parse.urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        body = ctx.http_get(robots_url)
    except urllib.error.HTTPError as exc:
        return (exc.code in (404, 410)), ("no_robots" if exc.code in (404, 410) else "robots_unavailable")
    except Exception:  # noqa: BLE001 - 网络错误一律保守处理
        return False, "robots_unavailable"
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(body.decode("utf-8", errors="replace").splitlines())
    if not rp.can_fetch("info_hub", url) or not rp.can_fetch("*", url):
        return False, "robots_disallow"
    return True, "ok"


def _within_interval(state: dict[str, Any], host: str, now: datetime, min_hours: float) -> bool:
    last = state.get("hosts", {}).get(host)
    if not last:
        return False
    try:
        return now - datetime.fromisoformat(last) < timedelta(hours=min_hours)
    except ValueError:
        return False


def _quality(preview: dict[str, Any]) -> tuple[int, float]:
    items = preview.get("items") or []
    if not items:
        return 0, 0.0
    complete = sum(1 for it in items if it.get("title") and it.get("url"))
    return int(preview.get("total") or len(items)), complete / len(items)


def run(settings: Any, llm: Any, *, ctx: Any, now: Optional[datetime] = None,
        provider_label: str = "") -> dict[str, Any]:
    """执行一轮自动探索。返回 {added: [...], skipped: {原因: 数量}, sites: 本轮检查的站点数}。"""
    from core import source_generator
    from core.fingerprint import find_similar
    from core.source_store import SourceError, preview_spec, validate_candidate
    from core.sources import load_sources

    now = now or datetime.now().astimezone()
    root, data_dir = settings.paths.root, settings.paths.data_dir
    cfg = load_config(root)
    skipped: dict[str, int] = {}
    added: list[dict[str, Any]] = []

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    if llm is None:
        return {"added": [], "skipped": {"no_llm": 1}, "sites": 0}

    state = _load_state(data_dir)
    day = now.date().isoformat()
    today_count = int(state.get("days", {}).get(day, 0))
    dconf = discovery.load_config(root)
    cands = discovery.list_candidates(data_dir)
    pending = [c for c in cands if c.get("status") == "pending"]

    specs = load_sources(settings).specs
    known_hosts = {host_of(str((s.request or {}).get("url") or "")) for s in specs.values()}
    known_hosts.discard("")
    known_hosts |= {host_of(s["url"]) for s in load_seeds(root)}
    sites = load_seeds(root) + harvest_hosts(data_dir, known_hosts, int(cfg["harvest_limit"]), now)

    checked = 0
    for site in sites:
        url = site["url"]
        host = host_of(url)
        cid = f"{PREFIX}{host}"
        if today_count >= cfg["daily"]:
            skip("daily_cap")
            break
        if len(pending) >= dconf["pending_cap"]:
            skip("pending_cap")
            break
        if cid in {c["id"] for c in cands}:          # 已入箱、已采纳或已忽略的站点不再探索
            skip("already_known")
            continue
        if _within_interval(state, host, now, cfg["min_interval_hours"]):
            skip("min_interval")
            continue

        checked += 1
        state.setdefault("hosts", {})[host] = now.isoformat(timespec="seconds")
        today_count += 1
        state.setdefault("days", {})[day] = today_count

        def fail(step: str, reason: str) -> None:
            skip(reason)
            _log(data_dir, {"time": now.isoformat(timespec="seconds"), "host": host, "step": step,
                            "result": "skip", "reason": reason})

        ok, why = robots_allows(url, ctx)
        if not ok:
            fail("robots", why)
            continue
        try:
            body = ctx.http_get(url)
        except Exception:  # noqa: BLE001 - 任何网络错误都只记录，不中断本轮
            fail("fetch", "fetch_error")
            continue
        from adapters.base import detect_block
        from core.explore import explore_response
        if detect_block(body):
            fail("fetch", "blocked_or_login")          # 验证/登录页：放弃，不尝试绕过
            continue
        summary = explore_response(body, "")
        if not (summary.get("candidates") or summary.get("page_type") == "json"):
            fail("structure", "no_structure")
            continue

        try:
            draft = source_generator.generate_draft(settings, llm, url, name=site.get("name", ""),
                                                    hint="自动探索：" + host, provider_label=provider_label,
                                                    ctx=ctx)
        except SourceError:
            fail("generate", "generate_error")
            continue
        source = draft["source"]
        if source.get("adapter") not in AUTO_ADAPTERS:
            fail("generate", "script_not_auto")
            continue
        spec, errors, _ = validate_candidate(settings, source)
        if spec is None or errors:
            fail("validate", "invalid")
            continue
        similar = find_similar(specs, source)
        if similar:
            fail("dedupe", "duplicate")
            continue
        preview = preview_spec(spec, ctx)
        total, complete = _quality(preview)
        if not preview.get("ok") or total < cfg["min_items"] or complete < cfg["min_completeness"]:
            fail("preview", "low_quality")
            continue

        cand = {"id": cid, "template_id": "auto_explore", "template_name": "自动探索", "query": host,
                "reason": f"自动探索发现：{host}，预览 {total} 条，字段完整度 {round(complete * 100)}%",
                "url": url, "status": "pending", "created_at": now.isoformat(timespec="seconds"),
                "decided_at": None, "source": dict(source, enabled=False), "origin": "auto_explore"}
        discovery.add_candidate(data_dir, cand)
        cands.append(cand)
        pending.append(cand)
        added.append(cand)
        _log(data_dir, {"time": now.isoformat(timespec="seconds"), "host": host, "step": "candidate",
                        "result": "added", "total": total, "completeness": round(complete, 3)})

    _save_state(data_dir, state)
    return {"added": added, "skipped": skipped, "sites": checked}
