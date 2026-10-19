"""output_registry/auto_detect.py — 第二层：执行后目录差异检测（S5，补救层）。

对应 next_doc/goal_output_project_auto_link_plan.md §5 与 §12-3 决议。

用途：agent 漏调用 link_output_project（S2）时，依靠"执行前后 output_projects_root
一级目录的差异"补救——本轮新增的目录进入待确认账本，origin=auto_detect，由用户确认。

约束（均来自计划）：
  1. 首版只处理**新增目录**，不处理"已有目录被本轮修改"（§12-3 决议）；
  2. 只登记（register_project）+ 写待确认账本，不建立正式关联，不改变路由；
  3. 去重完全交给 add_pending：已有正式关联 → confirmed；已有待确认 → existing；
     已被忽略 → rejected。因此与第一层工具并存时不会重复提示；
  4. 管理根解析与第一层相同（resolve_management_root），找不到则跳过，交给巡检；
  5. 整个检测对执行结果零影响：调用方必须在 try/except 中调用，本模块内部也只记日志。

已知边界（写入实施记录）：
  - 多个 goal 并行执行时，同一时间段新增的目录可能被误归到当前 goal；这由待确认环节兜底；
  - 检测依赖执行开始时的快照，守护进程重启后内存快照丢失，该次执行不做检测（不会误报）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mini_agent.evolution.output_projects_root import resolve_root

_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
DETECT_NOTE = "执行后检测到本轮新增的产出目录（agent 未主动关联）"


def snapshot_project_names(project_root: Path | str) -> set[str]:
    """output_projects_root 下一级目录名集合（排除 _ 与 . 开头的内部目录）。"""
    root = resolve_root(Path(project_root))
    if not root.is_dir():
        return set()
    return {
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and not entry.name.startswith(("_", "."))
    }


def detect_new_project_links(
    project_root: Path | str,
    *,
    before: set[str],
    trigger_node_id: str,
) -> list[dict[str, Any]]:
    """对比快照，把本轮新增且合法的目录写入待确认账本。返回每个新目录的处理结果。

    返回 [{"slug": str, "outcome": "created"|"existing"|"rejected"|"confirmed"}, ...]；
    无新增、无法解析管理根或目标树不可读时返回空列表。
    """
    from mini_agent.output_registry.pending_links import ORIGIN_AUTO_DETECT, add_pending, read_goal_nodes
    from mini_agent.output_registry.registry import RegistryError, load_index, register_project
    from mini_agent.output_registry.resolve import auto_role, resolve_management_root

    project_root = Path(project_root)
    new_slugs = sorted(snapshot_project_names(project_root) - set(before))
    if not new_slugs:
        return []

    nodes = read_goal_nodes(project_root)
    if nodes is None:
        return []
    management_ids = set(load_index(project_root).get("management_roots", {}))
    management_id = resolve_management_root(trigger_node_id, nodes, management_ids)
    if management_id is None:
        return []

    results: list[dict[str, Any]] = []
    for slug in new_slugs:
        if not _SLUG_RE.match(slug):
            continue
        try:
            register_project(project_root, slug)
            role = auto_role(project_root, slug, management_id)
            res = add_pending(
                project_root,
                slug=slug,
                goal_id=trigger_node_id,
                management_id=management_id,
                role=role,
                origin=ORIGIN_AUTO_DETECT,
                note=DETECT_NOTE,
                valid_goal_ids=nodes.keys(),
            )
        except RegistryError:
            # 例如目录在检测与登记之间被删除：跳过该目录，不影响其他目录
            continue
        results.append({"slug": slug, "outcome": res["outcome"]})
    return results


__all__ = ["snapshot_project_names", "detect_new_project_links", "DETECT_NOTE"]
