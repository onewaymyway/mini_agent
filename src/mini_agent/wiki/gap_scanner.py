"""
wiki/gap_scanner.py — wiki 知识缺口主动扫描
（next_doc/wiki_next_phase_improvement_plan.md 第 4.2.3 / 5 节）

被动提取（不管是 connective_density 还是 history/extraction_trigger.py 新增的
entity_density 触发器）永远受限于"对话里到底聊没聊到"。本模块提供的是反方向的
能力：定期扫描已有 wiki，找出"该补全但没人补全"的页面，交给调用方决定要不要
派发子任务主动补全——这是让 wiki 从"聊天记录的镜像"升级成"主动维护的知识库"
的关键一环。

本模块只做**规则扫描**，不调用 LLM、不派发任务：
    - shallow_entity：strong_links 数量 <= 1 的 entity 页面（几乎没有和其它
      页面建立关系，是"孤零零"的一句话描述）。
    - orphan_page：`wiki/validator.py` 已经实现的孤儿页面检测（无入边也无出边）。
    - stale_topic：改进计划第 2 节新增的缺口——topic 页面的 absorbs 链接指向的
      成员页面中，status != "active" 的比例超过阈值，说明这个专题页引用的内容
      已经大量作废，但专题页本身还没有被标注。

是否要基于扫描结果派发子任务（"读 xxx 补全该模块的依赖关系"这类），由调用方
（cli/commands/wiki.py 的 `/wiki gap-scan --dispatch` 或
evolution/cron_scheduler.py 的 `sys:wiki_gap_scan` job）决定：发现缺口和
处理缺口是两个职责，分开更容易独立测试、独立调整频率。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.graph import GraphIndex
from mini_agent.wiki.indexer import discover_pages
from mini_agent.wiki.parser import WikiPage, parse_page
from mini_agent.wiki.lifecycle import mark_page_state
from mini_agent.wiki.validator import validate_pages

# 陈旧专题页判定阈值：absorbs 链接指向的成员页面中，status != active 的比例。
_STALE_TOPIC_RATIO_THRESHOLD = 0.6
# entity 页面的强链接数 <= 此值视为"浅层实体"。
_SHALLOW_ENTITY_MAX_STRONG_LINKS = 1


@dataclass
class KnowledgeGap:
    page_id: str
    gap_kind: str  # "shallow_entity" | "orphan_page" | "stale_topic"
    suggested_action: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "page_id": self.page_id,
            "gap_kind": self.gap_kind,
            "suggested_action": self.suggested_action,
            "detail": self.detail,
        }


def _load_pages(paths: AgentPaths) -> list[WikiPage]:
    pages: list[WikiPage] = []
    for p in discover_pages(paths):
        try:
            pages.append(parse_page(p))
        except Exception:
            continue
    return pages


def _scan_shallow_entities(pages: list[WikiPage], graph: GraphIndex) -> list[KnowledgeGap]:
    gaps: list[KnowledgeGap] = []
    for page in pages:
        if page.type != "entity" or page.status != "active":
            continue
        strong_out = len([e for e in graph.outgoing(page.id) if e.strong])
        strong_in = len([e for e in graph.incoming(page.id) if e.strong])
        if strong_out + strong_in <= _SHALLOW_ENTITY_MAX_STRONG_LINKS:
            gaps.append(
                KnowledgeGap(
                    page_id=page.id,
                    gap_kind="shallow_entity",
                    suggested_action=(
                        f"补全实体「{page.id}」的背景与关系：读相关源码/文档，"
                        "确认它依赖/被依赖的模块，用 frontmatter.links 补上强关系"
                    ),
                    detail=f"strong_links={strong_out + strong_in}",
                )
            )
    return gaps


def _scan_orphan_pages(pages: list[WikiPage]) -> list[KnowledgeGap]:
    gaps: list[KnowledgeGap] = []
    report = validate_pages(pages)
    for issue in report.warnings:
        if issue.kind != "orphan_page":
            continue
        gaps.append(
            KnowledgeGap(
                page_id=issue.page_id,
                gap_kind="orphan_page",
                suggested_action=(
                    f"检查孤儿页面「{issue.page_id}」是否该和其它页面建立链接，"
                    "或者本身已经过时该标记 status=deprecated"
                ),
                detail=issue.detail,
            )
        )
    return gaps


def _scan_stale_topics(
    pages: list[WikiPage],
    *,
    ratio_threshold: float = _STALE_TOPIC_RATIO_THRESHOLD,
) -> list[KnowledgeGap]:
    pages_by_id = {p.id: p for p in pages}
    gaps: list[KnowledgeGap] = []
    for page in pages:
        if page.type != "topic" or page.status != "active":
            continue
        if str(page.raw_frontmatter.get("knowledge_state") or "fresh") == "stale":
            continue  # 已经标注过，避免每轮扫描都重复报告同一个缺口
        member_ids = [
            link.target for link in page.strong_links() if link.relation == "absorbs"
        ]
        if not member_ids:
            continue
        known_members = [pages_by_id[mid] for mid in member_ids if mid in pages_by_id]
        if not known_members:
            continue
        inactive = sum(1 for m in known_members if m.status != "active")
        ratio = inactive / len(known_members)
        if ratio >= ratio_threshold:
            gaps.append(
                KnowledgeGap(
                    page_id=page.id,
                    gap_kind="stale_topic",
                    suggested_action=(
                        f"专题页「{page.id}」引用的成员页面中 {inactive}/{len(known_members)} "
                        "已作废，建议标注 status=stale 并在正文提示可信度下降"
                    ),
                    detail=f"inactive_ratio={ratio:.2f}",
                )
            )
    return gaps


def scan_gaps(paths: AgentPaths, *, max_results: int = 5) -> list[KnowledgeGap]:
    """纯规则扫描，零 LLM 成本。返回按发现顺序（浅层实体 → 孤儿页面 → 陈旧专题页）
    截断到 `max_results` 条的缺口列表，避免单次扫描产出过多、把下游任务队列打满。

    任何环节失败都吞掉异常、返回已经扫描完成的部分结果——这是"锦上添花"的
    主动扫描，不应该因为某一类检测出错就让整次扫描失败。
    """
    try:
        pages = _load_pages(paths)
    except Exception:
        return []
    if not pages:
        return []

    try:
        graph = GraphIndex.build(pages)
    except Exception:
        graph = GraphIndex()

    gaps: list[KnowledgeGap] = []
    for scanner in (
        lambda: _scan_shallow_entities(pages, graph),
        lambda: _scan_orphan_pages(pages),
        lambda: _scan_stale_topics(pages),
    ):
        try:
            gaps.extend(scanner())
        except Exception:
            continue
        if len(gaps) >= max_results:
            break

    return gaps[:max_results]


def mark_stale_topics(paths: AgentPaths, gaps: list[KnowledgeGap]) -> int:
    """把 scan_gaps() 里 gap_kind=="stale_topic" 的候选真正写回 wiki。

    复用 O4 已经实现的统一知识生命周期状态机（wiki/lifecycle.py::
    mark_page_state，knowledge_state=fresh|stale|superseded），不新造一个
    专属状态字段——"专题页引用大量作废成员"本质上就是 O4 定义的
    "knowledge_state=stale"（内容还在但需要重新验证），语义上直接复用。

    这是 gap_scanner 里唯一会产生写副作用的函数，其余扫描逻辑都是纯读取——
    陈旧专题页标注是规则可以直接确定结论的场景（成员大量作废是客观事实），
    不需要像 shallow_entity/orphan_page 那样交给子任务去做需要判断力的补全，
    所以直接在扫描时顺手写回，不用等 --dispatch。

    返回实际标注成功的页面数，单条失败不影响其余条目。
    """
    if not gaps:
        return 0
    marked = 0
    for gap in gaps:
        if gap.gap_kind != "stale_topic":
            continue
        try:
            ok = mark_page_state(
                paths, gap.page_id,
                confidence="stale",
                reason=f"gap_scanner: {gap.detail}",
                validated_by="wiki_gap_scan",
            )
            if ok:
                marked += 1
        except Exception:
            continue
    return marked


__all__ = ["KnowledgeGap", "scan_gaps", "mark_stale_topics"]
