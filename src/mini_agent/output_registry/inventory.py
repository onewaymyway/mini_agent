"""output_registry/inventory.py — P0 只读盘点。

对应 next_doc/management_output_project_model_plan.md 第 9 章 P0：
  - 扫描 output_projects_root，生成注册表草稿（候选项目清单）；
  - 列出六处产出位置与目标节点的对应关系；
  - 不写任何现有文件。

约束（由测试保证）：
  1. 不写 output_projects_root 下的任何内容；
  2. 不调用 GoalBacklog.load()（会回填字段、可能落盘），改为读取 goals.json 原始 JSON；
  3. 唯一写入是 .agent/output_registry/inventory_draft.json，且该路径必须不在
     output_projects_root 之下。

输出的草稿是"候选"而非"正式注册"：所有项目都标记为 legacy=true、managed=false，
表示它们目前只存在于文件系统中，尚未被接入注册表（那是 P2/P6 的事）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from mini_agent.evolution.output_projects_root import (
    PROJECT_INFO_FILENAME,
    resolve_root,
)
from mini_agent.storage.paths import AgentPaths
from mini_agent.utils.atomic_write import atomic_write_json

INVENTORY_SCHEMA = "output_registry.inventory.v1"
DRAFT_DIRNAME = "output_registry"
DRAFT_FILENAME = "inventory_draft.json"

# 与 output_projects_root 约定保持一致：下划线开头的一级目录视为内部/保留目录
# （例如未来的 _registry/），不作为产出项目列出。
_RESERVED_PREFIX = "_"

# 单个项目的文件统计上限，避免超大目录拖慢盘点。超过后标记 truncated。
_FILE_SCAN_LIMIT = 5000
_TOP_ENTRY_LIMIT = 30


# ── 六处产出位置 ──────────────────────────────────────────────────────────────

def _scan_output_locations(paths: AgentPaths, root: Path, goal_rows: list[dict]) -> list[dict]:
    """列出六处产出位置及其现状（只统计，不创建目录）。

    六处（对应方案第 1.1 节）：
      1. session_output     .agent/sessions/<sid>/output
      2. adhoc_output       .agent/adhoc/<key>/output
      3. workflow_output    .agent/workflow_sessions/<wid>/output
      4. daemon_outputs     .agent/daemon_run_outputs/{goals,cron}
      5. goal_user_output   goals.json 中 user_output_dir 指向的路径
      6. output_projects    output_projects_root（全局产出根）
    """
    workdir = Path(paths.workdir_dir)

    def _count_children(base: Path, suffix: str) -> tuple[int, bool]:
        if not base.is_dir():
            return 0, False
        n = 0
        has_any = False
        try:
            for child in base.iterdir():
                if not child.is_dir():
                    continue
                n += 1
                if (child / suffix).is_dir():
                    has_any = True
        except OSError:
            return n, has_any
        return n, has_any

    session_n, _ = _count_children(workdir / "sessions", "output")
    adhoc_n, _ = _count_children(workdir / "adhoc", "output")
    wf_n, _ = _count_children(workdir / "workflow_sessions", "output")

    daemon_base = workdir / "daemon_run_outputs"
    goal_daemon = daemon_base / "goals"
    cron_daemon = daemon_base / "cron"
    goal_daemon_n = sum(1 for p in goal_daemon.iterdir() if p.is_dir()) if goal_daemon.is_dir() else 0
    cron_daemon_n = sum(1 for p in cron_daemon.iterdir() if p.is_dir()) if cron_daemon.is_dir() else 0

    user_out_dirs = sorted({r["user_output_dir"] for r in goal_rows if r.get("user_output_dir")})
    user_out_existing = [d for d in user_out_dirs if (paths.project_root / d).is_dir()]

    root_n = 0
    if root.is_dir():
        try:
            root_n = sum(1 for p in root.iterdir() if p.is_dir())
        except OSError:
            root_n = 0

    return [
        {
            "key": "session_output",
            "label": "会话产出目录",
            "path": str(workdir / "sessions"),
            "exists": (workdir / "sessions").is_dir(),
            "entry_count": session_n,
            "classification": "ephemeral_scratch",
            "note": "随会话存在，不可跨会话查找；P4 之后仅绑定项目的执行会改写到项目目录。",
        },
        {
            "key": "adhoc_output",
            "label": "adhoc 工作目录",
            "path": str(workdir / "adhoc"),
            "exists": (workdir / "adhoc").is_dir(),
            "entry_count": adhoc_n,
            "classification": "ephemeral_scratch",
            "note": "无会话绑定的临时任务产出。",
        },
        {
            "key": "workflow_output",
            "label": "workflow 会话目录",
            "path": str(workdir / "workflow_sessions"),
            "exists": (workdir / "workflow_sessions").is_dir(),
            "entry_count": wf_n,
            "classification": "ephemeral_scratch",
            "note": "工作流执行的交付物落点，按执行实例隔离。",
        },
        {
            "key": "daemon_outputs",
            "label": "daemon 周期产出（goal / cron）",
            "path": str(daemon_base),
            "exists": daemon_base.is_dir(),
            "entry_count": goal_daemon_n + cron_daemon_n,
            "classification": "process_records",
            "note": "过程数据（manifest、cycle 记录）为主，保留在 .agent/，不纳入产出项目。",
            "detail": {"goal_run_dirs": goal_daemon_n, "cron_run_dirs": cron_daemon_n},
        },
        {
            "key": "goal_user_output",
            "label": "目标指定的产出目录（user_output_dir）",
            "path": None,
            "exists": bool(user_out_existing),
            "entry_count": len(user_out_dirs),
            "classification": "legacy_binding",
            "note": "作为遗留绑定保留，只用于推导项目路径，不移动文件。",
            "detail": {"distinct_paths": user_out_dirs, "existing_paths": user_out_existing},
        },
        {
            "key": "output_projects_root",
            "label": "全局产出根（output_projects_root）",
            "path": str(root),
            "exists": root.is_dir(),
            "entry_count": root_n,
            "classification": "managed_candidate",
            "note": "P0 的盘点对象；现有项目在此只读识别，不做任何改动。",
        },
    ]


# ── goals.json 只读解析 ──────────────────────────────────────────────────────

def _read_goal_rows(paths: AgentPaths) -> tuple[list[dict], list[str]]:
    """直接读取 .agent/goals.json 的原始 JSON。

    不走 GoalBacklog.load()，因为后者会回填 user_output_dir_suggested 并可能落盘。
    返回 (目标行列表, 警告列表)。文件不存在时返回空列表，不视为错误。
    """
    goals_path = Path(paths.workdir_dir) / "goals.json"
    warnings: list[str] = []
    if not goals_path.is_file():
        return [], warnings
    try:
        data = json.loads(goals_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"goals.json 无法解析，已跳过目标绑定盘点：{exc}")
        return [], warnings
    if not isinstance(data, dict):
        warnings.append("goals.json 顶层不是对象，已跳过目标绑定盘点")
        return [], warnings

    rows: list[dict] = []
    for g in data.get("goals", []) or []:
        if not isinstance(g, dict) or "id" not in g:
            continue
        rows.append(
            {
                "goal_id": g.get("id"),
                "title": g.get("title", ""),
                "level": g.get("level", ""),
                "status": g.get("status", ""),
                "parent_id": g.get("parent_id"),
                "user_output_dir": g.get("user_output_dir") or None,
            }
        )
    return rows, warnings


def _match_user_output_to_slug(user_output_dir: Optional[str], paths: AgentPaths, root: Path) -> Optional[str]:
    """把 user_output_dir 映射到 output_projects_root 下的一级项目 slug。

    返回：
      - 指向 root/<slug>（或其子路径）时返回 slug；
      - 指向 root 本身或 root 之外时返回 None（调用方据此区分分类）。
    """
    if not user_output_dir:
        return None
    candidate = Path(user_output_dir)
    if not candidate.is_absolute():
        candidate = Path(paths.project_root) / candidate
    try:
        rel = candidate.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    if not rel.parts:
        return None
    return rel.parts[0]


# ── 项目候选扫描 ─────────────────────────────────────────────────────────────

def _scan_project_dir(entry: Path) -> dict[str, Any]:
    """对单个一级目录做只读统计。任何单项失败都不影响其余字段。"""
    info_path = entry / PROJECT_INFO_FILENAME
    file_count = 0
    size_bytes = 0
    latest_mtime = 0.0
    truncated = False
    try:
        for dirpath, _dirnames, filenames in os.walk(entry):
            for fn in filenames:
                file_count += 1
                try:
                    st = os.stat(os.path.join(dirpath, fn))
                    size_bytes += st.st_size
                    if st.st_mtime > latest_mtime:
                        latest_mtime = st.st_mtime
                except OSError:
                    pass
                if file_count >= _FILE_SCAN_LIMIT:
                    truncated = True
                    break
            if truncated:
                break
    except OSError:
        pass

    try:
        top_entries = sorted(p.name for p in entry.iterdir())[:_TOP_ENTRY_LIMIT]
    except OSError:
        top_entries = []

    try:
        dir_mtime = entry.stat().st_mtime
    except OSError:
        dir_mtime = 0.0

    return {
        "has_project_info": info_path.is_file(),
        "has_project_json": (entry / "PROJECT.json").is_file(),
        "has_git": (entry / ".git").exists(),
        "has_releases": (entry / "releases").is_dir(),
        "file_count": file_count,
        "file_count_truncated": truncated,
        "size_bytes": size_bytes,
        "latest_file_mtime": _iso(latest_mtime) if latest_mtime else None,
        "dir_mtime": _iso(dir_mtime) if dir_mtime else None,
        "top_level_entries": top_entries,
    }


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _scan_root(root: Path, goal_rows: list[dict], paths: AgentPaths) -> tuple[list[dict], list[dict], list[str]]:
    """扫描 output_projects_root：返回 (候选项目, 保留/内部目录, 异常说明)。"""
    projects: list[dict] = []
    reserved: list[dict] = []
    anomalies: list[str] = []

    if not root.is_dir():
        return projects, reserved, anomalies

    # 反向索引：slug -> 指向它的目标
    owners: dict[str, list[str]] = {}
    unmatched_goal_paths: list[dict] = []
    for r in goal_rows:
        uod = r.get("user_output_dir")
        if not uod:
            continue
        slug = _match_user_output_to_slug(uod, paths, root)
        if slug is None:
            unmatched_goal_paths.append({"goal_id": r["goal_id"], "user_output_dir": uod})
        else:
            owners.setdefault(slug, []).append(r["goal_id"])

    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        anomalies.append(f"无法读取 output_projects_root：{exc}")
        return projects, reserved, anomalies

    for entry in entries:
        if not entry.is_dir():
            if not entry.name.startswith("."):
                anomalies.append(f"根目录下存在非目录条目：{entry.name}")
            continue
        if entry.name.startswith(_RESERVED_PREFIX):
            reserved.append({"name": entry.name, "path": str(entry)})
            continue
        if entry.name.startswith("."):
            continue

        stats = _scan_project_dir(entry)
        linked_goals = owners.get(entry.name, [])
        record = {
            "slug": entry.name,
            "path": str(entry),
            "legacy": True,
            "managed": stats["has_project_json"],
            "in_registry": False,
            "suggested_owner_goal_ids": linked_goals,
            "suggested_status": "candidate_bound" if linked_goals else "floating",
            **stats,
            "notes": [],
        }
        if not stats["has_project_info"]:
            record["notes"].append("缺少 PROJECT_INFO.md")
        if not linked_goals:
            record["notes"].append("未被任何目标的 user_output_dir 引用")
        projects.append(record)

    for g in unmatched_goal_paths:
        anomalies.append(
            f"目标 {g['goal_id']} 的 user_output_dir 不在 output_projects_root 之下：{g['user_output_dir']}"
        )
    return projects, reserved, anomalies


# ── 对外入口 ─────────────────────────────────────────────────────────────────

def build_inventory(project_root: Path | str) -> dict[str, Any]:
    """构建 P0 盘点结果（纯内存，不写盘）。"""
    paths = AgentPaths(project_root=Path(project_root))
    root = resolve_root(paths.project_root)

    goal_rows, warnings = _read_goal_rows(paths)
    projects, reserved, anomalies = _scan_root(root, goal_rows, paths)
    anomalies = warnings + anomalies

    bound = sum(1 for p in projects if p["suggested_owner_goal_ids"])
    return {
        "schema": INVENTORY_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "readonly_scan": True,
        "project_root": str(paths.project_root),
        "output_projects_root": str(root),
        "summary": {
            "candidate_projects": len(projects),
            "candidate_bound": bound,
            "candidate_floating": len(projects) - bound,
            "goals_total": len(goal_rows),
            "goals_with_user_output_dir": sum(1 for r in goal_rows if r.get("user_output_dir")),
            "reserved_dirs": len(reserved),
            "anomalies": len(anomalies),
        },
        "projects": projects,
        "reserved_dirs": reserved,
        "goal_bindings": [
            {
                **r,
                "matched_slug": _match_user_output_to_slug(r.get("user_output_dir"), paths, root),
            }
            for r in goal_rows
        ],
        "output_locations": _scan_output_locations(paths, root, goal_rows),
        "anomalies": anomalies,
    }


def draft_path_for(project_root: Path | str) -> Path:
    """草稿路径：.agent/output_registry/inventory_draft.json。"""
    paths = AgentPaths(project_root=Path(project_root))
    return Path(paths.workdir_dir) / DRAFT_DIRNAME / DRAFT_FILENAME


def write_inventory_draft(project_root: Path | str, inventory: Optional[dict] = None) -> Path:
    """把盘点结果写入草稿文件。

    这是 P0 唯一的写操作。写入前校验目标路径不在 output_projects_root 之下，
    防止草稿混入产出目录。
    """
    project_root = Path(project_root)
    if inventory is None:
        inventory = build_inventory(project_root)
    paths = AgentPaths(project_root=project_root)
    root = resolve_root(paths.project_root).resolve()
    target = draft_path_for(project_root)
    try:
        target.resolve().relative_to(root)
    except ValueError:
        pass
    else:  # pragma: no cover - 防御性检查，正常配置下不会发生
        raise RuntimeError(f"草稿路径不能位于 output_projects_root 之下：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target, inventory)
    return target
