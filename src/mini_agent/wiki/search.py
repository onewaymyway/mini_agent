"""
wiki/search.py — 三段式检索：规则粗筛 → 图扩展 → LLM 精排

对应重构计划 5.4 节 / 阶段三第一条。作为 shelf_search（perception/
library_index.py 的分类树两步检索）的平行实现，通过
LibraryIndex.wiki_search() 暴露，两套检索并存、互不替换，供 A/B 对比效果。

三段：
    1. 规则粗筛（零 LLM 成本）：对 query 与每篇既有页面做 tag 重合度 +
       正文关键词 Jaccard 相似度的加权打分，取 top tag_top_n 篇候选。
       复用 wiki/indexer.py 的分词逻辑（wiki/dedup.py 判重也用同一套），
       保持粗筛口径全库一致。
    2. 图扩展（零 LLM 成本）：命中候选的 frontmatter 强链接展开一跳
       （GraphIndex.expand(strong_only=True)），把结构化相关页面（依赖/
       取代/因果关系）自动带入候选池——这是相对分类树检索的核心增量
       能力，不走正文 [[..]] 弱引用，避免被泛泛的 mentions 关系稀释。
    3. LLM 精排：候选收窄到 rerank_top_n 篇后，把完整正文（不是摘要）
       交给 llm_call 排序并生成综合回答，同时要求标注"回答主要基于哪几
       篇页面"（WikiSearchResult.grounded_page_ids），供后续反馈定位到
       具体页面。没有传 llm_call 时跳过第三步，直接返回图扩展后的候选
       （按规则分数排序），调用方可以自行展示候选列表。

规模考虑：与 wiki/dedup.py 一致，每次调用对 wiki/ 下全部页面做一次
parse_page（成本是 IO + yaml 解析，不是网络调用），当前规模下足够快；
真正变慢时可以复用 indexer.py 生成的 tags.json / search_index.json 做
粗筛，图扩展也可以直接读 graph.json 而不必重新 build，本函数的签名不需要
因此变化。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.graph import GraphIndex
from mini_agent.wiki.indexer import _tokenize, discover_pages
from mini_agent.wiki.parser import WikiPage, parse_page

LLMCall = Callable[[str], str]

_BODY_CHARS_FOR_SCORING = 2000
_RULE_TAG_WEIGHT = 0.4
_RULE_TOKEN_WEIGHT = 0.6

_GROUNDED_MARKER = "基于页面:"


@dataclass
class WikiSearchResult:
    """三段式检索的结果。stage_reached 标注实际走到了哪一段：
    "none"（零命中）| "rule"（未传 llm_call，规则粗筛结果）|
    "graph"（未传 llm_call，图扩展后的候选）| "llm"（走完三段）。
    """

    pages: list[WikiPage] = field(default_factory=list)
    answer: str = ""
    grounded_page_ids: list[str] = field(default_factory=list)
    stage_reached: str = "none"


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _rule_score(query_tokens: set[str], query_tags: set[str], page: WikiPage) -> float:
    page_tokens = set(_tokenize(page.body[:_BODY_CHARS_FOR_SCORING]) + _tokenize(page.id))
    token_score = _jaccard(query_tokens, page_tokens)
    tag_score = 0.0
    if query_tags or page.tags:
        tag_score = _jaccard(query_tags, {t.lower() for t in page.tags})
    return _RULE_TOKEN_WEIGHT * token_score + _RULE_TAG_WEIGHT * tag_score


def _rule_prefilter(
    query: str, tags: list[str], pages: list[WikiPage], top_n: int
) -> list[WikiPage]:
    query_tokens = set(_tokenize(query))
    query_tags = {t.lower() for t in tags}
    scored = [(p, _rule_score(query_tokens, query_tags, p)) for p in pages]
    scored = [item for item in scored if item[1] > 0]
    scored.sort(key=lambda item: -item[1])
    return [p for p, _ in scored[:top_n]]


def _llm_rerank(
    query: str, candidates: list[WikiPage], llm_call: LLMCall
) -> WikiSearchResult:
    numbered = "\n\n".join(
        f"[{i + 1}] id={p.id} (type={p.type}, status={p.status})\n{p.body[:1500]}"
        for i, p in enumerate(candidates)
    )
    prompt = (
        "以下是若干候选 wiki 页面（已按初步相关性粗筛），请只依据这些页面的"
        "正文内容回答用户问题，不要编造页面中没有的信息。"
        f"回答完成后另起一行，以「{_GROUNDED_MARKER}」开头，列出你主要依据的"
        "页面 id（逗号分隔，按重要性排序）。\n\n"
        f"用户问题: {query}\n\n候选页面:\n{numbered}"
    )
    try:
        reply = llm_call(prompt) or ""
    except Exception:
        reply = ""

    answer = reply
    grounded: list[str] = []
    if _GROUNDED_MARKER in reply:
        answer, _, tail = reply.rpartition(_GROUNDED_MARKER)
        grounded = [pid.strip() for pid in tail.strip().split(",") if pid.strip()]

    return WikiSearchResult(
        pages=candidates,
        answer=answer.strip(),
        grounded_page_ids=grounded,
        stage_reached="llm",
    )


def wiki_shelf_search(
    paths: AgentPaths,
    query: str,
    *,
    tags: Optional[list[str]] = None,
    k: int = 5,
    tag_top_n: int = 25,
    rerank_top_n: int = 8,
    llm_call: Optional[LLMCall] = None,
) -> WikiSearchResult:
    """三段式检索入口：规则粗筛 → 图扩展 → （可选）LLM 精排。

    没有 wiki 页面、或规则粗筛零命中时返回空结果（stage_reached="none"），
    调用方应回退到旧的 shelf_search / store.search()——这是"平行实现"的
    应有语义，本函数不负责兜底全库检索。
    """
    if not paths.wiki_dir.exists():
        return WikiSearchResult()

    md_paths = discover_pages(paths)
    if not md_paths:
        return WikiSearchResult()

    all_pages: list[WikiPage] = []
    for md_path in md_paths:
        try:
            all_pages.append(parse_page(md_path))
        except Exception:
            continue
    if not all_pages:
        return WikiSearchResult()

    rule_hits = _rule_prefilter(query, tags or [], all_pages, tag_top_n)
    if not rule_hits:
        return WikiSearchResult()

    by_id = {p.id: p for p in all_pages}
    rule_hit_ids = [p.id for p in rule_hits]

    graph = GraphIndex.build(all_pages)
    expanded_ids = graph.expand(rule_hit_ids, strong_only=True)

    # 规则命中排在前面（已按分数排序），图扩展带入的新增页面接在后面；
    # dict.fromkeys 去重同时保序。
    ordered_ids = list(dict.fromkeys([*rule_hit_ids, *sorted(expanded_ids)]))
    candidate_pages = [by_id[pid] for pid in ordered_ids if pid in by_id][:rerank_top_n]

    if llm_call is None:
        stage = "graph" if expanded_ids else "rule"
        return WikiSearchResult(pages=candidate_pages[:k], stage_reached=stage)

    return _llm_rerank(query, candidate_pages, llm_call)
