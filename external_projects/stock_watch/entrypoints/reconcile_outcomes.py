#!/usr/bin/env python
"""entrypoints/reconcile_outcomes.py — 结果回溯任务。

对应 `next_doc/stock_watch_continuous_improvement_plan.md` 第 3.2 节：
拿 `outcomes.lookback_days` 天前的候选池归档快照（见
`stock_watch/candidate_pool.py::save_pool_snapshot`），核对这些标的到
今天为止的实际涨跌幅，写进 `data/outcome_ledger.jsonl`，并渲染一份
Markdown 报告。涨跌幅超过 `outcomes.notable_gain_pct` 阈值的案例，额外
记进改进积压账本（不管是"打了高分确实大涨"还是"打了高分结果大跌"，
都是值得被下一轮优化 review 看到的证据）。

单只标的查询失败（停牌/退市/接口抖动）不影响其它标的继续查，最终按
"有多少只查到了结果"判断整体退出码。若当天没有对应的归档快照（比如
项目刚上线不满 lookback_days 天），直接跳过，不算失败——这是预期内的
冷启动状态，不是错误。

    python entrypoints/reconcile_outcomes.py
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import _common  # noqa: F401

from stock_watch.candidate_pool import load_pool_snapshot
from stock_watch.config import (
    DATA_DIR,
    OUTCOME_LEDGER_PATH,
    POOL_SNAPSHOTS_DIR,
    REPORTS_DIR,
    ensure_dirs,
    load_config,
)
from stock_watch.data_sources import DataSourceError, fetch_price_change_pct
from stock_watch.outcomes import (
    append_outcomes,
    build_outcome_records,
    notable_outcomes,
    summarize_by_score_bucket,
)
from stock_watch.report import render_outcome_report

logger = logging.getLogger("stock_watch.reconcile_outcomes")


def main() -> int:
    ensure_dirs()
    cfg = load_config()

    snapshot_date = (
        datetime.now(timezone.utc) - timedelta(days=cfg.outcome_lookback_days)
    ).strftime("%Y%m%d")
    snapshot = load_pool_snapshot(POOL_SNAPSHOTS_DIR, snapshot_date)
    if not snapshot:
        logger.info(
            "没有找到 %s 的候选池归档快照（可能项目刚上线不满 %d 天），跳过本次回溯",
            snapshot_date, cfg.outcome_lookback_days,
        )
        return 0

    end_date = datetime.now(timezone.utc).strftime("%Y%m%d")
    change_pcts: dict = {}
    errors: dict = {}
    for code, entry in snapshot.items():
        try:
            change_pcts[code] = fetch_price_change_pct(
                code, entry.type, snapshot_date, end_date,
            )
        except DataSourceError as exc:
            logger.warning("回溯 %s(%s) 失败: %s", entry.name, code, exc)
            errors[code] = str(exc)

    records = build_outcome_records(
        snapshot, change_pcts, errors, snapshot_date=snapshot_date,
    )
    append_outcomes(OUTCOME_LEDGER_PATH, records)

    bucket_summary = summarize_by_score_bucket(records)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out_path = REPORTS_DIR / "outcomes" / f"{snapshot_date}_reconciled_{end_date}.md"
    render_outcome_report(
        records, bucket_summary, out_path,
        snapshot_date=snapshot_date, generated_at=generated_at,
    )
    logger.info("结果回溯报告已生成: %s（%d 只标的，%d 只查询失败）", out_path, len(records), len(errors))

    for item in notable_outcomes(records, threshold_pct=cfg.outcome_notable_gain_pct):
        direction = "大涨" if item.change_pct > 0 else "大跌"
        _common.append_backlog(
            f"{item.name}({item.code}) 在候选池快照分数 {item.score_at_snapshot:.1f}，"
            f"{cfg.outcome_lookback_days} 天后{direction} {item.change_pct:.1f}%，"
            "值得核对评分/来源逻辑是否需要调整",
            source="outcome_review",
            evidence_ref=str(out_path),
        )

    ok_count = sum(1 for r in records if r.ok)
    return 0 if ok_count > 0 or not records else 1


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("reconcile_outcomes", main))
