#!/usr/bin/env python
"""entrypoints/manage_pool.py — 手动池管理工具。

支持对手动池（data/manual_pool.json）进行增删改查操作：
- add: 添加标的手动池
- remove: 从手动池移除标的
- list: 列出手动池所有标的
- move: 将算法池标的手动迁移到手动池（保留状态历史）
- stats: 显示双池统计

用法示例：
    python entrypoints/manage_pool.py add 600519 贵州茅台
    python entrypoints/manage_pool.py add 600519 贵州茅台 --type stock --market sh
    python entrypoints/manage_pool.py remove 600519
    python entrypoints/manage_pool.py list
    python entrypoints/manage_pool.py list --state watching
    python entrypoints/manage_pool.py move 000001 平安银行 --note "用户手动加入"
    python entrypoints/manage_pool.py stats

手动池特点：
- 无数量上限
- 不受 score_decay 影响
- 用户可以随时增删改
- 与算法池完全独立
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import Dict

import _common  # noqa: F401

from stock_watch.candidate_pool import (
    DEFAULT_STATE,
    CandidateEntry,
    StateEvent,
    load_pool,
    save_pool,
)
from stock_watch.config import ALGO_POOL_PATH, DATA_DIR, MANUAL_POOL_PATH, ensure_dirs

logger = logging.getLogger("stock_watch.manage_pool")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def cmd_add(args: argparse.Namespace) -> int:
    """添加标的到手动池。"""
    ensure_dirs()
    pool = load_pool(MANUAL_POOL_PATH)

    code = args.code
    name = args.name or code

    # 检查是否已在池中
    if code in pool:
        logger.warning("标的 %s(%s) 已在手动池中，跳过添加", name, code)
        print(f"标的 {code}({name}) 已在手动池中")
        return 0

    # 创建新条目
    now = _now_iso()
    entry = CandidateEntry(
        code=code,
        name=name,
        type=args.type,
        score=0.0,
        sources=[],
        reasons=[],
        first_seen=now,
        last_seen=now,
        state=args.state or DEFAULT_STATE,
        pool_type="manual",
    )
    entry.state_history.append(
        StateEvent(state=entry.state, entered_at=now, note=args.note or "")
    )
    pool[code] = entry

    save_pool(MANUAL_POOL_PATH, pool)
    logger.info("已添加 %s(%s) 到手动池", name, code)
    print(f"✅ 已添加: {name}({code}) 到手动池")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    """从手动池移除标的。"""
    ensure_dirs()
    pool = load_pool(MANUAL_POOL_PATH)

    code = args.code
    if code not in pool:
        logger.error("标的 %s 不在手动池中", code)
        print(f"❌ 错误: 标的 {code} 不在手动池中")
        return 1

    entry = pool[code]
    del pool[code]

    save_pool(MANUAL_POOL_PATH, pool)
    logger.info("已从手动池移除 %s(%s)", entry.name, code)
    print(f"✅ 已从手动池移除: {entry.name}({code})")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """列出手动池标的。"""
    ensure_dirs()
    pool = load_pool(MANUAL_POOL_PATH)

    if not pool:
        print("手动池为空")
        return 0

    # 按状态过滤
    filtered = pool
    if args.state:
        filtered = {code: e for code, e in pool.items() if e.state == args.state}

    if not filtered:
        print("无匹配标的")
        return 0

    # 排序输出
    sorted_entries = sorted(filtered.values(), key=lambda e: e.state, reverse=True)

    print(f"\n📋 手动池（共 {len(sorted_entries)} 只）")
    print("-" * 90)
    print(
        f"{'代码':<10} {'名称':<12} {'类型':<6} {'状态':<12} {'分数':<8} "
        f"{'首次发现':<22} {'最近活跃':<22}"
    )
    print("-" * 90)

    for entry in sorted_entries:
        first_seen = entry.first_seen[:19] if entry.first_seen else "N/A"
        last_seen = entry.last_seen[:19] if entry.last_seen else "N/A"
        print(
            f"{entry.code:<10} {entry.name:<12} {entry.type:<6} {entry.state:<12} "
            f"{entry.score:<8.1f} {first_seen:<22} {last_seen:<22}"
        )

    print("-" * 90)
    return 0


def cmd_move(args: argparse.Namespace) -> int:
    """将算法池标的迁移到手动池。"""
    ensure_dirs()

    algo_pool = load_pool(ALGO_POOL_PATH)
    manual_pool = load_pool(MANUAL_POOL_PATH)

    code = args.code
    # 先从算法池获取名称，如果有的话
    if code in algo_pool:
        name = algo_pool[code].name
    else:
        name = args.name or code

    # 从算法池找到或创建条目
    if code in algo_pool:
        entry = algo_pool[code]
        # 复制到手动池，保留状态历史
        new_entry = CandidateEntry(
            code=entry.code,
            name=entry.name,
            type=entry.type,
            score=0.0,  # 手动池从0开始
            sources=["moved_from_algo"],
            reasons=[args.note] if args.note else [],
            first_seen=entry.first_seen,
            last_seen=_now_iso(),
            state=args.state or DEFAULT_STATE,
            pool_type="manual",
        )
        # 复制状态历史（不含最后一条，最后一条是迁移事件）
        for ev in entry.state_history[:-1]:
            new_entry.state_history.append(
                StateEvent(
                    state=ev.state,
                    entered_at=ev.entered_at,
                    price_at_entry=ev.price_at_entry,
                    note=ev.note,
                )
            )
        # 添加新的状态事件
        new_entry.state_history.append(
            StateEvent(
                state=new_entry.state,
                entered_at=_now_iso(),
                note=f"从算法池迁移，原因: {args.note or '用户手动操作'}",
            )
        )
    else:
        # 算法池不存在，创建新条目
        now = _now_iso()
        new_entry = CandidateEntry(
            code=code,
            name=name,
            type=args.type or "stock",
            score=0.0,
            sources=["manual_add"],
            reasons=[args.note] if args.note else [],
            first_seen=now,
            last_seen=now,
            state=args.state or DEFAULT_STATE,
            pool_type="manual",
        )
        new_entry.state_history.append(
            StateEvent(state=new_entry.state, entered_at=now, note=args.note or "")
        )

    manual_pool[code] = new_entry
    save_pool(MANUAL_POOL_PATH, manual_pool)

    # 从算法池移除
    if code in algo_pool:
        del algo_pool[code]
        save_pool(ALGO_POOL_PATH, algo_pool)

    logger.info("已将 %s(%s) 从算法池迁移到手动池", name, code)
    print(f"✅ 已将 {name}({code}) 迁移到手动池")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    """显示双池统计信息。"""
    ensure_dirs()

    algo_pool = load_pool(ALGO_POOL_PATH)
    manual_pool = load_pool(MANUAL_POOL_PATH)

    algo_max = args.max_size

    print("\n📊 股票池统计")
    print("=" * 60)
    print(f"算法池: {len(algo_pool)} 只 (上限 {algo_max})")
    print(f"手动池: {len(manual_pool)} 只 (无上限)")
    print("-" * 60)

    # 状态分布
    for pool_name, pool in [("algo", algo_pool), ("manual", manual_pool)]:
        if not pool:
            continue
        state_counts: Dict[str, int] = {}
        for entry in pool.values():
            state_counts[entry.state] = state_counts.get(entry.state, 0) + 1

        print(f"\n【{pool_name}池】状态分布:")
        for state, count in sorted(state_counts.items()):
            print(f"  {state}: {count} 只")

    print("=" * 60)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="手动池管理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s add 600519 贵州茅台
  %(prog)s add 000001 平安银行 --type stock --state focused
  %(prog)s remove 600519
  %(prog)s list
  %(prog)s list --state watching
  %(prog)s move 000001 平安银行 --note "技术突破"
  %(prog)s stats
""",
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # add 命令
    add_parser = subparsers.add_parser("add", help="添加标的到手动池")
    add_parser.add_argument("code", help="股票代码，如 600519")
    add_parser.add_argument("name", nargs="?", help="股票名称（可选）")
    add_parser.add_argument("--type", default="stock", help="类型: stock/etf (默认: stock)")
    add_parser.add_argument("--state", default=DEFAULT_STATE, help=f"初始状态 (默认: {DEFAULT_STATE})")
    add_parser.add_argument("--note", default="", help="备注信息")

    # remove 命令
    remove_parser = subparsers.add_parser("remove", help="从手动池移除标的")
    remove_parser.add_argument("code", help="股票代码")

    # list 命令
    list_parser = subparsers.add_parser("list", help="列出手动池标的")
    list_parser.add_argument("--state", help="按状态过滤")

    # move 命令
    move_parser = subparsers.add_parser("move", help="从算法池迁移到手动池")
    move_parser.add_argument("code", help="股票代码")
    move_parser.add_argument("name", nargs="?", help="股票名称（可选）")
    move_parser.add_argument("--type", default="stock", help="类型 (默认: stock)")
    move_parser.add_argument("--state", default=DEFAULT_STATE, help=f"新状态 (默认: {DEFAULT_STATE})")
    move_parser.add_argument("--note", default="", help="迁移原因")

    # stats 命令
    stats_parser = subparsers.add_parser("stats", help="显示双池统计")
    stats_parser.add_argument("--max-size", type=int, default=50, help="算法池上限")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 2

    command_map = {
        "add": cmd_add,
        "remove": cmd_remove,
        "list": cmd_list,
        "move": cmd_move,
        "stats": cmd_stats,
    }

    return command_map[args.command](args)


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("manage_pool", main))
