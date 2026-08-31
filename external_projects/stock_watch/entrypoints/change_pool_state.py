#!/usr/bin/env python
"""entrypoints/change_pool_state.py — 手动变更候选池内某标的的状态。

对应 `next_doc/stock_watch_pool_state_tracking_and_kanban_plan.md` 阶段2：
把某标的从"观察池"标记为"重点关注"/"建议买入"/"已建仓"/"建议卖出"/
"已淘汰"，供人工判断使用，也是未来看板「变更状态」按钮的落地对象。

用法：
    python entrypoints/change_pool_state.py <代码> <新状态> [备注]

`project.yaml` 声明了 `code`/`state`/`note` 三个 `params`，通过 mini_agent
看板「▶️ 手动触发」触发时会渲染成对应输入框，不需要记命令行参数顺序
（与 `run_stock_analysis.py` 现有的参数模式一致）。

与其它 entrypoint 不同的一点：这是本项目唯一"网络取价失败也要保证核心
操作（状态变更）成功"的入口——用户点了按钮就应该看到状态变更生效，
"记录不到当前价格"不该导致操作本身失败，只是这次少一个
`price_at_entry` 数据点（后续每日跟踪任务补不上这个历史点，但不影响
之后的区间跟踪）。
"""

from __future__ import annotations

import logging
import sys

import _common  # noqa: F401

from stock_watch.candidate_pool import POOL_STATES, change_state, load_pool, save_pool
from stock_watch.config import ALGO_POOL_PATH, DATA_DIR, MANUAL_POOL_PATH, ensure_dirs
from stock_watch.data_sources import DataSourceError, fetch_latest_close

logger = logging.getLogger("stock_watch.change_pool_state")


def main() -> int:
    if len(sys.argv) < 3:
        logger.error(
            "用法: python entrypoints/change_pool_state.py <代码> <新状态> [备注]\n可选状态: %s",
            ", ".join(POOL_STATES),
        )
        return 2

    code, new_state = sys.argv[1], sys.argv[2]
    note = sys.argv[3] if len(sys.argv) > 3 else ""

    if new_state not in POOL_STATES:
        logger.error("未知状态 %r，可选值: %s", new_state, ", ".join(POOL_STATES))
        return 2

    ensure_dirs()

    # 先在算法池查找
    algo_pool = load_pool(ALGO_POOL_PATH)
    if code in algo_pool:
        pool = algo_pool
        pool_path = ALGO_POOL_PATH
    else:
        # 再在手动池查找
        manual_pool = load_pool(MANUAL_POOL_PATH)
        if code in manual_pool:
            pool = manual_pool
            pool_path = MANUAL_POOL_PATH
        else:
            logger.error("标的 %s 不在候选池中，无法变更状态（先跑一次 hotlist_scan 或确认代码是否正确）", code)
            return 1

    entry = pool[code]
    price = None
    try:
        price = fetch_latest_close(code, entry.type)
    except DataSourceError as exc:
        logger.warning("取当前价格失败（不影响状态变更本身）: %s", exc)

    change_state(pool, code, new_state, price_at_entry=price, note=note)
    save_pool(pool_path, pool)

    price_str = f"{price:.2f}" if price is not None else "（未取到）"
    logger.info("已将 %s(%s) 状态变更为 %s，当前价 %s", entry.name, code, new_state, price_str)
    return 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("change_pool_state", main))
