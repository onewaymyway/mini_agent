"""
wiki/topics.py — 专题页生成（重构计划阶段四第一条）

巩固循环里判断：如果某个 tag 下聚集的页面数达到阈值，且这些页面之间的
frontmatter 强链接密度较高，说明它们描述的是同一次跨模块的大动作（比如
判断/调度系统的整合），值得生成一篇综合叙事的专题页，解决重构计划问题4
"没有可读的综合层"——一次跨多个模块的大重构，此前没有任何地方能承载
"这件事的完整来龙去脉"，只能靠人去几个实体的 summary 里拼凑。

触发进 LibraryIndex.consolidate() 步骤 7，只在传入 llm_call 时生效：专题页
正文由 LLM 综合改写生成，规则打分只负责"值不值得生成"这一步的判断，不负责
生成内容本身（这一点与 wiki/dedup.py 的"规则粗筛 + LLM 兜底确认"思路一致，
LLM 只在真正需要语言能力的地方被调用）。

同一个 tag 不会被反复触发生成——已生成过的专题页会在 frontmatter 里记录
`source_tag`，下次扫描据此排除，避免每次巩固循环都对同一批页面重新生成一遍
（如果这批页面又新增了成员，未来可以扩展成"更新既有专题页"而不是本模块当前
支持的"只生成新专题页"，暂不在阶段四范围内）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.graph import GraphIndex
from mini_agent.wiki.indexer import discover_pages
from mini_agent.wiki.parser import WikiPage, parse_page
from mini_agent.wiki.writer import WikiLink, write_page

LLMCall = Callable[[str], str]

_MIN_PAGES_PER_TAG = 4     # tag 下页面数达到此阈值才考虑生成专题页
_MIN_LINK_DENSITY = 0.5    # 组内强链接边数 / 页面数 达到此阈值才算"密度较高"
_BODY_CHARS_FOR_PROMPT = 1500


@dataclass
class TopicCandidate:
    tag: str
    page_ids: list[str] = field(default_factory=list)
    link_density: float = 0.0


def _existing_topic_source_tags(pages: list[WikiPage]) -> set[str]:
    """已经生成过专题页的 tag 集合，来自既有 topics/*.md 的 source_tag 字段。"""
    tags: set[str] = set()
    for p in pages:
        if p.type != "topic":
            continue
        src = p.raw_frontmatter.get("source_tag")
        if src:
            tags.add(str(src))
    return tags


def find_topic_candidates(
    pages: list[WikiPage],
    *,
    min_pages: int = _MIN_PAGES_PER_TAG,
    min_density: float = _MIN_LINK_DENSITY,
    exclude_tags: Optional[set[str]] = None,
) -> list[TopicCandidate]:
    """扫描全部页面按 tag 分组，找出页面数与组内强链接密度都达标的候选。

    密度定义：组内页面之间的 frontmatter 强链接边数（只统计 target 也在
    同一组内的边）/ 组内页面数——用"平均每篇页面对组内其它成员有多少条
    强关系"衡量这批页面是否真的紧密关联，而不只是恰好共享同一个 tag。
    """
    exclude_tags = exclude_tags or set()
    graph = GraphIndex.build(pages)

    by_tag: dict[str, list[str]] = {}
    for p in pages:
        if p.type == "topic":
            continue
        for tag in p.tags:
            by_tag.setdefault(tag, []).append(p.id)

    out: list[TopicCandidate] = []
    for tag, ids in by_tag.items():
        if tag in exclude_tags or len(ids) < min_pages:
            continue
        id_set = set(ids)
        edge_count = 0
        for pid in ids:
            for e in graph.outgoing(pid):
                if e.strong and e.target in id_set:
                    edge_count += 1
        density = edge_count / len(ids) if ids else 0.0
        if density >= min_density:
            out.append(TopicCandidate(tag=tag, page_ids=sorted(ids), link_density=density))

    out.sort(key=lambda c: -c.link_density)
    return out


def generate_topic_page(
    candidate: TopicCandidate,
    pages_by_id: dict[str, WikiPage],
    paths: AgentPaths,
    llm_call: LLMCall,
) -> Optional[str]:
    """对一个候选 tag 调 LLM 综合生成一篇 topics/*.md，返回新页面 id。

    LLM 调用或写盘失败均返回 None——专题页生成是巩固循环里"锦上添花"的
    一步，调用方（consolidate_topics）应当继续处理下一个候选而不是中断。
    """
    member_pages = [pages_by_id[pid] for pid in candidate.page_ids if pid in pages_by_id]
    if not member_pages:
        return None

    numbered = "\n\n".join(
        f"[{p.id}] (type={p.type}, status={p.status})\n{p.body[:_BODY_CHARS_FOR_PROMPT]}"
        for p in member_pages
    )
    prompt = (
        f"以下是 tag「{candidate.tag}」下一组相互关联的 wiki 页面正文"
        "（实体/决策/流程/经验型页面混合）。请把它们综合成一篇专题页叙事，"
        "讲清楚这件事完整的来龙去脉（起因、关键决策、当前状态），"
        "让读者一次通读就能理解全貌，而不是自己去几篇原文里拼凑。"
        "直接输出正文 markdown 本身（不要 frontmatter，不要逐篇复述原文，"
        "要真正综合改写）。\n\n"
        f"{numbered}"
    )
    try:
        body = llm_call(prompt)
    except Exception:
        return None
    if not body or not body.strip():
        return None

    page_id = f"topic-{candidate.tag}"
    links = [
        WikiLink(target=pid, relation="absorbs", source="frontmatter")
        for pid in candidate.page_ids
    ]
    try:
        write_page(
            paths,
            page_id=page_id,
            page_type="topic",
            body=body.strip(),
            tags=[candidate.tag],
            confidence=0.5,
            links=links,
            source_entries=[],
            extra_frontmatter={"source_tag": candidate.tag},
        )
    except Exception:
        return None
    return page_id


def consolidate_topics(
    paths: AgentPaths,
    llm_call: Optional[LLMCall],
    *,
    min_pages: int = _MIN_PAGES_PER_TAG,
    min_density: float = _MIN_LINK_DENSITY,
) -> list[str]:
    """consolidate() 步骤 7 的入口：扫描候选、逐个生成专题页。

    返回本次新生成的 page_id 列表。没有 llm_call（专题页正文依赖 LLM 综合
    改写，规则本身只判断"值不值得生成"）或没有 wiki 页面时直接返回空列表。
    """
    if llm_call is None or not paths.wiki_dir.exists():
        return []

    md_paths = discover_pages(paths)
    if not md_paths:
        return []

    pages: list[WikiPage] = []
    for md_path in md_paths:
        try:
            pages.append(parse_page(md_path))
        except Exception:
            continue
    if not pages:
        return []

    exclude = _existing_topic_source_tags(pages)
    candidates = find_topic_candidates(
        pages, min_pages=min_pages, min_density=min_density, exclude_tags=exclude
    )
    if not candidates:
        return []

    pages_by_id = {p.id: p for p in pages}
    created: list[str] = []
    for cand in candidates:
        page_id = generate_topic_page(cand, pages_by_id, paths, llm_call)
        if page_id:
            created.append(page_id)
    return created
