#!/usr/bin/env python3
"""scripts/cleanup_external_goal_candidates.py — 存量数据清理（P8）

背景：`external_input/policy.py` 的 `goal_candidate` 落点（P5 引入）曾经
直接调用 `GoalBacklog.add_goal(source="external_input", tags=["needs_review",
"external_input"], ...)`，把外部输入凭空变成一个新 Goal。P8 已经移除了这
条落点——外部输入与 Goal/Objective 的关联现在完全交给
`goal_relevance.py::GoalRelevanceEngine` 去关联/挂载到*已有*的 Goal 上，
不再凭空创建新节点。

本脚本一次性清理历史遗留：删除 `goals.json` 里所有 `source ==
"external_input"` 的 Goal 节点，以及这些 Goal 名下的子 Objective（避免
留下没有父节点的孤儿 Objective）。

用法：
    python scripts/cleanup_external_goal_candidates.py [--agent-dir PATH] [--dry-run] [--yes]

  --agent-dir PATH   .agent 目录所在的工作目录（默认当前目录，即约定的
                      AgentPaths root）。
  --dry-run          只打印将要删除的 Goal/Objective，不实际修改文件。
  --yes              跳过交互确认，直接执行（用于非交互式/CI 场景）。

安全性：
  - 写入前会把原始 goals.json 备份为
    `<goals.json>.bak.<unix_timestamp>`，避免误删无法恢复。
  - 复用 `GoalBacklog.save()` 的原子写入（tmp + os.replace），不会因为
    进程中途被杀导致文件损坏。
  - 找不到 goals.json（还没有任何 Goal 数据）时直接退出，不报错。
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

# 允许脚本从仓库根目录之外的 cwd 直接运行：把 src/ 加入 sys.path。
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mini_agent.perception.goal_backlog import GoalBacklog  # noqa: E402
from mini_agent.storage.paths import AgentPaths  # noqa: E402

# 与旧版 policy.py::EXTERNAL_GOAL_SOURCE 保持一致的字面量——该常量已随
# goal_candidate 落点一起从 policy.py 移除，这里不再从那里导入，直接写死
# 这个历史取值，避免脚本反过来依赖一个已经不存在的符号。
LEGACY_EXTERNAL_GOAL_SOURCE = "external_input"


def find_stale_goals(backlog: GoalBacklog) -> list:
    """找出所有 source == LEGACY_EXTERNAL_GOAL_SOURCE 的 Goal 节点
    （level == "goal"，不含其子 Objective——子节点单独处理）。"""
    return [
        node
        for node in backlog.all_nodes()
        if node.level == "goal" and node.source == LEGACY_EXTERNAL_GOAL_SOURCE
    ]


def collect_removal_set(backlog: GoalBacklog, stale_goals: list) -> set[str]:
    """把待删除 Goal 及其全部子 Objective 的 id 收集成一个集合。"""
    removal: set[str] = set()
    for goal in stale_goals:
        removal.add(goal.id)
        for child_id in goal.children_ids:
            removal.add(child_id)
    return removal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-dir", type=Path, default=Path.cwd(), help=".agent 所在目录，默认当前目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印不修改")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认")
    args = parser.parse_args()

    paths = AgentPaths(args.agent_dir)
    backlog = GoalBacklog(paths)
    goals_path = backlog._goals_path  # type: ignore[attr-defined]  # workdir_dir/goals.json，见 GoalBacklog.__init__

    if not goals_path.exists():
        print(f"未找到 {goals_path}，没有任何 Goal 数据，无需清理。")
        return 0

    backlog.load()

    stale_goals = find_stale_goals(backlog)
    if not stale_goals:
        print("没有发现由旧版 goal_candidate 落点创建的 Goal（source=external_input），无需清理。")
        return 0

    removal_ids = collect_removal_set(backlog, stale_goals)

    print(f"发现 {len(stale_goals)} 个待清理 Goal（含子 Objective 共 {len(removal_ids)} 个节点）：")
    for goal in stale_goals:
        child_count = len(goal.children_ids)
        print(f"  - [{goal.id}] {goal.title!r} (status={goal.status}, tags={goal.tags}, 子节点 {child_count} 个)")

    if args.dry_run:
        print("\n--dry-run：未修改任何文件。")
        return 0

    if not args.yes:
        answer = input(f"\n确认删除以上 {len(removal_ids)} 个节点？[y/N] ").strip().lower()
        if answer != "y":
            print("已取消，未修改任何文件。")
            return 1

    backup_path = Path(f"{goals_path}.bak.{int(time.time())}")
    shutil.copy2(goals_path, backup_path)
    print(f"已备份原始数据到 {backup_path}")

    for node_id in removal_ids:
        backlog._nodes.pop(node_id, None)  # noqa: SLF001 — 脚本内部直接操作内存态后统一 save()

    # 清理其余节点里可能残留的、指向已删除 Goal 的 parent_id/children_ids
    # 引用，避免留下悬挂指针。
    for node in backlog.all_nodes():
        if node.parent_id in removal_ids:
            node.parent_id = None
        node.children_ids = [cid for cid in node.children_ids if cid not in removal_ids]

    backlog.save()
    print(f"已删除 {len(removal_ids)} 个节点，goals.json 已更新。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
