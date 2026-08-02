"""wiki/decision_consumption.py — 决策消费校验器（F1）

背景见 next_doc/system_connectivity_gaps_and_missing_capabilities_plan.md
断点 C1：`history/decision_extraction.py` 把决策沉淀写入
`wiki/decisions/*.md`，但 `role_agents/goal_judge.py` 等判定模块此前从不
反查这些页面——决策库只写不读。

本模块只做两件事，均为只读/追加统计，不修改任何既有判定逻辑：
  1. `find_relevant_decisions()` — 给定当前任务的关键词，检索
     `wiki/decisions/` 下相关的历史决策页，返回摘要供调用方（如
     GoalJudgeAgent）可选地拼进 prompt。
  2. `record_consumption()` / `decision_consumption_rate()` — 记录调用方
     是否真的引用了检索到的决策，供 `sys:wiki_utility_audit` 一类巡检
     job 统计"检索到但未被采纳"的比例。

不做：不引入新的检索算法（复用 `wiki/search.py::wiki_shelf_search()`），
不改变 wiki 页面的存储格式，不强制任何调用方必须使用本模块（可选挂载）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

MAX_SUMMARY_CHARS = 150
DEFAULT_TOP_K = 3

_CONSUMPTION_LOG_SCAN_LIMIT = 500  # decision_consumption_rate() 只扫最近 N 条记录


@dataclass
class RelevantDecision:
    """检索到的一条相关历史决策，供 prompt 拼装使用。"""

    page_id: str
    title: str
    summary: str  # 已截断到 MAX_SUMMARY_CHARS

    def to_prompt_line(self) -> str:
        return f"- [{self.page_id}] {self.title}：{self.summary}"


@dataclass
class DecisionConsumptionQuery:
    """一次检索结果，供调用方决定是否采纳、以及记录消费情况。"""

    decisions: list[RelevantDecision] = field(default_factory=list)
    query: str = ""

    @property
    def has_hits(self) -> bool:
        return bool(self.decisions)

    def to_prompt_block(self) -> str:
        """拼成可直接注入 prompt 的文本块；无命中时返回空字符串。"""
        if not self.decisions:
            return ""
        lines = "\n".join(d.to_prompt_line() for d in self.decisions)
        return (
            "以下是过去针对相关问题已经做出的决策记录，如果当前情况与某条一致，"
            "请直接沿用该决策而不是重新论证；如果情况有变化，请说明为什么这次不同：\n"
            f"{lines}"
        )


def find_relevant_decisions(
    paths: "AgentPaths",
    query: str,
    *,
    k: int = DEFAULT_TOP_K,
) -> DecisionConsumptionQuery:
    """检索与当前任务相关的历史决策页（只读，规则粗筛，不调用 LLM）。

    复用 `wiki/search.py::wiki_shelf_search()`，但限定只看 decisions 目录
    （通过页面 id 前缀/所在目录过滤，不新增检索算法）。wiki 目录不存在或
    零命中时返回空结果，调用方应视为"无相关历史决策"，正常走原有逻辑。
    """
    result = DecisionConsumptionQuery(query=query)
    if not query or not paths.wiki_decisions_dir.exists():
        return result

    try:
        from mini_agent.wiki.search import wiki_shelf_search

        search_result = wiki_shelf_search(paths, query, k=k)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception

        log_exception(_mini_agent_exc, where="mini_agent.wiki.decision_consumption.find_relevant_decisions")
        return result

    for page in search_result.pages:
        page_id = getattr(page, "id", "") or ""
        if "decision" not in page_id and "/decisions/" not in str(getattr(page, "path", "")):
            # 粗筛：wiki_shelf_search 是全库检索，这里只保留看起来是决策页
            # 的结果（id 或路径带 decision 标识），避免把实体页也当决策引用。
            continue
        title = getattr(page, "title", "") or page_id
        body = getattr(page, "summary", "") or getattr(page, "content", "") or ""
        summary = body.strip().replace("\n", " ")[:MAX_SUMMARY_CHARS]
        result.decisions.append(RelevantDecision(page_id=page_id, title=title, summary=summary))
        if len(result.decisions) >= k:
            break

    return result


def _consumption_log_path(paths: "AgentPaths"):
    return paths.wiki_dir / "decision_consumption_log.jsonl"


def record_consumption(
    paths: "AgentPaths",
    query: DecisionConsumptionQuery,
    *,
    referenced_page_ids: list[str],
) -> None:
    """记录一次判定是否真的采纳了检索到的历史决策，供利用率统计使用。

    referenced_page_ids 为空但 query.has_hits 为 True，代表"检索到了但
    判定没有引用"——这类记录本身就是有价值的信号（说明检索质量或判定
    prompt 拼装可能有问题），不是失败，只是如实记录。
    """
    if not query.has_hits:
        return
    try:
        p = _consumption_log_path(paths)
        p.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "query": query.query,
            "retrieved_page_ids": [d.page_id for d in query.decisions],
            "referenced_page_ids": list(referenced_page_ids),
            "consumed": bool(referenced_page_ids),
        }
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception

        log_exception(_mini_agent_exc, where="mini_agent.wiki.decision_consumption.record_consumption")


def decision_consumption_rate(paths: "AgentPaths") -> Optional[dict]:
    """统计最近若干条检索记录里，真正被判定采纳的比例。

    返回 None 表示暂无记录（不代表 0 命中，避免和"利用率为 0"混淆）。
    供 `sys:wiki_utility_audit` 巡检 job 汇总展示，不是独立 cron job。
    """
    p = _consumption_log_path(paths)
    if not p.exists():
        return None
    try:
        lines = p.read_text(encoding="utf-8").splitlines()[-_CONSUMPTION_LOG_SCAN_LIMIT:]
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception

        log_exception(_mini_agent_exc, where="mini_agent.wiki.decision_consumption.decision_consumption_rate")
        return None

    total = 0
    consumed = 0
    for line in lines:
        try:
            rec = json.loads(line)
        except Exception:
            continue
        total += 1
        if rec.get("consumed"):
            consumed += 1

    if total == 0:
        return None
    return {
        "total_retrievals": total,
        "consumed": consumed,
        "consumption_rate": round(consumed / total, 3),
    }
