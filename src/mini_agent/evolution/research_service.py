"""evolution/research_service.py — 共享调研服务。

[next_doc/initiative_systems_unification_plan.md §4.3 阶段三]

背景：`capability_learning.py` 里已经跑通了一整套"检索 → 合规过滤 → 落地"
的实现（`make_web_search_retriever`/`make_agent_retriever`/
`apply_compliance_filter`），但这些函数的签名都绑死在
`(topic: OutlineTopic, track: CapabilityTrack)` 这两个 capability_learning
私有的数据类型上，`growth_advisor.py` 想复用同一套检索/合规过滤能力时
没有一个不依赖这两个类型的入口。

这里把"真正通用"的部分（怎么调 web_search、怎么起一个只读 SubAgent 做
调研、怎么做句级合规过滤）抽成只依赖"字符串 query/prompt/domain_hint"的
纯函数，不感知 `OutlineTopic`/`CapabilityTrack`/`GrowthCandidate` 中任何
一种具体类型。

**抽取原则（对齐方案 §5/§6"不重写已经跑通的核心算法逻辑"）**：
- 这是**纯抽取**，不是重新实现——下面每个函数的核心逻辑都是从
  `capability_learning.py` 对应函数原样搬过来的（query 拼接方式、
  WebSearchError 兜底、SubAgent 的工具白名单和调用方式、句级过滤的正则
  表达式，全部保持不变），只是把"从 topic/track 对象取字段"这一步换成
  "调用方直接传字符串"。
- `capability_learning.py` 里原有的 `make_web_search_retriever`/
  `make_agent_retriever`/`apply_compliance_filter`/
  `is_disclaimer_required_track` 全部保留，只是内部改成委托给这里的
  通用版本（见各函数改动后的实现），**外部签名和可观察行为完全不变**
  ——不是"废弃旧接口"，两边并存，`capability_learning.py` 继续用它已经
  在用的签名，新的调用方（未来 growth_advisor.py 或其它模块需要检索/
  合规过滤能力时）直接用这里更通用的版本，不需要先构造一个
  `OutlineTopic`/`CapabilityTrack`。
- `growth_advisor.py` 当前的报告生成逻辑（`generate_growth_report()`）
  暂不接入——它已经有一套独立演化了多轮、经过大量测试验证的
  active-search/two-stage prompt 逻辑，"能用就不动"，接入共享服务留给
  后续需要新增调研能力时再做，不做一次性替换（方案 §6）。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.config.models import AppConfig


# ── 合规过滤（原样从 capability_learning.py 搬迁，正则表达式/切句逻辑
#    完全不变，只是把 `track: CapabilityTrack` 换成 `domain_hint: str`）──

_COMPLIANCE_RISKY_PHRASE_PATTERNS = [
    r"建议(买入|卖出|加仓|减仓|做多|做空)",
    r"(现在|目前|近期).{0,6}(应该|可以|值得).{0,4}(买入|卖出|入场|建仓)",
    r"(推荐|首推).{0,4}(买入|买进|加仓)",
    r"目标价.{0,10}(元|美元|港元|\$)",
    r"止损位",
    r"仓位建议",
    r"(强烈)?(推荐|建议).{0,4}(购买|投资).{0,10}(股票|基金|标的)",
]

_COMPLIANCE_DISCLAIMER_DOMAIN_KEYWORDS = [
    "股票", "基金", "投资", "证券", "期货", "外汇", "理财",  # 金融
    "疾病", "诊断", "用药", "药物", "治疗", "病症", "医疗",    # 医疗
    "诉讼", "法律", "合同纠纷", "律师", "法规",              # 法律
]


def _filter_risky_text(text: str) -> tuple[str, bool]:
    if not text:
        return text, False
    sentences = re.split(r"(?<=[。！？\n])", text)
    kept: list[str] = []
    filtered_any = False
    for sent in sentences:
        if not sent.strip():
            continue
        if any(re.search(pat, sent) for pat in _COMPLIANCE_RISKY_PHRASE_PATTERNS):
            filtered_any = True
            continue
        kept.append(sent)
    return "".join(kept).strip(), filtered_any


def is_disclaimer_required_domain(domain_hint: str) -> bool:
    """判断 `domain_hint`（通常是"标题 + 描述 + 标签"拼接成的一段文本）
    是否落在需要 `requires_disclaimer` 标记的专业建议领域（金融/医疗/
    法律等）。规则式关键词匹配，宁可多标注也不漏标注——见
    `capability_learning.is_disclaimer_required_track()` 同款设计取舍。"""
    return any(kw in (domain_hint or "") for kw in _COMPLIANCE_DISCLAIMER_DOMAIN_KEYWORDS)


def filter_compliance_text(
    results: list[dict], *, domain_hint: str = "",
) -> tuple[list[dict], bool, bool]:
    """对检索结果的每条 summary/text 做句级风险过滤。

    返回 (过滤后的 results 副本, 本次是否实际剔除了内容, 是否需要
    requires_disclaimer 标记)。不修改传入的 results。

    `domain_hint`：调用方拼好的一段用于判定专业建议领域的文本（比如
    `capability_learning` 传 `f"{track.title} {track.persona_desc} {track.wiki_tag}"`）。
    """
    filtered_results: list[dict] = []
    any_filtered = False
    for r in results:
        r2 = dict(r)
        for key in ("summary", "text"):
            if r2.get(key):
                cleaned, did_filter = _filter_risky_text(r2[key])
                r2[key] = cleaned
                any_filtered = any_filtered or did_filter
        filtered_results.append(r2)
    requires_disclaimer = is_disclaimer_required_domain(domain_hint) or any_filtered
    return filtered_results, any_filtered, requires_disclaimer


# ── web_search 检索（原样从 make_web_search_retriever 内部搬迁）─────────

def research_via_web_search(
    cfg: "AppConfig", query: str, *, max_results: Optional[int] = None,
    summary_max_chars: Optional[int] = None,
) -> list[dict]:
    """对一个 query 字符串做一次 web_search 检索，返回
    `[{"url": ..., "summary": ...}, ...]`。

    `WebSearchError` 会被捕获并转成空列表——与
    `capability_learning.make_web_search_retriever()` 的既有兜底行为
    一致，调用方应该把空列表当成"没有可用结果"的正常情况处理，而不是
    异常。
    """
    from mini_agent.web_search.base import WebSearchError
    from mini_agent.web_search.factory import create_web_search_provider

    max_results = max(1, max_results if max_results is not None else cfg.capability_learning.max_results_per_topic)
    summary_max_chars = max(
        0, summary_max_chars if summary_max_chars is not None else cfg.capability_learning.summary_max_chars,
    )

    provider = create_web_search_provider(cfg)
    try:
        results = provider.search(query, max_results=max_results)
    except WebSearchError:
        return []
    out: list[dict] = []
    for r in results:
        summary = (r.snippet or r.title or "").strip()
        if summary_max_chars and len(summary) > summary_max_chars:
            summary = summary[:summary_max_chars].rstrip() + "…"
        if not summary:
            continue
        out.append({"url": r.url, "summary": summary})
    return out


# ── 只读 SubAgent 调研（原样从 make_agent_retriever 内部搬迁）───────────

def research_via_agent(
    cfg: "AppConfig", prompt: str, *, task_name: str = "research_service_query",
    max_turns: Optional[int] = None, timeout_seconds: Optional[int] = None,
    allowed_tools: Optional[list[str]] = None, summary_max_chars: Optional[int] = None,
) -> list[dict]:
    """起一个受限工具集的只读 `SubAgent` 完成一轮调研，返回
    `[{"summary": ..., "source": "agent_research"}]`（无有效产出时返回
    空列表）。

    与 `capability_learning.make_agent_retriever()` 内部逻辑一致：
    `auto_approve=True`（无人值守场景不能等交互式审批）、超时/失败/空
    产出统一按"没有可用结果"处理，不向上抛异常中断调用方。

    `allowed_tools` 不传时使用 `capability_learning._AGENT_RETRIEVER_
    ALLOWED_TOOLS` 同一份只读白名单（惰性 import，避免本模块反向依赖
    `capability_learning.py` 造成循环 import——只在真正调用且未显式传
    `allowed_tools` 时才会触发这次 import）。
    """
    from mini_agent.orchestrator.sub_agent import SubAgent
    from mini_agent.orchestrator.task import Task, TaskRecord, TaskStatus

    if allowed_tools is None:
        from mini_agent.evolution.capability_learning import _AGENT_RETRIEVER_ALLOWED_TOOLS
        allowed_tools = list(_AGENT_RETRIEVER_ALLOWED_TOOLS)

    max_turns = max(1, max_turns if max_turns is not None else cfg.capability_learning.agent_retriever_max_turns)
    timeout_seconds = max(
        30, timeout_seconds if timeout_seconds is not None else cfg.capability_learning.agent_retriever_timeout_seconds,
    )
    summary_max_chars = max(
        0, summary_max_chars if summary_max_chars is not None else cfg.capability_learning.summary_max_chars,
    )

    task = Task(
        prompt=prompt, name=task_name, auto_approve=True,
        max_turns=max_turns, allowed_tools=allowed_tools,
    )
    record = TaskRecord(task=task)
    sub = SubAgent(record, cfg)
    try:
        sub.start()
        sub.join(timeout=timeout_seconds)
    except Exception:
        return []

    if record.status != TaskStatus.DONE:
        try:
            sub.cancel()
        except Exception:
            pass
        return []

    output = (record.result.output if record.result else "").strip()
    if not output:
        return []
    if summary_max_chars and len(output) > summary_max_chars:
        output = output[:summary_max_chars].rstrip() + "…"
    return [{"summary": output, "source": "agent_research"}]
