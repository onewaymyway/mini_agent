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


class DiscoveredSource(SubscriptionSource):
    """读取 `discovered_sources.json` 里由 agent/skill 自动发现并写入的订阅地址。

    设计目的: 让"抓订阅源地址"这件事本身也能交给 agent 去做(比如一个专门的
    skill 去搜索/浏览网页找当天可用的订阅链接),skill 只需要把结果追加写入
    这个文件,不需要碰 sources.json 或懂 proxy_ctl 内部实现,两边解耦。

    文件格式(list[dict]):
        [{"name": "xxx", "url": "https://...", "discovered_at": 1234567890.0,
          "discovered_by": "skill_name (可选)"}]

    每次 fetch 时会用当前的 URL 抓取内容并解析成节点,文件本身只是"地址列表",
    不缓存节点内容。
    """

    name = "discovered"

    def __init__(self, file_path):
        self.file_path = file_path

    def _load_entries(self) -> list[dict]:
        from pathlib import Path

        p = Path(self.file_path)
        if not p.exists():
            return []
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []

    async def fetch(self, client: httpx.AsyncClient) -> list[ProxyNode]:
        import logging

        log = logging.getLogger(__name__)
        entries = self._load_entries()
        nodes: list[ProxyNode] = []
        for e in entries:
            url = e.get("url")
            if not url:
                continue
            try:
                resp = await client.get(url, headers={"User-Agent": "clash-verge/1.0"}, timeout=15.0)
                resp.raise_for_status()
                parsed = parse_subscription_text(resp.text)
                log.info("[discovered:%s] %s -> %d 个节点", e.get("name", "?"), url, len(parsed))
                nodes.extend(parsed)
            except Exception as ex:
                log.warning("[discovered:%s] 拉取失败 %s: %s", e.get("name", "?"), url, ex)
        return nodes

    @staticmethod
    def append_entry(file_path, name: str, url: str, discovered_by: str = "") -> None:
        """供 agent/skill 调用: 把新发现的订阅地址追加进 discovered_sources.json。
        按 (name, url) 去重,重复调用不会产生重复条目。"""
        import time as _time
        from pathlib import Path

        p = Path(file_path)
        entries = []
        if p.exists():
            try:
                entries = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                entries = []
        key = (name, url)
        if any((e.get("name"), e.get("url")) == key for e in entries):
            return
        entries.append({
            "name": name, "url": url,
            "discovered_at": _time.time(), "discovered_by": discovered_by,
        })
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")


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


async def fetch_all(sources: list[SubscriptionSource], return_stats: bool = False):
    async with httpx.AsyncClient(follow_redirects=True) as client:
        all_nodes: list[ProxyNode] = []
        per_source_counts: dict[str, int] = {}
        for src in sources:
            try:
                fetched = await src.fetch(client)
            except Exception:
                # 单个订阅源失败不应该影响其它源
                fetched = []
            per_source_counts[getattr(src, "name", src.__class__.__name__)] = len(fetched)
            all_nodes.extend(fetched)
        # 按 key(protocol+server+port) 去重: 同一个真实节点常常被多个订阅源
        # 重复收录(不同站点转发同一批公共节点是常态),验证前先去重能显著减少
        # 重复的本地代理进程/网络请求开销。后出现的同 key 节点会覆盖前面的
        # (通常意味着"更新的订阅源",名字/参数更可能是最新的)。
        dedup: dict[str, ProxyNode] = {}
        for n in all_nodes:
            dedup[n.key()] = n
        deduped = list(dedup.values())
        if return_stats:
            return deduped, {
                "raw_total": len(all_nodes),
                "deduped_total": len(deduped),
                "duplicates_removed": len(all_nodes) - len(deduped),
                "per_source": per_source_counts,
            }
        return deduped


# 订阅源类型注册表: type 字符串 -> (entry: dict, paths) -> SubscriptionSource | None
# 新增一种订阅源类型时,只需要在这里注册一个工厂函数,不需要改 proxy_ctl.py /
# cli/commands/proxy.py 里构造 sources 列表的逻辑,做到"新增来源方式"和
# "调用来源方式"两边解耦,方便以后接入更多站点或抓取方式(包括未来可能新增的
# 一个专门"发现订阅源"的 skill,让它产出的地址通过 "discovered" 类型接入)。
SOURCE_TYPE_REGISTRY: dict[str, Any] = {}


def register_source_type(type_name: str):
    """装饰器: 注册一个 {"type": type_name, ...} 配置条目 -> SubscriptionSource 的工厂函数。

    工厂函数签名: (entry: dict, paths: AgentPaths | None) -> SubscriptionSource | None
    返回 None 表示这个条目应该被跳过(比如缺少必需字段)。
    """

    def _decorator(fn):
        SOURCE_TYPE_REGISTRY[type_name] = fn
        return fn

    return _decorator


def build_source_from_entry(entry: dict, paths=None) -> "SubscriptionSource | None":
    """根据 sources.json 里一条配置构造对应的 SubscriptionSource。未知 type 返回 None
    并留给调用方决定是否警告,不在这里抛异常影响其它源的构造。"""
    factory = SOURCE_TYPE_REGISTRY.get(entry.get("type"))
    if factory is None:
        return None
    return factory(entry, paths)


@register_source_type("url")
def _build_url_source(entry: dict, paths=None) -> "SubscriptionSource | None":
    if "url" not in entry:
        return None
    return URLSubscriptionSource(entry.get("name", entry["url"]), entry["url"])


@register_source_type("mibei77")
def _build_mibei77_source(entry: dict, paths=None) -> "SubscriptionSource | None":
    return MiBei77Source(entry.get("page_url", "https://www.mibei77.com/"))


@register_source_type("discovered")
def _build_discovered_source(entry: dict, paths=None) -> "SubscriptionSource | None":
    file_path = entry.get("file_path") or (
        str(paths.workdir_proxy_discovered_sources) if paths is not None else None
    )
    if file_path is None:
        return None
    return DiscoveredSource(file_path)
