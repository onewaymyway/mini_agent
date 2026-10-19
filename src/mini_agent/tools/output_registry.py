"""tools/output_registry.py — 执行期内置工具 link_output_project（S2）。

对应 next_doc/goal_output_project_auto_link_plan.md §4.2 / §4.3。

行为要点：
  - 只在 goal/objective 执行上下文中可见（available 判定依赖 thread-local，
    而该上下文只在 auto_link_tool_enabled=true 时写入）；交互对话里工具不出现；
  - 只写待确认账本（output_registry/pending_links.py），不建立正式关联，
    因此不改变任何产出路径，requires_approval=False；
  - 管理项目根解析（§4.3）：沿 parent_id 从触发节点向上，先找最近的已标记管理根，
    找不到退回最近的 goal 层级祖先，仍找不到则返回 floating，交给巡检；
  - 角色：未指定时默认 build；若该项目已被其他管理项目以 build 关联，则改为 iterate；
  - 幂等与忽略语义完全由 add_pending() 决定（见账本模块）。

目标树只通过只读方式解析（读取 .agent/goals.json 原始 JSON，与 routing.py 相同），
不调用 GoalBacklog.load()，避免写盘副作用。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from . import tool
from mini_agent.evolution.goal_exec_context import get_current_goal_exec
from mini_agent.evolution.output_projects_root import resolve_root

_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _available() -> bool:
    """只有处于 goal/objective 执行上下文中时工具才对模型可见。"""
    return get_current_goal_exec() is not None


def _err(message: str, **extra: Any) -> str:
    return json.dumps({"ok": False, "error": message, **extra}, ensure_ascii=False)


def _resolve_management_root(
    trigger: str, nodes: dict[str, dict[str, Any]], management_ids: set[str],
) -> Optional[str]:
    """计划 §4.3：优先最近的已标记管理根，其次最近的 goal 祖先（均含自身）。"""
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


def _auto_role(project_root: Path, slug: str, management_id: str) -> str:
    """默认 build；若该项目已被其他管理项目以 build 关联，则为 iterate。拿不准一律 build。"""
    from mini_agent.output_registry.registry import list_links, ROLE_BUILD, ROLE_ITERATE

    for link in list_links(project_root):
        if (
            link.get("output_id") == slug
            and link.get("role") == ROLE_BUILD
            and link.get("management_id") != management_id
        ):
            return ROLE_ITERATE
    return ROLE_BUILD


@tool(
    name="link_output_project",
    description=(
        "把本次目标执行中用到的产出项目（output_projects_root 下的目录）关联到当前目标。"
        "新建或继续写入某个产出项目后调用。关联先进入待确认，由用户确认后才影响产出路径。"
        "仅在 goal/objective 执行中可用。"
    ),
    schema={
        "type": "object",
        "properties": {
            "slug": {"type": "string", "description": "产出项目目录名（output_projects_root 下的一级目录）"},
            "role": {
                "type": "string",
                "enum": ["build", "iterate", "reference"],
                "description": "角色；不填则自动判定（默认 build，已被其他管理项目 build 关联时为 iterate）",
            },
            "note": {"type": "string", "description": "一句话理由，便于用户确认时判断"},
            "create": {"type": "boolean", "description": "目录不存在时是否受管新建（只创建，不覆盖）"},
            "title": {"type": "string", "description": "新建时的项目标题（可选）"},
        },
        "required": ["slug"],
    },
    requires_approval=False,
    available=_available,
)
def link_output_project(
    slug: str,
    role: str = "",
    note: str = "",
    create: bool = False,
    title: str = "",
) -> str:
    from mini_agent.output_registry.pending_links import (
        OUTCOME_CONFIRMED,
        OUTCOME_CREATED,
        OUTCOME_EXISTING,
        OUTCOME_REJECTED,
        ORIGIN_AGENT_TOOL,
        add_pending,
        read_goal_nodes,
    )
    from mini_agent.output_registry.registry import RegistryError, VALID_ROLES, load_index
    from mini_agent.output_registry.lifecycle import create_output_project

    ctx = get_current_goal_exec()
    if ctx is None:
        return _err("当前不在 goal 执行上下文中")

    project_root = Path(ctx["project_root"])
    trigger = ctx["trigger_node_id"]
    slug = (slug or "").strip()
    if not _SLUG_RE.match(slug):
        return _err(f"项目名不合法：{slug!r}")
    role = (role or "").strip()
    if role and role not in VALID_ROLES:
        return _err(f"非法角色 {role!r}，合法取值为 {list(VALID_ROLES)}")

    nodes = read_goal_nodes(project_root)
    if nodes is None:
        return _err("目标树（.agent/goals.json）不可读")
    management_ids = set(load_index(project_root)["management_roots"])
    management_id = _resolve_management_root(trigger, nodes, management_ids)
    if management_id is None:
        return _err("无法确定管理项目（目标树中找不到 goal 祖先）", floating=True)

    created = False
    if not (resolve_root(project_root) / slug).is_dir():
        if not create:
            return _err(f"产出项目目录不存在：{slug!r}；如需新建请设置 create=true")
        try:
            create_output_project(project_root, slug, title=(title or "").strip())
        except RegistryError as exc:
            return _err(str(exc))
        created = True

    final_role = role or _auto_role(project_root, slug, management_id)
    try:
        res = add_pending(
            project_root,
            slug=slug,
            goal_id=trigger,
            management_id=management_id,
            role=final_role,
            origin=ORIGIN_AGENT_TOOL,
            note=(note or "")[:200],
            valid_goal_ids=nodes.keys(),
        )
    except RegistryError as exc:
        return _err(str(exc))

    outcome = res["outcome"]
    state = {
        OUTCOME_CREATED: "pending",
        OUTCOME_EXISTING: "pending",
        OUTCOME_CONFIRMED: "confirmed",
        OUTCOME_REJECTED: "rejected",
    }[outcome]
    return json.dumps(
        {
            "ok": True,
            "state": state,
            "slug": slug,
            "role": final_role,
            "management_id": management_id,
            "created_project": created,
            "message": (
                "用户已忽略过该关联，不再建议。" if state == "rejected"
                else "已正式关联。" if state == "confirmed"
                else "已提交待确认，用户在目标树中确认后生效。"
            ),
        },
        ensure_ascii=False,
    )
