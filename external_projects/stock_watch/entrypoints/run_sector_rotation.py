#!/usr/bin/env python
"""entrypoints/run_sector_rotation.py — 板块轮动分析。

查询今日各行业板块的涨跌幅排名、强势/弱势板块以及资金流向，
生成板块轮动分析报告，帮助识别当前市场主线和轮动特征。

    python entrypoints/run_sector_rotation.py                # 分析今日板块轮动
    python entrypoints/run_sector_rotation.py --days 5        # 近5日轮动趋势
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import _common  # noqa: F401

from stock_watch.config import REPORTS_DIR, ensure_dirs, load_config
from stock_watch.data_sources import set_iwencai_cookie, fetch_sector_rotation_analysis, fetch_sector_performance
from stock_watch.screener import ScreenResult, run_queries

logger = logging.getLogger("stock_watch.sector_rotation")


def render_sector_report(
    strong: list,
    weak: list,
    flow: list,
    top_sectors: list,
    out_path: Path,
    *,
    generated_at: str,
) -> None:
    """渲染板块轮动分析报告。"""
    lines = [
        f"# 板块轮动分析报告 — {generated_at}",
        "",
        "## 一、今日强势板块（涨幅前10）",
        "",
    ]
    if strong:
        lines.append("| 排名 | 板块 | 涨跌幅 |")
        lines.append("|---|---|---|")
        for i, s in enumerate(strong[:10], 1):
            lines.append(f"| {i} | {s['sector']} | {s['change_pct']:+.2f}% |")
    else:
        lines.append("暂无数据\n")

    lines += [
        "",
        "## 二、今日弱势板块（跌幅前10）",
        "",
    ]
    if weak:
        lines.append("| 排名 | 板块 | 涨跌幅 |")
        lines.append("|---|---|---|")
        for i, s in enumerate(weak[:10], 1):
            lines.append(f"| {i} | {s['sector']} | {s['change_pct']:+.2f}% |")
    else:
        lines.append("暂无数据\n")

    lines += [
        "",
        "## 三、主力资金流向（净流入前10）",
        "",
    ]
    if flow:
        lines.append("| 排名 | 板块 | 主力净流入（亿元） |")
        lines.append("|---|---|---|")
        for i, s in enumerate(flow[:10], 1):
            lines.append(f"| {i} | {s['sector']} | {s['net_inflow']:+.2f} |")
    else:
        lines.append("暂无数据\n")

    lines += [
        "",
        "## 四、板块涨幅排行榜",
        "",
    ]
    if top_sectors:
        lines.append("| 排名 | 板块 | 涨跌幅 |")
        lines.append("|---|---|---|")
        for i, s in enumerate(top_sectors[:30], 1):
            lines.append(f"| {i} | {s['sector']} | {s['change_pct']:+.2f}% |")
    else:
        lines.append("暂无数据\n")

    # 轮动特征分析
    lines += ["", "## 五、轮动特征观察", ""]
    if strong and weak:
        lines.append(f"- **强势板块**：{', '.join(s['sector'] for s in strong[:5])}")
        lines.append(f"- **弱势板块**：{', '.join(s['sector'] for s in weak[:5])}")
        lines.append(f"- **市场特征**：{'成长风格占优' if any('科技' in s or '电子' in s or '半导体' in s for s in strong) else '价值风格占优'}")

    lines += ["", "---", f"\n报告生成时间：{generated_at}"]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="板块轮动分析")
    parser.add_argument("--days", type=int, default=1, help="分析周期（默认1天）")
    args = parser.parse_args()

    ensure_dirs()
    cfg = load_config()
    set_iwencai_cookie(cfg.iwencai_cookie)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    date_str = datetime.now().strftime("%Y%m%d")
    out_path = REPORTS_DIR / "sector_rotation" / f"{date_str}.md"

    logger.info("开始获取板块轮动数据...")

    # 获取板块轮动分析
    rotation_data = fetch_sector_rotation_analysis()
    strong = rotation_data.get("strong_sectors", [])
    weak = rotation_data.get("weak_sectors", [])
    flow = rotation_data.get("capital_flow", [])

    # 获取完整板块排名
    top_sectors = fetch_sector_performance(top_n=50)

    # 渲染报告
    render_sector_report(strong, weak, flow, top_sectors, out_path, generated_at=generated_at)

    logger.info("板块轮动分析完成，报告: %s", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("sector_rotation", main))
