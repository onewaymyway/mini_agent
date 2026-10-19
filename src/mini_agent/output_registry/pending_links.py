"""output_registry/pending_links.py — 待确认关联账本（S1）。

对应 next_doc/goal_output_project_auto_link_plan.md 第 3 章。

账本文件：
    <output_projects_root>/_registry/pending_links.json

设计约束：
  1. 账本独立于正式注册表，不改动 index.json 与分表结构（零破坏）。
  2. 账本只承载"建议"：待确认（pending）与已忽略（rejected）。
     确认后才写入正式关联（projects/<id>.json 的 links），账本条目随之删除。
  3. 路由（routing.py）只读正式关联，不读账本——待确认关联不影响任何产出路径。
  4. 账本可以整体删除：正式关联保存在分表中，删除账本只会丢失"待确认"建议，
     不会丢失任何已确认关联。
  5. 标记管理根（mark_management_root）的副作用延后到确认时才发生。
  6. 锁顺序固定为 账本锁 → 注册表 index 锁；注册表模块不会反向获取账本锁。

状态：
    pending   待用户确认
    rejected  用户忽略；同 (slug, goal_id) 不再重复生成
    （确认后条目从账本删除，不保留 confirmed 状态）
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional

from mini_agent.external_input.filelock import ExclusiveFileLock
from mini_agent.output_registry.registry import (
    VALID_ROLES,
    RegistryError,
    _project_dir_exists,
    link_management_output,
    load_record,
    mark_management_root,
    registry_dir,
)
from mini_agent.utils.atomic_write import atomic_write_json

LEDGER_FILENAME = "pending_links.json"
LEDGER_SCHEMA = "output_registry.pending_links.v1"

STATE_PENDING = "pending"
STATE_REJECTED = "rejected"
VALID_STATES = (STATE_PENDING, STATE_REJECTED)

ORIGIN_AGENT_TOOL = "agent_tool"
ORIGIN_AUTO_DETECT = "auto_detect"
VALID_ORIGINS = (ORIGIN_AGENT_TOOL, ORIGIN_AUTO_DETECT)

# add_pending() 的返回状态
OUTCOME_CREATED = "created"        # 新建了一条待确认
OUTCOME_EXISTING = "existing"      # 已有同 (slug, goal_id, role) 的待确认，原样返回
OUTCOME_REJECTED = "rejected"      # 同 (slug, goal_id) 已被用户忽略，不再生成
OUTCOME_CONFIRMED = "confirmed"    # 正式关联已存在，无需再建议


def ledger_path(project_root: Path | str) -> Path:
    return registry_dir(project_root) / LEDGER_FILENAME


def _empty_ledger() -> dict[str, Any]:
    return {"schema": LEDGER_SCHEMA, "updated_at": None, "items": []}


def _read_ledger(project_root: Path | str) -> dict[str, Any]:
    path = ledger_path(project_root)
    if not path.is_file():
        return _empty_ledger()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 账本损坏视为空账本：它只是建议，丢失不影响任何正式关联。
        return _empty_ledger()
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return _empty_ledger()
    return data


def _write_ledger(project_root: Path | str, ledger: dict[str, Any]) -> None:
    path = ledger_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger["schema"] = LEDGER_SCHEMA
    ledger["updated_at"] = time.time()
    atomic_write_json(path, ledger)


def _lock_path(project_root: Path | str) -> Path:
    # ExclusiveFileLock 会在 <path>.lock 上加锁；父目录需先存在。
    path = ledger_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _formal_link_exists(project_root: Path | str, slug: str, management_id: str, role: str) -> bool:
    record = load_record(project_root, slug)
    if not record:
        return False
    return {"management_id": management_id, "role": role} in record.get("links", [])



def read_goal_nodes(project_root: Path | str) -> Optional[dict[str, dict[str, Any]]]:
    """只读解析 .agent/goals.json：id → {parent_id, level, title}。不可读时返回 None。

    与 routing.py 相同的只读方式，不调用 GoalBacklog.load()，无写盘副作用。
    """
    goals_path = Path(project_root) / ".agent" / "goals.json"
    if not goals_path.is_file():
        return None
    try:
        data = json.loads(goals_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    nodes: dict[str, dict[str, Any]] = {}
    for g in data.get("goals", []) or []:
        if isinstance(g, dict) and "id" in g:
            nodes[g["id"]] = {
                "parent_id": g.get("parent_id"),
                "level": g.get("level"),
                "title": g.get("title", "") or "",
            }
    return nodes

def list_pending(project_root: Path | str, state: Optional[str] = None) -> list[dict[str, Any]]:
    """列出账本条目；state 为空时返回全部（pending 与 rejected）。"""
    items = _read_ledger(project_root)["items"]
    if state is None:
        return [dict(i) for i in items]
    return [dict(i) for i in items if i.get("state") == state]


def get_pending(project_root: Path | str, pending_id: str) -> Optional[dict[str, Any]]:
    for item in _read_ledger(project_root)["items"]:
        if item.get("id") == pending_id:
            return dict(item)
    return None


def add_pending(
    project_root: Path | str,
    *,
    slug: str,
    goal_id: str,
    management_id: str,
    role: str,
    origin: str,
    note: str = "",
    valid_goal_ids: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """登记一条待确认关联（幂等）。

    返回 {"outcome": OUTCOME_*, "item": {...}}。
      - 同 (slug, goal_id, role) 已有正式关联 → confirmed，不写账本；
      - 同 (slug, goal_id, role) 已有待确认 → existing，原样返回；
      - 同 (slug, goal_id) 已被忽略 → rejected，不再生成；
      - 否则新建一条 pending。

    管理根是否已标记不在此检查，也不在此标记（延后到确认时）。
    valid_goal_ids 提供时校验 goal_id / management_id 在目标树中存在。
    """
    if role not in VALID_ROLES:
        raise RegistryError(f"非法角色 {role!r}，合法取值为 {VALID_ROLES}")
    if origin not in VALID_ORIGINS:
        raise RegistryError(f"非法来源 {origin!r}，合法取值为 {VALID_ORIGINS}")
    if not goal_id or not management_id:
        raise RegistryError("goal_id 与 management_id 不能为空")
    if not _project_dir_exists(project_root, slug):
        raise RegistryError(f"产出项目目录不存在：{slug!r}")
    if valid_goal_ids is not None:
        valid = set(valid_goal_ids)
        for gid in (goal_id, management_id):
            if gid not in valid:
                raise RegistryError(f"目标节点不存在：{gid!r}")

    if _formal_link_exists(project_root, slug, management_id, role):
        return {"outcome": OUTCOME_CONFIRMED, "item": None}

    with ExclusiveFileLock(_lock_path(project_root)):
        ledger = _read_ledger(project_root)
        items = ledger["items"]
        for item in items:
            if item.get("slug") == slug and item.get("goal_id") == goal_id:
                if item.get("state") == STATE_REJECTED:
                    return {"outcome": OUTCOME_REJECTED, "item": dict(item)}
            if (
                item.get("state") == STATE_PENDING
                and item.get("slug") == slug
                and item.get("goal_id") == goal_id
                and item.get("role") == role
            ):
                return {"outcome": OUTCOME_EXISTING, "item": dict(item)}

        item = {
            "id": f"pl_{uuid.uuid4().hex[:12]}",
            "slug": slug,
            "goal_id": goal_id,
            "management_id": management_id,
            "role": role,
            "origin": origin,
            "state": STATE_PENDING,
            "note": note or "",
            "created_at": time.time(),
        }
        items.append(item)
        _write_ledger(project_root, ledger)
        return {"outcome": OUTCOME_CREATED, "item": dict(item)}


def confirm_pending(
    project_root: Path | str,
    pending_id: str,
    *,
    confirm: bool,
    role: Optional[str] = None,
    title: str = "",
) -> dict[str, Any]:
    """确认一条待确认关联：标记管理根 → 写入正式关联 → 删除账本条目。

    confirm 必须为 True（显式确认，防止误调用）。role 可覆盖建议角色。
    任一步失败时账本条目保留，可以重试。返回正式关联记录（分表）。
    """
    if confirm is not True:
        raise RegistryError("确认操作必须显式传入 confirm=True")
    if role is not None and role not in VALID_ROLES:
        raise RegistryError(f"非法角色 {role!r}，合法取值为 {VALID_ROLES}")

    with ExclusiveFileLock(_lock_path(project_root)):
        ledger = _read_ledger(project_root)
        item = next((i for i in ledger["items"] if i.get("id") == pending_id), None)
        if item is None:
            raise RegistryError(f"待确认条目不存在：{pending_id!r}")
        if item.get("state") != STATE_PENDING:
            raise RegistryError(f"该条目不是待确认状态（当前为 {item.get('state')!r}）")

        final_role = role or item["role"]
        mark_management_root(project_root, item["management_id"], title)
        record = link_management_output(project_root, item["management_id"], item["slug"], final_role)

        ledger["items"] = [i for i in ledger["items"] if i.get("id") != pending_id]
        _write_ledger(project_root, ledger)
        return record


def reject_pending(project_root: Path | str, pending_id: str) -> dict[str, Any]:
    """忽略一条待确认关联。条目保留为 rejected，同 (slug, goal_id) 不再重复生成。"""
    with ExclusiveFileLock(_lock_path(project_root)):
        ledger = _read_ledger(project_root)
        item = next((i for i in ledger["items"] if i.get("id") == pending_id), None)
        if item is None:
            raise RegistryError(f"待确认条目不存在：{pending_id!r}")
        if item.get("state") == STATE_REJECTED:
            return dict(item)
        item["state"] = STATE_REJECTED
        item["rejected_at"] = time.time()
        _write_ledger(project_root, ledger)
        return dict(item)


__all__ = [
    "LEDGER_FILENAME",
    "LEDGER_SCHEMA",
    "STATE_PENDING",
    "STATE_REJECTED",
    "ORIGIN_AGENT_TOOL",
    "ORIGIN_AUTO_DETECT",
    "OUTCOME_CREATED",
    "OUTCOME_EXISTING",
    "OUTCOME_REJECTED",
    "OUTCOME_CONFIRMED",
    "ledger_path",
    "list_pending",
    "get_pending",
    "add_pending",
    "confirm_pending",
    "reject_pending",
    "read_goal_nodes",
]
