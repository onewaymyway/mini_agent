"""research/search.py — 搜索后端与查询缓存（方案 §7 第 3 步、Q10）。

* MiniAgentSearch：包装 mini_agent 的 web_search provider（默认 duckduckgo，无需 key）。
  mini_agent 不可用或搜索失败时抛出 SearchError，调研会记录原因并降级为仅用手动资料。
* cached_search：同一查询在 TTL 内只请求一次；失败不缓存。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from typing import Any, List, Optional

from .. import store
from ..paths import mini_agent_root

CACHE_TTL_SEC = 30 * 24 * 3600


class SearchError(Exception):
    """搜索失败（网络、后端不可用、缺少 key 等）。"""


@dataclass
class Hit:
    title: str
    url: str
    snippet: str = ""


class MiniAgentSearch:
    """复用 mini_agent.web_search 的 provider；惰性创建，失败统一转为 SearchError。"""

    def __init__(self, provider: str = "duckduckgo", project_root: Optional[Any] = None):
        self.provider_name = provider or "duckduckgo"
        self._root = project_root
        self._provider = None

    def _get(self):
        if self._provider is None:
            try:
                from mini_agent.config import load_config
                from mini_agent.web_search.factory import create_web_search_provider

                cfg = load_config(project_root=self._root or mini_agent_root(), verbose=False,
                                  sandbox=True, auto_approve=True)
                self._provider = create_web_search_provider(cfg, provider=self.provider_name)
            except Exception as exc:  # noqa: BLE001 — 原因展示给用户
                raise SearchError(f"搜索后端不可用：{type(exc).__name__}: {str(exc)[:160]}") from exc
        return self._provider

    def search(self, query: str, max_results: int) -> List[Hit]:
        try:
            rows = self._get().search(query, max_results=max_results)
        except SearchError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SearchError(f"搜索失败：{type(exc).__name__}: {str(exc)[:160]}") from exc
        return [Hit(title=str(r.title or ""), url=str(r.url or ""), snippet=str(r.snippet or "")) for r in rows]


def _key(query: str, max_results: int) -> str:
    return hashlib.sha256(f"{query}|{max_results}".encode("utf-8")).hexdigest()[:24]


def cached_search(ctx, backend, query: str, max_results: int) -> List[Hit]:
    p = ctx.path("research", "cache", "search", _key(query, max_results) + ".json")
    now = time.time()
    try:
        data = store.read_json(p, default=None)
    except ValueError:
        data = None
    if isinstance(data, dict) and now - float(data.get("ts", 0)) < CACHE_TTL_SEC:
        return [Hit(**h) for h in data.get("hits", []) if isinstance(h, dict)]
    hits = backend.search(query, max_results)            # SearchError 直接抛出，不写缓存
    store.write_json_atomic(p, {"ts": now, "query": query, "hits": [asdict(h) for h in hits]})
    return hits
