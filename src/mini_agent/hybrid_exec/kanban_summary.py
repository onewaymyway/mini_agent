"""
hybrid_exec/kanban_summary.py — 供 kanban 一次性拉取的只读汇总

对应 next_doc/hybrid_exec_design_plan.md §6/§8 P4。

只读、单点失败不影响其它 task（与 routes.py::get_feedback_loop_summary
一致的风格）：扫描 `.agent/hybrid_exec/scripts/` 和 `.agent/hybrid_exec/runs/`
两个目录，按 task_id 合并成一份摘要，供 API 层（routes.py 新增的
`GET /v1/hybrid_exec/summary`）直接透传给看板，不需要看板前端分别拼多个
请求，也不需要看板知道 ScriptRepository/RunRecorder 内部的存储细节。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .recorder import RunRecorder
from .repository import ScriptRepository


def build_kanban_summary(project_root: "Path | str") -> dict:
    """扫描当前项目下所有 hybrid_exec 任务，返回：
        {"tasks": [ {task_id, active_version, active_status,
                      active_success_count, active_fail_count,
                      active_consecutive_fail, version_count,
                      run_summary: {...} | None}, ... ] }
    按 task_id 字典序排列，便于看板渲染成稳定顺序的表格。
    """
    project_root = Path(project_root)
    scripts_dir = project_root / ".agent" / "hybrid_exec" / "scripts"
    runs_dir = project_root / ".agent" / "hybrid_exec" / "runs"

    task_ids: set = set()
    if scripts_dir.is_dir():
        task_ids.update(p.name for p in scripts_dir.iterdir() if p.is_dir())
    if runs_dir.is_dir():
        task_ids.update(p.name for p in runs_dir.iterdir() if p.is_dir())

    repo = ScriptRepository(scripts_dir)
    recorder = RunRecorder(runs_dir)

    tasks: "list[dict[str, Any]]" = []
    for task_id in sorted(task_ids):
        entry: "dict[str, Any]" = {"task_id": task_id}
        try:
            active = repo.get_active_script(task_id)
            versions = repo.list_versions(task_id)
            entry["version_count"] = len(versions)
            if active is not None:
                entry["active_version"] = active.version
                entry["active_status"] = active.status
                entry["active_created_by"] = active.created_by
                entry["active_success_count"] = active.success_count
                entry["active_fail_count"] = active.fail_count
                entry["active_consecutive_fail"] = active.consecutive_fail
            else:
                entry["active_version"] = None
                entry["active_status"] = "none"  # 从未产出过脚本，或已全部退役
        except Exception as e:  # noqa: BLE001 — 单个 task 读取失败不应影响其它 task
            entry["_script_error"] = str(e)

        try:
            entry["run_summary"] = recorder.get_summary(task_id)
        except Exception as e:  # noqa: BLE001
            entry["_run_error"] = str(e)

        tasks.append(entry)

    return {"tasks": tasks}
