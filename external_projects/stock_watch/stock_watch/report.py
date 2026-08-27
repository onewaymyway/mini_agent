"""stock_watch/report.py — 报告渲染（Markdown）公共函数。

四个功能各自产出一份 Markdown 报告到 `reports/<子目录>/`，格式统一走
本模块，避免每个 entrypoint 各写一套拼字符串逻辑。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from stock_watch.analysis import StockAnalysis
from stock_watch.candidate_pool import CandidateEntry, StateReturn
from stock_watch.screener import ScreenResult


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def render_candidate_pool_report(
    pool: List[CandidateEntry], out_path: Path, *, generated_at: str
) -> Path:
    lines = [f"# 候选池报告 — {generated_at}", "", f"共 {len(pool)} 只标的\n"]
    lines.append("| 代码 | 名称 | 类型 | 状态 | 分数 | 来源 | 备注 |")
    lines.append("|---|---|---|---|---|---|---|")
    for e in pool:
        lines.append(
            f"| {e.code} | {e.name} | {e.type} | {e.state} | {e.score:.1f} | "
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


def render_outcome_report(
    records,
    bucket_summary: Dict[str, dict],
    out_path: Path,
    *,
    snapshot_date: str,
    generated_at: str,
) -> Path:
    """结果回溯报告（对应 `next_doc/stock_watch_continuous_improvement_plan.md`
    第 3.2 节）：把某天候选池快照 vs 实际涨跌幅的对照结果渲染成 Markdown，
    供人 / review session 快速判断评分逻辑是不是真的有效。
    """
    lines = [
        f"# 结果回溯报告 — 快照日期 {snapshot_date}（生成于 {generated_at}）",
        "",
        "## 按打分区间汇总平均涨跌幅",
        "",
        "| 分数区间 | 样本数 | 平均涨跌幅(%) |",
        "|---|---|---|",
    ]
    for bucket, stats in bucket_summary.items():
        avg = stats["avg_change_pct"]
        avg_str = f"{avg:.2f}" if avg is not None else "（无数据）"
        lines.append(f"| {bucket} | {stats['count']} | {avg_str} |")

    lines.append("")
    lines.append(f"## 明细（{len(records)} 只标的）")
    lines.append("| 代码 | 名称 | 快照分数 | 涨跌幅(%) | 备注 |")
    lines.append("|---|---|---|---|---|")
    for r in records:
        change_str = f"{r.change_pct:.2f}" if r.ok else "（查询失败）"
        note = r.error or ""
        lines.append(f"| {r.code} | {r.name} | {r.score_at_snapshot:.1f} | {change_str} | {note} |")

    return _write(out_path, "\n".join(lines) + "\n")


def render_pool_tracking_report(
    tracked: List[Tuple[CandidateEntry, Optional[float], List[StateReturn], Optional[str]]],
    out_path: Path,
    *,
    generated_at: str,
) -> Path:
    """候选池状态区间跟踪报告（`stock_watch_pool_state_tracking_and_kanban_plan.md`
    阶段2）：每只标的当前状态、当前价格，以及历史每一段状态各自的涨跌幅。

    `tracked` 每项是 `(entry, current_price, state_returns, price_error)`，
    `price_error` 为 `None` 表示本次取价成功，否则是取价失败的原因说明
    （单只失败不影响其它标的继续渲染，与项目既有的容错风格一致）。
    """
    lines = [f"# 候选池状态跟踪报告 — {generated_at}", "", f"共 {len(tracked)} 只标的\n"]
    for entry, current_price, state_returns, price_error in tracked:
        price_str = f"{current_price:.2f}" if current_price is not None else "（取价失败）"
        lines.append(f"## {entry.name}({entry.code}) — 当前状态: {entry.state}，当前价: {price_str}")
        if price_error:
            lines.append(f"> 取价失败: {price_error}")
        lines.append("")
        lines.append("| 状态 | 进入时间 | 进入时价格 | 已持续天数 | 区间涨跌幅(%) | 备注 |")
        lines.append("|---|---|---|---|---|---|")
        for sr in state_returns:
            price_at_entry_str = f"{sr.price_at_entry:.2f}" if sr.price_at_entry is not None else "（无）"
            change_str = f"{sr.change_pct:.2f}" if sr.change_pct is not None else "（无数据）"
            lines.append(
                f"| {sr.state} | {sr.entered_at} | {price_at_entry_str} | "
                f"{sr.days_in_state} | {change_str} | |"
            )
        lines.append("")
    return _write(out_path, "\n".join(lines) + "\n")


def write_pool_tracking_json(
    tracked: List[Tuple[CandidateEntry, Optional[float], List[StateReturn], Optional[str]]],
    out_path: Path,
    *,
    generated_at: str,
) -> Path:
    """结构化产出物，供未来的看板直接读取（阶段4），不强迫看板解析
    Markdown 表格。字段与 `render_pool_tracking_report()` 展示的信息
    一一对应。
    """
    payload = {
        "generated_at": generated_at,
        "entries": [
            {
                "code": entry.code,
                "name": entry.name,
                "type": entry.type,
                "state": entry.state,
                "score": entry.score,
                "current_price": current_price,
                "price_error": price_error,
                "state_returns": [
                    {
                        "state": sr.state,
                        "entered_at": sr.entered_at,
                        "price_at_entry": sr.price_at_entry,
                        "days_in_state": sr.days_in_state,
                        "change_pct": sr.change_pct,
                    }
                    for sr in state_returns
                ],
            }
            for entry, current_price, state_returns, price_error in tracked
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out_path)
    return out_path
