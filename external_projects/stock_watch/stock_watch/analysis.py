"""stock_watch/analysis.py — 单个标的综合分析（功能 4）。

抓取公告、股吧帖子、相关新闻三类信息，拼成一份结构化 `StockAnalysis`，
不在本模块内做"AI 综合研判"——那一步交给调用 entrypoint 的 mini_agent
会话/大管家用 LLM 去读这份结构化材料再给结论，本模块只负责"把材料
收集齐、结构化好"，保持职责单一、可离线单测。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List

from stock_watch.data_sources import (
    DataSourceError,
    fetch_announcements,
    fetch_guba_posts,
    fetch_news,
)

logger = logging.getLogger("stock_watch.analysis")


@dataclass
class StockAnalysis:
    code: str
    name: str
    generated_at: str
    announcements: List[Dict[str, Any]] = field(default_factory=list)
    guba_posts: List[Dict[str, Any]] = field(default_factory=list)
    news: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def collect(code: str, name: str = "") -> StockAnalysis:
    result = StockAnalysis(
        code=code, name=name,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    try:
        result.announcements = fetch_announcements(code).to_dict("records")
    except DataSourceError as exc:
        logger.warning("公告抓取失败(%s): %s", code, exc)
        result.errors.append(f"announcements: {exc}")

    try:
        result.guba_posts = fetch_guba_posts(code)
    except DataSourceError as exc:
        logger.warning("股吧帖子抓取失败(%s): %s", code, exc)
        result.errors.append(f"guba_posts: {exc}")

    try:
        result.news = fetch_news(code).to_dict("records")
    except DataSourceError as exc:
        logger.warning("新闻抓取失败(%s): %s", code, exc)
        result.errors.append(f"news: {exc}")

    return result
