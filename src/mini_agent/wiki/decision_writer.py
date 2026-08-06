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

批量节流新建（避免碎片化）：
  compact 阶段不再直接调用 process_candidates()，而是调用本模块的
  queue_candidates() 把候选原样 append 到一个 pending JSONL 队列文件
  （AgentPaths.decision_candidates_pending_path）。真正的落盘延后到
  巩固循环（evolution/consolidation.py::run_consolidation）里调用本模块的
  consolidate_pending()：批量读取 pending 队列 → 按 topic/related_entities
  合并同批次里指向同一件事的多条候选（只留最新一条 chosen）→ 对合并结果
  逐条调用 process_candidates() 的核心匹配/落盘逻辑（未变）→ 清空 pending
  队列。"新建"这个动作额外套 evolution/consolidation.py 的
  rhythm_is_allowed()/record_proposal() 冷却治理（key 为 topic 的 slug），
  避免同一个决定短时间内被反复提炼出候选而多次新建页面；"更新"不受此限制。
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.indexer import discover_pages
from mini_agent.wiki.parser import WikiPage, parse_page
from mini_agent.wiki.writer import render_page, set_status, write_page
from mini_agent.wiki.parser import WikiLink
from mini_agent.utils.atomic_write import atomic_write_text

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
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.wiki.decision_writer._load_decision_pages')
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
        extra_frontmatter={"source_kind": "decision"},
    )
    return parse_page(target_path)


_CORE_FRONTMATTER_KEYS = {
    "id", "type", "tags", "status", "confidence", "created", "updated",
    "links", "source_entries",
}


def _update_existing(paths: AgentPaths, page: WikiPage, *, source_entries: list[str]) -> None:
    merged_sources = sorted(set(page.source_entries) | set(source_entries))
    # 保留原有的非核心 frontmatter 字段（尤其是 source_kind），否则每次
    # "更新"分支都会悄悄抹掉页面创建时打上的来源标记——wiki/stats.py 的
    # 统计口径依赖这个字段在整个页面生命周期里保持稳定。
    extra = {k: v for k, v in page.raw_frontmatter.items() if k not in _CORE_FRONTMATTER_KEYS}
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
        extra_frontmatter=extra,
        overwrite=True,
    )


def _link_back_superseded_by(paths: AgentPaths, old_page: WikiPage, new_page_id: str) -> None:
    """给旧页面追加一条 superseded_by -> new_page_id 的反向链接，保持双向可追溯。"""
    new_links = [*old_page.strong_links(), WikiLink(target=new_page_id, relation="superseded_by", source="frontmatter")]
    extra = {k: v for k, v in old_page.raw_frontmatter.items() if k not in _CORE_FRONTMATTER_KEYS}
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
        extra_frontmatter=extra,
    )
    atomic_write_text(old_page.path, text)


def process_candidates(
    paths: AgentPaths,
    candidates: list["DecisionCandidate"],
    *,
    source_entries: Optional[list[str]] = None,
    rhythm_check: Optional["Callable[[str], bool]"] = None,
    rhythm_record: Optional["Callable[[str], None]"] = None,
) -> DecisionWriteReport:
    """处理一批决策候选，返回本次采取的动作列表。

    Args:
        paths: 项目 AgentPaths（用于定位 wiki/decisions/ 目录）。
        candidates: DecisionCandidate 列表（通常来自一次 compact 的结构化输出，
            或 consolidate_pending() 合并批次后的候选）。
        source_entries: 本次触发提取的来源标识（如 compact 的 turn 范围 id），
            写入决策页 frontmatter.source_entries，供追溯。
        rhythm_check: 可选。传入时，每次即将"新建"决策页前调用
            rhythm_check(topic_slug)，返回 False 表示该 topic 处于冷却期，
            本次跳过新建（记为 skipped 动作），不影响 update 分支。
            用于 consolidate_pending() 接入 evolution/consolidation.py 的
            节奏治理；直接调用本函数（不传该参数）时行为与之前完全一致。
        rhythm_record: 可选，与 rhythm_check 配套，新建成功后记录一次提案时间。
    """
    report = DecisionWriteReport()
    source_entries = source_entries or []
    if not candidates:
        return report

    decision_pages = _load_decision_pages(paths)

    def _gate_new(topic: str) -> bool:
        """新建前的节奏门控：无 rhythm_check 时不限制（保持旧行为）。"""
        if rhythm_check is None:
            return True
        return rhythm_check(_slugify(topic))

    def _record_new(topic: str) -> None:
        if rhythm_record is not None:
            rhythm_record(_slugify(topic))

    for candidate in candidates:
        if not candidate.is_meaningful:
            report.actions.append(DecisionWriteAction("skipped", "", "候选缺少 topic/chosen"))
            continue

        matched = _find_matching_decision(candidate, decision_pages)

        if matched is None:
            if not _gate_new(candidate.topic):
                report.actions.append(
                    DecisionWriteAction("skipped", "", f"新建冷却期内，跳过：{candidate.topic}")
                )
                continue
            new_page = _create_decision_page(paths, candidate, source_entries=source_entries)
            decision_pages.append(new_page)
            _record_new(candidate.topic)
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
        if not _gate_new(candidate.topic):
            report.actions.append(
                DecisionWriteAction(
                    "skipped", matched.id,
                    f"推翻新建冷却期内，跳过：{candidate.topic}",
                )
            )
            continue
        new_page = _create_decision_page(
            paths, candidate, source_entries=source_entries, supersedes=matched
        )
        _link_back_superseded_by(paths, matched, new_page.id)
        decision_pages.append(new_page)
        _record_new(candidate.topic)
        report.actions.append(
            DecisionWriteAction(
                "overturned_and_created", new_page.id,
                f"取代 {matched.id}（旧方案不再是: {candidate.chosen}）",
            )
        )

    return report


