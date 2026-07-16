"""
evolution/decision_recall.py — 提案前主动召回相关历史决策

对应《决策/取舍知识提炼计划》5.4 节：这是决策提炼真正的价值出口。没有这一步，
decisions/*.md 记录得再完整也只是存档，不会真正减少重复劳动。

实现方式（复用已有基础设施，不新增检索通路）：
  - 检索直接复用 wiki/search.py::wiki_shelf_search()（三段式：规则粗筛 → 图
    扩展 → 可选 LLM 精排），只把候选限定在 type=decision 的页面。
  - 转换成可注入 prompt 的提醒文本这一步，风格上对齐
    evolution/lesson_to_reminder.py：同样是"结构化知识 → 一段简短的前馈提示
    文字"，但决策召回是按需查询（每次新方案讨论触发一次），不是像 lesson 那样
    离线批量生成 reminder 文件——因为决策召回的触发条件（"即将提出新方案"）
    本质上是语义性的，没有 lesson 场景里"连续失败 N 次"那样可以离线预生成的
    确定性信号，所以设计成同步查询接口，由调用方（agent 在生成新方案之前）
    主动调用，而不是走 ReminderLoader 的文件轮询机制。

用法（供 agent 层在生成架构改动/重构提案之前调用）：

    from mini_agent.evolution.decision_recall import recall_related_decisions

    note = recall_related_decisions(paths, proposal_summary, llm_call=llm_call)
    if note:
        # 把 note 作为一条 reminder 文本注入到即将发送给 LLM 的 system/user 消息里
        ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.parser import WikiPage
from mini_agent.wiki.search import LLMCall, wiki_shelf_search

_SETTLED_HEADER = "以下历史决策与当前讨论的方向相关，可能已经采纳过，请先确认是否要重复论证："
_OVERTURNED_HEADER = "以下方案此前被考虑过又被否决，请先确认新提案是否与被否决的方案相同："

# ── 路径 B：轻量前置门控（对应本轮"C和B"实现计划）─────────────────────────────
# 便宜的规则判断——"这条用户消息像不像是在重新讨论一个方案取舍"——命中才触发
# recall_related_decisions()，避免每轮都做三段式检索（wiki_shelf_search 最后一段
# 可能触发 LLM 精排，成本不低）。宁可漏判（少数该提醒的没提醒），也不要错判
# （把正常讨论误判成"在重新做决定"进而频繁打断），所以关键词故意选得比较窄、
# 偏向方案取舍类的表达，而不是任何提到技术名词的句子都命中。
_GATE_KEYWORDS = (
    "要不要", "换成", "换一种", "换一个", "改成", "改用", "选型", "重构成",
    "重新设计", "以前是不是", "之前是不是", "为什么用", "为什么选", "为什么不用",
    "考虑过", "方案", "取舍", "要不要用", "还是用", "对比一下", "哪个更好",
)


def should_trigger_recall(user_message: str) -> bool:
    """路径 B 的启发式门控：命中任一关键词才认为"值得查一次历史决策"。

    纯字符串包含判断，无正则/无 LLM 调用，成本可忽略。设计成独立的公开函数
    （而不是内联在调用方），方便后续替换成更精细的规则或加一档 LLM 二次确认
    （对齐 CompressConfig.topic_shift_detection 的 off/heuristic/llm 分档思路），
    调用方不需要跟着改。
    """
    if not user_message or not isinstance(user_message, str):
        return False
    text = user_message.strip()
    if not text:
        return False
    return any(kw in text for kw in _GATE_KEYWORDS)


@dataclass
class DecisionRecallResult:
    settled: list[WikiPage] = field(default_factory=list)
    overturned: list[WikiPage] = field(default_factory=list)

    @property
    def has_hits(self) -> bool:
        return bool(self.settled or self.overturned)


def _decision_pages_only(pages: list[WikiPage]) -> list[WikiPage]:
    return [p for p in pages if p.type == "decision"]


def find_related_decisions(
    paths: AgentPaths,
    proposal_text: str,
    *,
    tags: Optional[list[str]] = None,
    k: int = 5,
    llm_call: Optional[LLMCall] = None,
) -> DecisionRecallResult:
    """按新提案主题检索相关的既有决策页，区分 settled（已采纳）与
    overturned（已否决）两类，供调用方分别渲染不同语气的提醒。"""
    result = wiki_shelf_search(paths, proposal_text, tags=tags, k=k, llm_call=llm_call)
    decision_pages = _decision_pages_only(result.pages)

    settled = [p for p in decision_pages if p.status in ("settled", "active", "revisited")]
    overturned = [p for p in decision_pages if p.status == "overturned"]
    return DecisionRecallResult(settled=settled, overturned=overturned)


def _render_page_note(page: WikiPage) -> str:
    # 只截取"问题"/"采纳理由"附近的正文片段，避免整页塞进提醒文字；
    # decision 模板正文结构固定，这里做粗糙的按行截断而非解析 markdown 结构。
    snippet = page.body.strip().replace("\n\n", "\n")
    if len(snippet) > 400:
        snippet = snippet[:400] + " …"
    return f"- 【{page.id}】(status={page.status})\n  {snippet}"


def render_recall_reminder(recall: DecisionRecallResult) -> str:
    """把 DecisionRecallResult 渲染成一段可以直接注入 prompt 的提醒文字。
    没有命中任何决策页时返回空字符串（调用方据此判断是否需要注入）。"""
    if not recall.has_hits:
        return ""

    sections: list[str] = []
    if recall.settled:
        body = "\n".join(_render_page_note(p) for p in recall.settled)
        sections.append(f"{_SETTLED_HEADER}\n{body}")
    if recall.overturned:
        body = "\n".join(_render_page_note(p) for p in recall.overturned)
        sections.append(f"{_OVERTURNED_HEADER}\n{body}")

    return "\n\n".join(sections)


def recall_related_decisions(
    paths: AgentPaths,
    proposal_text: str,
    *,
    tags: Optional[list[str]] = None,
    k: int = 5,
    llm_call: Optional[LLMCall] = None,
) -> str:
    """find_related_decisions() + render_recall_reminder() 的便捷组合，是最
    常用的调用方式。返回空字符串表示没有相关历史决策，调用方不需要注入任何
    提醒。"""
    recall = find_related_decisions(paths, proposal_text, tags=tags, k=k, llm_call=llm_call)
    return render_recall_reminder(recall)


__all__ = [
    "DecisionRecallResult",
    "find_related_decisions",
    "render_recall_reminder",
    "recall_related_decisions",
    "should_trigger_recall",
]
