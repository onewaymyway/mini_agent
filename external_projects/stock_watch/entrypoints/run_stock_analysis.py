#!/usr/bin/env python
"""entrypoints/run_stock_analysis.py — 功能 4：个股综合分析。

用法：
    python entrypoints/run_stock_analysis.py 600519 [贵州茅台]

抓取该标的的历史公告、股吧帖子、相关新闻，生成一份结构化材料报告
（`reports/analysis/<code>_<timestamp>.md`）。报告本身不含"AI 综合
研判"结论——那一步建议由 mini_agent 会话/大管家读取这份材料后用 LLM
生成，见 `stock_watch/report.py` 里"综合研判"小节的说明。
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime

import _common  # noqa: F401

from stock_watch.analysis import collect
from stock_watch.config import REPORTS_DIR, ensure_dirs
from stock_watch.report import render_analysis_report

logger = logging.getLogger("stock_watch.analysis_entry")


def main() -> int:
    if len(sys.argv) < 2:
        logger.error("用法: python entrypoints/run_stock_analysis.py <代码> [名称]")
        return 2

    code = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else code

    ensure_dirs()
    result = collect(code, name)

    out_path = (
        REPORTS_DIR / "analysis" / f"{code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    render_analysis_report(result, out_path)
    logger.info("个股分析材料已生成: %s（%d 处抓取失败）", out_path, len(result.errors))

    # 三类材料全部抓取失败才算本次执行失败；部分失败仍产出可用报告
    # （报告里已如实标注哪些材料缺失），不因局部失败掩盖已有信息。
    all_failed = len(result.errors) >= 3
    return 1 if all_failed else 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("stock_analysis", main, trigger="manual"))
