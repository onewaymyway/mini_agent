"""research/fetch.py — 抓取：robots.txt、按域名节流、超时、类型与大小限制、网页缓存（方案 §7 第 4 步）。

* robots：200 按规则判断；404/410 允许；401/403 及其它错误一律拒绝（无法读取时保守不抓）。
* 节流：同一域名两次请求之间至少间隔 min_interval 秒（robots 请求也计入）。
* 不读取系统代理（trust_env 关闭，与 netproxy 约定一致）。
* 只接受 HTML；超过 MAX_BYTES 截断；成功的页面缓存 CACHE_TTL_SEC。
"""

from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple
from urllib import robotparser
from urllib.parse import urlsplit

from .. import cancel, store

TIMEOUT_SEC = 15.0
MAX_BYTES = 2 * 1024 * 1024
CACHE_TTL_SEC = 30 * 24 * 3600
ROBOTS_TTL_SEC = 7 * 24 * 3600
USER_AGENT = "article_garden/0.5 (+research; respects robots.txt)"

HttpGet = Callable[[str, float], Tuple[int, str, str]]


def default_http_get(url: str, timeout: float) -> Tuple[int, str, str]:
    """返回 (状态码, Content-Type, 正文文本)；网络错误返回 (0, "", "")。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 不读取系统代理
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "text/html,*/*;q=0.5"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            ctype = resp.headers.get("Content-Type", "") or ""
            charset = resp.headers.get_content_charset() or "utf-8"
            raw = resp.read(MAX_BYTES)
            return resp.status, ctype, raw.decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, (exc.headers.get("Content-Type", "") if exc.headers else ""), ""
    except Exception:  # noqa: BLE001 — 网络类错误统一视为 0
        return 0, "", ""


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:24]


class Fetcher:
    def __init__(self, ctx, http_get: Optional[HttpGet] = None, min_interval: float = 1.0,
                 sleep: Callable[[float], Any] = time.sleep, timeout: float = TIMEOUT_SEC):
        self.ctx = ctx
        self.http = http_get or default_http_get
        self.min_interval = float(min_interval)
        self.sleep = sleep
        self.timeout = timeout
        self._last: Dict[str, float] = {}

    # ---------------------------------------------------------------- 节流
    def _throttle(self, host: str) -> None:
        cancel.check()
        now = time.monotonic()
        last = self._last.get(host)
        if last is not None and self.min_interval > 0:
            wait = self.min_interval - (now - last)
            if wait > 0:
                self.sleep(wait)
        self._last[host] = time.monotonic()

    # ---------------------------------------------------------------- robots
    def _robots_status(self, scheme: str, host: str) -> Tuple[int, str]:
        p = self.ctx.path("research", "cache", "robots", _hash(host) + ".json")
        try:
            rec = store.read_json(p, default=None)
        except ValueError:
            rec = None
        if isinstance(rec, dict) and time.time() - float(rec.get("ts", 0)) < ROBOTS_TTL_SEC:
            return int(rec.get("status", 0)), str(rec.get("text", ""))
        self._throttle(host)
        status, _ctype, text = self.http(f"{scheme}://{host}/robots.txt", self.timeout)
        store.write_json_atomic(p, {"ts": time.time(), "status": status, "text": text})
        return status, text

    def allowed(self, url: str) -> Tuple[bool, str]:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return False, "不是可抓取的网页地址"
        status, text = self._robots_status(parts.scheme, parts.netloc)
        if status in (404, 410):
            return True, ""
        if status == 200:
            rp = robotparser.RobotFileParser()
            rp.parse(text.splitlines())
            if rp.can_fetch(USER_AGENT, url):
                return True, ""
            return False, "robots.txt 禁止抓取该页面"
        return False, f"robots.txt 无法读取（HTTP {status or '网络错误'}），保守起见不抓取"

    # ---------------------------------------------------------------- 页面
    def fetch(self, url: str) -> Dict[str, Any]:
        url = (url or "").strip()
        ok, why = self.allowed(url)
        if not ok:
            return {"ok": False, "html": "", "error": why, "cached": False}
        parts = urlsplit(url)
        p = self.ctx.path("research", "cache", "pages", _hash(url) + ".json")
        try:
            rec = store.read_json(p, default=None)
        except ValueError:
            rec = None
        if isinstance(rec, dict) and time.time() - float(rec.get("ts", 0)) < CACHE_TTL_SEC:
            return {"ok": True, "html": rec.get("html", ""), "error": "", "cached": True}
        self._throttle(parts.netloc)
        status, ctype, html = self.http(url, self.timeout)
        if status == 0:
            return {"ok": False, "html": "", "error": "网络错误或超时", "cached": False}
        if status != 200:
            return {"ok": False, "html": "", "error": f"HTTP {status}", "cached": False}
        if ctype and "html" not in ctype.lower():
            return {"ok": False, "html": "", "error": f"不是网页（{ctype.split(';')[0]}）", "cached": False}
        store.write_json_atomic(p, {"ts": time.time(), "url": url, "html": html})
        return {"ok": True, "html": html, "error": "", "cached": False}
