"""
wiki/dedup.py — wiki 页面相似度判断（去重/合并候选）

默认方案：规则粗筛 + LLM 二次确认，不依赖 embedding。
    1. 规则打分（零成本）：对候选文本与每篇既有页面做 tag 重合度 +
       关键词 Jaccard 相似度的加权打分，复用 wiki/indexer.py 里同一套
       轻量分词（不追求分词质量，够用于粗筛）。
    2. 按打分分三档：
         >= HIGH_THRESHOLD  直接判定为同一主题，不用问 LLM
         [LOW_THRESHOLD, HIGH_THRESHOLD)  规则不确定，仅对分数最高的
             一个候选调用一次 LLM 做 YES/NO 确认（只问 top-1，不是每个
             候选都问，避免组合数爆炸——与 entity_index.py::
             consolidate_entities 里"中等相似度才兜底问 LLM"的既有策略
             一致）；没有传 llm_call 时，这一档一律判定为不相似（宁可
             多生成一篇页面，也不该在没有把握时盲目合并）。
         < LOW_THRESHOLD  判定为不相似，直接跳过
    这一方案的默认调用方式不需要任何 embedding 依赖，是 consolidate() 的
    默认路径。

可选方案：embedding 余弦相似度（find_similar_page_embedding /
embed_pages），语义捕捉能力更强，但需要调用方显式传入 embed_call 才会启用
（比如通过 memory_factory.py 里配置好的本地 embedding 模型）。两条路径
互斥使用，由调用方决定用哪个，本模块不替调用方做隐式降级。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from mini_agent.perception.local_embedding import cosine_similarity
from mini_agent.wiki.indexer import _tokenize
from mini_agent.wiki.parser import WikiPage

EmbedCall = Callable[[str], list[float]]
LLMCall = Callable[[str], str]

_BODY_CHARS_FOR_SCORING = 2000  # 正文前 N 字符足够代表主题，避免超长页面拖慢打分/推理

# ── 默认方案：规则打分阈值 ──────────────────────────────────────────────
_RULE_HIGH_THRESHOLD = 0.55   # 达到此分数，直接判定相似，不用问 LLM
_RULE_LOW_THRESHOLD = 0.25    # 低于此分数，直接判定不相似，不用问 LLM
_RULE_TAG_WEIGHT = 0.4
_RULE_TOKEN_WEIGHT = 0.6

# ── 可选方案：embedding 相似度阈值 ──────────────────────────────────────
_EMBED_SIMILARITY_THRESHOLD = 0.86


@dataclass
class SimilarPageMatch:
    page_id: str
    score: float
    method: str = "rule"  # "rule" | "rule+llm" | "embedding"


def _page_text(page: WikiPage) -> str:
    return f"{page.id} {' '.join(page.tags)}\n{page.body[:_BODY_CHARS_FOR_SCORING]}"


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _rule_score(text: str, tags: list[str], page: WikiPage) -> float:
    """规则打分：tag 重合度 + 正文关键词 Jaccard 相似度的加权和，取值 [0, 1]。"""
    text_tokens = set(_tokenize(text))
    page_tokens = set(_tokenize(page.body[:_BODY_CHARS_FOR_SCORING]) + _tokenize(page.id))
    token_score = _jaccard(text_tokens, page_tokens)

    tag_score = 0.0
    if tags or page.tags:
        tag_score = _jaccard({t.lower() for t in tags}, {t.lower() for t in page.tags})

    return _RULE_TOKEN_WEIGHT * token_score + _RULE_TAG_WEIGHT * tag_score


def _llm_confirm_same_topic(text: str, page: WikiPage, llm_call: LLMCall) -> bool:
    """对规则打分处于不确定区间的 top-1 候选，用一次轻量 LLM 调用确认是否
    属于同一主题。异常/非 YES 开头一律判定为不相似（保守，避免误合并）。"""
    prompt = (
        "以下是一段候选知识文本，以及一篇已有 wiki 页面的摘要。"
        "判断它们是否在描述同一个主题/实体/概念（允许别名、缩写、中英文差异，"
        "但描述的必须是同一件事）。只回答 YES 或 NO。\n\n"
        f"候选文本:\n{text[:500]}\n\n"
        f"已有页面「{page.id}」正文片段:\n{page.body[:500]}"
    )
    try:
        reply = (llm_call(prompt) or "").strip().upper()
    except Exception:
        return False
    return reply.startswith("Y")


def find_similar_page_rules(
    text: str,
    tags: list[str],
    existing_pages: list[WikiPage],
    *,
    llm_call: Optional[LLMCall] = None,
    high_threshold: float = _RULE_HIGH_THRESHOLD,
    low_threshold: float = _RULE_LOW_THRESHOLD,
) -> Optional[SimilarPageMatch]:
    """默认方案入口：规则粗筛 + （可选）LLM 二次确认，不依赖 embedding。

    返回 None 表示没有找到足够相似的既有页面，调用方应该新建页面。
    """
    if not existing_pages:
        return None

    scored = sorted(
        ((page, _rule_score(text, tags, page)) for page in existing_pages),
        key=lambda item: item[1],
        reverse=True,
    )
    top_page, top_score = scored[0]

    if top_score >= high_threshold:
        return SimilarPageMatch(page_id=top_page.id, score=top_score, method="rule")

    if top_score >= low_threshold and llm_call is not None:
        if _llm_confirm_same_topic(text, top_page, llm_call):
            return SimilarPageMatch(page_id=top_page.id, score=top_score, method="rule+llm")

    return None


# ── 可选方案：embedding 余弦相似度 ──────────────────────────────────────

def embed_pages(pages: list[WikiPage], embed_call: EmbedCall) -> dict[str, list[float]]:
    """对一批页面计算 embedding，返回 page_id -> vector。

    单个页面 embed 失败（比如模型偶发抽风）时跳过该页面，不中断整批。
    """
    out: dict[str, list[float]] = {}
    for p in pages:
        try:
            out[p.id] = embed_call(_page_text(p))
        except Exception:
            continue
    return out


def find_similar_page_embedding(
    text: str,
    page_embeddings: dict[str, list[float]],
    embed_call: EmbedCall,
    threshold: float = _EMBED_SIMILARITY_THRESHOLD,
) -> Optional[SimilarPageMatch]:
    """可选方案：在已 embed 好的现有页面里找与 text 最相似、且超过阈值的一篇。

    只有调用方显式传入 embed_call/page_embeddings 时才会用到这个路径，
    consolidate() 默认不启用。
    """
    if not page_embeddings:
        return None
    try:
        vec = embed_call(text)
    except Exception:
        return None
    best: Optional[SimilarPageMatch] = None
    for pid, pvec in page_embeddings.items():
        score = cosine_similarity(vec, pvec)
        if score >= threshold and (best is None or score > best.score):
            best = SimilarPageMatch(page_id=pid, score=score, method="embedding")
    return best


def find_similar_page(
    text: str,
    tags: list[str],
    existing_pages: list[WikiPage],
    *,
    llm_call: Optional[LLMCall] = None,
    embed_call: Optional[EmbedCall] = None,
    page_embeddings: Optional[dict[str, list[float]]] = None,
) -> Optional[SimilarPageMatch]:
    """统一入口：默认走规则+LLM 方案；只有显式传入 embed_call 才切换到
    embedding 方案（两条路径互斥，不做隐式降级/回退）。
    """
    if embed_call is not None:
        return find_similar_page_embedding(text, page_embeddings or {}, embed_call)
    return find_similar_page_rules(text, tags, existing_pages, llm_call=llm_call)
