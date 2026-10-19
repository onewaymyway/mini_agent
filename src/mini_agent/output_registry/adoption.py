"""output_registry/adoption.py — P6 接入辅助：发现未登记目录，并在用户确认后一键接入。

对应 next_doc/management_output_project_model_plan.md 第 8.6 节与第 9 章 P6。

两个动作：
  - list_unregistered：只读。列出产出根下"存在目录但未登记进注册表"的候选项目。
  - adopt_output_project：用户确认后，把一个未登记目录接入注册表（managed=True），
    并写入 PROJECT.json 镜像。

约束（计划第 7 节与 P6 退出条件）：
  1. 接入必须 confirm=True，否则拒绝，且不产生任何写入；
  2. 接入只新增文件（PROJECT.json，以及调用方明确给出介绍时的 PROJECT_INFO.md），
     从不修改、移动或删除目录中已有的文件；
  3. PROJECT_INFO.md 仅在缺失且用户给出介绍时才创建，已存在则绝不覆盖；
  4. 接入不改变项目状态（保持 floating），不关联任何管理项目；
  5. 重复接入是幂等的：已登记的项目直接返回，不做任何写入。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mini_agent.evolution.output_projects_root import PROJECT_INFO_FILENAME, resolve_root
from mini_agent.external_input.filelock import ExclusiveFileLock
from mini_agent.output_registry.registry import (
    RegistryError,
    _SLUG_RE,
    _index_path,
    _new_record,
    _refresh_index_entry,
    _save_index,
    _write_record,
    load_index,
    load_record,
)

_ADOPTED_EVENT = "adopted"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _title_from_intro_file(entry: Path) -> str:
    """若存在 PROJECT_INFO.md，取第一行一级标题作为建议标题；否则返回空串。只读。"""
    path = entry / PROJECT_INFO_FILENAME
    if not path.is_file():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
    except (OSError, UnicodeDecodeError):
        return ""
    return ""


def _count_files(entry: Path, limit: int = 5000) -> tuple[int, bool]:
    count = 0
    for _dirpath, _dirnames, filenames in os.walk(entry):
        count += len(filenames)
        if count >= limit:
            return count, True
    return count, False


def list_unregistered(project_root: Path | str) -> list[dict[str, Any]]:
    """只读：列出产出根下未登记进注册表的目录。

    判定依据：分表（_registry/projects/<slug>.json）不存在。
    以下划线或点开头的目录视为内部目录，不列出。
    """
    root = resolve_root(Path(project_root))
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        if load_record(project_root, entry.name) is not None:
            continue
        file_count, truncated = _count_files(entry)
        out.append({
            "slug": entry.name,
            "suggested_title": _title_from_intro_file(entry) or entry.name,
            "has_project_info": (entry / PROJECT_INFO_FILENAME).is_file(),
            "file_count": file_count,
            "file_count_truncated": truncated,
        })
    return out


def adopt_output_project(
    project_root: Path | str,
    slug: str,
    *,
    confirm: bool,
    title: str = "",
    intro: str = "",
) -> dict[str, Any]:
    """用户确认后，把未登记目录接入注册表。

    返回接入后的摘要。若已登记，则返回 already_registered=True 且不做任何写入。
    """
    if not confirm:
        raise RegistryError("接入产出项目需要用户确认（confirm=True）")
    if not _SLUG_RE.match(slug or ""):
        raise RegistryError(f"项目名不合法：{slug!r}")
    root = resolve_root(Path(project_root))
    entry = root / slug
    if not entry.is_dir():
        raise RegistryError(f"产出项目目录不存在：{slug!r}")

    existing = load_record(project_root, slug)
    if existing is not None:
        return {"slug": slug, "already_registered": True, "managed": bool(existing.get("managed"))}

    with ExclusiveFileLock(_index_path(project_root)):
        # 锁内再次确认，防止并发接入同一目录
        if load_record(project_root, slug) is not None:
            return {"slug": slug, "already_registered": True, "managed": True}

        display = (title or "").strip() or _title_from_intro_file(entry) or slug
        intro_path = entry / PROJECT_INFO_FILENAME
        if intro and intro.strip() and not intro_path.exists():
            # 仅在缺失时补充介绍，已有文件绝不覆盖
            intro_path.write_text(f"# {display}\n\n{intro.strip()}\n", encoding="utf-8")

        record = _new_record(slug)
        record.update({
            "title": display,
            "legacy": False,
            "managed": True,
            "created_at": _now(),
        })
        record.setdefault("status_history", []).append({"at": _now(), "event": _ADOPTED_EVENT})
        index = load_index(project_root)
        _write_record(project_root, record)
        _refresh_index_entry(index, record)
        _save_index(project_root, index)

    return {
        "slug": slug,
        "already_registered": False,
        "managed": True,
        "status": record["status"],
        "title": display,
        "project_json_written": (entry / "PROJECT.json").is_file(),
    }
