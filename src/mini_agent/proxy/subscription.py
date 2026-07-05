"""代理订阅抓取与节点解析。

设计原则:
- 订阅源可插拔(SubscriptionSource 接口),不硬编码某一个网站。
- 支持最常见的几种分享链接协议: ss:// vmess:// vless:// trojan:// hysteria2://
- 只做"解析成结构化 ProxyNode",不在这里做可用性验证(验证在 validator.py 里)。
"""

from __future__ import annotations

import base64
import json
import re
import urllib.parse as urlparse
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class ProxyNode:
    """一个代理节点的结构化描述。"""

    protocol: str  # ss / vmess / vless / trojan / hysteria2
    name: str
    server: str
    port: int
    raw: str  # 原始分享链接,便于调试
    params: dict[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        return f"{self.protocol}://{self.server}:{self.port}"


def _b64_decode(s: str) -> str:
    s = s.strip()
    # 补齐 padding
    s += "=" * (-len(s) % 4)
    try:
        return base64.urlsafe_b64decode(s).decode("utf-8", errors="ignore")
    except Exception:
        return base64.b64decode(s).decode("utf-8", errors="ignore")


def parse_ss_uri(uri: str) -> ProxyNode | None:
    # ss://BASE64(method:password)@server:port#name  或旧格式 ss://BASE64(method:password@server:port)
    try:
        body = uri[len("ss://"):]
        name = ""
        if "#" in body:
            body, frag = body.split("#", 1)
            name = urlparse.unquote(frag)
        if "@" in body:
            userinfo_b64, hostpart = body.split("@", 1)
            userinfo = _b64_decode(userinfo_b64) if not re.match(r"^[\w-]+:", userinfo_b64) else userinfo_b64
            method, password = userinfo.split(":", 1)
            server, port = hostpart.split(":", 1)
            port = port.split("/")[0].split("?")[0]
        else:
            decoded = _b64_decode(body)
            method_pw, server_port = decoded.split("@", 1)
            method, password = method_pw.split(":", 1)
            server, port = server_port.split(":", 1)
        return ProxyNode(
            protocol="ss",
            name=name or server,
            server=server,
            port=int(port),
            raw=uri,
            params={"method": method, "password": password},
        )
    except Exception:
        return None


def parse_vmess_uri(uri: str) -> ProxyNode | None:
    # vmess://BASE64(json)
    try:
        payload = json.loads(_b64_decode(uri[len("vmess://"):]))
        return ProxyNode(
            protocol="vmess",
            name=payload.get("ps") or payload.get("add", "vmess"),
            server=payload["add"],
            port=int(payload["port"]),
            raw=uri,
            params=payload,
        )
    except Exception:
        return None


def _parse_uri_style(uri: str, protocol: str) -> ProxyNode | None:
    # vless://uuid@server:port?params#name , trojan://password@server:port?params#name
    try:
        parsed = urlparse.urlparse(uri)
        server = parsed.hostname
        port = parsed.port
        name = urlparse.unquote(parsed.fragment) if parsed.fragment else server
        query = dict(urlparse.parse_qsl(parsed.query))
        query["_userinfo"] = urlparse.unquote(parsed.username or "")
        return ProxyNode(
            protocol=protocol, name=name, server=server, port=int(port), raw=uri, params=query
        )
    except Exception:
        return None


_PARSERS = {
    "ss://": lambda u: parse_ss_uri(u),
    "vmess://": lambda u: parse_vmess_uri(u),
    "vless://": lambda u: _parse_uri_style(u, "vless"),
    "trojan://": lambda u: _parse_uri_style(u, "trojan"),
    "hysteria2://": lambda u: _parse_uri_style(u, "hysteria2"),
    "hy2://": lambda u: _parse_uri_style(u, "hysteria2"),
}


def parse_node_uri(uri: str) -> ProxyNode | None:
    uri = uri.strip()
    for prefix, fn in _PARSERS.items():
        if uri.startswith(prefix):
            return fn(uri)
    return None


def parse_subscription_text(text: str) -> list[ProxyNode]:
    """subscription 内容可能整体是 base64,也可能是明文,每行一个节点链接。"""
    text = text.strip()
    if not text:
        return []
    # 先尝试判断是不是"整体 base64"(不含协议头,且能 decode 出协议头)
    if not any(p in text for p in _PARSERS):
        try:
            decoded = _b64_decode(text)
            if any(p in decoded for p in _PARSERS):
                text = decoded
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.proxy.subscription')
            pass

    nodes: list[ProxyNode] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        node = parse_node_uri(line)
        if node:
            nodes.append(node)
    return nodes


class SubscriptionSource:
    """订阅源抽象接口,便于接入不同站点/自建订阅。"""

    name: str = "base"

    async def fetch(self, client: httpx.AsyncClient) -> list[ProxyNode]:
        raise NotImplementedError


class URLSubscriptionSource(SubscriptionSource):
    """最通用的实现: 给一个订阅 URL,GET 下来解析。

    mibei77.com 等站点通常是"每日更新的一个静态订阅链接页面",实际可用地址
    经常会变化,建议把当天抓到的 URL 存到配置里,而不是把域名硬编码进代码。
    """

    def __init__(self, name: str, url: str, headers: dict[str, str] | None = None):
        self.name = name
        self.url = url
        self.headers = headers or {"User-Agent": "clash-verge/1.0"}

    async def fetch(self, client: httpx.AsyncClient) -> list[ProxyNode]:
        resp = await client.get(self.url, headers=self.headers, timeout=15.0)
        resp.raise_for_status()
        return parse_subscription_text(resp.text)


class MiBei77Source(SubscriptionSource):
    """mibei77.com 站点的抓取器。

    真实站点结构(2026年观察到的版式,可能随改版变化):
      1. 首页 https://www.mibei77.com/ 是一个文章列表,每天发一篇标题形如
         "2026年07月05日免费精选节点203条 ..." 的帖子,链接形如
         https://www.mibei77.com/340.html。首页本身不包含订阅直链。
      2. 需要先从首页找到"标题里带日期 + 免费精选节点"的帖子中日期最新的一篇
         (不能简单取列表第一条,因为顶部可能有置顶的无关广告/资讯贴)。
      3. 进入该帖子正文后,订阅直链是形如
         https://mm.mibei77.com/202607/07.0564bacfr.txt (v2ray/小火箭/winxray 通用)
         https://mm.mibei77.com/202607/0705Clashold.yaml (Clash Meta 专用格式)
         这两种链接。我们只处理前者(.txt,内容是 ss/vmess/vless/trojan 链接列表或
         base64 blob),因为 Clash yaml 里的节点写法和这里的 URI 解析器不兼容。
    """

    name = "mibei77"

    _POST_LINK_RE = re.compile(
        r'href="(https://www\.mibei77\.com/\d+\.html)"[^>]*>((?:(?!</a>).)*)</a>',
        re.S,
    )
    _DATE_RE = re.compile(r"(\d{4})年(\d{2})月(\d{2})日免费精选节点")
    _SUB_LINK_RE = re.compile(r"https://mm\.mibei77\.com/\S+?\.(?:txt|yaml|yml)")

    def __init__(self, page_url: str = "https://www.mibei77.com/"):
        self.page_url = page_url
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.mibei77.com/",
        }

    def _find_latest_post_url(self, homepage_html: str) -> str | None:
        """在首页 HTML 里找出标题含日期且包含"免费精选节点"的帖子,取日期最新的一个。

        不依赖"列表第一条 = 最新"这个假设,因为首页可能有置顶的无关内容
        排在真正最新的节点帖前面(实测确实如此)。
        """
        candidates: list[tuple[tuple[str, str, str], str]] = []
        for m in self._POST_LINK_RE.finditer(homepage_html):
            url, inner_text = m.group(1), m.group(2)
            date_m = self._DATE_RE.search(inner_text)
            if date_m:
                candidates.append((date_m.groups(), url))
        if not candidates:
            return None
        candidates.sort(key=lambda c: c[0], reverse=True)  # 按 (年,月,日) 字符串排序,最新的在前
        return candidates[0][1]

    async def fetch(self, client: httpx.AsyncClient) -> list[ProxyNode]:
        import logging

        log = logging.getLogger(__name__)
        resp = await client.get(self.page_url, headers=self._headers, timeout=15.0)
        resp.raise_for_status()
        post_url = self._find_latest_post_url(resp.text)
        if not post_url:
            log.warning("[mibei77] 首页没找到匹配 '日期+免费精选节点' 的帖子链接,网站版式可能变了")
            return []
        log.info("[mibei77] 定位到最新帖子: %s", post_url)

        post_resp = await client.get(post_url, headers=self._headers, timeout=15.0)
        post_resp.raise_for_status()
        sub_links = list(dict.fromkeys(self._SUB_LINK_RE.findall(post_resp.text)))
        if not sub_links:
            log.warning("[mibei77] 帖子正文里没找到 mm.mibei77.com 订阅直链,页面结构可能变了")
            return []
        log.info("[mibei77] 找到 %d 个订阅直链: %s", len(sub_links), sub_links)

        # 优先用 .txt(通用 URI 列表),yaml 是 Clash 专用格式,这里的解析器不支持
        txt_links = [l for l in sub_links if l.endswith(".txt")]
        chosen_links = txt_links or sub_links

        nodes: list[ProxyNode] = []
        for link in chosen_links:
            try:
                sub_resp = await client.get(link, headers=self._headers, timeout=15.0)
                if sub_resp.status_code == 200:
                    parsed = parse_subscription_text(sub_resp.text)
                    log.info("[mibei77] %s -> %d 个节点", link, len(parsed))
                    nodes.extend(parsed)
                else:
                    log.warning("[mibei77] 订阅链接返回 %d: %s", sub_resp.status_code, link)
            except Exception as e:
                log.warning("[mibei77] 拉取订阅链接失败 %s: %s", link, e)
                continue
        return nodes


async def fetch_all(sources: list[SubscriptionSource]) -> list[ProxyNode]:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        all_nodes: list[ProxyNode] = []
        for src in sources:
            try:
                all_nodes.extend(await src.fetch(client))
            except Exception:
                # 单个订阅源失败不应该影响其它源
                continue
        # 按 key 去重
        dedup: dict[str, ProxyNode] = {}
        for n in all_nodes:
            dedup[n.key()] = n
        return list(dedup.values())
