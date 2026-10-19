"""output_registry/resolve.py — 管理项目根解析与角色判定（S5 抽出，供工具与自动检测共用）。

对应 next_doc/goal_output_project_auto_link_plan.md §4.3 与 §4.2。

这两个函数此前是 tools/output_registry.py 的私有实现；S5 的第二层目录差异检测
需要完全相同的规则，因此抽到这里，避免两处实现逐渐分叉。

纯函数：只接收已解析好的目标树（id → {parent_id, level, title}）与管理根 id 集合，
不做任何 IO。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


def resolve_management_root(
    trigger: str, nodes: dict[str, dict[str, Any]], management_ids: set[str],
) -> Optional[str]:
    """计划 §4.3：优先最近的已标记管理根（含自身），其次最近的 goal 祖先（含自身）。

    两者都找不到返回 None（游离情形，交给巡检）。
    """
    seen: set[str] = set()
    cur: Optional[str] = trigger
    while cur is not None and cur in nodes and cur not in seen:
        seen.add(cur)
        if cur in management_ids:
            return cur
        cur = nodes[cur].get("parent_id")

    seen.clear()
    cur = trigger
    while cur is not None and cur in nodes and cur not in seen:
        seen.add(cur)
        if nodes[cur].get("level") == "goal":
            return cur
        cur = nodes[cur].get("parent_id")
    return None


def auto_role(project_root: Path | str, slug: str, management_id: str) -> str:
    """默认 build；若该项目已被其他管理项目以 build 关联，则为 iterate。拿不准一律 build。"""
    from mini_agent.output_registry.registry import ROLE_BUILD, ROLE_ITERATE, list_links

    for link in list_links(Path(project_root)):
        if (
            link.get("output_id") == slug
            and link.get("role") == ROLE_BUILD
            and link.get("management_id") != management_id
        ):
            return ROLE_ITERATE
    return ROLE_BUILD


__all__ = ["resolve_management_root", "auto_role"]
