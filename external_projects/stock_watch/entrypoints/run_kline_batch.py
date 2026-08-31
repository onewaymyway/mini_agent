#!/usr/bin/env python
"""entrypoints/run_kline_batch.py — 功能 2：候选池标的 K 线批量生成。

每天定时为候选池（`data/algo_pool.json` 和 `data/manual_pool.json`）里的
所有标的（股票/ETF 泛指）生成最新 K 线图，分别存到
`reports/kline/algo/<date>/` 和 `reports/kline/manual/<date>/`。
单只标的失败不影响其它标的继续生成，最终按"有多少只成功"判断整体退出码。
"""

from __future__ import annotations

import logging
from datetime import datetime

import _common  # noqa: F401

from stock_watch.candidate_pool import load_pool
from stock_watch.config import (
    ALGO_POOL_PATH,
    DATA_DIR,
    MANUAL_POOL_PATH,
    REPORTS_DIR,
    ensure_dirs,
    load_config,
)
from stock_watch.data_sources import DataSourceError
from stock_watch.kline import plot_kline
from stock_watch.browser_manager import ensure_browser_running

logger = logging.getLogger("stock_watch.kline_batch")


def _generate_klines(
    pool_path,
    out_dir,
    cfg,
    pool_name="候选池",
) -> tuple:
    """为指定池生成 K 线图。

    Returns:
        (ok_count, failed_list, failure_lines)
    """
    pool = load_pool(pool_path)
    if not pool:
        logger.info("%s 为空，跳过 K 线生成", pool_name)
        return 0, [], []

    ok, failed = 0, []
    failure_lines = []
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
            failure_lines.append(f"{entry.name}({entry.code}): {exc}")
        except Exception as exc:  # noqa: BLE001 - 绘图库异常类型不固定，统一兜底不中断批处理
            logger.warning("K 线绘图异常: %s(%s) -> %s", entry.name, entry.code, exc)
            failed.append(entry.code)
            failure_lines.append(f"{entry.name}({entry.code}): {type(exc).__name__}: {exc}")

    logger.info("%s K 线批量生成完成: 成功 %d, 失败 %d, 目录 %s", pool_name, ok, len(failed), out_dir)
    return ok, failed, failure_lines


def main() -> int:
    ensure_dirs()
    cfg = load_config()

    # 确保 CDP 浏览器已启动（K 线默认走 CDP 路径）
    try:
        port, tab_id = ensure_browser_running(port=9333)
        logger.info("CDP 浏览器就绪: 端口=%s 标签=%s", port, tab_id)
    except Exception as exc:
        logger.warning("CDP 浏览器启动失败，将降级到 urllib/akshare: %s", exc)

    today_str = datetime.now().strftime("%Y%m%d")

    # 算法池 K 线图
    algo_out_dir = REPORTS_DIR / "kline" / "algo" / today_str
    algo_ok, algo_failed, algo_failures = _generate_klines(
        ALGO_POOL_PATH, algo_out_dir, cfg, "算法池"
    )

    # 手动池 K 线图
    manual_out_dir = REPORTS_DIR / "kline" / "manual" / today_str
    manual_ok, manual_failed, manual_failures = _generate_klines(
        MANUAL_POOL_PATH, manual_out_dir, cfg, "手动池"
    )

    # 统计汇总
    total_ok = algo_ok + manual_ok
    total_failed = algo_failed + manual_failed
    all_failures = algo_failures + manual_failures

    logger.info(
        "K 线批量生成总完成: 算法池成功 %d/失败 %d, 手动池成功 %d/失败 %d",
        algo_ok, len(algo_failed), manual_ok, len(manual_failed),
    )

    # 只有全军覆没才算失败；部分失败是预期内的正常情况（个别标的当天
    # 停牌/接口临时抖动等），不应该让整批任务标红。
    if total_ok == 0 and total_failed:
        _common.set_run_detail(
            f"所有池 {len(total_failed)} 只标的全部生成失败：\n"
            + "\n".join(all_failures)
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("kline_batch", main))