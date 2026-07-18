"""
wiki/world_writer.py — 世界模型候选（entities[]/facts[]）批量落盘

对应《wiki 式知识库改进计划》P1：与 wiki/decision_writer.py 同构——compact
阶段只把候选 append 到 pending JSONL 队列（queue_entities/queue_facts），
真正的判重/新建/合并延后到巩固循环调用 consolidate_pending() 批量执行，
避免逐条即时落盘导致 wiki/entities/ 碎片化。

判重复用已有的 wiki/dedup.py::find_similar_page（规则打分 + 可选 LLM 确认，
不依赖 embedding 时零额外调用），不重新实现一套相似度判断。

所有新建/追加的页面都打上 frontmatter.source_kind="world_model"，用于
wiki/stats.py 统计——这是验证“wiki 内容来源是否单一”的关键埋点。

fact 候选不单独建页面类型：优先合并进它关联的 entity 页面的“事实”
section；找不到关联实体时，归入当天的兜底页面
`entities/session-facts-<date>.md`，避免既不新建又无处安放。
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.indexer import discover_pages
from mini_agent.wiki.parser import WikiPage, parse_page
from mini_agent.wiki.writer import append_section, write_page

try:
    from mini_agent.history.world_extraction import EntityCandidate, FactCandidate
except ImportError:  # pragma: no cover - 允许独立单测本模块
    EntityCandidate = None  # type: ignore[assignment, misc]
    FactCandidate = None  # type: ignore[assignment, misc]

LLMCall = Callable[[str], str]
SOURCE_KIND = "world_model"
_FALLBACK_PAGE_PREFIX = "session-facts-"


@dataclass
class WorldWriteAction:
    """一次 consolidate_pending() 对单个候选采取的动作记录，供调用方日志/审计。"""

    kind: str  # "entity_created" | "entity_updated" | "fact_merged" | "fact_fallback" | "skipped"
    page_id: str
    detail: str = ""


@dataclass
class WorldWriteReport:
    actions: list[WorldWriteAction] = field(default_factory=list)


def _slugify(name: str, fallback: str = "entity") -> str:
    import re

    ascii_tokens = re.findall(r"[a-zA-Z0-9]+", name.lower())
    slug = "-".join(ascii_tokens)[:50].strip("-")
    if not slug:
        import hashlib
        slug = f"{fallback}-{hashlib.sha1(name.encode('utf-8')).hexdigest()[:8]}"
    return slug


def _pending_paths(paths: AgentPaths) -> tuple:
    return (paths.world_candidates_pending_path,)


def queue_entities(
    paths: AgentPaths,
    candidates: list["EntityCandidate"],
    *,
    source_entries: Optional[list[str]] = None,
) -> None:
    """compact 阶段调用：把实体候选原样 append 到 pending JSONL 队列。"""
    if not candidates:
        return
    source_entries = source_entries or []
    p = paths.world_candidates_pending_path
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for candidate in candidates:
            if not candidate.is_meaningful:
                continue
            row = {
                "kind": "entity",
                "candidate": candidate.to_dict(),
                "source_entries": source_entries,
                "queued_at": time.time(),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def queue_facts(
    paths: AgentPaths,
    candidates: list["FactCandidate"],
    *,
    source_entries: Optional[list[str]] = None,
) -> None:
    """compact 阶段调用：把事实候选原样 append 到 pending JSONL 队列。"""
    if not candidates:
        return
    source_entries = source_entries or []
    p = paths.world_candidates_pending_path
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for candidate in candidates:
            if not candidate.is_meaningful:
                continue
            row = {
                "kind": "fact",
                "candidate": candidate.to_dict(),
                "source_entries": source_entries,
                "queued_at": time.time(),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_pending_queue(paths: AgentPaths) -> list[dict]:
    p = paths.world_candidates_pending_path
    if not p.exists():
        return []
    rows: list[dict] = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return rows


def _clear_pending_queue(paths: AgentPaths) -> None:
    p = paths.world_candidates_pending_path
    if not p.exists():
        return
    tmp = p.with_suffix(".tmp")
    try:
        tmp.write_text("", encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _load_entity_pages(paths: AgentPaths) -> list[WikiPage]:
    pages: list[WikiPage] = []
    for md_path in discover_pages(paths):
        try:
            page = parse_page(md_path)
        except Exception:
            continue
        if page.type == "entity":
            pages.append(page)
    return pages


def _write_or_merge_entity(
    paths: AgentPaths,
    candidate: "EntityCandidate",
    *,
    source_entries: list[str],
    existing_pages: list[WikiPage],
    llm_call: Optional[LLMCall],
) -> WorldWriteAction:
    from mini_agent.wiki.dedup import find_similar_page

    text = f"{candidate.name}\n{candidate.description}"
    match = find_similar_page(text, [candidate.entity_type], existing_pages, llm_call=llm_call)
    if match is not None:
        matched_page = next((p for p in existing_pages if p.id == match.page_id), None)
        if matched_page is not None:
            append_section(
                paths, matched_page,
                heading="新增认知",
                content=candidate.description,
            )
            return WorldWriteAction("entity_updated", matched_page.id, candidate.name)

    page_id = _slugify(candidate.name)
    body = (
        f"## 概述\n\n{candidate.name}，类型：{candidate.entity_type}。\n\n"
        f"## 当前状态\n\n{candidate.description}\n"
    )
    write_page(
        paths,
        page_id=page_id,
        page_type="entity",
        body=body,
        tags=[candidate.entity_type],
        status="active",
        confidence=0.5,
        source_entries=source_entries,
        extra_frontmatter={"source_kind": SOURCE_KIND},
        overwrite=True,
    )
    new_page = parse_page(paths.wiki_entities_dir / f"{page_id}.md")
    existing_pages.append(new_page)
    return WorldWriteAction("entity_created", page_id, candidate.name)


def _merge_fact(
    paths: AgentPaths,
    candidate: "FactCandidate",
    *,
    existing_pages: list[WikiPage],
) -> WorldWriteAction:
    target_page: Optional[WikiPage] = None
    if candidate.related_entities:
        related_slugs = {_slugify(e) for e in candidate.related_entities}
        for p in existing_pages:
            if p.id in related_slugs or p.id.lower() in {e.lower() for e in candidate.related_entities}:
                target_page = p
                break

    if target_page is not None:
        append_section(
            paths, target_page,
            heading="事实",
            content=f"（confidence={candidate.confidence}）{candidate.statement}",
        )
        return WorldWriteAction("fact_merged", target_page.id, candidate.statement)

    # 找不到关联实体：归入当天的兜底页面
    fallback_id = f"{_FALLBACK_PAGE_PREFIX}{date.today().isoformat()}"
    fallback_path = paths.wiki_entities_dir / f"{fallback_id}.md"
    if fallback_path.exists():
        fallback_page = parse_page(fallback_path)
        append_section(
            paths, fallback_page,
            heading="事实",
            content=f"（confidence={candidate.confidence}）{candidate.statement}",
        )
    else:
        write_page(
            paths,
            page_id=fallback_id,
            page_type="entity",
            body=(
                "## 概述\n\n本页汇总当天巩固循环中，未能关联到已有实体页面"
                "的孤立事实候选（wiki 改进计划 P1 兜底策略）。\n\n"
                f"## 事实\n\n（confidence={candidate.confidence}）{candidate.statement}\n"
            ),
            tags=["session-facts"],
            status="active",
            confidence=0.4,
            extra_frontmatter={"source_kind": SOURCE_KIND},
            overwrite=False,
        )
        existing_pages.append(parse_page(fallback_path))
    return WorldWriteAction("fact_fallback", fallback_id, candidate.statement)


def consolidate_pending(
    paths: AgentPaths,
    llm_call: Optional[LLMCall] = None,
) -> WorldWriteReport:
    """巩固循环批量消费入口：读取 pending 队列 → 实体判重合并/新建 →
    事实合并进关联实体或兜底页面 → 清空 pending 队列。

    任何异常都不应该向上抛出中断整个巩固循环——调用方
    （perception/library_index.py::consolidate）已用 try/except 包裹。
    """
    rows = _read_pending_queue(paths)
    if not rows:
        return WorldWriteReport()

    report = WorldWriteReport()
    existing_pages = _load_entity_pages(paths)

    entity_rows = [r for r in rows if r.get("kind") == "entity"]
    fact_rows = [r for r in rows if r.get("kind") == "fact"]

    for row in entity_rows:
        try:
            candidate = EntityCandidate.from_dict(row["candidate"])
            if not candidate.is_meaningful:
                continue
            source_entries = row.get("source_entries") or []
            action = _write_or_merge_entity(
                paths, candidate,
                source_entries=source_entries,
                existing_pages=existing_pages,
                llm_call=llm_call,
            )
            report.actions.append(action)
        except Exception:
            continue

    for row in fact_rows:
        try:
            candidate = FactCandidate.from_dict(row["candidate"])
            if not candidate.is_meaningful:
                continue
            action = _merge_fact(paths, candidate, existing_pages=existing_pages)
            report.actions.append(action)
        except Exception:
            continue

    _clear_pending_queue(paths)
    return report


__all__ = [
    "WorldWriteAction",
    "WorldWriteReport",
    "queue_entities",
    "queue_facts",
    "consolidate_pending",
    "SOURCE_KIND",
]
