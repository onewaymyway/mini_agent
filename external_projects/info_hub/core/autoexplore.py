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

import re

import yaml

from core import discovery
from core.explore_forms import (category_links, feed_links, parse_sitemap_urls, rss_source,
                                 section_listings, sitemap_locations)
from core.explore_json_api import api_source
from core.store import append_jsonl, atomic_write_json, file_lock, read_json, read_jsonl

SEED_FILE = "config/seed_sites.yaml"
AUTO_ADAPTERS = ("rss", "json_api", "html_list")   # script 草稿不会被自动探索生成后运行
DEFAULTS = {"daily": 20, "min_interval_hours": 1, "min_items": 3, "min_completeness": 0.8,
            "harvest_limit": 30, "max_listing_pages": 4, "max_feeds_per_site": 3, "max_apis_per_site": 3,
            "render": "off", "render_daily": 40, "frontier_batch": 10, "host_daily_pages": 30,
            "frontier_max_queued": 500, "page_links_cap": 20, "cross_site": False}
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
        if k == "cross_site":
            continue
        try:
            out[k] = type(default)(raw.get(k, default))
        except (TypeError, ValueError):
            out[k] = default
    out["cross_site"] = str(raw.get("cross_site", False)).strip().lower() in ("1", "true", "yes", "on")
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


def robots_allows(url: str, ctx: Any) -> tuple[bool, str, str]:
    """返回 (是否允许, 原因, robots.txt 正文)。404/410 视为允许；其它错误保守地不允许。"""
    parts = urllib.parse.urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        body = ctx.http_get(robots_url)
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            return True, "no_robots", ""
        return False, "robots_unavailable", ""
    except Exception:  # noqa: BLE001 - 网络错误一律保守处理
        return False, "robots_unavailable", ""
    text = body.decode("utf-8", errors="replace")
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(text.splitlines())
    if not rp.can_fetch("info_hub", url) or not rp.can_fetch("*", url):
        return False, "robots_disallow", text
    return True, "ok", text


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


class _RenderBrowser:
    """把有头渲染器包装成 BrowserSession 的 fetch_html 接口，供 html_list 预览与草稿生成使用。每次渲染计入预算。"""

    def __init__(self, renderer: Any, budget: dict[str, int]) -> None:
        self.renderer = renderer
        self.budget = budget
        self.cache: dict[str, str] = {}   # 同一页面在一次探索中只渲染一次（检测、草稿、预览共用）
        self.obs: dict[str, list[dict[str, Any]]] = {}   # P15：渲染时观察到的列表型接口

    def fetch_html(self, url: str, site: Optional[str] = None, headless: bool = True,
                   wait_selector: Optional[str] = None, expired_pattern: Optional[str] = None,
                   timeout_ms: int = 30000) -> str:
        if url in self.cache:
            return self.cache[url]
        if self.budget["used"] >= self.budget["cap"]:
            raise RuntimeError("render_budget")
        self.budget["used"] += 1
        html = self.renderer.html(url)
        self.cache[url] = html
        self.obs[url] = list(getattr(self.renderer, "last_observations", None) or [])
        return html

    def observations(self, url: str) -> list[dict[str, Any]]:
        return self.obs.get(url, [])


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "root"


def _sitemap_listings(root_url: str, robots_txt: str, ctx: Any) -> list[str]:
    """sitemap 栏目页地址：robots.txt 声明的 sitemap；未声明时尝试 /sitemap.xml。读取失败只跳过。"""
    parts = urllib.parse.urlsplit(root_url)
    locs = sitemap_locations(robots_txt) or [f"{parts.scheme}://{parts.netloc}/sitemap.xml"]
    urls: list[str] = []
    for loc in locs[:2]:
        try:
            xml = ctx.http_get(loc).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        urls += parse_sitemap_urls(xml)
    return [s["url"] for s in section_listings(urls)]


