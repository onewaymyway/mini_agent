"""
wiki/entity_digest.py — 实体索引摘要生成器
（wiki 提取层与组织层改进计划 E3 §3.2.1）

把组织层沉淀出的已有实体索引，反向注入提取层的抽取 prompt，让模型在识别
新实体前先看一眼"已经有哪些"，减少同一实体被反复以不同措辞重新识别、
产出重复候选的问题（计划 E3 §3.1 现状分析）。

只产出一份极简摘要（id + entity_type + 一句话描述），不返回原始正文，
控制 prompt token 开销；数量上限 max_entities，排序依据（计划 §3.2.1）：
    1. relevance_hint 命中（与当前 workdir/对话相关的实体优先）
    2. grounded_hit_count 高的实体优先（O1 §4.2.2 沉淀的信度字段，见
       wiki/writer.py::increment_grounded_hit_count）
    3. 最近更新（frontmatter.updated）的实体优先

实现说明：只扫描 `entities/` 目录（page_type=entity），不是全库扫描——
entity 页面规模天然远小于全库，直接 parse_page 成本可控，本身不构成
search.py/dedup.py 面对的那种全库扫描性能问题，因此不需要依赖 O1 的
`_index/` 派生索引来提速；本模块真正复用 O1 的产出是 grounded_hit_count
这个字段本身。计划原文区分"过渡版"（O1 落地前，无相关性排序）与"完整版"
（依赖 O1 排序）两阶段，本次 O1 已完成，直接实现排序完整版，不再分阶段。

失败处理：单页面解析失败静默跳过；整体异常（entities 目录不存在等）
返回空字符串，调用方据此不注入任何实体索引段落，等同于本次改动前的行为
（计划 §3.4 风险与兜底）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.parser import WikiPage, parse_page

_ENTITY_TYPE_LABELS = {
    "module": "模块",
    "tool": "工具",
    "concept": "概念",
    "person": "人物",
    "project": "项目",
    "external_system": "外部系统",
}

_OVERVIEW_HEADINGS = ("## 概述", "## overview")
_DEFAULT_MAX_ENTITIES = 40
_DEFAULT_SENTENCE_MAX_CHARS = 60


@dataclass
class _RankedEntity:
    page: WikiPage
    relevance_hit: bool
    grounded_hit_count: int


def _truncate_sentence(text: str, max_chars: int = _DEFAULT_SENTENCE_MAX_CHARS) -> str:
    for sep in ("。", "\n"):
        idx = text.find(sep)
        if 0 < idx <= max_chars:
            return text[: idx + (1 if sep == "。" else 0)].strip()
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "…"
    return text.strip()


def _first_overview_sentence(body: str) -> str:
    """取正文"## 概述"小节的首个非空行；找不到该小节时退回正文首个非空行。"""
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower() in _OVERVIEW_HEADINGS:
            for follow in lines[i + 1 :]:
                follow = follow.strip()
                if follow and not follow.startswith("#"):
                    return _truncate_sentence(follow)
            break
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return _truncate_sentence(stripped)
    return ""


def _load_entity_pages(paths: AgentPaths) -> list[WikiPage]:
    pages: list[WikiPage] = []
    if not paths.wiki_entities_dir.exists():
        return pages
    for md_path in sorted(paths.wiki_entities_dir.glob("*.md")):
        try:
            pages.append(parse_page(md_path))
        except Exception:
            continue
    return pages


def _grounded_hit_count(page: WikiPage) -> int:
    try:
        return int(page.raw_frontmatter.get("grounded_hit_count") or 0)
    except (TypeError, ValueError):
        return 0


def _is_relevant(page: WikiPage, relevance_hint: Optional[str]) -> bool:
    if not relevance_hint:
        return False
    hint = relevance_hint.lower()
    haystack = f"{page.id} {' '.join(page.tags)}".lower()
    return hint in haystack or any(part in haystack for part in hint.split("/") if len(part) > 1)


def _rank_entities(pages: list[WikiPage], relevance_hint: Optional[str]) -> list[_RankedEntity]:
    ranked = [
        _RankedEntity(
            page=p,
            relevance_hit=_is_relevant(p, relevance_hint),
            grounded_hit_count=_grounded_hit_count(p),
        )
        for p in pages
    ]
    ranked.sort(
        key=lambda r: (
            r.relevance_hit,
            math.log1p(r.grounded_hit_count),
            r.page.updated or "",
        ),
        reverse=True,
    )
    return ranked


def build_entity_digest(
    paths: AgentPaths,
    *,
    max_entities: int = _DEFAULT_MAX_ENTITIES,
    relevance_hint: Optional[str] = None,
) -> str:
    """生成一份用于注入抽取 prompt 的极简实体索引文本。

    每行格式：`- <id>（<entity_type 中文标签>）：<一句话描述>`。
    没有任何实体页面、或读取失败时返回空字符串——调用方（history/
    compression.py::LLMSummaryStrategy）应据此不注入任何实体索引段落，
    等同于本次改动前的行为，不阻断抽取主流程（计划 §3.4）。
    """
    try:
        pages = _load_entity_pages(paths)
    except Exception:
        return ""
    if not pages:
        return ""

    ranked = _rank_entities(pages, relevance_hint)[: max(0, max_entities)]
    if not ranked:
        return ""

    lines: list[str] = []
    for item in ranked:
        page = item.page
        label = _ENTITY_TYPE_LABELS.get(page.tags[0] if page.tags else "", "")
        # entity_type 目前没有独立 frontmatter 字段，写入时约定第一个 tag
        # 就是 entity_type（见 wiki/world_writer.py::_write_or_merge_entity
        # 的 tags=[candidate.entity_type]），digest 复用这一约定取中文标签；
        # 取不到时留空，不影响该条目本身的展示。
        desc = _first_overview_sentence(page.body)
        type_part = f"（{label}）" if label else ""
        if desc:
            lines.append(f"- {page.id}{type_part}：{desc}")
        else:
            lines.append(f"- {page.id}{type_part}")

    return "\n".join(lines)


def build_entity_digest_section(
    paths: AgentPaths,
    *,
    max_entities: int = _DEFAULT_MAX_ENTITIES,
    relevance_hint: Optional[str] = None,
) -> str:
    """`build_entity_digest()` 的 prompt-ready 包装：带上说明性表头，直接
    可以填进 `{{ entity_digest_section }}` 占位符（计划 §3.2.2）。

    没有已知实体时返回空字符串——`prompts/manager.py::_render_template`
    对空字符串变量的处理是整段替换为空，等同于完全不注入这一段，不会在
    prompt 里留下孤零零的表头。
    """
    digest = build_entity_digest(paths, max_entities=max_entities, relevance_hint=relevance_hint)
    if not digest:
        return ""
    return (
        "\nAlready-known entities (if a newly identified entity refers to the "
        "same thing as one of the items below, reuse its id via "
        "`reused_existing_id` instead of creating a near-duplicate):\n" + digest
    )


__all__ = ["build_entity_digest", "build_entity_digest_section"]
