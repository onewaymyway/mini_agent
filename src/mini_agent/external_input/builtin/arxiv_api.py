"""external_input/builtin/arxiv_api.py — ArxivApiInputSource（外部数据知识化计划 P5，可选）

设计背景见
next_doc/external_knowledge_wiki_and_self_improvement_plan.md §3 P5：
现有 `watch` source 的 `rss` fetcher 在"追踪技术动态"场景下信息密度
有限——例如 `arxiv_cs_ai` 这个 RSS 源往往只有标题、没有摘要，喂给
`external_input/knowledge_extractor.py` 抽取时经常抽不出有信息量的
entity/fact。本 source 直接调用 arXiv 官方 API，拿到结构化的
title/abstract/authors，作为 `watch` RSS 的替代/补充。

只是"来源"层面的替换：产生的事件依旧走
`channel: agent_watch`（`sources.yaml` 里配置），依旧被
`external_input/knowledge_extractor.py` 消费（P1，只认
`payload.channel == "agent_watch"`），不需要改动下游任何消费链路。

严格遵循 `ExternalInputSource` 扩展点（`@register_source`），实现方式
参考 `builtin/watch.py`/`builtin/weather.py`：不调用 LLM，只做纯脚本
抓取 + 去重判断；跨轮询状态（已见过的 arXiv id 集合）全部通过 `state`
dict 传递，不使用实例属性。

`params` 支持的键：

- ``category``（必填，如 ``"cs.AI"``）：arXiv 的
  `search_query=cat:<category>` 分类过滤。
- ``keywords``（可选，list[str]）：标题命中任一关键词才产生事件
  （大小写不敏感），跟 `watch.py` 的 rss fetcher 语义一致；不配置时
  不做标题过滤。
- ``max_results``（可选，默认 20）：单次拉取条数上限（按提交时间倒序，
  arXiv API 的 `sortBy=submittedDate&sortOrder=descending`）。

去重：`state["seen_ids"]` 记录已产生过事件的 arXiv id（形如
`http://arxiv.org/abs/2401.12345v1`），跟 `watch.py::_poll_rss()` 同款
"只保留最近若干条，防止无限增长"策略。
"""

from __future__ import annotations

from typing import Optional
from xml.etree import ElementTree as ET

from mini_agent.external_input.source import (
    ExternalInputEvent,
    ExternalInputSource,
    register_source,
)

_DEFAULT_TIMEOUT = 15
_DEFAULT_MAX_RESULTS = 20
_DEFAULT_MAX_SEEN_IDS = 500
_ARXIV_API_URL = "http://export.arxiv.org/api/query"

_ATOM_NS = "{http://www.w3.org/2005/Atom}"


class ArxivFetchError(RuntimeError):
    """抓取/解析 arXiv API 失败。直接向上抛给 GatewayPoller，交由其统一的
    退避熔断处理（跟 watch.py::WatchFetchError 同样的分工），本文件不
    重复实现重试逻辑。"""


def fetch_arxiv_entries(
    category: str, *, max_results: int = _DEFAULT_MAX_RESULTS, timeout: float = _DEFAULT_TIMEOUT,
) -> list[dict]:
    """调用 arXiv 官方 API，返回按提交时间倒序的
    [{"id","title","summary","link","published","authors"}, ...]。

    用标准库 xml.etree 解析 Atom 响应（不引入 feedparser 之类的第三方
    依赖），跟 `watch.py::fetch_rss()` 用同一套 xml.etree 惯用法。
    """
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - 环境应始终已安装
        raise ArxivFetchError("arxiv_api source 需要 requests 库") from exc

    try:
        resp = requests.get(
            _ARXIV_API_URL,
            params={
                "search_query": f"cat:{category}",
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": max_results,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
    except Exception as exc:
        raise ArxivFetchError(f"抓取 arXiv API 失败 (category={category!r}): {exc}") from exc

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        raise ArxivFetchError(f"arXiv Atom 响应解析失败: {exc}") from exc

    entries: list[dict] = []
    for entry in root.findall(f"{_ATOM_NS}entry"):
        entry_id = (entry.findtext(f"{_ATOM_NS}id") or "").strip()
        if not entry_id:
            continue
        title = " ".join((entry.findtext(f"{_ATOM_NS}title") or "").split())
        summary = " ".join((entry.findtext(f"{_ATOM_NS}summary") or "").split())
        published = (entry.findtext(f"{_ATOM_NS}published") or "").strip()
        authors = [
            (a.findtext(f"{_ATOM_NS}name") or "").strip()
            for a in entry.findall(f"{_ATOM_NS}author")
        ]
        entries.append({
            "id": entry_id,
            "title": title,
            "summary": summary,
            "link": entry_id,
            "published": published,
            "authors": [a for a in authors if a],
        })
    return entries


def _matches_keywords(title: str, keywords: Optional[list]) -> bool:
    if not keywords:
        return True
    title_lower = title.lower()
    return any(str(k).lower() in title_lower for k in keywords)


@register_source("arxiv_api")
class ArxivApiInputSource(ExternalInputSource):
    """追踪某个 arXiv 分类下的新论文（结构化 title+abstract，替代信息密度
    有限的 RSS 标题）。

    示例 `sources.yaml` 片段::

        sources:
          - id: arxiv_cs_ai_api
            type: arxiv_api
            interval_seconds: 21600   # 6 小时轮询一次即可，论文更新不需要
                                       # 高频轮询
            channel: agent_watch       # 复用 P1 抽取管道消费的频道
            params:
              category: cs.AI
              keywords: ["agent", "reasoning"]
              max_results: 20
    """

    source_type = "arxiv_api"

    def poll(
        self, params: dict, state: dict,
    ) -> tuple[list[ExternalInputEvent], dict]:
        category = params.get("category")
        if not category:
            raise ArxivFetchError("arxiv_api source 需要在 params 里配置 category")

        max_results = int(params.get("max_results", _DEFAULT_MAX_RESULTS))
        entries = fetch_arxiv_entries(str(category), max_results=max_results)

        seen_ids = set(state.get("seen_ids") or [])
        keywords = params.get("keywords")
        source_id = str(params.get("source_id", ""))

        events: list[ExternalInputEvent] = []
        newly_seen: list[str] = []
        for e in entries:
            if e["id"] in seen_ids:
                continue
            newly_seen.append(e["id"])
            if not _matches_keywords(e["title"], keywords):
                continue
            events.append(
                ExternalInputEvent(
                    id=e["id"],
                    source_id=source_id,
                    source_type=self.source_type,
                    signal="new_paper",
                    title=e["title"],
                    detail=e["summary"][:500],
                    url=e["link"],
                    fields={
                        "category": category,
                        "authors": e["authors"],
                        "published": e["published"],
                        "fetcher": "arxiv_api",
                    },
                    suggested_tier="tick",
                )
            )

        # 跟 watch.py::_poll_rss() 同款策略：只保留最近若干条，防止 state
        # 里的 seen_ids 无限增长——arXiv 分类下的论文数量本身有限且按时间
        # 排序，最近若干条已经足够覆盖去重需求。
        all_seen = list(seen_ids) + newly_seen
        new_state = dict(state)
        new_state["seen_ids"] = all_seen[-_DEFAULT_MAX_SEEN_IDS:]
        return events, new_state


__all__ = [
    "ArxivApiInputSource",
    "ArxivFetchError",
    "fetch_arxiv_entries",
]
