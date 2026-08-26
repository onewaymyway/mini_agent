"""stock_watch/report.py — 报告渲染（Markdown）公共函数。

四个功能各自产出一份 Markdown 报告到 `reports/<子目录>/`，格式统一走
本模块，避免每个 entrypoint 各写一套拼字符串逻辑。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from stock_watch.analysis import StockAnalysis
from stock_watch.candidate_pool import CandidateEntry
from stock_watch.screener import ScreenResult


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def render_candidate_pool_report(
    pool: List[CandidateEntry], out_path: Path, *, generated_at: str
) -> Path:
    lines = [f"# 候选池报告 — {generated_at}", "", f"共 {len(pool)} 只标的\n"]
    lines.append("| 代码 | 名称 | 类型 | 分数 | 来源 | 备注 |")
    lines.append("|---|---|---|---|---|---|")
    for e in pool:
        lines.append(
            f"| {e.code} | {e.name} | {e.type} | {e.score:.1f} | "
            f"{','.join(e.sources)} | {';'.join(e.reasons)[:60]} |"
        )
    return _write(out_path, "\n".join(lines) + "\n")


def render_screener_report(
    results: List[ScreenResult], out_path: Path, *, generated_at: str
) -> Path:
    lines = [f"# 选股结果报告 — {generated_at}", ""]
    for r in results:
        lines.append(f"## {r.query}")
        if not r.ok:
            lines.append(f"> 抓取失败: {r.error}\n")
            continue
        if not r.rows:
            lines.append("（无结果）\n")
            continue
        keys = list(r.rows[0].keys())[:8]  # 只展示前几列，避免表格过宽
        lines.append("| " + " | ".join(keys) + " |")
        lines.append("|" + "---|" * len(keys))
        for row in r.rows[:50]:
            lines.append("| " + " | ".join(str(row.get(k, "")) for k in keys) + " |")
        lines.append("")
    return _write(out_path, "\n".join(lines) + "\n")


def render_analysis_report(analysis: StockAnalysis, out_path: Path) -> Path:
    lines = [
        f"# {analysis.name}({analysis.code}) 综合分析材料",
        f"生成时间: {analysis.generated_at}",
        "",
    ]
    if analysis.errors:
        lines.append("> **部分数据抓取失败，以下分析材料不完整**：")
        for e in analysis.errors:
            lines.append(f"> - {e}")
        lines.append("")

    lines.append(f"## 历史公告（{len(analysis.announcements)} 条）")
    for item in analysis.announcements[:20]:
        title = item.get("公告标题", item.get("标题", ""))
        date = item.get("公告日期", item.get("日期", ""))
        lines.append(f"- [{date}] {title}")
    lines.append("")

    lines.append(f"## 股吧热帖（{len(analysis.guba_posts)} 条）")
    for item in analysis.guba_posts[:30]:
        title = item.get("title", item.get("标题", ""))
        read = item.get("read", item.get("阅读", ""))
        reply = item.get("reply", item.get("评论", ""))
        lines.append(f"- {title} （阅读 {read}，评论 {reply}）")
    lines.append("")

    lines.append(f"## 相关新闻（{len(analysis.news)} 条）")
    for item in analysis.news[:20]:
        title = item.get("新闻标题", item.get("标题", ""))
        date = item.get("发布时间", item.get("日期", ""))
        lines.append(f"- [{date}] {title}")
    lines.append("")

    lines.append(
        "## 综合研判\n\n"
        "（本节留空，由调用方的 mini_agent 会话读取以上结构化材料后，"
        "用 LLM 给出综合研判结论——本报告只负责把材料收集、结构化好。）"
    )
    return _write(out_path, "\n".join(lines) + "\n")
