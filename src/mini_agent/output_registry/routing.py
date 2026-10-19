"""output_registry/routing.py — P4 产出路由：目标 → 绑定的产出项目 output/ 目录。

对应 next_doc/management_output_project_model_plan.md 第 8.3 节与第 9 章 P4。

规则：
  - 沿目标树从当前节点向上查找，第一个有 build 或 iterate 关联的管理项目生效；
    当前节点本身也参与查找（objective 可以直接挂在某个管理项目之下）；
  - 同一管理项目上 build 优先于 iterate；
  - 只有 reference 关联的管理项目不参与路由（reference 只读，不产出）；
  - 找到后返回 <output_projects_root>/<slug>/output；
  - 任何异常（goals.json 损坏、注册表缺失等）都返回 None，由调用方退回默认路径。
    路由绝不能因为注册表问题而让执行失败。

本模块只读：不写 goals.json，不写注册表。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from mini_agent.evolution.output_projects_root import resolve_root

_ROUTING_ROLES = ("build", "iterate")


def _read_parent_map(goals_path: Path) -> dict[str, Optional[str]]:
    """只读解析 goals.json 的 id → parent_id 映射。不调用 GoalBacklog.load()。"""
    data = json.loads(goals_path.read_text(encoding="utf-8"))
    out: dict[str, Optional[str]] = {}
    for g in data.get("goals", []) or []:
        if isinstance(g, dict) and "id" in g:
            out[g["id"]] = g.get("parent_id")
    return out


def _management_routes(project_root: Path) -> dict[str, tuple[str, str]]:
    """management_id → (slug, role)。同一管理项目多个关联时 build 优先。"""
    from mini_agent.output_registry.registry import list_links

    best: dict[str, tuple[str, str]] = {}
    for link in list_links(project_root):
        role = link.get("role")
        if role not in _ROUTING_ROLES:
            continue
        mid = link["management_id"]
        current = best.get(mid)
        if current is None or (role == "build" and current[1] != "build"):
            best[mid] = (link["output_id"], role)
    return best


def resolve_bound_output_dir(project_root: Path | str, goal_id: str) -> Optional[Path]:
    """返回目标绑定的产出项目 output/ 目录；未绑定或出错时返回 None。"""
    try:
        project_root = Path(project_root)
        goals_path = project_root / ".agent" / "goals.json"
        if not goals_path.is_file() or not goal_id:
            return None
        parents = _read_parent_map(goals_path)
        if goal_id not in parents:
            return None
        routes = _management_routes(project_root)
        if not routes:
            return None

        seen: set[str] = set()
        current: Optional[str] = goal_id
        while current is not None and current not in seen:
            seen.add(current)
            if current in routes:
                slug, _role = routes[current]
                return resolve_root(project_root) / slug / "output"
            current = parents.get(current)
        return None
    except Exception:  # noqa: BLE001 — 路由失败必须退回默认路径，不能影响执行
        return None
