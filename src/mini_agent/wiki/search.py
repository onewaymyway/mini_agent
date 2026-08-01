"""
wiki/search.py — 三段式检索：规则粗筛 → 图扩展 → LLM 精排

对应重构计划 5.4 节 / 阶段三第一条。作为 shelf_search（perception/
library_index.py 的分类树两步检索）的平行实现，通过
LibraryIndex.wiki_search() 暴露，两套检索并存、互不替换，供 A/B 对比效果。

三段：
    1. 规则粗筛（零 LLM 成本）：对 query 与每篇既有页面做 tag 重合度 +
       正文关键词 Jaccard 相似度 + grounded_hit_count 信度加权的打分，取
       top tag_top_n 篇候选。复用 wiki/indexer.py 的分词逻辑（wiki/
       dedup.py 判重也用同一套），保持粗筛口径全库一致。
    2. 图扩展（零 LLM 成本）：命中候选的 frontmatter 强链接展开一跳
       （GraphIndex.expand_legacy(strong_only=True)），把结构化相关页面
       （依赖/取代/因果关系）自动带入候选池——这是相对分类树检索的核心
       增量能力，不走正文 [[..]] 弱引用，避免被泛泛的 mentions 关系稀释。
       规则粗筛候选数量明显不足以覆盖 rerank_top_n（或调用方显式传
       `deep=True`）时，自动切到多跳衰减扩展（GraphIndex.expand()，
       `max_hops=2`），把更深一层但确实相关的页面也带进来，权重随跳数
       衰减，供第 3 步 LLM 精排参考（wiki 提取层与组织层改进计划 O2
       §5.2.2）。
    3. LLM 精排：候选收窄到 rerank_top_n 篇后，把完整正文（不是摘要）
       交给 llm_call 排序并生成综合回答，同时要求标注"回答主要基于哪几
       篇页面"（WikiSearchResult.grounded_page_ids），供后续反馈定位到
       具体页面。没有传 llm_call 时跳过第三步，直接返回图扩展后的候选
       （按规则分数排序），调用方可以自行展示候选列表。

规模考虑（wiki 提取层与组织层改进计划 O1 §4.2.1）：优先复用
wiki/indexer.py 生成的 _index/ 派生索引（tags.json / search_index.json /
graph.json）做规则粗筛与图扩展的数据源，只在索引缺失或明显过期
（wiki/index_reader.py::load_index 的"读时校验"未通过）时才退回对
wiki/ 下全部页面执行一次 parse_page 的全量扫描（与改动前行为完全一致）。
这一步只改变数据来源，不改变对外接口/返回结果的语义。
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.graph import GraphIndex
from mini_agent.wiki.index_reader import IndexData, load_index
from mini_agent.wiki.indexer import _tokenize, discover_pages
from mini_agent.wiki.parser import WikiPage, parse_page

LLMCall = Callable[[str], str]

_BODY_CHARS_FOR_SCORING = 2000
_RULE_TAG_WEIGHT = 0.4
_RULE_TOKEN_WEIGHT = 0.6
_DEFAULT_CONFIDENCE_WEIGHT = 0.1

_GROUNDED_MARKER = "基于页面:"

# wiki 提取层与组织层改进计划 O2 §5.2.2：深度检索（多跳图扩展）参数。
_DEEP_MAX_HOPS = 2
_DEEP_DECAY = 0.5
_DEEP_CANDIDATE_MULTIPLIER = 3  # 深度扩展候选硬上限 = rerank_top_n * 此倍数（计划 §5.4）


@dataclass
class WikiSearchResult:
    """三段式检索的结果。stage_reached 标注实际走到了哪一段：
    "none"（零命中）| "rule"（未传 llm_call，规则粗筛结果）|
    "graph"（未传 llm_call，一跳图扩展后的候选）|
    "graph_deep"（未传 llm_call，多跳图扩展后的候选，O2 §5.2.2）|
    "llm"（走完三段，一跳/多跳均可能）。
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