# ════════════════════════════════════════════════════════════════════════════
# 批量节流新建：pending 队列（compact 时写入）+ consolidate_pending（巩固循环时消费）
# ════════════════════════════════════════════════════════════════════════════

def queue_candidates(
    paths: AgentPaths,
    candidates: list["DecisionCandidate"],
    *,
    source_entries: Optional[list[str]] = None,
) -> None:
    """compact 阶段调用：把决策候选原样 append 到 pending JSONL 队列，不做任何
    匹配/落盘。轻量、只追加，失败也不应影响 compact 主流程（调用方已有 try/except）。
    """
    if not candidates:
        return
    source_entries = source_entries or []
    p = paths.decision_candidates_pending_path
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for candidate in candidates:
            if not candidate.is_meaningful:
                continue
            row = {
                "candidate": candidate.to_dict(),
                "source_entries": source_entries,
                "queued_at": time.time(),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_pending_queue(paths: AgentPaths) -> list[dict]:
    p = paths.decision_candidates_pending_path
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
                    log_exception(_mini_agent_exc, where='mini_agent.wiki.decision_writer._read_pending_queue')
                    continue  # 单行损坏跳过，不影响其它行
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.wiki.decision_writer._read_pending_queue')
        return []
    return rows


def _clear_pending_queue(paths: AgentPaths) -> None:
    """原子清空 pending 队列（consolidate_pending 消费完成后调用）。"""
    p = paths.decision_candidates_pending_path
    if not p.exists():
        return
    atomic_write_text(p, "")


def _merge_same_batch_candidates(
    rows: list[dict],
) -> list[tuple["DecisionCandidate", list[str]]]:
    """批内合并：同一批 pending 队列里，topic slug 相同或 related_entities 有交集
    的多条候选视为指向同一件事，只保留 queued_at 最新的一条 chosen 作为代表，
    source_entries 取并集。这是解决"逐条即时落盘"碎片化的核心一步——同一个
    决定在短时间内被多次 compact 提炼出候选时，不应该新建好几条决策页。

    分组用简单并查集：按 topic slug 分桶，再看 related_entities 是否与已有桶
    有交集，有则合并入该桶。启发式实现，宁可少合并（多新建几条，靠 rhythm
    冷却兜底）也不要把明显不相关的候选误合并。
    """
    from mini_agent.history.decision_extraction import DecisionCandidate

    groups: list[dict] = []  # 每个 group: {"slugs": set, "entities": set, "items": [(candidate, source_entries, queued_at)]}

    for row in rows:
        try:
            candidate = DecisionCandidate.from_dict(row["candidate"])
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.wiki.decision_writer._merge_same_batch_candidates')
            continue
        if not candidate.is_meaningful:
            continue
        slug = _slugify(candidate.topic)
        entities = set(candidate.related_entities)
        source_entries = row.get("source_entries") or []
        queued_at = row.get("queued_at", 0.0)

        target = None
        for g in groups:
            if slug in g["slugs"] or (entities and entities & g["entities"]):
                target = g
                break
        if target is None:
            target = {"slugs": set(), "entities": set(), "items": []}
            groups.append(target)
        target["slugs"].add(slug)
        target["entities"] |= entities
        target["items"].append((candidate, source_entries, queued_at))

    merged: list[tuple["DecisionCandidate", list[str]]] = []
    for g in groups:
        items = sorted(g["items"], key=lambda t: t[2])  # 按 queued_at 升序，最后一个最新
        representative = items[-1][0]
        all_sources: list[str] = []
        for _, sources, _ in items:
            for s in sources:
                if s not in all_sources:
                    all_sources.append(s)
        merged.append((representative, all_sources))

    return merged


def consolidate_pending(
    paths: AgentPaths,
    *,
    min_new_interval_days: float = 1.0,
) -> DecisionWriteReport:
    """巩固循环批量消费入口（对齐 perception/library_index.py::consolidate 的
    命名风格）。由 evolution/consolidation.py::run_consolidation 调用。

    流程：读取 pending 队列 → 批内合并同一件事的多条候选 → 对合并结果调用
    process_candidates()（core 匹配/落盘逻辑不变，"新建"动作套节奏治理冷却）
    → 清空 pending 队列。

    任何异常都不应该向上抛出中断整个巩固循环——调用方（run_consolidation）
    已经用 try/except 包裹每一步，这里保持一致的防御性风格。
    """
    rows = _read_pending_queue(paths)
    if not rows:
        return DecisionWriteReport()

    merged = _merge_same_batch_candidates(rows)
    if not merged:
        _clear_pending_queue(paths)
        return DecisionWriteReport()

    # 延迟导入，避免与 evolution/consolidation.py 的模块级循环依赖
    # （consolidation.py 会在其 run_consolidation 里导入本模块）。
    from mini_agent.evolution.consolidation import rhythm_is_allowed, record_proposal

    report = DecisionWriteReport()
    for candidate, source_entries in merged:
        sub_report = process_candidates(
            paths,
            [candidate],
            source_entries=source_entries,
            rhythm_check=lambda slug: rhythm_is_allowed(
                paths, "decision_new", slug, min_new_interval_days
            ),
            rhythm_record=lambda slug: record_proposal(paths, "decision_new", slug),
        )
        report.actions.extend(sub_report.actions)

    _clear_pending_queue(paths)
    return report


__all__ = [
    "DecisionWriteAction",
    "DecisionWriteReport",
    "process_candidates",
    "queue_candidates",
    "consolidate_pending",
]
