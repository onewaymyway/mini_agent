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
    python scripts/cleanup_external_goal_candidates.py [--agent-dir PATH] [--dry-run] [--yes] [--force]

  --agent-dir PATH   .agent 目录所在的工作目录（默认当前目录，即约定的
                      AgentPaths root）。
  --dry-run          只打印将要删除的 Goal/Objective，不实际修改文件。
  --yes              跳过交互确认，直接执行（用于非交互式/CI 场景）。
  --force            [危险] 连同子节点里 source 不是 external_input 的
                      Objective 一起删除（默认不传，见下方"安全性"）。

安全性：
  - [Bugfix] 默认只删除 Goal 本身，以及子节点里 source 也是
    external_input 的 Objective；子节点里 source 是 "user"（用户后来
    手动加的）或其它值（比如 "agent_derived"，daemon 自动拆解产生）的
    Objective 会被保留，只是不再挂在已删除的父 Goal 下面（parent_id 清空），
    并在输出里列出来提醒手动确认。改造前的版本会无条件删除
    `goal.children_ids` 里的所有子节点，不管它们的 source 是什么——这是
    实际发生过的"把用户自己创建的也删除掉"问题的根因，见
    `collect_removal_set()` 的详细说明。
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


def collect_removal_set(backlog: GoalBacklog, stale_goals: list) -> tuple[set[str], list[dict]]:
    """把待删除 Goal 及其"安全"子 Objective 的 id 收集成一个集合。

    [Bugfix] 改造前会无条件把 `goal.children_ids` 里的每一个 id 都塞进
    删除集合——但 `children_ids` 只记录"当前挂在这个 Goal 下面"，不代表
    "这些子节点也是旧版 goal_candidate 落点凭空建出来的"。用户后续可能
    在这个（本来是外部输入创建的）Goal 下面手动加了自己的 Objective
    （`source="user"`），或者 daemon 后来自动给它拆解了子 Objective
    （`source="agent_derived"`，跟 Goal 本身是不是 external_input 无关，
    是 `add_objectives_for_goal()` 拆解时统一打的标签，参见
    `perception/goal_backlog.py`）——这些子节点不是"历史遗留的凭空创建"，
    盲目跟着父 Goal 一起删掉就是本函数改造前的实际行为，也是用户反馈的
    "把自己创建的也删除掉"的根因。

    改造后：只把子节点里 `source == LEGACY_EXTERNAL_GOAL_SOURCE` 的
    Objective（旧版 goal_candidate 落点是否也会给子节点打上这个 source
    不确定，保留这一档以防万一）算作"安全可删"；其余 source（尤其
    `"user"`）一律跳过，返回值里额外带上"被跳过的可疑子节点"列表，供
    调用方打印出来提醒用户手动确认，而不是默默留在数据里也不默默删掉。

    返回 (removal_ids, skipped_children)：
      removal_ids     — 确认可以安全删除的节点 id 集合（含 Goal 本身）。
      skipped_children — 因为 source 不是"安全可删"名单而被跳过的子节点
                         摘要列表，每项 {"id","title","source","parent_title"}。
    """
    removal: set[str] = set()
    skipped: list[dict] = []
    for goal in stale_goals:
        removal.add(goal.id)
        for child_id in goal.children_ids:
            child = backlog._nodes.get(child_id)  # noqa: SLF001 — 脚本内部只读查询
            if child is None:
                # 节点已经不存在（悬挂引用），无需处理，也无需当作"跳过"提醒。
                continue
            if child.source == LEGACY_EXTERNAL_GOAL_SOURCE:
                removal.add(child_id)
            else:
                skipped.append({
                    "id": child_id,
                    "title": child.title,
                    "source": child.source,
                    "parent_title": goal.title,
                })
    return removal, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-dir", type=Path, default=Path.cwd(), help=".agent 所在目录，默认当前目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印不修改")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认")
    parser.add_argument(
        "--force", action="store_true",
        help=(
            "[危险] 连同 source 不是 external_input 的子 Objective 一起删除"
            "（比如用户后来手动加的、或 daemon 自动拆解的子节点）。默认不传"
            "——这些节点会被保留并在输出里列出来，只删掉 Goal 本身和"
            "source=external_input 的子节点，避免误删非『历史遗留凭空创建』的内容。"
        ),
    )
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

    removal_ids, skipped_children = collect_removal_set(backlog, stale_goals)
    if args.force and skipped_children:
        removal_ids |= {c["id"] for c in skipped_children}

    print(f"发现 {len(stale_goals)} 个待清理 Goal（含安全可删子 Objective 共 {len(removal_ids)} 个节点）：")
    for goal in stale_goals:
        child_count = len(goal.children_ids)
        print(f"  - [{goal.id}] {goal.title!r} (status={goal.status}, tags={goal.tags}, 子节点 {child_count} 个)")

    if skipped_children and not args.force:
        print(
            f"\n⚠️  发现 {len(skipped_children)} 个子节点的 source 不是 "
            f"\"{LEGACY_EXTERNAL_GOAL_SOURCE}\"（可能是用户后来手动添加，或 daemon "
            "自动拆解产生的），默认不会被删除，会在父 Goal 删除后变成没有父节点的"
            "独立 Objective（不会丢失，只是看板上不再显示父子缩进关系）："
        )
        for c in skipped_children:
            print(f"  - [{c['id']}] {c['title']!r} (source={c['source']}, 原父 Goal: {c['parent_title']!r})")
        print(
            "  如果确认这些节点也应该一起删除（比如整个 Goal 从来没被真正使用过"
            "），重新运行时加 --force。"
        )
    elif skipped_children and args.force:
        print(f"\n⚠️  --force 已指定，以下 {len(skipped_children)} 个非 external_input 子节点也会被删除：")
        for c in skipped_children:
            print(f"  - [{c['id']}] {c['title']!r} (source={c['source']}, 原父 Goal: {c['parent_title']!r})")

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
