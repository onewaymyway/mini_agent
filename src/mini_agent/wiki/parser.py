"""
wiki/parser.py — 解析单个 wiki 页面

页面格式（重构计划 5.2 节）：

    ---
    id: role-agent-dispatcher
    type: entity
    tags: [judge-system, dispatcher, phase6]
    status: active
    confidence: 0.8
    created: 2026-06-01
    updated: 2026-07-10
    links:
      - target: turn-judge
        relation: absorbs
        note: "Phase6b将TurnJudge的职责迁移至此"
    source_entries: [entry_a1b2, entry_c3d4]
    ---

    正文...包含 [[some-page-id]] 弱引用...

frontmatter 的 `links` 字段是结构化强关系；正文内 `[[page-id]]` 是自然行文
中的弱引用，解析时自动记为 `relation: mentions`。两者在 WikiPage.links 中
合并为统一的 WikiLink 列表，通过 `source` 字段区分（"frontmatter" |
"body"），供 graph.py 区分强弱关系时使用。

本模块只做解析，不做校验（校验逻辑在 validator.py），也不做磁盘遍历
（遍历逻辑在 indexer.py）——保持单一职责，方便独立测试。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:  # pragma: no cover - 环境缺少 pyyaml 时的兜底提示
    yaml = None  # type: ignore[assignment]

PAGE_TYPES = ("entity", "decision", "process", "experience", "topic")
# 通用 status 词表 + 决策页专用的 settled/overturned（决策/取舍知识提炼计划 5.1 节）。
# decision 页面的生命周期语义是 settled -> revisited -> overturned，与 entity/topic
# 等页面沿用的 active/deprecated/superseded/revisited 共享同一个 frontmatter 字段，
# 因此在这里合并成一个词表，而不是按 type 拆分校验（parser 不感知业务语义，只做
# 结构校验；status 的业务含义由各 type 自行解释）。
STATUS_VALUES = (
    "active", "deprecated", "superseded", "revisited",
    "settled", "overturned",
)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?\n)---\s*\n?", re.DOTALL)
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


class PageParseError(ValueError):
    """页面解析失败（frontmatter 缺失/格式错误/必填字段缺失）。"""


@dataclass
class WikiLink:
    """一条页面间关系（强关系来自 frontmatter.links，弱关系来自正文 [[..]]）。"""

    target: str
    relation: str = "mentions"
    note: str = ""
    source: str = "body"  # "frontmatter" | "body"


@dataclass
class WikiPage:
    """一个 wiki 页面的解析结果。"""

    id: str
    type: str
    path: Path
    tags: list[str] = field(default_factory=list)
    status: str = "active"
    confidence: Optional[float] = None
    created: str = ""
    updated: str = ""
    source_entries: list[str] = field(default_factory=list)
    links: list[WikiLink] = field(default_factory=list)
    body: str = ""
    raw_frontmatter: dict = field(default_factory=dict)

    def strong_links(self) -> list[WikiLink]:
        return [l for l in self.links if l.source == "frontmatter"]

    def weak_links(self) -> list[WikiLink]:
        return [l for l in self.links if l.source == "body"]


def _require_yaml() -> None:
    if yaml is None:
        raise PageParseError(
            "解析 wiki 页面需要 pyyaml，请先安装：pip install pyyaml"
        )


def extract_body_links(body: str) -> list[WikiLink]:
    """从正文中提取 [[page-id]] / [[page-id|显示文本]] / [[page-id#anchor]] 弱引用。"""
    seen: list[str] = []
    links: list[WikiLink] = []
    for m in _WIKILINK_RE.finditer(body):
        target = m.group(1).strip()
        if not target or target in seen:
            continue
        seen.append(target)
        links.append(WikiLink(target=target, relation="mentions", source="body"))
    return links


def _parse_frontmatter_links(raw_links: object) -> list[WikiLink]:
    if not raw_links:
        return []
    if not isinstance(raw_links, list):
        raise PageParseError(f"frontmatter.links 必须是列表，实际是 {type(raw_links)!r}")
    out: list[WikiLink] = []
    for i, item in enumerate(raw_links):
        if not isinstance(item, dict) or "target" not in item:
            raise PageParseError(f"frontmatter.links[{i}] 缺少 target 字段: {item!r}")
        out.append(
            WikiLink(
                target=str(item["target"]),
                relation=str(item.get("relation", "relates_to")),
                note=str(item.get("note", "")),
                source="frontmatter",
            )
        )
    return out


def parse_page(path: Path, *, text: Optional[str] = None) -> WikiPage:
    """解析单个 md 文件为 WikiPage。

    Args:
        path: 页面文件路径（用于报错定位，以及在未显式传 text 时读取内容）。
        text: 可选，直接传入文件内容（用于测试/避免重复 IO）。
    """
    _require_yaml()
    raw = text if text is not None else path.read_text(encoding="utf-8")

    m = _FRONTMATTER_RE.match(raw)
    if not m:
        raise PageParseError(f"{path}: 缺少 frontmatter（必须以 --- 开头并有配对的 --- 结尾）")

    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception as exc:  # noqa: BLE001 - 统一包装成 PageParseError
        raise PageParseError(f"{path}: frontmatter YAML 解析失败: {exc}") from exc

    if not isinstance(fm, dict):
        raise PageParseError(f"{path}: frontmatter 顶层必须是映射（dict）")

    for required in ("id", "type"):
        if not fm.get(required):
            raise PageParseError(f"{path}: frontmatter 缺少必填字段 {required!r}")

    page_type = str(fm["type"])
    if page_type not in PAGE_TYPES:
        raise PageParseError(
            f"{path}: type={page_type!r} 不合法，可选: {PAGE_TYPES}"
        )

    status = str(fm.get("status", "active"))
    if status not in STATUS_VALUES:
        raise PageParseError(f"{path}: status={status!r} 不合法，可选: {STATUS_VALUES}")

    body = raw[m.end():]

    tags = fm.get("tags") or []
    if not isinstance(tags, list):
        raise PageParseError(f"{path}: frontmatter.tags 必须是列表")

    source_entries = fm.get("source_entries") or []
    if not isinstance(source_entries, list):
        raise PageParseError(f"{path}: frontmatter.source_entries 必须是列表")

    strong_links = _parse_frontmatter_links(fm.get("links"))
    weak_links = extract_body_links(body)
    # 若同一 target 既有强关系又被正文提及，保留强关系、丢弃重复的弱引用，
    # 避免图上出现同目标的两条边造成计数失真。
    strong_targets = {l.target for l in strong_links}
    weak_links = [l for l in weak_links if l.target not in strong_targets]

    confidence = fm.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError) as exc:
            raise PageParseError(f"{path}: confidence 必须是数字") from exc

    return WikiPage(
        id=str(fm["id"]),
        type=page_type,
        path=path,
        tags=[str(t) for t in tags],
        status=status,
        confidence=confidence,
        created=str(fm.get("created", "")),
        updated=str(fm.get("updated", "")),
        source_entries=[str(s) for s in source_entries],
        links=[*strong_links, *weak_links],
        body=body,
        raw_frontmatter=fm,
    )
