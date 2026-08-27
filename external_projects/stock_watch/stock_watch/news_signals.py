"""stock_watch/news_signals.py — 新闻舆情统计信号（阶段3c）。

对应 `next_doc/stock_watch_pool_state_tracking_and_kanban_plan.md` 阶段
3c：输入 `data_sources.fetch_news()` 已经在抓的新闻列表（字段沿用
akshare `stock_news_em` 的中文列名，标题字段常见"新闻标题"/"标题"），
用简单的关键词库统计正负面词频，以及"近期新闻数量是否明显偏多"这个
更基础的信号——"新闻数量突增"本身就有信息量，不需要精确的情感分析
模型才能起步。

与 `announcement_signals.py` 同样的设计取向：先用规则方法验证"这层
信号有没有用"，效果不够再考虑接入更复杂的情感分析模型。
"""

from __future__ import annotations

from typing import Any, Dict, List

from stock_watch.signals import Signal

_POSITIVE_WORDS = [
    "增长", "上涨", "突破", "中标", "签约", "创新高", "获批", "扩产",
    "订单", "利好", "龙头", "领先",
]
_NEGATIVE_WORDS = [
    "下跌", "亏损", "违规", "处罚", "调查", "诉讼", "下滑", "风险",
    "警示", "停牌", "问询",
]


def _title_of(item: Dict[str, Any]) -> str:
    return str(item.get("新闻标题") or item.get("标题") or "")


def compute_news_signals(
    news: List[Dict[str, Any]], *, volume_threshold: int = 15
) -> List[Signal]:
    """给定一批新闻（通常是 `fetch_news(code, top_n=...)` 的结果，代表
    近期新闻），统计正负面词频差值和新闻总量，产出信号。

    - 新闻数量 >= `volume_threshold`：视为"近期关注度明显偏高"，本身
      是一个中性偏正的信号（不代表利好，只代表"值得进一步关注"，分值
      故意设得比其它信号低）。
    - 正负面词频差值明显（差值 >= 3）：产出对应方向的信号。
    词频统计是粗粒度的启发式方法，不做真正的语义情感分析，容易有噪音
    （比如"风险可控"里含"风险"两个字但整体是正面表述），因此分值本身
    设得比公告/技术指标类信号低，只作为辅助参考。
    """
    signals: List[Signal] = []
    titles = [_title_of(item) for item in news if _title_of(item)]
    if not titles:
        return signals

    if len(titles) >= volume_threshold:
        signals.append(
            Signal(
                name="news_volume_spike",
                category="news",
                score=3.0,
                reason=f"近期相关新闻数量达 {len(titles)} 条，关注度明显偏高",
            )
        )

    pos_hits = sum(1 for t in titles for w in _POSITIVE_WORDS if w in t)
    neg_hits = sum(1 for t in titles for w in _NEGATIVE_WORDS if w in t)
    diff = pos_hits - neg_hits

    if diff >= 3:
        signals.append(
            Signal(
                name="news_sentiment_positive",
                category="news",
                score=min(diff * 1.0, 6.0),
                reason=f"近期新闻正面关键词命中 {pos_hits} 次，负面 {neg_hits} 次，偏正面",
            )
        )
    elif diff <= -3:
        signals.append(
            Signal(
                name="news_sentiment_negative",
                category="news",
                score=max(diff * 1.0, -6.0),
                reason=f"近期新闻正面关键词命中 {pos_hits} 次，负面 {neg_hits} 次，偏负面",
            )
        )
    return signals