def _rule_score(
    query_tokens: set[str],
    query_tags: set[str],
    page: WikiPage,
    *,
    confidence_weight: float = _DEFAULT_CONFIDENCE_WEIGHT,
    lifecycle_discount_enabled: bool = False,
) -> float:
    page_tokens = set(_tokenize(page.body[:_BODY_CHARS_FOR_SCORING]) + _tokenize(page.id))
    token_score = _jaccard(query_tokens, page_tokens)
    tag_score = 0.0
    if query_tags or page.tags:
        tag_score = _jaccard(query_tags, {t.lower() for t in page.tags})

    # wiki 提取层与组织层改进计划 O1 §4.2.2：知识信度分层——被 LLM 精排
    # 反复命中过的页面（grounded_hit_count）在粗筛阶段获得少量加权，对数
    # 避免头部页面赢者通吃；confidence_weight=0 时与改动前完全一致。
    grounded_hit_count = 0
    try:
        grounded_hit_count = int(page.raw_frontmatter.get("grounded_hit_count") or 0)
    except (TypeError, ValueError):
        grounded_hit_count = 0
    confidence_score = confidence_weight * math.log(1 + max(grounded_hit_count, 0))

    total = _RULE_TOKEN_WEIGHT * token_score + _RULE_TAG_WEIGHT * tag_score + confidence_score

    # wiki 提取层与组织层改进计划 O4 §7.2.2：知识生命周期状态折扣——
    # knowledge_state=stale 的页面打五折、superseded 的页面直接归零（相当于
    # 从粗筛候选池里排除，但不物理删除，仍可被 /wiki <page-id> 直接浏览）。
    # 默认关闭（lifecycle_discount_enabled=False 时与改动前完全一致），
    # 需要显式开启，符合 O4 §7.4 风险条款"先只记录不影响排序"的执行纪律。
    if lifecycle_discount_enabled:
        state = str(page.raw_frontmatter.get("knowledge_state") or "fresh")
        if state == "superseded":
            return 0.0
        if state == "stale":
            total *= 0.5

    return total


def _rule_prefilter(
    query: str,
    tags: list[str],
    pages: list[WikiPage],
    top_n: int,
    *,
    confidence_weight: float = _DEFAULT_CONFIDENCE_WEIGHT,
) -> list[WikiPage]:
    query_tokens = set(_tokenize(query))
    query_tags = {t.lower() for t in tags}
    scored = [
        (p, _rule_score(query_tokens, query_tags, p, confidence_weight=confidence_weight))
        for p in pages
    ]
    scored = [item for item in scored if item[1] > 0]
    scored.sort(key=lambda item: -item[1])
    return [p for p, _ in scored[:top_n]]


def _llm_rerank(
    query: str,
    candidates: list[WikiPage],
    llm_call: LLMCall,
    *,
    weights: Optional[dict[str, float]] = None,
) -> WikiSearchResult:
    weights = weights or {}
    numbered = "\n\n".join(
        f"[{i + 1}] id={p.id} (type={p.type}, status={p.status}"
        + (
            f", graph_relation=indirect~{weights[p.id]:.2f}"
            if p.id in weights else ""
        )
        + f")\n{p.body[:1500]}"
        for i, p in enumerate(candidates)
    )
    prompt = (
        "以下是若干候选 wiki 页面（已按初步相关性粗筛），请只依据这些页面的"
        "正文内容回答用户问题，不要编造页面中没有的信息。部分页面标注了"
        "`graph_relation=indirect~<权重>`，代表它是通过图谱间接关联带入的"
        "候选（权重越低说明关系跳数越深、越不直接），排序/引用时请酌情"
        "降低这类页面的优先级，除非其内容确实是回答问题所必需的。"
        f"回答完成后另起一行，以「{_GROUNDED_MARKER}」开头，列出你主要依据的"
        "页面 id（逗号分隔，按重要性排序）。\n\n"
        f"用户问题: {query}\n\n候选页面:\n{numbered}"
    )
    try:
        reply = llm_call(prompt) or ""
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.wiki.search._llm_rerank')
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


