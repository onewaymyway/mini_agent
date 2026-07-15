"""
wiki/decision_writer.py — 决策候选落盘（决策/取舍知识提炼计划 5.3 节）

输入是 history/decision_extraction.py 解析出的 DecisionCandidate 列表，输出是
对 wiki/decisions/*.md 的三种可能操作：

    1. 命中已有决策页且 chosen 一致  → 只更新 source_entries / updated，不新建
    2. 命中已有决策页但 chosen 不一致 → 旧页面 status 改 overturned，新建一条
       决策页并用 supersedes / superseded_by 双向 links 串联沿革链条
    3. 未命中任何已有决策页            → 新建一条候选决策页（status=settled）

“命中”的判定：candidate.related_entities 中的某个 id，与某个既有 decision 页面
frontmatter.links 里 relation 为 affects/part_of 的 target 重合。这一跳直接复用
parser.py 已经解析好的 WikiPage.strong_links()，不需要额外的实体侧索引。

本模块不负责“要不要新建”的节流（计划里说的“巩固循环批量决定是否新建，避免
碎片化”）——那是调度层面的策略，本模块只提供 process_candidates() 作为可以被
巩固循环包一层节流逻辑再调用的基础动作，保持职责单一。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.indexer import discover_pages
from mini_agent.wiki.parser import WikiPage, parse_page
from mini_agent.wiki.writer import render_page, set_status, write_page
from mini_agent.wiki import writer as _writer_mod
from mini_agent.wiki.parser import WikiLink

try:
    from mini_agent.history.decision_extraction import DecisionCandidate
except ImportError:  # pragma: no cover - 允许独立单测本模块
    DecisionCandidate = None  # type: ignore[assignment, misc]

_MATCH_RELATIONS = ("affects", "part_of")
_DECISION_CONFIDENCE = 0.5  # 决策复盘类知识固定低于规则触发(0.6)与人类纠正(0.7)


@dataclass
class DecisionWriteAction:
    """一次 process_candidates() 对单个候选采取的动作记录，供调用方日志/审计。"""

    kind: str  # "updated" | "overturned_and_created" | "created" | "skipped"
    page_id: str
    detail: str = ""


@dataclass
class DecisionWriteReport:
    actions: list[DecisionWriteAction] = field(default_factory=list)


def _slugify(text: str, fallback: str = "decision") -> str:
    # 中文主题没有天然的 ascii slug，退化为拼接一个简短 hash 后缀保证唯一性;
    # 英文/数字词照常提取。
    ascii_tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    slug = "-".join(ascii_tokens)[:50].strip("-")
    if not slug:
        import hashlib
        slug = f"{fallback}-{hashlib.sha1(text.encode('utf-8')).hexdigest()[:8]}"
    return slug


def _load_decision_pages(paths: AgentPaths) -> list[WikiPage]:
    pages: list[WikiPage] = []
    decisions_dir = paths.wiki_decisions_dir
    if not decisions_dir.exists():
        return pages
    for md_path in sorted(decisions_dir.glob("*.md")):
        try:
            pages.append(parse_page(md_path))
        except Exception:
            continue
    return pages


def _find_matching_decision(
    candidate: "DecisionCandidate", decision_pages: list[WikiPage]
) -> Optional[WikiPage]:
    if not candidate.related_entities:
        return None
    related = set(candidate.related_entities)
    best: Optional[WikiPage] = None
    for page in decision_pages:
        if page.status == "overturned":
            continue  # 已被推翻的旧页面不再作为匹配目标，应匹配到它的替代页
        targets = {l.target for l in page.strong_links() if l.relation in _MATCH_RELATIONS}
        if targets & related:
            best = page
            break
    return best


def _render_decision_body(candidate: "DecisionCandidate") -> str:
    options_lines = "\n".join(f"- {opt}" for opt in candidate.options_considered) or "- （未记录）"
    rejected_lines = "\n".join(
        f"- **{opt}**：{reason}" for opt, reason in candidate.rejected_because.items()
    ) or "- （未记录）"
    return (
        f"## 问题\n\n{candidate.topic}\n\n"
        f"## 考虑过的方案\n\n{options_lines}\n\n"
        f"## 采纳理由\n\n最终选择：**{candidate.chosen}**\n\n"
        f"## 被否决的方案及原因\n\n{rejected_lines}\n\n"
        f"## 如果要推翻这个决定\n\n（从 compact 摘要自动提炼，尚未人工补充推翻条件。）\n"
    )


def _create_decision_page(
    paths: AgentPaths,
    candidate: "DecisionCandidate",
    *,
    source_entries: list[str],
    supersedes: Optional[WikiPage] = None,
) -> WikiPage:
    page_id = _slugify(candidate.topic)
    target_path = paths.wiki_type_dir("decision") / f"{page_id}.md"
    suffix = 2
    base_id = page_id
    while target_path.exists():
        page_id = f"{base_id}-{suffix}"
        target_path = paths.wiki_type_dir("decision") / f"{page_id}.md"
        suffix += 1

    links = [
        WikiLink(target=eid, relation="affects", source="frontmatter")
        for eid in candidate.related_entities
    ]
    if supersedes is not None:
        links.append(WikiLink(target=supersedes.id, relation="supersedes", source="frontmatter"))

    write_page(
        paths,
        page_id=page_id,
        page_type="decision",
        body=_render_decision_body(candidate),
        tags=[],
        status="settled",
        confidence=_DECISION_CONFIDENCE,
        links=links,
        source_entries=source_entries,
    )
    return parse_page(target_path)


def _update_existing(paths: AgentPaths, page: WikiPage, *, source_entries: list[str]) -> None:
    merged_sources = sorted(set(page.source_entries) | set(source_entries))
    write_page(
        paths,
        page_id=page.id,
        page_type=page.type,
        body=page.body,
        tags=page.tags,
        status=page.status,
        confidence=page.confidence if page.confidence is not None else _DECISION_CONFIDENCE,
        created=page.created,
        updated=date.today().isoformat(),
        links=page.strong_links(),
        source_entries=merged_sources,
        overwrite=True,
    )


def _link_back_superseded_by(paths: AgentPaths, old_page: WikiPage, new_page_id: str) -> None:
    """给旧页面追加一条 superseded_by -> new_page_id 的反向链接，保持双向可追溯。"""
    new_links = [*old_page.strong_links(), WikiLink(target=new_page_id, relation="superseded_by", source="frontmatter")]
    text = render_page(
        page_id=old_page.id,
        page_type=old_page.type,
        body=old_page.body,
        tags=old_page.tags,
        status="overturned",
        confidence=old_page.confidence,
        created=old_page.created,
        updated=date.today().isoformat(),
        links=new_links,
        source_entries=old_page.source_entries,
    )
    _writer_mod._atomic_write_text(old_page.path, text)  # noqa: SLF001 - writer 内部原子写复用


def process_candidates(
    paths: AgentPaths,
    candidates: list["DecisionCandidate"],
    *,
    source_entries: Optional[list[str]] = None,
) -> DecisionWriteReport:
    """处理一批决策候选，返回本次采取的动作列表。

    Args:
        paths: 项目 AgentPaths（用于定位 wiki/decisions/ 目录）。
        candidates: DecisionCandidate 列表（通常来自一次 compact 的结构化输出）。
        source_entries: 本次触发提取的来源标识（如 compact 的 turn 范围 id），
            写入决策页 frontmatter.source_entries，供追溯。
    """
    report = DecisionWriteReport()
    source_entries = source_entries or []
    if not candidates:
        return report

    decision_pages = _load_decision_pages(paths)

    for candidate in candidates:
        if not candidate.is_meaningful:
            report.actions.append(DecisionWriteAction("skipped", "", "候选缺少 topic/chosen"))
            continue

        matched = _find_matching_decision(candidate, decision_pages)

        if matched is None:
            new_page = _create_decision_page(paths, candidate, source_entries=source_entries)
            decision_pages.append(new_page)
            report.actions.append(DecisionWriteAction("created", new_page.id, candidate.topic))
            continue

        # 一致性判断：粗略字符串比较（大小写/首尾空白不敏感）。这是启发式判断，
        # 不追求语义级别的精确匹配——宁可漏判为"不一致"触发一次新建，也不要
        # 把明显不同的方案误判为同一个决定。
        same_choice = matched.body and candidate.chosen.strip().lower() in matched.body.lower()
        if same_choice:
            _update_existing(paths, matched, source_entries=source_entries)
            report.actions.append(DecisionWriteAction("updated", matched.id, candidate.chosen))
            continue

        # chosen 不一致 → 旧决定被推翻：新建替代页，双向 supersedes/superseded_by
        new_page = _create_decision_page(
            paths, candidate, source_entries=source_entries, supersedes=matched
        )
        _link_back_superseded_by(paths, matched, new_page.id)
        decision_pages.append(new_page)
        report.actions.append(
            DecisionWriteAction(
                "overturned_and_created", new_page.id,
                f"取代 {matched.id}（旧方案不再是: {candidate.chosen}）",
            )
        )

    return report


__all__ = ["DecisionWriteAction", "DecisionWriteReport", "process_candidates"]
