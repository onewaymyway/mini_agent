"""output_registry/registry.py — P2 注册表：管理项目标记、管理-产出关联、游离项目绑定。

对应 next_doc/management_output_project_model_plan.md 第 6、8.5、9 章 P2。

存储结构（均位于 output_projects_root/_registry/ 下，不改动任何项目目录）：
  index.json          总表：项目索引、管理项目（子树根）标记、关联汇总
  projects/<id>.json  分表：每个项目一份，权威记录（owner / goal_refs / links / versions / status_history）

设计约束：
  1. 分表是权威记录；总表是索引。两者不一致时以分表为准（rebuild_index 据此重建）。
  2. 删除 _registry/ 后可以重建：项目记录由目录扫描补齐，关联由分表恢复，
     管理项目标题由目标树补齐。
  3. 所有写操作在 index.json 的文件锁内完成，原子写入。
  4. 管理项目标记存放在总表中，而不是 GoalNode 字段，避免再次改动目标序列化
     （计划 6.3 的偏差，见 impl_p2 记录）。

角色取值：build / iterate / reference（计划第 5.5 节）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from mini_agent.evolution.output_projects_root import PROJECT_INFO_FILENAME, resolve_root
from mini_agent.external_input.filelock import ExclusiveFileLock
from mini_agent.utils.atomic_write import atomic_write_json

REGISTRY_DIRNAME = "_registry"
INDEX_FILENAME = "index.json"
PROJECTS_DIRNAME = "projects"
INDEX_SCHEMA = "output_registry.index.v1"
RECORD_SCHEMA = "output_registry.record.v1"

ROLE_BUILD = "build"
ROLE_ITERATE = "iterate"
ROLE_REFERENCE = "reference"
VALID_ROLES = (ROLE_BUILD, ROLE_ITERATE, ROLE_REFERENCE)

STATUS_FLOATING = "floating"
STATUS_ACTIVE = "active"

_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class RegistryError(ValueError):
    """注册表操作不合法（未知项目、未知管理项目、非法角色等）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 路径与读写 ────────────────────────────────────────────────────────────────

def registry_dir(project_root: Path | str) -> Path:
    return resolve_root(Path(project_root)) / REGISTRY_DIRNAME


def _index_path(project_root: Path | str) -> Path:
    return registry_dir(project_root) / INDEX_FILENAME


def _record_path(project_root: Path | str, project_id: str) -> Path:
    return registry_dir(project_root) / PROJECTS_DIRNAME / f"{project_id}.json"


def _empty_index() -> dict[str, Any]:
    return {"schema": INDEX_SCHEMA, "updated_at": None, "projects": {}, "management_roots": {}}


