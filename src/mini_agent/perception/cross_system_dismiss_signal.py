"""perception/cross_system_dismiss_signal.py — 跨系统"不感兴趣"信号
（只读标注，非强制抑制）。

[next_doc/personal_assistant_experience_improvement_directions.md
第 4 节"值得关注但暂不建议现在做的方向"——本模块正是那条开放问题的
落地，用户已明确要求推进]

## 为什么只做"标注"，不做"抑制"

之前那份文档明确指出了风险：`growth_advisor`（服务用户成长方向）和
`capability_learning`（服务 Agent 自身知识/人设）的候选，语义上不是
同一件事——用户不想让 Agent 学某个知识，不等于用户对某个成长方向不
感兴趣，反之亦然。贸然把两边的 dismiss 历史合并成一个"统一负反馈"，
可能会用一边的信号错误压低另一边本来有效的候选。

所以本模块**不修改任何候选的 confidence/排序权重**，也**不阻止**任何
候选生成或展示——它只是跟 `situational_relevance.py`（阶段四"处境
相关度"）完全同一个定位的**只读标注层**：算出"这条候选的标题，跟另一
个系统里已经被反复 dismiss 过的某条历史候选，文本上有多相似"，附加成
`InitiativeItem` 上的一个展示字段，让用户自己判断"这是不是我之前在
另一个地方就明确表示过不感兴趣的方向"，是否要因此忽略，交给用户自己
判断——不替用户做这个判断。

同系统内部的 dismiss 冷却（`growth_advisor._dismiss_counts_by_
dedupe_key()` / `capability_learning` 里 `OutlineSuggestion.status`）
已经在各自内部正常工作，本模块刻意**只看跨系统**的相似匹配（即
"来自成长顾问历史的 dismissed 标题"只用来标注"能力学习"这边的候选，
反之亦然），避免跟已有的同系统冷却机制重复计算/职责重叠。

## 相似度算法

复用 `situational_relevance.py` 已经验证过的"字符级 bigram +
Jaccard"，不重新发明——短中文标题场景下这是项目里已经在用、且专门
处理过"中文按单字切分导致无法比较"这个坑的实现，直接导入复用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from mini_agent.perception.situational_relevance import _jaccard, _tokens

# 跨系统匹配才提示的相似度下限——低于这个阈值大概率是巧合，不值得
# 打扰用户去分辨"这两条到底是不是同一件事"。取值比
# `situational_relevance` 的处境相关度更保守（那边是"排序参考"，越
# 敏感越好；这边是"提醒用户可能不感兴趣"，误报的打扰成本更高，宁可
# 漏标也不要错标）。
DEFAULT_MIN_SIMILARITY = 0.5

# 同一个 dedupe 后的标题至少被 dismiss 过这么多次，才纳入信号来源——
# 单次 dismiss 可能只是"这次报告质量不好"，不代表方向层面不感兴趣，
# 跟 growth_advisor 自己"报告质量差单独计数、不连累方向层面负反馈"的
# 处理思路一致（见 growth_advisor.py `_report_quality_dismiss_counts`）。
DEFAULT_MIN_DISMISS_COUNT = 2


@dataclass
class DismissSignal:
    """某个系统里被反复 dismiss 过的一条历史候选标题及其次数。"""

    text: str
    count: int
    source_system: str   # "growth_advisor" | "capability_learning"
    tokens: frozenset[str] = field(default_factory=frozenset)


def _normalize_key(text: str) -> str:
    from mini_agent.evolution.growth_advisor import normalize_title_key
    return normalize_title_key(text)


def _load_growth_dismiss_signals(paths) -> list[DismissSignal]:
    try:
        from mini_agent.evolution.growth_advisor import GrowthBacklog, STATUS_DISMISSED
    except Exception:
        return []
    try:
        counts: dict[str, tuple[str, int]] = {}
        for c in GrowthBacklog(paths).load_all():
            if c.status != STATUS_DISMISSED:
                continue
            key = c.dedupe_key()
            title, n = counts.get(key, (c.title, 0))
            counts[key] = (title, n + 1)
        return [
            DismissSignal(text=title, count=n, source_system="growth_advisor", tokens=_tokens(title))
            for title, n in counts.values()
        ]
    except Exception:
        return []


def _load_capability_dismiss_signals(paths) -> list[DismissSignal]:
    try:
        from mini_agent.evolution.capability_learning import CapabilityOutlineSuggestionStore
    except Exception:
        return []
    try:
        counts: dict[str, tuple[str, int]] = {}
        store = CapabilityOutlineSuggestionStore(paths)
        for s in store.list_suggestions(status="dismissed"):
            key = _normalize_key(s.suggested_name)
            title, n = counts.get(key, (s.suggested_name, 0))
            counts[key] = (title, n + 1)
        return [
            DismissSignal(text=title, count=n, source_system="capability_learning", tokens=_tokens(title))
            for title, n in counts.values()
        ]
    except Exception:
        return []


def load_cross_system_dismiss_signals(paths, *, min_count: int = DEFAULT_MIN_DISMISS_COUNT) -> list[DismissSignal]:
    """聚合两条线里达到 `min_count` 次 dismiss 的历史标题。任一路读取
    失败都不影响另一路，与项目里其它只读聚合模块（`initiative_inbox.py`/
    `situational_relevance.py`）同一容错风格。"""
    signals = _load_growth_dismiss_signals(paths) + _load_capability_dismiss_signals(paths)
    return [s for s in signals if s.count >= min_count]


def find_cross_system_match(
    text: str,
    own_system: str,
    signals: list[DismissSignal],
    *,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
) -> Optional[tuple[float, DismissSignal]]:
    """在 `signals` 里找跟 `text` 最相似、且**不是**来自 `own_system`
    的一条（只看跨系统匹配，理由见模块文档字符串）。返回
    `(相似度, 命中的 DismissSignal)`，没有任何一条达到 `min_similarity`
    时返回 `None`。"""
    if not text:
        return None
    text_tokens = _tokens(text)
    if not text_tokens:
        return None

    best_score = 0.0
    best_signal: Optional[DismissSignal] = None
    for sig in signals:
        if sig.source_system == own_system:
            continue
        score = _jaccard(text_tokens, sig.tokens)
        if score > best_score:
            best_score = score
            best_signal = sig
    if best_signal is not None and best_score >= min_similarity:
        return best_score, best_signal
    return None
