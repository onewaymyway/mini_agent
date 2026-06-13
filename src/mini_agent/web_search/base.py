"""
web_search/base.py — 网络搜索后端抽象接口

所有搜索后端必须实现 WebSearchProvider，返回统一的 SearchResult 列表。
Agent / tools/builtin.py 只依赖此接口，与具体搜索 API 完全解耦。

接入新后端步骤：
  1. 在 web_search/providers/ 下新建模块，继承 WebSearchProvider，实现 search()
  2. 在 web_search/factory.py 的 _REGISTRY 中注册 (name -> 构造函数)
  3. 通过 WebSearchConfig.provider = "<name>" 或环境变量 WEB_SEARCH_PROVIDER 切换

内置实现：
  duckduckgo — 免费，无需 API key（默认，HTML 抓取）
  brave      — Brave Search API，需要 BRAVE_API_KEY（有免费额度）
  serper     — Serper.dev（Google 结果代理），需要 SERPER_API_KEY
  tavily     — Tavily AI 搜索 API，需要 TAVILY_API_KEY（有免费额度）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.config import AppConfig


@dataclass
class SearchResult:
    """单条搜索结果。"""
    title: str
    url: str
    snippet: str = ""

    def format(self, index: int) -> str:
        snippet = self.snippet.strip()
        lines = [f"{index}. {self.title or '(no title)'}", f"   {self.url}"]
        if snippet:
            lines.append(f"   {snippet}")
        return "\n".join(lines)


class WebSearchError(RuntimeError):
    """搜索失败（网络错误、API 报错、缺少 key 等）。"""


class WebSearchProvider(ABC):
    """网络搜索后端统一接口。"""

    #: 子类覆盖：该 provider 是否需要 API key
    requires_api_key: bool = False
    #: 子类覆盖：需要的环境变量名（requires_api_key=True 时使用）
    api_key_env: str = ""

    def __init__(self, cfg: "AppConfig") -> None:
        self.cfg = cfg

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """
        执行搜索，返回结果列表（最多 max_results 条）。

        失败时应抛出 WebSearchError，由调用方统一格式化为提示文本，
        而不是返回空列表（便于区分"无结果"和"出错"）。
        """
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__

    def format_results(self, query: str, results: list[SearchResult]) -> str:
        """将结果列表格式化为模型可读的文本。"""
        if not results:
            return f"[web_search via {self.name}] No results found for: {query!r}"
        body = "\n".join(r.format(i + 1) for i, r in enumerate(results))
        return f"[web_search via {self.name}] Results for: {query!r}\n\n{body}"