def _load_all_pages_full_scan(paths: AgentPaths) -> list[WikiPage]:
    """改动前的全量扫描路径，索引缺失/过期时的兜底，行为不变。"""
    all_pages: list[WikiPage] = []
    for md_path in discover_pages(paths):
        try:
            all_pages.append(parse_page(md_path))
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.wiki.search._load_all_pages_full_scan')
            continue
    return all_pages


def _gather_via_index(
    paths: AgentPaths,
    index: IndexData,
    query: str,
    tags: list[str],
    tag_top_n: int,
    confidence_weight: float,
) -> tuple[list[WikiPage], dict[str, WikiPage], GraphIndex]:
    """用派生索引做候选粗筛，只 parse_page 那些真正命中 token/tag 的页面
    （而不是全库），大幅减少 IO/yaml 解析次数（O1 §4.2.1 的核心收益）。

    返回 (排序后的规则命中候选, id->WikiPage 惰性解析缓存, 复用的图索引)。
    id->WikiPage 缓存后续被图扩展阶段按需追加解析，不会一次性解析全库。
    """
    query_tokens = set(_tokenize(query))
    query_tags = {t.lower() for t in tags}
    candidate_ids = index.candidate_ids_for(query_tokens, query_tags)

    by_id: dict[str, WikiPage] = {}
    for pid in candidate_ids:
        md_path = index.id_to_path.get(pid)
        if md_path is None:
            continue
        try:
            by_id[pid] = parse_page(md_path)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.wiki.search._gather_via_index')
            continue

    rule_hits = _rule_prefilter(
        query, tags, list(by_id.values()), tag_top_n, confidence_weight=confidence_weight
    )
    return rule_hits, by_id, index.graph


def _resolve_lazy(
    pid: str, by_id: dict[str, WikiPage], index: Optional[IndexData]
) -> Optional[WikiPage]:
    """图扩展带入的新 id 如果还没被解析过，按需惰性 parse_page 单个文件
    （而不是重新做一次全库扫描）。"""
    if pid in by_id:
        return by_id[pid]
    if index is None:
        return None
    md_path = index.id_to_path.get(pid)
    if md_path is None:
        return None
    try:
        page = parse_page(md_path)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.wiki.search._resolve_lazy')
        return None
    by_id[pid] = page
    return page