def _read_json(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_index(project_root: Path | str) -> dict[str, Any]:
    data = _read_json(_index_path(project_root))
    if data is None:
        return _empty_index()
    data.setdefault("projects", {})
    data.setdefault("management_roots", {})
    return data


def load_record(project_root: Path | str, project_id: str) -> Optional[dict[str, Any]]:
    return _read_json(_record_path(project_root, project_id))


def _write_record(project_root: Path | str, record: dict[str, Any]) -> None:
    path = _record_path(project_root, record["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, record)
    # [P3 / 计划 13 章问题 A] 受管项目在目录内同步一份 PROJECT.json 镜像，
    # 使注册表删除后版本历史等信息可以从目录恢复。旧项目（managed=False）不写入。
    if record.get("managed"):
        project_dir = resolve_root(Path(project_root)) / record["slug"]
        if project_dir.is_dir():
            atomic_write_json(project_dir / "PROJECT.json", record)


def _record_from_project_json(project_dir: Path) -> Optional[dict[str, Any]]:
    """从受管项目目录内的 PROJECT.json 恢复记录（用于 rebuild）。仅对 managed=True 生效。"""
    data = _read_json(project_dir / "PROJECT.json")
    if not data or not data.get("managed"):
        return None
    record = _new_record(project_dir.name)
    for key in ("title", "legacy", "managed", "status", "owner_goal_id", "goal_refs",
                "links", "versions", "status_history", "created_at"):
        if key in data:
            record[key] = data[key]
    return record


def _latest_version(record: dict[str, Any]) -> Optional[str]:
    versions = [v.get("version") for v in record.get("versions", []) if v.get("version")]
    if not versions:
        return None
    return max(versions, key=lambda s: tuple(int(x) for x in s.split(".")))


def save_project_record(project_root: Path | str, record: dict[str, Any]) -> dict[str, Any]:
    """在文件锁内保存一条项目记录并同步总表（供 lifecycle 模块使用）。"""
    with ExclusiveFileLock(_index_path(project_root)):
        index = load_index(project_root)
        _write_record(project_root, record)
        _refresh_index_entry(index, record)
        _save_index(project_root, index)
    return record


def append_management_history(project_root: Path | str, goal_id: str, event: dict[str, Any]) -> None:
    """为管理项目追加一条历史事件（只追加，不修改已有条目）。"""
    with ExclusiveFileLock(_index_path(project_root)):
        index = load_index(project_root)
        entry = index["management_roots"].get(goal_id)
        if entry is None:
            raise RegistryError(f"{goal_id!r} 尚未标记为管理项目")
        entry.setdefault("history", []).append({"at": _now(), **event})
        _save_index(project_root, index)


def _summary_of(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": record["slug"],
        "status": record["status"],
        "owner_goal_id": record.get("owner_goal_id"),
        "goal_refs": list(record.get("goal_refs", [])),
        "legacy": bool(record.get("legacy", True)),
        "managed": bool(record.get("managed", False)),
        "latest_version": _latest_version(record),
    }


def _new_record(slug: str) -> dict[str, Any]:
    return {
        "schema": RECORD_SCHEMA,
        "id": slug,
        "slug": slug,
        "legacy": True,
        "managed": False,
        "status": STATUS_FLOATING,
        "owner_goal_id": None,
        "goal_refs": [],
        "links": [],
        "versions": [],
        "status_history": [],
        "created_at": _now(),
    }


def _project_dir_exists(project_root: Path | str, slug: str) -> bool:
    if not _SLUG_RE.match(slug or ""):
        return False
    return (resolve_root(Path(project_root)) / slug).is_dir()


def _refresh_index_entry(index: dict[str, Any], record: dict[str, Any]) -> None:
    index["projects"][record["id"]] = _summary_of(record)


def _save_index(project_root: Path | str, index: dict[str, Any]) -> None:
    index["updated_at"] = _now()
    path = _index_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, index)


# ── 对外操作 ─────────────────────────────────────────────────────────────────

def register_project(project_root: Path | str, slug: str) -> dict[str, Any]:
    """把一个已存在的产出项目目录登记进注册表（幂等）。

    只写 _registry/ 下的文件；不读取也不修改项目目录内容。
    """
    if not _project_dir_exists(project_root, slug):
        raise RegistryError(f"产出项目目录不存在：{slug!r}")
    with ExclusiveFileLock(_index_path(project_root)):
        index = load_index(project_root)
        record = load_record(project_root, slug) or _new_record(slug)
        if not record.get("created_at"):
            record["created_at"] = _now()
        _write_record(project_root, record)
        _refresh_index_entry(index, record)
        _save_index(project_root, index)
        return record


def mark_management_root(
    project_root: Path | str,
    goal_id: str,
    title: str = "",
    *,
    valid_goal_ids: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """把一个目标节点标记为管理项目（子树根）。幂等。

    valid_goal_ids：调用方提供的合法目标 id 集合（来自目标树）。提供时校验存在性。
    """
    if not goal_id:
        raise RegistryError("goal_id 不能为空")
    if valid_goal_ids is not None and goal_id not in set(valid_goal_ids):
        raise RegistryError(f"目标节点不存在：{goal_id!r}")
    with ExclusiveFileLock(_index_path(project_root)):
        index = load_index(project_root)
        entry = index["management_roots"].get(goal_id)
        if entry is None:
            entry = {"title": title, "created_at": _now()}
        elif title:
            entry["title"] = title
        index["management_roots"][goal_id] = entry
        _save_index(project_root, index)
        return {"goal_id": goal_id, **entry}


def _validate_role(role: str) -> None:
    if role not in VALID_ROLES:
        raise RegistryError(f"非法角色 {role!r}，合法取值为 {VALID_ROLES}")


def link_management_output(
    project_root: Path | str,
    management_id: str,
    slug: str,
    role: str,
) -> dict[str, Any]:
    """建立 管理项目 ↔ 产出项目 的关联（幂等：同一三元组重复调用不产生重复记录）。

    规则：
      - management_id 必须已被标记为管理项目；
      - slug 必须是存在的产出项目目录；未登记的项目会先自动登记；
      - build 角色且项目尚无 owner 时，该管理项目成为 owner；
        其余情况进入 goal_refs（引用方）。
    """
    _validate_role(role)
    with ExclusiveFileLock(_index_path(project_root)):
        index = load_index(project_root)
        if management_id not in index["management_roots"]:
            raise RegistryError(f"{management_id!r} 尚未标记为管理项目")
        if not _project_dir_exists(project_root, slug):
            raise RegistryError(f"产出项目目录不存在：{slug!r}")

        record = load_record(project_root, slug) or _new_record(slug)
        link = {"management_id": management_id, "role": role}
        if link not in record["links"]:
            record["links"].append(link)
            if role == ROLE_BUILD and record["owner_goal_id"] is None:
                record["owner_goal_id"] = management_id
            elif management_id != record["owner_goal_id"] and management_id not in record["goal_refs"]:
                record["goal_refs"].append(management_id)
            record["status"] = STATUS_ACTIVE
            record.setdefault("status_history", []).append(
                {"at": _now(), "event": "linked", "management_id": management_id, "role": role}
            )
        _write_record(project_root, record)
        _refresh_index_entry(index, record)
        _save_index(project_root, index)
        return record


def unlink_management_output(
    project_root: Path | str,
    management_id: str,
    slug: str,
    role: str,
) -> dict[str, Any]:
    """解除关联。解除后若无任何关联，项目回到游离状态。"""
    _validate_role(role)
    with ExclusiveFileLock(_index_path(project_root)):
        index = load_index(project_root)
        record = load_record(project_root, slug)
        if record is None:
            raise RegistryError(f"项目未登记：{slug!r}")
        link = {"management_id": management_id, "role": role}
        if link not in record["links"]:
            raise RegistryError(f"不存在该关联：{management_id!r} / {slug!r} / {role}")
        record["links"].remove(link)
        still_linked_to = {l["management_id"] for l in record["links"]}
        if record["owner_goal_id"] == management_id and management_id not in still_linked_to:
            record["owner_goal_id"] = None
        if management_id not in still_linked_to and management_id in record["goal_refs"]:
            record["goal_refs"].remove(management_id)
        if not record["links"]:
            record["status"] = STATUS_FLOATING
        record.setdefault("status_history", []).append(
            {"at": _now(), "event": "unlinked", "management_id": management_id, "role": role}
        )
        _write_record(project_root, record)
        _refresh_index_entry(index, record)
        _save_index(project_root, index)
        return record


def bind_floating_project(
    project_root: Path | str,
    slug: str,
    management_id: str,
    role: str,
) -> dict[str, Any]:
    """把游离项目绑定到已有管理项目（计划第 8.5 节路径 1）。"""
    record = load_record(project_root, slug)
    if record is None:
        register_project(project_root, slug)
    return link_management_output(project_root, management_id, slug, role)


def derive_management_from_project(
    project_root: Path | str,
    slug: str,
    backlog: Any,
    parent_goal_id: str,
    title: str,
    role: str = ROLE_BUILD,
) -> str:
    """根据游离项目派生一个新的管理项目，挂到目标树的指定节点下（计划第 8.5 节路径 2）。

    在目标树中新增一个 goal 节点作为管理项目根，并建立关联。返回新节点 id。
    backlog 为 GoalBacklog 实例（由调用方提供，便于测试与复用）。
    """
    _validate_role(role)
    if backlog.get(parent_goal_id) is None:
        raise RegistryError(f"父目标节点不存在：{parent_goal_id!r}")
    if not _project_dir_exists(project_root, slug):
        raise RegistryError(f"产出项目目录不存在：{slug!r}")
    try:
        node = backlog.add_node("goal", title, parent_id=parent_goal_id, source="user")
    except ValueError as exc:  # 目标树的层级约束（如 goal 不能直接挂在 ultimate 下）
        raise RegistryError(str(exc)) from exc
    mark_management_root(project_root, node.id, title)
    link_management_output(project_root, node.id, slug, role)
    return node.id


def list_links(project_root: Path | str) -> list[dict[str, Any]]:
    """从分表汇总全部关联（分表为权威来源）。"""
    out: list[dict[str, Any]] = []
    pdir = registry_dir(project_root) / PROJECTS_DIRNAME
    if not pdir.is_dir():
        return out
    for path in sorted(pdir.glob("*.json")):
        record = _read_json(path)
        if not record:
            continue
        for link in record.get("links", []):
            out.append({"output_id": record["id"], **link})
    return out


def rebuild_index(project_root: Path | str) -> dict[str, Any]:
    """从分表与目录重建总表。删除 _registry/index.json 后调用即可恢复。

    - 项目：扫描产出目录，已有分表则沿用，没有则新建（legacy=True）；
    - 关联与 owner / goal_refs：从分表恢复；
    - 管理项目：由分表中出现过的 management_id 推导；标题只能在目标树中找到时补齐，
      否则为空（这是唯一无法从注册表本身恢复的信息）。
    """
    root = resolve_root(Path(project_root))
    with ExclusiveFileLock(_index_path(project_root)):
        index = _empty_index()
        if root.is_dir():
            for entry in sorted(root.iterdir(), key=lambda p: p.name):
                if not entry.is_dir() or entry.name.startswith(("_", ".")):
                    continue
                record = (
                    load_record(project_root, entry.name)
                    or _record_from_project_json(entry)
                    or _new_record(entry.name)
                )
                _write_record(project_root, record)
                _refresh_index_entry(index, record)
                for link in record.get("links", []):
                    mid = link["management_id"]
                    index["management_roots"].setdefault(mid, {"title": "", "created_at": _now()})
        _save_index(project_root, index)
        return index


def fill_management_titles(project_root: Path | str, titles: dict[str, str]) -> None:
    """用目标树中的标题补齐管理项目标题（供 rebuild 之后调用）。"""
    with ExclusiveFileLock(_index_path(project_root)):
        index = load_index(project_root)
        changed = False
        for gid, title in titles.items():
            if gid in index["management_roots"] and not index["management_roots"][gid].get("title"):
                index["management_roots"][gid]["title"] = title
                changed = True
        if changed:
            _save_index(project_root, index)
