"""research/fetch.py — 抓取：robots.txt、按域名节流、超时、类型与大小限制、网页缓存（方案 §7 第 4 步）。

* robots：200 按规则判断；404/410 允许；401/403 及其它错误一律拒绝（无法读取时保守不抓）。
* 节流：同一域名两次请求之间至少间隔 min_interval 秒（robots 请求也计入）。
* 不读取系统代理（trust_env 关闭，与 netproxy 约定一致）。
* 只接受 HTML；超过 MAX_BYTES 截断；成功的页面缓存 CACHE_TTL_SEC。
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple
from urllib import robotparser
from urllib.parse import urlsplit

from .. import cancel, settings, store

BINARY_EXT = {".pdf", ".zip", ".gz", ".tar", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp3", ".mp4",
              ".mov", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".epub"}

TIMEOUT_SEC = 15.0
MAX_BYTES = 2 * 1024 * 1024
CACHE_TTL_SEC = 30 * 24 * 3600
ROBOTS_TTL_SEC = 7 * 24 * 3600
USER_AGENT = "article_garden/0.5 (+research; respects robots.txt)"

HttpGet = Callable[[str, float], Tuple[int, str, str]]


def default_http_get(url: str, timeout: float, use_proxy: bool = False) -> Tuple[int, str, str]:
    """返回 (状态码, Content-Type, 正文文本)。网络错误返回 (0, "", 错误说明)。use_proxy=False 时不使用代理。"""
    handlers = [] if use_proxy else [urllib.request.ProxyHandler({})]
    opener = urllib.request.build_opener(*handlers)
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
    except Exception as exc:  # noqa: BLE001 — 网络类错误：状态码 0，正文字段保存错误说明
        return 0, "", f"{type(exc).__name__}: {str(exc)[:120]}"


def thin(html: str, min_chars: int = 200) -> bool:
    """去掉标签后可见文字很少（典型的脚本空壳页面）。"""
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", html or "", flags=re.S | re.I)
    return len("".join(text.split())) < min_chars


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:24]


class Fetcher:
    def __init__(self, ctx, http_get: Optional[HttpGet] = None, min_interval: float = 1.0,
                 sleep: Callable[[float], Any] = time.sleep, timeout: float = TIMEOUT_SEC,
                 render: Optional[Callable[[str], Tuple[Optional[str], str]]] = None):
        self.ctx = ctx
        eff, _ = settings.effective_settings(ctx)
        if http_get is None:
            use_proxy = bool(eff["research"]["use_system_proxy"])
            self.http = lambda url, t: default_http_get(url, t, use_proxy=use_proxy)
        else:
            self.http = http_get
        if render is None and eff["research"]["use_browser"]:
            from . import browser
            render = browser.render_fn(int(eff["research"]["browser_port"]))
        self.render = render
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
        # 旧版本可能把网络错误（状态 0）写入了缓存：这种记录一律忽略，不能让一次断网持续七天
        if (isinstance(rec, dict) and int(rec.get("status", 0)) != 0
                and time.time() - float(rec.get("ts", 0)) < ROBOTS_TTL_SEC):
            return int(rec.get("status", 0)), str(rec.get("text", ""))
        self._throttle(host)
        status, _ctype, text = self.http(f"{scheme}://{host}/robots.txt", self.timeout)
        if status != 0:            # 网络错误不缓存：下次还会重试，不会把一次断网变成七天的结论
            store.write_json_atomic(p, {"ts": time.time(), "status": status, "text": text})
        return status, text

    def allowed(self, url: str) -> Tuple[bool, str]:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return False, "不是可抓取的网页地址"
        status, text = self._robots_status(parts.scheme, parts.netloc)
        if status == 0:
            return False, f"无法连接该网站（网络错误，本次跳过）：{text}"
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
        ext = os.path.splitext(urlsplit(url).path.lower())[1]
        if ext in BINARY_EXT:      # 先判断网址本身：PDF 等文件不发出任何请求
            return {"ok": False, "html": "", "error": f"不是网页（{ext[1:]} 文件，暂不抓取）", "cached": False}
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
        if status == 200 and (not ctype or "html" in ctype.lower()):
            if not thin(html) or self.render is None:
                store.write_json_atomic(p, {"ts": time.time(), "url": url, "html": html})
                return {"ok": True, "html": html, "error": "", "cached": False, "via": "http"}
            # 页面几乎没有文字：多半由脚本加载，改用浏览器渲染（若已开启）
            rhtml, rerr = self.render(url)
            if rhtml and not thin(rhtml):
                store.write_json_atomic(p, {"ts": time.time(), "url": url, "html": rhtml})
                return {"ok": True, "html": rhtml, "error": "", "cached": False, "via": "browser"}
            return {"ok": True, "html": html, "error": "", "cached": False, "via": "http",
                    "note": f"页面内容很少（可能由脚本加载）；浏览器渲染未成功：{rerr or '内容仍然很少'}"}
        if status == 0:
            reason = f"网络错误或超时：{html}"
        elif status in (401, 403):
            reason = (f"HTTP {status}（站点拒绝访问或需要人工验证，不使用浏览器绕过）")
            return {"ok": False, "html": "", "error": reason, "cached": False}
        elif status != 200:
            reason = f"HTTP {status}"
        else:
            reason = f"不是网页（{ctype.split(';')[0]}）"
        if self.render is not None:
            rhtml, rerr = self.render(url)
            if rhtml and not thin(rhtml):
                store.write_json_atomic(p, {"ts": time.time(), "url": url, "html": rhtml})
                return {"ok": True, "html": rhtml, "error": "", "cached": False, "via": "browser"}
            reason += f"；浏览器渲染也未成功：{rerr or '页面内容很少'}"
        return {"ok": False, "html": "", "error": reason, "cached": False}
