"""stock_watch/signals.py — 自主挖掘信号的统一接口。

对应 `next_doc/stock_watch_pool_state_tracking_and_kanban_plan.md` 阶段3：
不再单纯依赖外部网站（问财/股吧热榜等）已经算好的结论，本模块定义
"信号"这个统一形状，`indicators.py`（历史行情技术指标）/
`announcement_signals.py`（公告结构化分类）/ 新闻舆情统计（本模块内
`news_signals`，规则量不大，未独立成文件）各自产出 `Signal` 列表，
最终都合并进候选池的 `score`/`reasons`，与外部网站热度是并行的第二条
打分通路，互不替代——`entry.reasons` 里会同时看到"来自东方财富热榜"
和"自算：MA5/MA20 金叉"这类不同来源的说明，保证可解释、可回溯。

本模块本身不发起网络请求，只做"给定数据（K线/公告/新闻），算出信号"
这一步纯逻辑，方便离线单测；网络抓取仍然是 `data_sources.py` 的职责。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class Signal:
    name: str              # 如 "ma_golden_cross" / "notice_buyback" / "news_volume_spike"
    category: str          # "price" | "announcement" | "news"
    score: float            # 该信号贡献的分数（可正可负）
    reason: str              # 人类可读的解释，写入 CandidateEntry.reasons
    evidence_ref: str = ""   # 可选：指向具体数据点（如公告链接/新闻标题）

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "score": self.score,
            "reason": self.reason,
            "evidence_ref": self.evidence_ref,
        }


@dataclass
class SignalBundle:
    """一次分析产出的全部信号，附带来源统计，方便日志/报告展示。"""

    code: str
    signals: List[Signal] = field(default_factory=list)

    @property
    def total_score(self) -> float:
        return sum(s.score for s in self.signals)

    def by_category(self, category: str) -> List[Signal]:
        return [s for s in self.signals if s.category == category]


def summarize_signal_source(source_name: str, category: str) -> str:
    """给 `CandidateEntry.sources` 用的统一命名：区分"外部网站热度来源"
    与"自算信号来源"，前缀 `signal:` 让候选池报告一眼能看出这条来源是
    自己算的还是转发外部网站的。"""
    return f"signal:{category}:{source_name}"
