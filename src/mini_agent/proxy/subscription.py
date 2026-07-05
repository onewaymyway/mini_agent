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
    """mibei77.com 站点的抓取器示例。

    该站点页面本身是 HTML,里面嵌了当日订阅链接(链接内容经常变化),
    所以这里分两步: 1) 抓取页面找到当日订阅直链 2) 再 GET 该直链拿真正的节点列表。
    具体的页面解析规则需要跟着网站改版调整,这里给出可扩展的骨架和注释,
    不假设固定的 CSS 选择器(避免网站一改版就失效导致误报"能用")。
    """

    name = "mibei77"

    def __init__(self, page_url: str = "https://www.mibei77.com/"):
        self.page_url = page_url

    async def fetch(self, client: httpx.AsyncClient) -> list[ProxyNode]:
        resp = await client.get(self.page_url, timeout=15.0)
        resp.raise_for_status()
        html = resp.text
        # 找出页面里形如 https://xxx/xxx.txt 或包含 'sub' 关键字的直链
        candidate_links = re.findall(r'https?://[^\s"\'<>]+\.(?:txt|yaml|yml)', html)
        candidate_links += re.findall(r'https?://[^\s"\'<>]*sub[^\s"\'<>]*', html)
        nodes: list[ProxyNode] = []
        seen = set()
        for link in dict.fromkeys(candidate_links):  # 去重保序
            if link in seen:
                continue
            seen.add(link)
            try:
                sub_resp = await client.get(link, timeout=15.0)
                if sub_resp.status_code == 200:
                    nodes.extend(parse_subscription_text(sub_resp.text))
            except Exception:
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
