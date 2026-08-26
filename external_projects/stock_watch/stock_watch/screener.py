"""stock_watch/screener.py — 条件选股（功能 3）。

设计取舍：不自己重新实现"探底回升""放量上涨"这类技术形态的判定逻辑，
而是直接复用同花顺问财（iwencai）等网站已经做好的自然语言选股能力，
把查询语句发过去、拿回结果表——这类网站的选股引擎经过长期打磨，自己
重新实现技术指标引擎投入产出比很低，也是用户在需求里明确提到的思路。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List

from stock_watch.data_sources import DataSourceError, fetch_iwencai_screener

logger = logging.getLogger("stock_watch.screener")


@dataclass
class ScreenResult:
    query: str
    rows: List[Dict[str, Any]]
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def run_query(query: str, top_n: int = 100) -> ScreenResult:
    try:
        rows = fetch_iwencai_screener(query, top_n=top_n)
        return ScreenResult(query=query, rows=rows)
    except DataSourceError as exc:
        logger.warning("选股查询失败: %s -> %s", query, exc)
        return ScreenResult(query=query, rows=[], error=str(exc))


def run_queries(queries: List[str], top_n: int = 100) -> List[ScreenResult]:
    return [run_query(q, top_n=top_n) for q in queries]