def run(settings: Any, llm: Any, *, ctx: Any, now: Optional[datetime] = None,
        provider_label: str = "", renderer: Any = None) -> dict[str, Any]:
    """一轮自动探索（P16）。

    1) 先处理队列（Frontier）中上次留下的页面，按优先级与深度取出；
    2) 再处理站点：首页 → 订阅入口 → 栏目页（本轮内最多 max_listing_pages 个，其余入队）→ 首页深层链接入队；
    3) 每个页面的处理逻辑相同：候选（订阅 / 列表 / 接口）、渲染回退、模型草稿。
    入队与深度、禁止名单、每站点日请求预算见 core/frontier.py 与配置。
    返回 {added: [...], skipped: {原因: 数量}, sites: 本轮检查的站点数, queued: 队列中待访问数}。
    """
    from adapters.base import FetchContext, detect_block
    from core import source_generator
    from core.explore import explore_html, explore_response
    from core.explore_json_api import api_source
    from core.fingerprint import find_similar
    from core.frontier import Frontier, enqueue_links, host_key, is_denied, load_denylist, normalize
    from core.explore_forms import all_links, category_links, feed_links
    from core.source_store import SourceError, preview_spec, validate_candidate
    from core.sources import load_sources

    now = now or datetime.now().astimezone()
    root, data_dir = settings.paths.root, settings.paths.data_dir
    cfg = load_config(root)
    stamp = now.isoformat(timespec="seconds")
    day = now.date().isoformat()
    skipped: dict[str, int] = {}
    added: list[dict[str, Any]] = []

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    if llm is None:
        llm_note = True   # 没有模型时仍可发现订阅入口与接口，只是不生成 HTML 草稿
    else:
        llm_note = False

    state = _load_state(data_dir)
    today_count = int(state.get("days", {}).get(day, 0))
    renders = state.setdefault("renders", {})
    host_pages = state.setdefault("host_pages", {}).setdefault(day, {})
    dconf = discovery.load_config(root)
    cands = discovery.list_candidates(data_dir)
    pending = [c for c in cands if c.get("status") == "pending"]
    specs = load_sources(settings).specs
    known_hosts = {host_of(str((s.request or {}).get("url") or "")) for s in specs.values()}
    known_hosts.discard("")
    seeds = load_seeds(root)
    known_hosts |= {host_of(s["url"]) for s in seeds}
    sites = seeds + harvest_hosts(data_dir, known_hosts, int(cfg["harvest_limit"]), now)
    trusted = {host_of(s["url"]) for s in seeds}
    trusted |= {host_of(str(c.get("url") or "")) for c in cands if c.get("status") == "accepted"}
    denylist = load_denylist(root)
    frontier = Frontier(data_dir, int(cfg["frontier_max_queued"]))
    render_on = renderer is not None and cfg["render"] == "headed"
    budget = {"used": int(renders.get(day, 0)), "cap": int(cfg["render_daily"])}
    robots_cache: dict[str, tuple[bool, str, str]] = {}

    def robots_for(url: str) -> tuple[bool, str, str]:
        h = host_of(url)
        if h not in robots_cache:
            robots_cache[h] = robots_allows(url, ctx)
        return robots_cache[h]

    def host_ok(host: str) -> bool:
        return int(host_pages.get(host, 0)) < int(cfg["host_daily_pages"]) and not is_denied(host, denylist)

    def fetch_page(url: str) -> tuple[Optional[bytes], str]:
        try:
            body = ctx.http_get(url)
        except Exception:  # noqa: BLE001
            return None, "fetch_error"
        if detect_block(body):
            return None, "blocked_or_login"    # 验证/登录页：放弃，不尝试绕过
        return body, "ok"

    def gate_and_add(source: dict[str, Any], cid: str, template_id: str, template_name: str,
                     url: str, reason_text: str, preview_ctx: Any = None) -> Optional[str]:
        """通过校验、相似度与预览质量后入箱。返回 None 表示入箱，否则返回跳过原因。"""
        spec, errors, _ = validate_candidate(settings, source)
        if spec is None or errors:
            return "invalid"
        if find_similar(specs, source):
            return "duplicate"
        preview = preview_spec(spec, preview_ctx or ctx)
        total, complete = _quality(preview)
        if not preview.get("ok") or total < cfg["min_items"] or complete < cfg["min_completeness"]:
            return "low_quality"
        cand = {"id": cid, "template_id": template_id, "template_name": template_name, "query": host_of(url),
                "reason": f"{reason_text}：{host_of(url)}，预览 {total} 条，字段完整度 {round(complete * 100)}%",
                "url": url, "status": "pending", "created_at": stamp, "decided_at": None,
                "source": dict(source, enabled=False), "origin": "auto_explore"}
        discovery.add_candidate(data_dir, cand)
        cands.append(cand)
        pending.append(cand)
        added.append(cand)
        _log(data_dir, {"time": stamp, "host": host_of(url), "step": "candidate", "result": "added",
                        "candidate": cid, "total": total, "completeness": round(complete, 3)})
        return None

    def explore_page(page_url: str, body: bytes, is_home: bool) -> str:
        """处理一个已取得的页面：订阅入口、列表结构（可选渲染）、接口观察、模型草稿。返回页面文本。"""
        host = host_of(page_url)
        base_id = f"{PREFIX}{host}"
        text = body.decode("utf-8", errors="replace")
        for n, feed_url in enumerate(feed_links(text, page_url), start=1):
            if n > int(cfg["max_feeds_per_site"]):
                break
            if host_of(feed_url) != host:
                continue
            fid = f"{base_id}#feed-{n}"
            if fid in {c["id"] for c in cands}:
                continue
            src = rss_source(f"auto_{_slug(host).replace('-', '_')}_feed{n}", f"{host} 订阅 {n}", feed_url)
            outcome = gate_and_add(src, fid, "auto_feed", "自动探索·订阅", feed_url, "发现订阅入口")
            if outcome:
                skip(outcome)

        summary = explore_response(body, "")
        has = bool(summary.get("candidates") or summary.get("page_type") == "json")
        rbrowser = _RenderBrowser(renderer, budget) if render_on else None
        rendered_here = False
        if not has and rbrowser is not None and budget["used"] < budget["cap"]:
            try:
                rendered = rbrowser.fetch_html(page_url)
                if explore_html(rendered).get("candidates"):
                    has, rendered_here = True, True
                    body = rendered.encode("utf-8")
                    text = rendered
                    _log(data_dir, {"time": stamp, "host": host, "step": "render", "result": "ok", "page": page_url})
            except Exception:  # noqa: BLE001
                skip("render_error")
            finally:
                renders[day] = budget["used"]

        api_added = 0
        if rbrowser is not None and page_url in rbrowser.cache:
            for desc in rbrowser.observations(page_url):
                if api_added >= int(cfg["max_apis_per_site"]):
                    break
                slug = _slug(urllib.parse.urlsplit(desc["endpoint"]).path)
                aid = f"{base_id}#api-{slug}"
                if aid in {c["id"] for c in cands}:
                    continue
                src = api_source(f"auto_{_slug(host).replace('-', '_')}_api_{slug.replace('-', '_')}",
                                 f"{host} 接口 {slug}", desc)
                outcome = gate_and_add(src, aid, "auto_api", "自动探索·接口", desc["endpoint"], "发现站内接口")
                if outcome:
                    skip(outcome)
                else:
                    api_added += 1

        if not has:
            skip("no_structure" if is_home else "listing_no_structure")
            return text
        if llm_note:
            skip("no_llm")
            return text
        cid = base_id if is_home else f"{base_id}#{_slug(urllib.parse.urlsplit(page_url).path)}"
        if cid in {c["id"] for c in cands}:
            skip("already_known")
            return text
        gen_ctx = FetchContext(http_get=ctx.http_get, browser=rbrowser) if rendered_here else ctx
        try:
            draft = source_generator.generate_draft(settings, llm, page_url, name=host,
                                                    hint="自动探索：" + host, provider_label=provider_label,
                                                    use_browser=rendered_here, ctx=gen_ctx)
        except SourceError:
            skip("generate_error")
            return text
        source = draft["source"]
        if source.get("adapter") not in AUTO_ADAPTERS:
            skip("script_not_auto")                     # script 草稿不会被自动探索生成后运行
            return text
        if rendered_here:
            source["browser"] = {"required": True, "site": _slug(host).replace("-", "_"), "headless": False}
        outcome = gate_and_add(source, cid, "auto_explore", "自动探索", page_url, "发现信息源",
                               preview_ctx=gen_ctx if rendered_here else None)
        if outcome:
            skip(outcome)
        return text

    # ---- 1) 队列中上次留下的页面 ----
    for row in frontier.next_batch(int(cfg["frontier_batch"]), host_ok):
        url, h, depth = row["url"], row.get("host", ""), int(row.get("depth", 1))
        ok, why, _ = robots_for(url)
        if not ok:
            frontier.set_status(url, why, stamp)
            skip(why)
            continue
        body, reason = fetch_page(url)
        host_pages[h] = int(host_pages.get(h, 0)) + 1
        if body is None:
            frontier.set_status(url, reason, stamp)
            skip(reason)
            continue
        text = explore_page(url, body, is_home=False)
        frontier.set_status(url, "done", stamp)
        enqueue_links(frontier, all_links(text, url), parent=url, parent_depth=depth, denylist=denylist,
                      cross_site=bool(cfg["cross_site"]), trusted=h in trusted, now_iso=stamp,
                      per_page_cap=int(cfg["page_links_cap"]))

    # ---- 2) 站点：首页 → 栏目页（本轮内有限处理）→ 其余入队 ----
    checked = 0
    for site in sites:
        url = site["url"]
        host = host_of(url)
        base_id = f"{PREFIX}{host}"
        if today_count >= int(cfg["daily"]):
            skip("daily_cap")
            break
        if len(pending) >= dconf["pending_cap"]:
            skip("pending_cap")
            break
        if any(c["id"] == base_id or c["id"].startswith(base_id + "#") for c in cands):
            skip("already_known")
            continue
        if _within_interval(state, host, now, cfg["min_interval_hours"]):
            skip("min_interval")
            continue
        if is_denied(host, denylist):
            skip("denylisted")
            continue
        checked += 1
        state.setdefault("hosts", {})[host] = stamp
        today_count += 1
        state.setdefault("days", {})[day] = today_count

        ok, why, robots_txt = robots_for(url)
        if not ok:
            skip(why)
            _log(data_dir, {"time": stamp, "host": host, "step": "robots", "result": "skip", "reason": why})
            continue
        body, reason = fetch_page(url)
        if body is None:
            skip(reason)
            _log(data_dir, {"time": stamp, "host": host, "step": "fetch", "result": "skip", "reason": reason})
            continue
        host_pages[host] = int(host_pages.get(host, 0)) + 1
        text = explore_page(url, body, is_home=True)

        listings = _sitemap_listings(url, robots_txt, ctx) + category_links(text, url,
                                                                            int(cfg["max_listing_pages"]) * 2)
        listings = [n for n in dict.fromkeys(normalize(u) or "" for u in listings) if n and host_of(n) == host]
        inline, rest = listings[:int(cfg["max_listing_pages"])], listings[int(cfg["max_listing_pages"]):]
        for lu in inline:
            frontier.set_status(lu, "done", stamp)           # 本轮内处理，不再进入队列
        for lu in rest:
            frontier.enqueue(lu, 1, url, 2.0, stamp)
        enqueue_links(frontier, all_links(text, url), parent=url, parent_depth=0, denylist=denylist,
                      cross_site=bool(cfg["cross_site"]), trusted=host in trusted, now_iso=stamp,
                      per_page_cap=int(cfg["page_links_cap"]))
        for lu in inline:
            if not host_ok(host):
                skip("host_budget")
                break
            lbody, lreason = fetch_page(lu)
            if lbody is None:
                skip("listing_" + lreason)
                continue
            host_pages[host] = int(host_pages.get(host, 0)) + 1
            ltext = explore_page(lu, lbody, is_home=False)
            enqueue_links(frontier, all_links(ltext, lu), parent=lu, parent_depth=1, denylist=denylist,
                          cross_site=bool(cfg["cross_site"]), trusted=host in trusted, now_iso=stamp,
                          per_page_cap=int(cfg["page_links_cap"]))

    state["renders"] = renders
    state.setdefault("host_pages", {})[day] = host_pages
    _save_state(data_dir, state)
    return {"added": added, "skipped": skipped, "sites": checked, "queued": frontier.queued_count()}
