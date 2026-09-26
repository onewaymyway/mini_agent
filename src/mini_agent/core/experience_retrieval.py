"""core/experience_retrieval.py — ExperienceRetriever：按目标相似度检索。

见 `next_doc/refactor_plan/04-phase3-experience-layer-sprint-plan.md`
Sprint 3-2：“按‘目标相似度’检索历史 Experience（第一版可以用简单的
关键词/embedding 相似度，不需要复杂算法）”。

第一版选关键词重叠度，不引入 embedding——理由与 Sprint 2
`experience_store.py::search()` 选子串匹配时的理由一致：现阶段
Experience 记录量级低，检索质量的瓶颈不在算法精细度，而在“有没有真的
被用上”（对应 Sprint 3-2 验收标准第 2 条）。如果未来记录量增长到关键词
重叠不够用，再按需升级到 embedding，不提前引入复杂度。
"""

from __future__ import annotations

import re
from typing import Optional

from .experience import Experience
from .experience_store import ExperienceStore

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+")


def _tokenize(text: str) -> set[str]:
    """把目标文本切成词集合，供关键词重叠度比较。

    中文没有天然分词边界，这里退化成“逐字切分 + 连续拉丁字母/数字当一个
    词”的最小实现（`\\w` 覆盖拉丁字母数字下划线，`\\u4e00-\\u9fff` 覆盖
    常用汉字区间，逐字符匹配）。不引入分词库依赖，与止损原则一致——
    这只是“简单关键词相似度”的第一版。
    """
    if not text:
        return set()
    tokens: set[str] = set()
    for m in _TOKEN_RE.finditer(text):
        chunk = m.group(0)
        if chunk.isascii():
            tokens.add(chunk.lower())
        else:
            # 中文按字符拆开，逐字计入重叠度。
            tokens.update(chunk)
    return tokens


def _similarity(a: set[str], b: set[str]) -> float:
    """Jaccard 相似度（交集 / 并集），两边都为空时判定为 0（不相似）。"""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def retrieve_similar_experiences(
    goal_text: str,
    store: Optional[ExperienceStore] = None,
    limit: int = 3,
    min_similarity: float = 0.05,
) -> list[Experience]:
    """按 `goal_text` 与历史 Experience 的 `goal_text` 关键词重叠度检索，
    返回相似度最高的至多 `limit` 条（相似度低于 `min_similarity` 的不
    返回，避免"完全不相关也硬凑数量"）。相似度相同时最近的记录优先。
    """
    store = store or ExperienceStore()
    query_tokens = _tokenize(goal_text)
    if not query_tokens:
        return []

    scored: list[tuple[float, float, Experience]] = []
    for exp in store.all():
        sim = _similarity(query_tokens, _tokenize(exp.goal_text))
        if sim >= min_similarity:
            scored.append((sim, exp.created_at, exp))

    # 相似度降序，相似度相同时按时间降序（最近优先）。
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [exp for _, _, exp in scored[:limit]]


def render_experiences_as_context(experiences: list[Experience]) -> str:
    """把检索到的 Experience 渲染成一段人类可读文本，供注入 LLM 上下文。

    只挑对"这次目标该怎么做"有参考价值的字段（goal_text/status/
    final_report/lesson），不把 context/state_before 等目前大多是空
    dict 的字段也塞进去（那些字段要等 Phase 4 有真实数据后才有意义）。
    """
    if not experiences:
        return ""
    lines = ["以下是历史上执行过的相似目标，供参考（不是本次的验收标准）："]
    for i, exp in enumerate(experiences, start=1):
        lines.append(f"{i}. 目标：{exp.goal_text}")
        lines.append(f"   结果：{exp.status}；{exp.final_report}")
        if exp.lesson:
            lines.append(f"   教训：{exp.lesson}")
    return "\n".join(lines)
