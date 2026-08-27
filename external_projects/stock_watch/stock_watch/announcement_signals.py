"""stock_watch/announcement_signals.py — 公告结构化分类信号（阶段3b）。

对应 `next_doc/stock_watch_pool_state_tracking_and_kanban_plan.md` 阶段
3b：输入 `data_sources.fetch_announcements()` 已经在抓的公告列表（
`df.to_dict("records")`，字段沿用 akshare `stock_notice_report` 的中文
列名，常见的是"公告标题"/"标题"、"公告日期"/"日期"），用关键词做粗粒度
分类，每类给一个基础分值。

分类规则是启发式关键词匹配，不是 NLP 模型——先用简单规则验证"公告信号
这层有没有用"这个更基础的问题，规则效果不够时再考虑升级为更复杂的
文本分类（呼应 `stock_watch_pool_state_tracking_and_kanban_plan.md`
"先用规则方法验证"的既有设计原则）。

权重可以在 `config/watchlist.yaml` 的 `signals.announcement_weights`
段调整，不用改代码；本模块只定义默认权重和分类规则。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from stock_watch.signals import Signal

# 分类名 -> (关键词列表, 默认分值, 人类可读说明模板)
# 分值正负号体现"通常被市场解读为利好/利空"的方向，仅供参考，不是
# 绝对判断——同一类公告在不同情境下可能被解读为利空（如"业绩预减"）。
_DEFAULT_RULES: List[Dict[str, Any]] = [
    {
        "category": "earnings_beat",
        "keywords": ["业绩预增", "业绩快报", "净利润同比增长", "扭亏为盈"],
        "score": 6.0,
        "desc": "业绩预增/快报类公告",
    },
    {
        "category": "earnings_miss",
        "keywords": ["业绩预减", "业绩下滑", "净利润同比下降", "预计亏损"],
        "score": -6.0,
        "desc": "业绩预减/下滑类公告",
    },
    {
        "category": "buyback",
        "keywords": ["回购股份", "股份回购", "回购公司股份"],
        "score": 5.0,
        "desc": "股份回购类公告",
    },
    {
        "category": "equity_incentive",
        "keywords": ["股权激励", "员工持股计划", "限制性股票激励"],
        "score": 4.0,
        "desc": "股权激励/员工持股类公告",
    },
    {
        "category": "ma_restructure",
        "keywords": ["重大资产重组", "并购重组", "收购资产", "重大合同"],
        "score": 4.0,
        "desc": "并购重组/重大合同类公告",
    },
    {
        "category": "risk_warning",
        "keywords": ["风险提示", "退市风险", "立案调查", "违规处罚", "问询函"],
        "score": -5.0,
        "desc": "风险提示/监管问询类公告",
    },
    {
        "category": "shareholder_reduction",
        "keywords": ["股东减持", "减持计划", "大股东减持"],
        "score": -4.0,
        "desc": "股东减持类公告",
    },
]


def _title_of(item: Dict[str, Any]) -> str:
    return str(item.get("公告标题") or item.get("标题") or "")


def classify_announcements(
    announcements: List[Dict[str, Any]],
    *,
    weights: Optional[Dict[str, float]] = None,
    max_signals_per_category: int = 1,
) -> List[Signal]:
    """对一批公告做关键词分类，每个命中的分类最多产出
    `max_signals_per_category` 条信号（默认1条，避免同一类公告刷多条
    近乎重复的信号，稀释其它维度的分数权重）。

    `weights` 允许按分类名覆盖默认分值（对应
    `config/watchlist.yaml::signals.announcement_weights`），不传则用
    `_DEFAULT_RULES` 里的默认值。
    """
    weights = weights or {}
    signals: List[Signal] = []
    hits_per_category: Dict[str, int] = {}

    for item in announcements:
        title = _title_of(item)
        if not title:
            continue
        for rule in _DEFAULT_RULES:
            category = rule["category"]
            if hits_per_category.get(category, 0) >= max_signals_per_category:
                continue
            if any(kw in title for kw in rule["keywords"]):
                score = weights.get(category, rule["score"])
                signals.append(
                    Signal(
                        name=f"announcement_{category}",
                        category="announcement",
                        score=score,
                        reason=f"{rule['desc']}：{title[:40]}",
                        evidence_ref=title,
                    )
                )
                hits_per_category[category] = hits_per_category.get(category, 0) + 1
    return signals
