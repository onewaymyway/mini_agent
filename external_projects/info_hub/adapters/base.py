"""适配器基类与通用数据结构。

约定：
- 适配器只负责"从外部取数据并解析为 RawItem 列表"，不负责写盘、去重或报告。
- 写入与整理由 core/ 统一完成，保证适配器出错不会破坏已有数据。
- HTTP 请求通过 FetchContext.http_get 注入，便于测试替换。
"""
from __future__ import annotations

import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

DEFAULT_UA = "Mozilla/5.0 (info_hub; personal reader)"


@dataclass
class SourceSpec:
    """单个信息源的声明（来自 sources/<id>/source.yaml）。"""
    id: str
    name: str
    category: str
    kind: str
    adapter: str
    enabled: bool
    request: dict[str, Any]
    parse: dict[str, Any]
    policy: dict[str, Any]
    schedule: dict[str, Any]
    browser: dict[str, Any]
    tags: list[str]
    dir: Path
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RawItem:
    """适配器产出的原始条目，尚未归一化。"""
    title: str
    url: str = ""
    rank: Optional[int] = None
    heat: Optional[str] = None
    summary: str = ""
    published_at: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchResult:
    items: list[RawItem]
    error: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None


def default_http_get(url: str, headers: Optional[dict[str, str]] = None, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL 来自用户配置
        return resp.read()


@dataclass
class FetchContext:
    http_get: Callable[..., bytes] = default_http_get


class Adapter(ABC):
    """所有适配器的基类。子类定义 kind_name 后会被 registry 自动发现。"""

    kind_name: str = ""
    requires_browser: bool = False

    @abstractmethod
    def validate(self, spec: SourceSpec) -> list[str]:
        """校验与本适配器相关的配置，返回错误信息列表（空表示通过）。"""

    @abstractmethod
    def fetch(self, spec: SourceSpec, ctx: FetchContext) -> FetchResult:
        """执行抓取，返回 FetchResult。不应抛出网络异常，应转为 error 字段。"""

    @staticmethod
    def max_items(spec: SourceSpec, default: int = 50) -> int:
        try:
            return int(spec.policy.get("max_items", default))
        except (TypeError, ValueError):
            return default
