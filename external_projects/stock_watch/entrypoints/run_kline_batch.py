#!/usr/bin/env python
"""entrypoints/run_kline_batch.py — 功能 2：候选池标的 K 线批量生成。

每天定时为候选池（`data/candidate_pool.json`）里的所有标的（股票/ETF
泛指）生成最新 K 线图，存到 `reports/kline/<date>/`。单只标的失败不
影响其它标的继续生成，最终按"有多少只成功"判断整体退出码。
"""

from __future__ import annotations

import logging
from datetime import datetime

import _common  # noqa: F401

from stock_watch.candidate_pool import load_pool
from stock_watch.config import DATA_DIR, REPORTS_DIR, ensure_dirs, load_config
from stock_watch.data_sources import DataSourceError
from stock_watch.kline import plot_kline

logger = logging.getLogger("stock_watch.kline_batch")

POOL_PATH = DATA_DIR / "candidate_pool.json"


def main() -> int:
    ensure_dirs()
    cfg = load_config()
    pool = load_pool(POOL_PATH)
    if not pool:
        logger.warning("候选池为空，跳过 K 线生成（先跑 run_hotlist_scan.py 或配置 seeds）")
        return 0

    out_dir = REPORTS_DIR / "kline" / datetime.now().strftime("%Y%m%d")
    ok, failed = 0, []
    for entry in pool.values():
        try:
            plot_kline(
                entry.code, entry.name, entry.type, out_dir,
                days=cfg.kline_days, adjust=cfg.kline_adjust,
            )
            ok += 1
        except DataSourceError as exc:
            logger.warning("K 线生成失败: %s(%s) -> %s", entry.name, entry.code, exc)
            failed.append(entry.code)
        except Exception as exc:  # noqa: BLE001 - 绘图库异常类型不固定，统一兜底不中断批处理
            logger.warning("K 线绘图异常: %s(%s) -> %s", entry.name, entry.code, exc)
            failed.append(entry.code)

    logger.info("K 线批量生成完成: 成功 %d, 失败 %d, 目录 %s", ok, len(failed), out_dir)
    # 只有全军覆没才算失败；部分失败是预期内的正常情况（个别标的当天
    # 停牌/接口临时抖动等），不应该让整批任务标红。
    return 1 if ok == 0 and failed else 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("kline_batch", main))
