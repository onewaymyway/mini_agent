#!/usr/bin/env python
"""entrypoints/knowledge.py — 跨实例知识库的写入来源查看与撤销（第二十二轮 P9）。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §10 P9；
核心逻辑在 `world_simulator/knowledge_base.py`（`summarize_sources` / `retract_instance`）。

子命令：
  list                               各实例往知识库写了什么（未来树校准条目 / 树声明条目 /
                                     旧来源条目里引用了该实例的次数）。
  retract <sim_id> [--include-legacy] [--dry-run]
                                     撤销某实例的写入：
                                     · 总是移除该实例的 P9 条目（校准、树声明）；
                                     · `--include-legacy` 另外移除"确认只由该实例产生"的旧来源条目；
                                     · 与别的实例合并过计数的旧来源条目**不动**，只报告数量；
                                     · `--dry-run` 只看会发生什么，不落盘。

只读/写的都是 `data/_knowledge/causal_knowledge.jsonl`，不碰任何实例自己的数据。
"""

from __future__ import annotations

import argparse
import json
from typing import List, Optional

import _common  # noqa: F401

from world_simulator import knowledge_base as kb
from world_simulator.config import DATA_DIR


def _cmd_list() -> int:
    sources = kb.summarize_sources(DATA_DIR)
    if not sources:
        print("知识库里没有任何实例的写入记录。")
        return 0
    print(f"{'实例':<36} {'校准条目':>8} {'树声明条目':>10} {'旧来源引用':>10}")
    for sim_id, row in sorted(sources.items()):
        print(
            f"{sim_id:<36} {row[kb.CALIBRATION_ORIGIN]:>8} "
            f"{row[kb.TREE_DECLARATION_ORIGIN]:>10} {row['shared_refs']:>10}"
        )
    print("\n旧来源引用 = 因果链沉淀/边兑现回写留下的来源引用；其中与别的实例合并过计数的无法精确回退"
          "（见 retract 的 left_shared）。")
    return 0


def _cmd_retract(sim_id: str, include_legacy: bool, dry_run: bool) -> int:
    result = kb.retract_instance(DATA_DIR, sim_id, include_legacy=include_legacy, dry_run=dry_run)
    prefix = "[dry-run，未落盘] " if dry_run else ""
    print(
        f"{prefix}实例 {sim_id}：移除 P9 条目 {len(result['removed_p9'])} 条，"
        f"移除旧来源独占条目 {len(result['removed_legacy'])} 条，"
        f"保留的合并条目（含该实例的计数，无法精确回退）{result['left_shared']} 条。"
    )
    if result["left_shared"] and not include_legacy:
        print("提示：加 --include-legacy 可移除“确认只由该实例产生”的旧来源条目；合并条目仍不会被动。")
    print(json.dumps(result, ensure_ascii=False))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="各实例往知识库写了什么")
    p_retract = sub.add_parser("retract", help="撤销某实例的写入")
    p_retract.add_argument("sim_id")
    p_retract.add_argument("--include-legacy", action="store_true")
    p_retract.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "list":
        return _cmd_list()
    return _cmd_retract(args.sim_id, args.include_legacy, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
