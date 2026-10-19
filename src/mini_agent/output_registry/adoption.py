"""output_registry/adoption.py — P6 接入辅助：发现未登记目录，并在用户确认后一键接入。

对应 next_doc/management_output_project_model_plan.md 第 8.6 节与第 9 章 P6。

两个动作：
  - list_unregistered：只读。列出产出根下"存在目录但未登记进注册表"的候选项目。
  - adopt_output_project：用户确认后，把一个未登记目录接入注册表（managed=True），
    并写入 PROJECT.json 镜像。

接入有两档（用户 P7 决定）：
  - "registered"（只登记）：只在 _registry/ 下写入登记记录，目录内不产生任何文件，
    项目不受管（managed=False），不做版本管理与 PROJECT.json 镜像；
  - "managed"（受管）：登记并写入 PROJECT.json 镜像，项目进入版本管理与巡检的受管规则。
  已只登记的项目可以之后升级为受管（upgrade），升级同样需要确认。

约束（计划第 7 节与 P6 退出条件）：
  1. 接入必须 confirm=True，否则拒绝，且不产生任何写入；
  2. 任何一档都不修改、移动或删除目录中已有的文件；受管档只新增 PROJECT.json，
     以及调用方明确给出介绍且目录内缺失时的 PROJECT_INFO.md；
  3. PROJECT_INFO.md 绝不覆盖已有文件；
  4. 接入不改变项目状态（保持 floating），不关联任何管理项目；
  5. 重复接入是幂等的：已登记且档位不需升级的项目直接返回，不做任何写入。
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
_REGISTERED_EVENT = "registered"
_UPGRADED_EVENT = "upgraded_to_managed"

MODE_REGISTERED = "registered"
MODE_MANAGED = "managed"
VALID_MODES = (MODE_REGISTERED, MODE_MANAGED)


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
    mode: str = MODE_MANAGED,
    title: str = "",
    intro: str = "",
) -> dict[str, Any]:
    """用户确认后，把未登记目录接入注册表（两档，见模块说明）。

    返回摘要。已登记且无需升级时返回 already_registered=True，不做任何写入。
    """
    if not confirm:
        raise RegistryError("接入产出项目需要用户确认（confirm=True）")
    if mode not in VALID_MODES:
        raise RegistryError(f"接入档位非法：{mode!r}，合法取值为 {VALID_MODES}")
    if not _SLUG_RE.match(slug or ""):
        raise RegistryError(f"项目名不合法：{slug!r}")
    root = resolve_root(Path(project_root))
    entry = root / slug
    if not entry.is_dir():
        raise RegistryError(f"产出项目目录不存在：{slug!r}")

    with ExclusiveFileLock(_index_path(project_root)):
        index = load_index(project_root)
        existing = load_record(project_root, slug)

        if existing is not None:
            # 已登记：只有"只登记 → 受管"需要升级，其它情况幂等返回
            if mode == MODE_MANAGED and not existing.get("managed"):
                existing["managed"] = True
                existing["legacy"] = False
                existing.setdefault("status_history", []).append({"at": _now(), "event": _UPGRADED_EVENT})
                _write_record(project_root, existing)
                _refresh_index_entry(index, existing)
                _save_index(project_root, index)
                return {"slug": slug, "already_registered": True, "upgraded": True,
                        "managed": True, "project_json_written": (entry / "PROJECT.json").is_file()}
            return {"slug": slug, "already_registered": True, "upgraded": False,
                    "managed": bool(existing.get("managed")),
                    "project_json_written": (entry / "PROJECT.json").is_file()}

        display = (title or "").strip() or _title_from_intro_file(entry) or slug
        record = _new_record(slug)
        record.update({"title": display, "created_at": _now()})

        if mode == MODE_REGISTERED:
            # 只登记：目录内不写任何文件
            record.update({"legacy": True, "managed": False})
            record["status_history"].append({"at": _now(), "event": _REGISTERED_EVENT})
        else:
            intro_path = entry / PROJECT_INFO_FILENAME
            if intro and intro.strip() and not intro_path.exists():
                # 仅在缺失时补充介绍，已有文件绝不覆盖
                intro_path.write_text(f"# {display}\n\n{intro.strip()}\n", encoding="utf-8")
            record.update({"legacy": False, "managed": True})
            record["status_history"].append({"at": _now(), "event": _ADOPTED_EVENT})

        _write_record(project_root, record)
        _refresh_index_entry(index, record)
        _save_index(project_root, index)

    return {
        "slug": slug,
        "already_registered": False,
        "upgraded": False,
        "mode": mode,
        "managed": bool(record["managed"]),
        "status": record["status"],
        "title": display,
        "project_json_written": (entry / "PROJECT.json").is_file(),
    }
