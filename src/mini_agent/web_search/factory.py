"""
web_search/factory.py — 搜索后端工厂

根据 WebSearchConfig.provider 创建对应的 WebSearchProvider 实例。
切换方式（优先级：函数参数 > WebSearchConfig.provider > 环境变量 WEB_SEARCH_PROVIDER > 默认 "duckduckgo"）：

  # 1. 代码中切换
  cfg.web_search.provider = "brave"

  # 2. 环境变量切换（无需改代码/改配置文件）
  export WEB_SEARCH_PROVIDER=serper
  export SERPER_API_KEY=...

  # 3. 调用时临时指定（例如工具函数按参数切换）
  provider = create_web_search_provider(cfg, provider="tavily")

注册自定义后端：
  from mini_agent.web_search.factory import register_web_search_provider
  register_web_search_provider("my_engine", MyProviderClass)
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Callable, Optional, Type

from mini_agent.web_search.base import WebSearchProvider

if TYPE_CHECKING:
    from mini_agent.config import AppConfig


def _load_duckduckgo() -> Type[WebSearchProvider]:
    from mini_agent.web_search.providers.duckduckgo import DuckDuckGoProvider
    return DuckDuckGoProvider


def _load_brave() -> Type[WebSearchProvider]:
    from mini_agent.web_search.providers.brave import BraveSearchProvider
    return BraveSearchProvider


def _load_serper() -> Type[WebSearchProvider]:
    from mini_agent.web_search.providers.serper import SerperProvider
    return SerperProvider


def _load_tavily() -> Type[WebSearchProvider]:
    from mini_agent.web_search.providers.tavily import TavilyProvider
    return TavilyProvider


# key: WebSearchConfig.provider 字段值（小写）
# value: () -> Type[WebSearchProvider]（延迟导入，避免不必要的依赖加载）
_REGISTRY: dict[str, Callable[[], Type[WebSearchProvider]]] = {
    "duckduckgo": _load_duckduckgo,
    "ddg": _load_duckduckgo,
    "brave": _load_brave,
    "serper": _load_serper,
    "tavily": _load_tavily,
}


def create_web_search_provider(
    cfg: "AppConfig",
    provider: Optional[str] = None,
) -> WebSearchProvider:
    """
    创建搜索 provider 实例。

    Args:
        cfg:      AppConfig（含 web_search 子配置块）
        provider: 显式指定 provider 名称，覆盖 cfg.web_search.provider /
                  环境变量。用于"一次性切换"场景（例如工具调用参数）。
    """
    name = (
        provider
        or getattr(cfg.web_search, "provider", None)
        or os.environ.get("WEB_SEARCH_PROVIDER")
        or "duckduckgo"
    )
    key = name.lower().strip()

    loader = _REGISTRY.get(key)
    if loader is None:
        available = sorted(_REGISTRY)
        raise ValueError(
            f"Unknown web search provider: {name!r}.\n"
            f"Available: {available}\n"
            f"Register a custom provider via register_web_search_provider()."
        )

    cls = loader()
    return cls(cfg)


def register_web_search_provider(name: str, cls: Type[WebSearchProvider]) -> None:
    """动态注册自定义搜索后端。cls 必须是 WebSearchProvider 的子类。"""
    _REGISTRY[name.lower()] = lambda: cls


def list_web_search_providers() -> list[str]:
    return sorted(_REGISTRY)
