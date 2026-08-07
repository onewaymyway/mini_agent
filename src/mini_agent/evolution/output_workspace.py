"""
evolution/output_workspace.py — Goal/Cron 周期性执行的产出目录规范
（next_doc/goal_cron_output_directory_convention_plan.md）

目录结构（<project_root>/outputs/）：
    goals/<goal_id>/
        latest.json           指针文件：{"latest_dir": "cycle_0003", "updated_at": ...}
        cycle_0001/manifest.json
        cycle_0002/manifest.json
        ...
    cron/<job_id>/            job_id 里的 ':' 换成 '_'，与 CronJobWorkspace 一致
        latest.json
        run_<run_id>/manifest.json
        ...

本模块只负责 §2/§3 的目录分配和 manifest 读写，不关心"什么时候该分配/
该写"——那部分逻辑分别在 cron_job_executor.py（dedicated-execution cron）、
goal_cron_bridge.py（recurring Goal 触发时分配目录 + 拼 prompt）、
objective_executor.py（recurring Goal 对应的 Objective 收尾时落 manifest）。

不用符号链接（跨平台，Windows 默认无权限创建），"最新一轮"用 latest.json
这个小指针文件表达。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from mini_agent.utils.atomic_write import atomic_write_json

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


MANIFEST_VERSION = 1


# ── 目录归属 ──────────────────────────────────────────────────────────────────

def outputs_root(paths: "AgentPaths") -> Path:
    return Path(paths.project_root) / "outputs"


def goal_output_base_dir(paths: "AgentPaths", goal_id: str) -> Path:
    return outputs_root(paths) / "goals" / goal_id


def cron_output_base_dir(paths: "AgentPaths", job_id: str) -> Path:
    # job_id 里可能含 ':'，文件系统里用 '_' 替换，与 CronJobWorkspace 的
    # safe_id 规则保持一致，方便用户对照 .agent/cron_jobs/<safe_id>/ 找到
    # 对应的 outputs/cron/<safe_id>/。
    safe_id = job_id.replace(":", "_")
    return outputs_root(paths) / "cron" / safe_id


# ── 目录分配 ──────────────────────────────────────────────────────────────────

def allocate_cycle_dir(paths: "AgentPaths", goal_id: str, cycle: int) -> Path:
    """为 recurring Goal 的第 `cycle` 轮分配（幂等）产出目录，返回已存在的
    绝对路径。cycle 编号取 GoalNode.cycle_count + 1（触发前的值 +1）。
    """
    d = goal_output_base_dir(paths, goal_id) / f"cycle_{cycle:04d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def allocate_run_dir(paths: "AgentPaths", job_id: str, run_id: str) -> Path:
    """为普通 CronJob（非 goal_cycle）的一次触发分配（幂等）产出目录。"""
    d = cron_output_base_dir(paths, job_id) / f"run_{run_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── manifest 读写 ─────────────────────────────────────────────────────────────

def _latest_path(base_dir: Path) -> Path:
    return base_dir / "latest.json"


def write_manifest(
    base_dir: Path,
    cycle_dir: Path,
    *,
    task_summary: str = "",
    started_at: float = 0.0,
    finished_at: float = 0.0,
    status: str = "completed",
    artifacts: Optional[list[dict]] = None,
    progress_note: str = "",
    extra: Optional[dict] = None,
) -> Path:
    """把这一轮/这一次触发的产出清单写入 `cycle_dir/manifest.json`，并更新
    `base_dir/latest.json` 指向这一轮。

    base_dir  — goal_output_base_dir()/cron_output_base_dir() 的返回值
    cycle_dir — allocate_cycle_dir()/allocate_run_dir() 的返回值，必须是
                base_dir 的直接子目录
    """
    manifest = {
        "version": MANIFEST_VERSION,
        "dir_name": cycle_dir.name,
        "task_summary": task_summary,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "artifacts": artifacts or [],
        "progress_note": progress_note,
    }
    if extra:
        manifest.update(extra)

    # previous_cycle_dir：manifest 自己就是一条链表，读上一轮的 latest.json
    # 拿到"这一轮之前"的目录名（写入 latest.json 之前读，避免读到自己）。
    prev = _read_latest_pointer(base_dir)
    if prev and prev != cycle_dir.name:
        manifest["previous_cycle_dir"] = str((base_dir / prev).as_posix())
    else:
        manifest["previous_cycle_dir"] = None

    cycle_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(cycle_dir / "manifest.json", manifest)
    atomic_write_json(_latest_path(base_dir), {
        "latest_dir": cycle_dir.name,
        "updated_at": time.time(),
    })
    return cycle_dir / "manifest.json"


def _read_latest_pointer(base_dir: Path) -> Optional[str]:
    path = _latest_path(base_dir)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        latest_dir = d.get("latest_dir")
        return latest_dir if latest_dir else None
    except (OSError, json.JSONDecodeError):
        return None


def read_latest_manifest(base_dir: Path) -> Optional[dict]:
    """读 `base_dir/latest.json` 拿到最新一轮目录名，再读该目录下的
    manifest.json。没有任何历史轮次（latest.json 不存在/损坏，或指向的
    manifest.json 缺失）时返回 None——调用方据此判断"没有上一轮产出"。
    """
    latest_dir = _read_latest_pointer(base_dir)
    if not latest_dir:
        return None
    manifest_path = base_dir / latest_dir / "manifest.json"
    try:
        d = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    d.setdefault("previous_cycle_dir", None)
    d["_dir"] = str((base_dir / latest_dir).as_posix())
    return d


# ── prompt 注入格式化 ─────────────────────────────────────────────────────────

def format_manifest_for_prompt(manifest: dict) -> str:
    """把 manifest 的 artifacts/progress_note 格式化成几行文本，供
    {{previous_output}} 占位符注入。"""
    lines: list[str] = []
    task_summary = (manifest.get("task_summary") or "").strip()
    if task_summary:
        lines.append(f"上轮任务：{task_summary}")
    artifacts = manifest.get("artifacts") or []
    if artifacts:
        lines.append("产出文件：")
        for a in artifacts:
            path = a.get("path", "") if isinstance(a, dict) else str(a)
            desc = a.get("description", "") if isinstance(a, dict) else ""
            if not path:
                continue
            lines.append(f"- {path}" + (f"：{desc}" if desc else ""))
    progress_note = (manifest.get("progress_note") or "").strip()
    if progress_note:
        lines.append(f"备注：{progress_note}")
    return "\n".join(lines)


__all__ = [
    "MANIFEST_VERSION",
    "outputs_root",
    "goal_output_base_dir",
    "cron_output_base_dir",
    "allocate_cycle_dir",
    "allocate_run_dir",
    "write_manifest",
    "read_latest_manifest",
    "format_manifest_for_prompt",
]
