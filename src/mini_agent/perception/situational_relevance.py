"""perception/situational_relevance.py — 顶层"用户处境模型"相关度评分
（只读，阶段四）。

[next_doc/initiative_systems_unification_plan.md §4.5]

背景：`growth_advisor`/`capability_learning`/`soft_goal_deriver` 三条线
各自独立扫描 `memory`/`capability_map`/`wiki_gap` 生成候选，互不感知
"用户当前在做什么"——`work_index`/`WorkThread` 此前只服务
`soft_goal_deriver` 一条线（用于判定某个 WorkThread 是否长期停滞）。

方案原文明确要求**第一版只做只读的排序建议**（"与当前处境的相关度"
标注），不直接改变各模块现有的独立候选生成逻辑，避免一次性引入不可
预期的行为变化——本模块正是这个"只读标注层"，不修改
`GoalBacklog`/`GrowthBacklog`/`CapabilityQuestionStore`/`work_index`
任何一方的数据或生成逻辑，纯粹"读取现状 → 打一个 0~1 的相关度分数"。

打分方法：复用 `wiki/dedup.py` 已经在用的"分词 + Jaccard 相似度"整体
思路（规则粗筛、零成本、可离线单测），但**没有直接复用**
`wiki/indexer._tokenize`——那个分词器对中文按"单字"切分又过滤掉长度 1
的 token，短标题（"学做饭"这类 2~4 字候选标题/WorkThread 标题，本模块
最主要的输入形态）会因此完全没有可比较的 token。本模块改用更适合短
中文文本的字符级 bigram 分词（见 `_tokens()`），Jaccard 计算方式不变。
"用户当前处境"由两类信号构成：
    - `work_index` 里状态为 `active` 的 `WorkThread`（标题 + 累计进展 +
      待解决问题 + 建议后续，这几个字段最能代表"用户正在做的事情"）；
    - `GoalBacklog` 里状态为 `active` 的目标（标题 + 描述），不区分
      `source`——不论是用户自己加的、还是 growth_advisor/
      capability_learning 落地过来的，都代表"当前正在被推进的事"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SituationalSignal:
    """单个"用户处境"信号项，供打分和展示"最相关的是哪一件事"用。"""

    kind: str          # "work_thread" | "goal"
    signal_id: str
    title: str
    tokens: frozenset[str] = field(default_factory=frozenset)


@dataclass
class SituationalContext:
    """一次快照里全部处境信号的集合，`score_relevance()` 的输入。"""

    signals: list[SituationalSignal] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.signals


def _tokens(text: str) -> frozenset[str]:
    """字符级 bigram 分词，专门照顾中文短标题（"学做饭"这类 2~4 字短语，
    直接用 `wiki/indexer._tokenize` 会因为该函数对中文按"单字"切分、又
    过滤掉长度 1 的 token 而导致完全没有可比较的 token——短标题场景下
    这不是能接受的行为，本模块的输入恰恰以候选标题/WorkThread 标题这类
    短文本为主，所以没有直接复用 `wiki/indexer._tokenize`，而是用更适合
    短中文文本的字符级 bigram（"学做饭" → {"学做","做饭"}）。

    对英文/数字按 word boundary 取完整单词（长度 > 1），中文/其它非 ASCII
    字符按连续片段切出重叠 bigram——两类 token 混在同一个集合里参与
    Jaccard 比较，不区分来源。"""
    import re

    text = (text or "").lower()
    tokens: set[str] = set()
    for m in re.finditer(r"[a-z0-9_]+|[^\x00-\x7f\s]+", text):
        seg = m.group()
        if re.match(r"^[a-z0-9_]+$", seg):
            if len(seg) > 1:
                tokens.add(seg)
        else:
            # 连续的非 ASCII（主要是中文）片段，切成重叠 bigram；单字
            # 片段（len==1）保留原字符本身，避免完全没有 token 可比较。
            if len(seg) == 1:
                tokens.add(seg)
            else:
                tokens.update(seg[i:i + 2] for i in range(len(seg) - 1))
    return frozenset(tokens)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def load_situational_context(paths) -> SituationalContext:
    """读取当前"用户处境"信号（active WorkThread + active Goal），
    聚合成一个 `SituationalContext`。

    任何一路读取失败都不应该影响另一路——分别 try/except，失败的一路
    贡献空列表，与 `initiative_inbox.py` 同一风格（宁可少一部分信号，
    不要因为一路异常让整个只读标注功能不可用）。两路都失败时返回空的
    `SituationalContext`（`is_empty=True`），调用方应把这种情况下的
    相关度统一按"无法判断"处理（`score_relevance()` 对空 context 返回
    `0.0`，不是报错也不是虚构一个分数）。
    """
    signals: list[SituationalSignal] = []

    try:
        from mini_agent.perception.workdir_knowledge import get_active_work_threads
        for t in get_active_work_threads(paths):
            text = " ".join(filter(None, [
                t.title, t.cumulative_progress, t.next_suggested, " ".join(t.open_questions),
            ]))
            signals.append(SituationalSignal(
                kind="work_thread", signal_id=t.id, title=t.title, tokens=_tokens(text),
            ))
    except Exception:
        pass

    try:
        from mini_agent.perception.goal_backlog import GoalBacklog
        backlog = GoalBacklog(paths)
        backlog.load()
        for node in backlog.all_nodes():
            if node.status != "active":
                continue
            text = f"{node.title} {node.description}"
            signals.append(SituationalSignal(
                kind="goal", signal_id=node.id, title=node.title, tokens=_tokens(text),
            ))
    except Exception:
        pass

    try:
        # [next_doc/personal_assistant_experience_improvement_directions.md
        # 缺口二] 画像里的 tech_stack/habits 此前只用于看板展示，没有
        # 参与候选排序——补上第三类处境信号，跟 WorkThread/Goal 同样的
        # bigram+Jaccard 打分方式，不引入新算法。每条 tech_stack/habits
        # 作为独立信号项（而不是拼成一整段大文本），理由跟
        # `score_relevance()` 取"最大相似度"而非"整体相似度"一致：
        # 画像条目数量的多少不应该稀释单条的可比较性。
        from mini_agent.profile import UserProfileManager
        profile = UserProfileManager(paths).load()
        for kind, items in (
            ("profile_tech_stack", profile.derived.get("tech_stack") or []),
            ("profile_habit", profile.derived.get("habits") or []),
        ):
            for item in items:
                text = item.get("text") if isinstance(item, dict) else str(item)
                if not text:
                    continue
                signals.append(SituationalSignal(
                    kind=kind, signal_id=f"{kind}:{text[:40]}", title=text, tokens=_tokens(text),
                ))
    except Exception:
        pass

    return SituationalContext(signals=signals)


def score_relevance(
    text: str, context: SituationalContext,
) -> tuple[float, Optional[SituationalSignal]]:
    """给一段候选文本（通常是"标题 + 摘要"拼接）打一个 0~1 的"与当前
    用户处境的相关度"分数，返回 `(分数, 最相关的信号项或 None)`。

    取"与任意一个处境信号的最高 Jaccard 相似度"，而不是"与全部信号拼在
    一起的一个大文本做相似度"——后者会被信号数量稀释（处境信号越多，
    平均相似度天然越低，分数就失去了跨快照的可比性）；取最大值更贴近
    "这个候选是不是正好呼应了你正在做的某一件具体的事"这个问题本身。

    `context.is_empty` 或 `text` 为空时返回 `(0.0, None)`——没有任何
    处境信号可比对时，"相关度未知"应该显式表现为 0 分而不是抛异常，
    调用方（`initiative_inbox.py`）不需要额外判空。
    """
    if context.is_empty or not text:
        return 0.0, None
    text_tokens = _tokens(text)
    if not text_tokens:
        return 0.0, None

    best_score = 0.0
    best_signal: Optional[SituationalSignal] = None
    for sig in context.signals:
        score = _jaccard(text_tokens, sig.tokens)
        if score > best_score:
            best_score = score
            best_signal = sig
    return best_score, best_signal
