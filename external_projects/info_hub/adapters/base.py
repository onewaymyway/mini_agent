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


# 站点的安全验证页（WAF / 反爬挑战）特征：返回的是 HTML + 脚本，而不是请求的内容
BLOCK_MARKERS: tuple[tuple[str, str], ...] = (
    ("__waf_verification_challenge", "waf_challenge"),
    ("cf-chl-", "cloudflare_challenge"),
)
BLOCK_TEXT = {
    "waf_challenge": "站点安全验证（WAF 挑战页）",
    "cloudflare_challenge": "Cloudflare 安全验证页",
}


def detect_block(raw: Optional[bytes]) -> Optional[str]:
    """返回拦截类型（见 BLOCK_TEXT），未识别为拦截页时返回 None。只检查开头一段，避免大文件开销。"""
    if not raw:
        return None
    head = raw[:20000].decode("utf-8", errors="replace")
    for marker, kind in BLOCK_MARKERS:
        if marker in head:
            return kind
    return None


RAW_DEBUG_LIMIT = 100_000  # 失败时保留的原文上限（字符），超过则截断并标记


def failure_debug(raw: Optional[bytes], exc: Optional[BaseException] = None) -> dict[str, Any]:
    """抓取失败时的调试信息：错误类型与信息、原文开头与截断后的全文、XML 解析错误位置附近的原文。

    原文按 UTF-8 解码（非法字节替换为 �）。XML 错误位置按原始字节计算，与 expat 的列号一致。
    """
    import xml.etree.ElementTree as ET

    data = raw or b""
    text = data.decode("utf-8", errors="replace")
    dbg: dict[str, Any] = {
        "error_type": type(exc).__name__ if exc else "",
        "error": str(exc) if exc else "",
        "raw_length": len(data),
        "raw_head": text[:1500],
        "raw_text": text[:RAW_DEBUG_LIMIT],
        "raw_truncated": len(text) > RAW_DEBUG_LIMIT,
    }
    if isinstance(exc, ET.ParseError) and data:
        try:
            line, col = exc.position
        except (AttributeError, TypeError, ValueError):
            line, col = 0, 0
        lines = data.split(b"\n")
        if 1 <= line <= len(lines):
            target = lines[line - 1]
            start = max(0, col - 1 - 120)
            end = min(len(target), col - 1 + 80)
            snippet = target[start:end].decode("utf-8", errors="replace")
            pointer = len(target[start:col - 1].decode("utf-8", errors="replace"))
            dbg["parse_error_at"] = {"line": line, "column": col}
            dbg["context"] = (f"第 {line} 行第 {col} 列附近（[此处] 为出错位置）：\n"
                              + snippet[:pointer] + "[此处]" + snippet[pointer:])
            dbg["context_lines"] = [
                f"{n}: {lines[n - 1].decode('utf-8', errors='replace')[:300]}"
                for n in range(max(1, line - 2), min(len(lines), line) + 1)
            ]
    return dbg


def default_http_get(url: str, headers: Optional[dict[str, str]] = None, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL 来自用户配置
        return resp.read()


@dataclass
class FetchContext:
    """适配器运行时依赖。browser 为共享浏览器会话（BrowserSession 或兼容对象）。"""
    http_get: Callable[..., bytes] = default_http_get
    browser: Optional[Any] = None
    sites: dict[str, dict[str, Any]] = field(default_factory=dict)


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
