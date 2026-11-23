"""planning/dedup.py — 选题去重（方案 §6.3）。

* 概念集合 Jaccard（与库内每篇文章的概念集）：≥ HIGH 直接丢弃；[MID, HIGH) 交给模型判断；< MID 通过。
* 标题字符二元组 Jaccard（与库内文章标题、队列中的待办标题）：≥ 0.7 丢弃。
* 与被拒绝过的同一缺口相同 → 丢弃（被拒原因入库，避免反复提议）。
"""

from __future__ import annotations

from typing import Iterable, Set

from ..wiki import validate_util as vu

HIGH = 0.6
MID = 0.3
TITLE_HIGH = 0.7


def jaccard(a: Iterable, b: Iterable) -> float:
    a, b = set(a), set(b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def bigrams(text: str) -> Set[str]:
    k = vu.key(text)
    return {k[i:i + 2] for i in range(len(k) - 1)} or ({k} if k else set())


def title_similarity(a: str, b: str) -> float:
    return jaccard(bigrams(a), bigrams(b))


def concept_keys(names: Iterable[str]) -> Set[str]:
    return {vu.key(n) for n in names if vu.key(n)}
