"""output_registry/lifecycle.py — P3 受管新建、版本发布、管理项目重新打开。

对应 next_doc/management_output_project_model_plan.md 第 5.3、5.6、8.4、9 章 P3。

三个动作：
  - create_output_project：新建受管产出项目（带 PROJECT.json 镜像与骨架目录）；
  - publish_version：手动发布版本（语义化版本、快照复制全部内容、可选 git tag）；
  - reopen_management_root：重新打开已结束的管理项目（必须给出原因，记录历史）。

约束：
  1. 只新建、不覆盖：目标目录已存在时拒绝新建；
  2. 版本只追加：已发布的版本条目不修改、不删除；
  3. 快照为只读（文件级），快照目录本身不被系统修改；
  4. 版本发布与重新打开都是用户确认的动作，本模块不做交互确认，由调用方（命令行 --confirm）负责。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from mini_agent.evolution.output_projects_root import PROJECT_INFO_FILENAME, resolve_root
from mini_agent.output_registry.registry import (
    RegistryError,
    append_management_history,
    load_index,
    load_record,
    save_project_record,
    _new_record,
    _latest_version,
    _project_dir_exists,
)

RELEASES_DIRNAME = "releases"
_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_semver(version: str) -> tuple[int, int, int]:
    """严格解析 MAJOR.MINOR.PATCH。不合法时抛 RegistryError。"""
    m = _SEMVER_RE.match(version or "")
    if not m:
        raise RegistryError(f"版本号必须是语义化格式 MAJOR.MINOR.PATCH，收到 {version!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


# ── 1. 受管新建 ───────────────────────────────────────────────────────────────

def create_output_project(
    project_root: Path | str,
    slug: str,
    title: str = "",
    intro: str = "",
) -> dict[str, Any]:
    """新建受管产出项目。

    创建：<root>/<slug>/PROJECT_INFO.md、PROJECT.json（镜像）、output/。
    目标目录已存在时拒绝（不覆盖任何已有内容）。
    """
    if not _SLUG_RE.match(slug or ""):
        raise RegistryError(f"项目名不合法：{slug!r}")
    root = resolve_root(Path(project_root))
    target = root / slug
    if target.exists():
        raise RegistryError(f"目标目录已存在，拒绝新建：{slug!r}")

    target.mkdir(parents=True)
    (target / "output").mkdir()
    display = title or slug
    body = intro.strip() if intro else ""
    (target / PROJECT_INFO_FILENAME).write_text(f"# {display}\n\n{body}\n", encoding="utf-8")

    record = _new_record(slug)
    record.update({"title": display, "legacy": False, "managed": True, "created_at": _now()})
    return save_project_record(project_root, record)


# ── 2. 版本发布 ───────────────────────────────────────────────────────────────

def _make_files_readonly(path: Path) -> None:
    """把快照中的文件设为只读（文件级守卫）。目录保持可写，便于后续清理。"""
    for dirpath, _dirnames, filenames in os.walk(path):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            try:
                os.chmod(full, 0o444)
            except OSError:
                pass


def _try_git_tag(project_dir: Path, version: str) -> tuple[Optional[str], Optional[str]]:
    """若是 git 仓库则打 tag v<version>。返回 (tag, error)；失败不影响发布本身。"""
    if not (project_dir / ".git").exists():
        return None, None
    tag = f"v{version}"
    try:
        proc = subprocess.run(
            ["git", "-C", str(project_dir), "tag", tag],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"git 不可用：{exc}"
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout or "git tag 失败").strip()
    return tag, None


def publish_version(
    project_root: Path | str,
    slug: str,
    version: str,
    changelog: str = "",
    source_goal_id: Optional[str] = None,
) -> dict[str, Any]:
    """手动发布一个版本。

    规则：
      - 版本号必须是语义化格式，且严格大于已发布的最大版本；
      - 快照复制项目目录的全部内容到 releases/<version>/（仅排除 releases/ 本身）；
      - 快照文件设为只读；若为 git 仓库则同时打 tag；
      - 版本记录只追加，写入分表与总表（latest_version），受管项目同步 PROJECT.json。
    """
    parse_semver(version)
    record = load_record(project_root, slug)
    if record is None:
        if not _project_dir_exists(project_root, slug):
            raise RegistryError(f"产出项目不存在：{slug!r}")
        from mini_agent.output_registry.registry import register_project
        record = register_project(project_root, slug)

    previous = [v["version"] for v in record.get("versions", [])]
    if previous and parse_semver(version) <= max(parse_semver(v) for v in previous):
        raise RegistryError(f"新版本 {version} 必须大于已发布的最大版本 {_latest_version(record)}")

    project_dir = resolve_root(Path(project_root)) / slug
    snapshot = project_dir / RELEASES_DIRNAME / version
    if snapshot.exists():
        raise RegistryError(f"版本快照已存在：{snapshot}")

    releases_root = (project_dir / RELEASES_DIRNAME).resolve()
    releases_root.mkdir(parents=True, exist_ok=True)

    src_root = str(project_dir.resolve())

    def _ignore(src: str, names: list[str]) -> set[str]:
        if os.path.abspath(src) == src_root and RELEASES_DIRNAME in names:
            return {RELEASES_DIRNAME}
        return set()

    shutil.copytree(project_dir, snapshot, ignore=_ignore)
    _make_files_readonly(snapshot)

    git_tag, git_error = _try_git_tag(project_dir, version)
    entry = {
        "version": version,
        "released_at": _now(),
        "source_goal_id": source_goal_id,
        "changelog": changelog,
        "snapshot": f"{RELEASES_DIRNAME}/{version}",
        "git_tag": git_tag,
        "git_error": git_error,
    }
    record.setdefault("versions", []).append(entry)
    record.setdefault("status_history", []).append(
        {"at": _now(), "event": "published", "version": version}
    )
    return save_project_record(project_root, record)["versions"][-1]


# ── 3. 管理项目重新打开 ───────────────────────────────────────────────────────

def reopen_management_root(
    project_root: Path | str,
    goal_id: str,
    reason: str,
    backlog: Any,
) -> dict[str, Any]:
    """重新打开一个已结束（非 active）的管理项目。

    - reason 必填；
    - 目标节点状态改为 active（通过目标树的 set_status，不直接写 goals.json）；
    - 历史事件写入总表 management_roots[goal_id].history，只追加。
    """
    if not reason or not reason.strip():
        raise RegistryError("重新打开管理项目必须给出原因")
    index = load_index(project_root)
    if goal_id not in index["management_roots"]:
        raise RegistryError(f"{goal_id!r} 不是管理项目")
    node = backlog.get(goal_id)
    if node is None:
        raise RegistryError(f"目标节点不存在：{goal_id!r}")
    if node.status == "active":
        raise RegistryError("该管理项目已经是活跃状态，无需重新打开")

    previous = node.status
    ok = backlog.set_status(goal_id, "active", actor="output_registry", reason=reason.strip())
    if not ok:
        raise RegistryError("目标树拒绝将该节点设为活跃状态")
    append_management_history(
        project_root, goal_id,
        {"event": "reopened", "from_status": previous, "reason": reason.strip()},
    )
    return {"goal_id": goal_id, "from_status": previous, "status": "active"}
