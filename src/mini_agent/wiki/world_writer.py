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
import re
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
# 外部数据知识化改进计划 P1/P3：external_input/knowledge_extractor.py 消费
# agent_watch 频道事件产出的候选统一打这个 source_kind，与对话来源的
# "world_model" 区分开，供 wiki/stats.py 统计外部世界知识占比。
EXTERNAL_WATCH_SOURCE_KIND = "external_watch"
# P3（主动检索反哺 wiki）：external_input/tech_radar_search.py 消费
# sys:tech_radar_search 定期检索结果时使用，与 EXTERNAL_WATCH_SOURCE_KIND
# 区分（被动订阅 vs 主动检索），供 wiki/stats.py 分别统计。
EXTERNAL_SEARCH_SOURCE_KIND = "external_search"
_FALLBACK_PAGE_PREFIX = "session-facts-"
# wiki 提取层与组织层改进计划 E3 §3.4：reused_existing_id 命中后的最低校验
# 分数，低于此分数视为模型误判，忽略并退回规则判重流程（阈值取自计划原文
# "分数过低（比如 <0.15）"的建议值）。
_REUSED_ID_MIN_SCORE = 0.15


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
    source_kind: str = SOURCE_KIND,
) -> None:
    """compact 阶段调用：把实体候选原样 append 到 pending JSONL 队列。

    source_kind 默认沿用对话来源的 "world_model"；外部知识改进计划 P1 起，
    `external_input/knowledge_extractor.py` 等非对话来源的调用方会显式传
    "external_watch"/"external_search"，供 consolidate_pending() 落盘时
    原样打进 frontmatter（供 wiki/stats.py 统计各来源占比），不影响判重/
    合并逻辑本身。
    """
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
                "source_kind": source_kind,
                "queued_at": time.time(),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def queue_facts(
    paths: AgentPaths,
    candidates: list["FactCandidate"],
    *,
    source_entries: Optional[list[str]] = None,
    source_kind: str = SOURCE_KIND,
) -> None:
    """compact 阶段调用：把事实候选原样 append 到 pending JSONL 队列。

    source_kind 语义同 queue_entities()。
    """
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
                "source_kind": source_kind,
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
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.wiki.world_writer._read_pending_queue')
                    continue
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.wiki.world_writer._read_pending_queue')
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
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.wiki.world_writer._clear_pending_queue')
        try:
            tmp.unlink(missing_ok=True)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.wiki.world_writer._clear_pending_queue')
            pass


def _load_entity_pages(paths: AgentPaths) -> list[WikiPage]:
    pages: list[WikiPage] = []
    for md_path in discover_pages(paths):
        try:
            page = parse_page(md_path)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.wiki.world_writer._load_entity_pages')
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
    source_kind: str = SOURCE_KIND,
) -> WorldWriteAction:
    from mini_agent.wiki.dedup import find_similar_page, score_similarity

    text = f"{candidate.name}\n{candidate.description}"

    # wiki 提取层与组织层改进计划 E3 §3.2.2/§3.4：模型自报的
    # reused_existing_id 优先信任，但仍用 score_similarity 校验一次分数，
    # 分数过低（模型"过度复用"误判）则忽略，退回下面的规则判重流程——
    # "模型优先判断 + 规则兜底"的两段式，不是纯规则判重，也不是无条件
    # 信任模型判断。
    matched_page: Optional[WikiPage] = None
    match_label = candidate.name
    if candidate.reused_existing_id:
        reused_page = next(
            (p for p in existing_pages if p.id == candidate.reused_existing_id), None
        )
        if reused_page is not None:
            score = score_similarity(text, [candidate.entity_type], reused_page)
            if score >= _REUSED_ID_MIN_SCORE:
                matched_page = reused_page
                match_label = f"reused_existing_id={candidate.reused_existing_id} score={score:.2f}"

    if matched_page is None:
        match = find_similar_page(text, [candidate.entity_type], existing_pages, llm_call=llm_call)
        if match is not None:
            matched_page = next((p for p in existing_pages if p.id == match.page_id), None)

    if matched_page is not None:
        append_section(
            paths, matched_page,
            heading="新增认知",
            content=candidate.description,
        )
        return WorldWriteAction("entity_updated", matched_page.id, match_label)

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
        extra_frontmatter={"source_kind": source_kind},
        overwrite=True,
    )
    new_page = parse_page(paths.wiki_entities_dir / f"{page_id}.md")
    existing_pages.append(new_page)
    return WorldWriteAction("entity_created", page_id, candidate.name)


_FACT_ANCHOR_RE = re.compile(r"<!--\s*fact_id:\s*[\w\-]+#fact-(\d+)")


def _next_fact_anchor(body: str, page_id: str) -> str:
    """生成本页下一个 fact 锚点 id（wiki 提取层与组织层改进计划 O4 §7.2.3）：
    `<page_id>#fact-<n>`，n 取正文中已有锚点注释的最大序号 + 1。不为每条
    fact 单独开物理页面，只在正文里用一行 HTML 注释标记锚点 + 状态，供
    `wiki/lifecycle.py::mark_page_state(..., anchor=...)` 定位并原地更新。
    """
    nums = [int(m.group(1)) for m in _FACT_ANCHOR_RE.finditer(body)]
    n = (max(nums) + 1) if nums else 1
    return f"{page_id}#fact-{n}"


def _fact_content_with_anchor(page_id: str, page_body: str, candidate: "FactCandidate") -> str:
    anchor = _next_fact_anchor(page_body, page_id)
    return (
        f"<!-- fact_id: {anchor}; knowledge_state: fresh -->\n"
        f"（confidence={candidate.confidence}）{candidate.statement}"
    )


def _merge_fact(
    paths: AgentPaths,
    candidate: "FactCandidate",
    *,
    existing_pages: list[WikiPage],
    source_kind: str = SOURCE_KIND,
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
            content=_fact_content_with_anchor(target_page.id, target_page.body, candidate),
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
            extra_frontmatter={"source_kind": source_kind},
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
            row_source_kind = str(row.get("source_kind") or SOURCE_KIND)
            action = _write_or_merge_entity(
                paths, candidate,
                source_entries=source_entries,
                existing_pages=existing_pages,
                llm_call=llm_call,
                source_kind=row_source_kind,
            )
            report.actions.append(action)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.wiki.world_writer.consolidate_pending')
            continue

    for row in fact_rows:
        try:
            candidate = FactCandidate.from_dict(row["candidate"])
            if not candidate.is_meaningful:
                continue
            row_source_kind = str(row.get("source_kind") or SOURCE_KIND)
            action = _merge_fact(
                paths, candidate,
                existing_pages=existing_pages,
                source_kind=row_source_kind,
            )
            report.actions.append(action)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.wiki.world_writer.consolidate_pending')
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
    "EXTERNAL_WATCH_SOURCE_KIND",
    "EXTERNAL_SEARCH_SOURCE_KIND",
]