def _record_usage(paths: AgentPaths, query: str, result: "WikiSearchResult") -> None:
    """外部知识反馈闭环计划 P2：追加一条检索命中记录到
    `AgentPaths.wiki_usage_log_path`，供 `evolution/wiki_utility_audit.py`
    周期性聚合"近期利用率"。只做单次 append（不读、不改、不加锁——多进程
    并发追加到同一文件的行级交错在 jsonl 场景下可接受，聚合时按行独立解析，
    错一行不影响其它行），失败静默吞掉不影响检索主流程本身。"""
    try:
        page_ids = [p.id for p in result.pages]
        if not page_ids:
            return
        record = {
            "ts": time.time(),
            "query": query[:200],
            "page_ids": page_ids,
            "grounded_page_ids": result.grounded_page_ids,
            "stage_reached": result.stage_reached,
        }
        paths.wiki_usage_log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(paths.wiki_usage_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def wiki_shelf_search(
    paths: AgentPaths,
    query: str,
    *,
    tags: Optional[list[str]] = None,
    k: int = 5,
    tag_top_n: int = 25,
    rerank_top_n: int = 8,
    llm_call: Optional[LLMCall] = None,
    confidence_weight: float = _DEFAULT_CONFIDENCE_WEIGHT,
    use_index: bool = True,
    deep: Optional[bool] = None,
) -> WikiSearchResult:
    """三段式检索入口：规则粗筛 → 图扩展 → （可选）LLM 精排。

    没有 wiki 页面、或规则粗筛零命中时返回空结果（stage_reached="none"），
    调用方应回退到旧的 shelf_search / store.search()——这是"平行实现"的
    应有语义，本函数不负责兜底全库检索。

    use_index=True（默认）时优先复用 wiki/index_reader.py 加载的派生索引
    做候选粗筛；索引缺失/过期或 use_index=False 时退回全量 parse_page
    扫描（与本次改动前完全一致的行为，可用作回归对比）。

    deep（wiki 提取层与组织层改进计划 O2 §5.2.2）：
      - `None`（默认）：规则粗筛候选数不足以覆盖 `rerank_top_n` 时自动
        切到多跳（`max_hops=2`）图扩展，否则维持一跳（行为、性能特征与
        本次改动前完全一致）。
      - `True`：强制多跳图扩展（对应 `/wiki search --deep`）。
      - `False`：强制维持一跳，即使候选数量不足也不自动升级。
    """
    if not paths.wiki_dir.exists():
        return WikiSearchResult()

    index: Optional[IndexData] = load_index(paths) if use_index else None

    if index is not None:
        rule_hits, by_id, graph = _gather_via_index(
            paths, index, query, tags or [], tag_top_n, confidence_weight
        )
    else:
        all_pages = _load_all_pages_full_scan(paths)
        if not all_pages:
            return WikiSearchResult()
        rule_hits = _rule_prefilter(
            query, tags or [], all_pages, tag_top_n, confidence_weight=confidence_weight
        )
        by_id = {p.id: p for p in all_pages}
        graph = GraphIndex.build(all_pages)

    if not rule_hits:
        return WikiSearchResult()

    rule_hit_ids = [p.id for p in rule_hits]

    use_deep = deep if deep is not None else len(rule_hit_ids) < rerank_top_n
    if use_deep:
        expanded_weights = graph.expand(
            rule_hit_ids,
            strong_only=True,
            max_hops=_DEEP_MAX_HOPS,
            decay=_DEEP_DECAY,
            max_candidates=rerank_top_n * _DEEP_CANDIDATE_MULTIPLIER,
        )
    else:
        expanded_weights = {pid: 1.0 for pid in graph.expand_legacy(rule_hit_ids, strong_only=True)}
    expanded_ids = set(expanded_weights.keys())

    # 惰性补解析图扩展带入的新页面（索引路径下 by_id 只包含粗筛命中的
    # 候选，图扩展新增的 id 需要按需单独 parse_page；全量扫描路径下
    # by_id 已经覆盖全库，这里是 no-op）。
    for pid in expanded_ids:
        _resolve_lazy(pid, by_id, index)

    # 规则命中排在前面（已按分数排序），图扩展带入的新增页面按权重降序
    # 接在后面；dict.fromkeys 去重同时保序。
    expanded_ordered = sorted(expanded_ids, key=lambda pid: -expanded_weights.get(pid, 0.0))
    ordered_ids = list(dict.fromkeys([*rule_hit_ids, *expanded_ordered]))
    candidate_pages = [by_id[pid] for pid in ordered_ids if pid in by_id][:rerank_top_n]

    if llm_call is None:
        if expanded_ids:
            stage = "graph_deep" if use_deep else "graph"
        else:
            stage = "rule"
        result = WikiSearchResult(pages=candidate_pages[:k], stage_reached=stage)
        _record_usage(paths, query, result)
        return result

    # 只把"确实是通过图扩展带入、且不是 1.0（一跳兼容权重）"的页面标注
    # 权重信息，一跳模式下不改变 LLM prompt 的既有格式。
    rerank_weights = (
        {pid: w for pid, w in expanded_weights.items() if w < 1.0} if use_deep else {}
    )
    result = _llm_rerank(query, candidate_pages, llm_call, weights=rerank_weights)
    _record_usage(paths, query, result)
    return result
