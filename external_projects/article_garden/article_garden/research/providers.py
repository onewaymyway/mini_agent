"""research/providers.py — 检索来源（官方开放接口，不抓取搜索结果页，也不抓取知乎等站点的页面）。

* ArxivSearch：arXiv 官方 API（英文检索词；结果附摘要，可直接作为来源摘录）。
* CrossrefSearch：Crossref 官方 API（论文元数据与摘要，英文为主）。
* WikiSearch：中文维基百科搜索 API（中文检索词；结果附简介）。
* 网页搜索仍由 search.MiniAgentSearch 提供（DuckDuckGo 等，见设置）。

这些接口都是为程序调用提供的；它们不受网页 robots.txt 约束，但仍设置了明确的 User-Agent 与请求超时。
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Callable, List, Optional
from urllib.parse import quote

from .search import Hit, MiniAgentSearch, SearchError


def _text(s: Any) -> str:
    """去掉标记（如搜索结果中的高亮 span、JATS 段落标签），不插入多余空格。"""
    return " ".join(re.sub(r"<[^>]+>", "", str(s or "")).split())


class _Api:
    name = "api"
    languages = ("en",)

    def __init__(self, http: Callable[[str, float], Any], timeout: float = 20.0):
        self.http = http
        self.timeout = timeout

    def _get(self, url: str) -> str:
        status, _ctype, text = self.http(url, self.timeout)
        if status == 0:
            raise SearchError(f"{self.name} 无法连接：{text[:120]}")
        if status != 200:
            raise SearchError(f"{self.name} 请求失败：HTTP {status}")
        return text


class ArxivSearch(_Api):
    name = "arxiv"
    languages = ("en",)

    def search(self, query: str, max_results: int) -> List[Hit]:
        url = (f"https://export.arxiv.org/api/query?search_query=all:{quote(query)}"
               f"&start=0&max_results={int(max_results)}")
        try:
            root = ET.fromstring(self._get(url))
        except ET.ParseError as exc:
            raise SearchError(f"arxiv 返回内容无法解析：{exc}") from exc
        ns = {"a": "http://www.w3.org/2005/Atom"}
        out: List[Hit] = []
        for e in root.findall("a:entry", ns):
            title = " ".join((e.findtext("a:title", "", ns) or "").split())
            link = (e.findtext("a:id", "", ns) or "").strip().replace("http://", "https://")
            summary = " ".join((e.findtext("a:summary", "", ns) or "").split())
            if title and link:
                out.append(Hit(title=title, url=link, snippet=summary, source="arxiv"))
        return out


class CrossrefSearch(_Api):
    name = "crossref"
    languages = ("en",)

    def search(self, query: str, max_results: int) -> List[Hit]:
        url = (f"https://api.crossref.org/works?query.bibliographic={quote(query)}"
               f"&rows={int(max_results)}&select=DOI,title,abstract,URL")
        try:
            items = json.loads(self._get(url))["message"]["items"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SearchError(f"crossref 返回内容无法解析：{exc}") from exc
        out: List[Hit] = []
        for it in items:
            titles = it.get("title") or [""]
            title = " ".join(str(titles[0]).split())
            doi = it.get("DOI") or ""
            link = it.get("URL") or (f"https://doi.org/{doi}" if doi else "")
            if title and link:
                out.append(Hit(title=title, url=link, snippet=_text(it.get("abstract")), source="crossref"))
        return out


class WikiSearch(_Api):
    name = "wikipedia"
    languages = ("zh",)

    def search(self, query: str, max_results: int) -> List[Hit]:
        url = (f"https://zh.wikipedia.org/w/api.php?action=query&list=search&srsearch={quote(query)}"
               f"&srlimit={int(max_results)}&format=json")
        try:
            rows = json.loads(self._get(url))["query"]["search"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SearchError(f"wikipedia 返回内容无法解析：{exc}") from exc
        out: List[Hit] = []
        for r in rows:
            title = str(r.get("title") or "").strip()
            if title:
                link = "https://zh.wikipedia.org/wiki/" + quote(title.replace(" ", "_"))
                out.append(Hit(title=title, url=link, snippet=_text(r.get("snippet")), source="wikipedia"))
        return out


def default_backends(eff: dict, http: Optional[Callable[[str, float], Any]] = None) -> list:
    """按设置返回启用的检索来源。http 用于三个开放接口（沿用调研的代理设置）。"""
    if http is None:
        from . import fetch
        use_proxy = bool(eff["research"]["use_system_proxy"])
        http = lambda url, t: fetch.default_http_get(url, t, use_proxy=use_proxy)  # noqa: E731
    r = eff["research"]
    out: list = []
    if r["use_web_search"]:
        out.append(MiniAgentSearch(eff["search"]["provider"]))
    if r["use_arxiv"]:
        out.append(ArxivSearch(http))
    if r["use_crossref"]:
        out.append(CrossrefSearch(http))
    if r["use_wikipedia"]:
        out.append(WikiSearch(http))
    return out
