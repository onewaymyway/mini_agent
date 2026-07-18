"""
wiki/topics.py — 专题页生成（重构计划阶段四第一条 + wiki 改进计划 P3）

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

[wiki 改进计划 P3] 语义聚类候选（find_semantic_topic_candidates）：
    上面这条 tag+链接密度路径只对"决策沿革链"这类本来就强关联的场景友好——
    P1 新增的 world_model 实体/事实内容彼此之间未必共享 tag、也未必有强链接，
    但仍然可能是同一个主题下的一组相关知识（比如同一个项目下的多条零散
    事实）。这里增加一条基于 embedding 余弦相似度的连通分量聚类作为补充
    候选源，与 tag+密度路径并存、合并后一起生成，互不替代：
      - 只有调用方显式传入 embed_call 才会启用（复用 wiki/dedup.py 的
        "两条路径互斥，由调用方决定，不做隐式降级"原则）。
      - 聚类算法：对每一对页面计算余弦相似度 >= 阈值则连一条边，用并查集
        取连通分量，分量大小达标才算候选（简单的单链接聚类，足够应付
        "论文引用图"体量下的 wiki 页面数，不追求聚类算法的精细度）。
      - 候选打上 `source_tag=f"semantic-{代表 tag 或 hash}"`，与 tag 路径
        共用同一套"已生成过就排除"机制，不会重复生成。
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
EmbedCall = Callable[[str], list[float]]

_MIN_PAGES_PER_TAG = 4     # tag 下页面数达到此阈值才考虑生成专题页
_MIN_LINK_DENSITY = 0.5    # 组内强链接边数 / 页面数 达到此阈值才算"密度较高"
_BODY_CHARS_FOR_PROMPT = 1500

# [P3] 语义聚类参数：分量大小阈值与两两页面的相似度门槛。语义聚类天然比
# tag+密度路径更容易"误聚"，门槛设得比 wiki/dedup.py 的合并阈值（0.86）
# 略低一点点也没关系——这里只是"值不值得生成一篇综合页"的判断，即便聚类
# 不完全精确，生成出来的专题页最多是"话题略宽泛"，不像 dedup 误判合并那样
# 会真的丢失信息。
_MIN_PAGES_PER_SEMANTIC_CLUSTER = 4
_SEMANTIC_SIMILARITY_THRESHOLD = 0.80


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


class _UnionFind:
    """极简并查集，只用于语义聚类的连通分量计算，不追求通用性。"""

    def __init__(self, ids: list[str]) -> None:
        self._parent = {i: i for i in ids}

    def find(self, x: str) -> str:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def find_semantic_topic_candidates(
    pages: list[WikiPage],
    embed_call: EmbedCall,
    *,
    min_pages: int = _MIN_PAGES_PER_SEMANTIC_CLUSTER,
    similarity_threshold: float = _SEMANTIC_SIMILARITY_THRESHOLD,
    exclude_page_ids: Optional[set[str]] = None,
) -> list[TopicCandidate]:
    """[wiki 改进计划 P3] 基于 embedding 余弦相似度的连通分量聚类，作为
    tag+链接密度路径的补充候选源——覆盖那些彼此没有强链接、也未必共享 tag，
    但语义上属于同一主题的页面（典型场景：P1 世界模型抽取产出的零散
    entity/fact 页面）。

    exclude_page_ids：已经被某篇专题页 absorbs 过的页面 id，聚类时跳过，
    避免同一批页面反复被不同专题页收编。

    embedding/相似度计算任何一步失败都不应该中断整个巩固循环——单个页面
    embed 失败时跳过该页面（复用 wiki/dedup.py::embed_pages 的容错逻辑），
    整体异常时返回空列表。
    """
    exclude_page_ids = exclude_page_ids or set()
    candidate_pages = [p for p in pages if p.type != "topic" and p.id not in exclude_page_ids]
    if len(candidate_pages) < min_pages:
        return []

    try:
        from mini_agent.wiki.dedup import embed_pages
        from mini_agent.perception.local_embedding import cosine_similarity

        embeddings = embed_pages(candidate_pages, embed_call)
    except Exception:
        return []

    ids = list(embeddings.keys())
    if len(ids) < min_pages:
        return []

    uf = _UnionFind(ids)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            try:
                score = cosine_similarity(embeddings[ids[i]], embeddings[ids[j]])
            except Exception:
                continue
            if score >= similarity_threshold:
                uf.union(ids[i], ids[j])

    clusters: dict[str, list[str]] = {}
    for pid in ids:
        clusters.setdefault(uf.find(pid), []).append(pid)

    out: list[TopicCandidate] = []
    for root, members in clusters.items():
        if len(members) < min_pages:
            continue
        # 用聚类内出现频率最高的 tag（如果有）做候选标签，方便人工浏览时
        # 大致知道这个专题在讲什么；完全没有共享 tag 时退化为按内容 hash
        # 生成一个稳定短标签，避免 page_id 拼接成 source_tag 过长。
        tag_counts: dict[str, int] = {}
        for pid in members:
            page = next((p for p in candidate_pages if p.id == pid), None)
            if page is None:
                continue
            for t in page.tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1
        if tag_counts:
            label = max(tag_counts.items(), key=lambda kv: kv[1])[0]
        else:
            import hashlib
            label = "cluster-" + hashlib.sha1("|".join(sorted(members)).encode("utf-8")).hexdigest()[:8]
        out.append(TopicCandidate(tag=f"semantic-{label}", page_ids=sorted(members), link_density=0.0))

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
    embed_call: Optional[EmbedCall] = None,
    semantic_min_pages: int = _MIN_PAGES_PER_SEMANTIC_CLUSTER,
    semantic_similarity_threshold: float = _SEMANTIC_SIMILARITY_THRESHOLD,
) -> list[str]:
    """consolidate() 步骤 7 的入口：扫描候选、逐个生成专题页。

    返回本次新生成的 page_id 列表。没有 llm_call（专题页正文依赖 LLM 综合
    改写，规则本身只判断"值不值得生成"）或没有 wiki 页面时直接返回空列表。

    embed_call：[wiki 改进计划 P3] 显式传入时，额外跑一遍语义聚类候选
    （find_semantic_topic_candidates），与 tag+密度候选合并后一起生成；
    不传时行为与升级前完全一致，只走 tag+密度这一条路径。两条路径各自
    产出的候选按 source_tag 去重合并——理论上不会撞名（tag 路径用原始
    tag 名，语义路径统一加 `semantic-` 前缀），保留这一步只是为了防御性
    地避免未来任何一边改动导致意外重复生成。
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

    if embed_call is not None:
        # 已经被现有专题页 absorbs 过的页面 id，语义聚类时跳过，避免同一批
        # 页面反复被不同专题页收编。
        already_absorbed: set[str] = set()
        for p in pages:
            if p.type != "topic":
                continue
            for link in p.strong_links():
                if link.relation == "absorbs":
                    already_absorbed.add(link.target)

        semantic_candidates = find_semantic_topic_candidates(
            pages, embed_call,
            min_pages=semantic_min_pages,
            similarity_threshold=semantic_similarity_threshold,
            exclude_page_ids=already_absorbed,
        )
        seen_tags = {c.tag for c in candidates}
        for c in semantic_candidates:
            if c.tag not in seen_tags and c.tag not in exclude:
                candidates.append(c)
                seen_tags.add(c.tag)

    if not candidates:
        return []

    pages_by_id = {p.id: p for p in pages}
    created: list[str] = []
    for cand in candidates:
        page_id = generate_topic_page(cand, pages_by_id, paths, llm_call)
        if page_id:
            created.append(page_id)
    return created
