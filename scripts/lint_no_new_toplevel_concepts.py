#!/usr/bin/env python3
"""scripts/lint_no_new_toplevel_concepts.py — Sprint 0"冻结新增一级概念"检查脚本。

背景（`next_doc/refactor_plan/01-evaluation-and-gaps.md` 指出的结构性缺口）：
旧架构里"能力堆叠"的典型表现之一，是不断在 `src/mini_agent/` 顶层新增
`XxxManager` / `XxxScheduler` / `XxxAdvisor` 这类"新一级概念"模块，
导致概念数量失控。重构期间（架构收敛完成前），先冻结这类新增，逼着新功能
先考虑"能不能放进已有概念里"，而不是默认新开一个。

规则：
  - 扫描 `src/mini_agent/` 的**直接子项**（一级文件 / 一级目录），
    文件名或目录名（不含扩展名，大小写不敏感）以
    `manager` / `scheduler` / `advisor` 结尾的，视为违规。
  - 白名单里的名字（历史遗留，重构前就存在）不报错。
  - 只检查一级，不递归检查子包内部（子包内部叫什么名字不属于本规则
    要冻结的范围，比如 `role_agents/` 目录内部随便叫什么都不受限）。

用法：
  python scripts/lint_no_new_toplevel_concepts.py          # 检查，退出码非 0 则失败
  python scripts/lint_no_new_toplevel_concepts.py --root .. # 自定义仓库根目录

集成方式（当前仓库还没有 CI 配置文件，本脚本先作为本地 / pre-commit 可
运行的检查器交付；一旦仓库接入 CI，直接在流水线里加一步
`python scripts/lint_no_new_toplevel_concepts.py` 即可拦截违规 PR，
见 `next_doc/refactor_plan/README.md` 及 `docs/architecture_v2/00-overview.md`
的说明）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 违禁后缀（不含大小写）。
FORBIDDEN_SUFFIXES = ("manager", "scheduler", "advisor")

# 白名单：重构冻结规则生效前就已存在的顶层模块，允许保留，
# 但不允许新增同类命名的其它模块。每一项都应说明来源，方便日后 Phase 10
# （legacy 降级）阶段核对是否需要连带处理。
WHITELIST = {
    # 早于本次重构、且不属于本次冻结范围内新建的历史模块。
    "history_manager.py",
}


def find_violations(src_root: Path) -> list[str]:
    """返回违规的顶层文件/目录名（相对 src_root 的一级名字）列表。"""
    violations: list[str] = []
    if not src_root.is_dir():
        return violations

    for child in sorted(src_root.iterdir()):
        if child.name.startswith("_") or child.name.startswith("."):
            continue
        stem = child.stem if child.is_file() else child.name
        if child.is_file() and child.suffix != ".py":
            continue
        if stem.lower().endswith(FORBIDDEN_SUFFIXES) and child.name not in WHITELIST:
            violations.append(child.name)
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--root", default=None,
        help="仓库根目录（其下应有 src/mini_agent），默认自动推断为本脚本所在仓库根目录",
    )
    args = parser.parse_args()

    repo_root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    src_root = repo_root / "src" / "mini_agent"

    violations = find_violations(src_root)
    if violations:
        print("[lint_no_new_toplevel_concepts] 发现新增违规的一级概念模块（禁止 "
              f"Manager/Scheduler/Advisor 后缀，除非加入白名单）：", file=sys.stderr)
        for v in violations:
            print(f"  - src/mini_agent/{v}", file=sys.stderr)
        print(
            "\n如果这是必要的新增，请在 CONTRIBUTING.md 描述的评审流程中说明理由，"
            "评审通过后把名字加入 scripts/lint_no_new_toplevel_concepts.py 的 WHITELIST，"
            "并在 PR 描述里链接到评审记录。否则请把功能归并到已有概念（如 core/ 里"
            "对应的 Goal/Experience/Action 等）中实现。",
            file=sys.stderr,
        )
        return 1

    print("[lint_no_new_toplevel_concepts] 未发现违规的新增一级概念模块。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
