"""output_registry/tree_links.py — 目标树批量关联视图（S4 后端）。

对应 next_doc/goal_output_project_auto_link_plan.md §6.3 与 §7。

一次性计算出整棵目标树上每个节点的关联信息，供看板目标树渲染徽标与
「🔗 关联项目」子区块使用，避免递归渲染时逐节点请求（吸取 N+1 的教训）。

返回结构：
    {
      "by_node": {
        <node_id>: {
          "is_management_root": bool,
          "linked": [ {slug, role, latest_version, output_dir} ... ],   # 生效中的正式关联（含继承）
          "inherited_from": <management_root_id> | None,                # 非空表示继承自祖先管理根
          "pending": [ {id, slug, role, origin, note} ... ],            # 待确认（仅挂在管理根上）
        }, ...
      },
      "pending_total": int,
    }

规则：
  - 节点的 linked 取"自身或最近的已标记管理根祖先"上的正式关联（与路由 §4.3 的查找方向一致）；
    若取自祖先，inherited_from 指向该祖先，界面据此显示为只读；
  - pending 只挂在 management_id 对应的节点上：待确认是对"管理项目"的建议，确认入口也在那里；
  - 只输出"有关联或有待确认或是管理根"的节点，减小响应体积。

本模块只读：不写 goals.json，不写注册表与账本。goals.json 不可读时返回空结构并带 error 字段。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mini_agent.evolution.output_projects_root import resolve_root


def _output_dir_label(project_root: Path, slug: str) -> str:
    try:
        rel = resolve_root(project_root).relative_to(project_root)
        return f"{rel.as_posix()}/{slug}/output"
    except ValueError:
        return f"{slug}/output"


def _nearest_management_root(
    node_id: str, nodes: dict[str, dict[str, Any]], management_ids: set[str],
) -> str | None:
    seen: set[str] = set()
    cur: str | None = node_id
    while cur is not None and cur in nodes and cur not in seen:
        seen.add(cur)
        if cur in management_ids:
            return cur
        cur = nodes[cur].get("parent_id")
    return None


def build_tree_output_links(project_root: Path | str) -> dict[str, Any]:
    from mini_agent.output_registry.pending_links import list_pending, read_goal_nodes
    from mini_agent.output_registry.registry import list_links, load_index

    project_root = Path(project_root)
    nodes = read_goal_nodes(project_root)
    if nodes is None:
        return {"by_node": {}, "pending_total": 0, "error": "目标树（.agent/goals.json）不可读"}

    index = load_index(project_root)
    management_ids = set(index.get("management_roots", {}))
    projects = index.get("projects", {})

    links_by_root: dict[str, list[dict[str, Any]]] = {}
    for link in list_links(project_root):
        slug = link["output_id"]
        summary = projects.get(slug, {})
        links_by_root.setdefault(link["management_id"], []).append({
            "slug": slug,
            "role": link.get("role", ""),
            "latest_version": summary.get("latest_version"),
            "output_dir": _output_dir_label(project_root, slug),
        })

    pending_by_root: dict[str, list[dict[str, Any]]] = {}
    pending_items = list_pending(project_root, state="pending")
    for item in pending_items:
        pending_by_root.setdefault(item["management_id"], []).append({
            "id": item["id"],
            "slug": item["slug"],
            "role": item["role"],
            "origin": item.get("origin", ""),
            "note": item.get("note", ""),
            "goal_id": item.get("goal_id", ""),
        })

    by_node: dict[str, dict[str, Any]] = {}
    for node_id in nodes:
        root = _nearest_management_root(node_id, nodes, management_ids)
        linked = list(links_by_root.get(root, [])) if root else []
        pending = list(pending_by_root.get(node_id, []))
        is_root = node_id in management_ids
        if not (linked or pending or is_root):
            continue
        by_node[node_id] = {
            "is_management_root": is_root,
            "linked": linked,
            "inherited_from": (root if root != node_id else None) if linked else None,
            "pending": pending,
        }

    return {"by_node": by_node, "pending_total": len(pending_items)}


__all__ = ["build_tree_output_links"]
