"""
evolution/workflow_run_cleanup.py — 工作流运行目录（.agent/workflow_sessions/<wid>/）的清理。

背景见 next_doc/session_cleanup_scope_expansion_plan.md。每次工作流运行都会留下：
session.json、events.jsonl、watchdog.jsonl、各 step 子 Agent 会话目录（<agent_sid>/，
内含 raw_history 等），以及 output/（用户产出，output_registry 把它列为产出位置）。

判定规则（自上而下，命中即保留）：
  1. 无 session.json：目录年龄 < orphan_min_age_hours 保留（可能正在启动）；
     超过安全窗口且无产出 → 删除；有产出 → 保留。
  2. 状态非终结（running/paused/awaiting_approval/未知）→ 保留。崩溃遗留的假
     running 不自动删，需先在工作流页面 mark_interrupted。
  3. output/ 下有文件 → 仅在 protect_outputs=True 时保留（默认 False，用户已决定
     关闭产出保护）；关闭时照常删除，但 output_files 会记录产出文件数供预览提示。
  4. 按 updated_at 倒序，最近 keep_recent_count 个保留。
  5. updated_at 在 keep_recent_days 之内保留。
  6. 其余（done/failed/partial/cancelled）→ 删除。

删除时同时清理运行目录下每个子目录名对应的 raw_results/<名字>/（step 子 Agent 原文）。
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_TERMINAL_STATUSES = frozenset({"done", "failed", "partial", "cancelled"})


@dataclass
class WorkflowRunItem:
    run_id: str
    status: str            # 原始 status；无 session.json 时为 "unknown"
    updated_at: str        # ISO 字符串
    size_bytes: int
    output_files: int
    action: str            # "keep" | "delete" | "failed"
    reason: str
    raw_results_bytes: int = 0
    # 非终结状态且超过保留窗口未更新：疑似崩溃/中断遗留。只报告，永不自动删除。
    stale: bool = False

    def to_dict(self) -> dict:
        return {
            "stale": self.stale,
            "run_id": self.run_id,
            "status": self.status,
            "updated_at": self.updated_at,
            "size_bytes": self.size_bytes,
            "output_files": self.output_files,
            "action": self.action,
            "reason": self.reason,
            "raw_results_bytes": self.raw_results_bytes,
        }


def _count_files(path: Path) -> int:
    from mini_agent.evolution.session_cleanup import _walk_stats
    return _walk_stats(path)[0]


def _size(path: Path) -> int:
    from mini_agent.evolution.session_cleanup import _walk_stats
    return _walk_stats(path)[1]


def _last_activity(path: Path) -> float:
    from mini_agent.evolution.session_cleanup import _dir_last_activity
    return _dir_last_activity(path)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _nested_dir_names(run_dir: Path) -> set[str]:
    """运行目录下所有子目录名（step 子 Agent 的 session id 即在其中）。只遍历目录。"""
    from mini_agent.evolution.session_cleanup import _subdir_names_recursive
    return _subdir_names_recursive(run_dir)


def _read_session_json(run_dir: Path) -> Optional[dict]:
    import json
    meta = run_dir / "session.json"
    if not meta.is_file():
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def scan_workflow_runs(
    project_root,
    *,
    keep_recent_days: float = 30,
    keep_recent_count: int = 20,
    orphan_min_age_hours: float = 6.0,
    protect_outputs: bool = False,
) -> list[WorkflowRunItem]:
    """只分类，不删除。返回所有运行目录的判定结果（含 keep 与 delete）。

    性能约定：判定只依赖 session.json 的 updated_at（一次小文件读取），不递归遍历
    运行目录。大小与产出文件数只对真正要删除（或 protect_outputs 需要判断）的条目
    才计算，避免在上千个运行目录上做整树 rglob（曾导致外部项目清理超过 30 秒）。
    无 session.json 的残留目录是少数情况，才会做一次递归活动时间判断。
    """
    from mini_agent.storage.paths import AgentPaths

    try:
        base = AgentPaths(Path(project_root)).workflow_sessions_dir
    except Exception:
        return []
    if not base.is_dir():
        return []

    now = time.time()
    age_cutoff = now - orphan_min_age_hours * 3600.0
    keep_days_cutoff = now - keep_recent_days * 86400.0

    try:
        run_dirs = [d for d in base.iterdir() if d.is_dir()]
    except OSError:
        return []

    items: list[WorkflowRunItem] = []
    terminal_candidates: list[tuple[float, WorkflowRunItem, Path]] = []

    for d in run_dirs:
        data = _read_session_json(d)

        if data is None:
            # 无 session.json：少数情况，递归取活动时间判断是否正在启动
            last = _last_activity(d)
            size = _size(d)
            out_n = _count_files(d / "output") if (d / "output").is_dir() else 0
            if last >= age_cutoff:
                reason = f"无 session.json，目录年龄 < {orphan_min_age_hours} 小时，可能正在启动"
                action = "keep"
            elif out_n and protect_outputs:
                reason = f"无 session.json 的残留，但含产出文件 {out_n} 个，受保护"
                action = "keep"
            else:
                reason = "无 session.json 且超过安全窗口，判定为孤儿运行目录"
                if out_n:
                    reason += f"（含产出文件 {out_n} 个，保护已关闭，将一并删除）"
                action = "delete"
            items.append(WorkflowRunItem(
                run_id=d.name, status="unknown", updated_at=_iso(last),
                size_bytes=size, output_files=out_n, action=action, reason=reason,
            ))
            continue

        status = str(data.get("status", "unknown"))
        try:
            ts = float(data.get("updated_at", 0.0))
        except (TypeError, ValueError):
            ts = 0.0
        if ts <= 0:
            try:
                ts = d.stat().st_mtime
            except OSError:
                ts = now

        item = WorkflowRunItem(
            run_id=d.name, status=status, updated_at=_iso(ts),
            size_bytes=0, output_files=0, action="keep", reason="",
        )

        if status not in _TERMINAL_STATUSES:
            if ts < keep_days_cutoff:
                item.stale = True
                item.reason = (
                    f"状态仍为 {status} 且超过 {keep_recent_days} 天未更新，疑似中断遗留；"
                    "请先在工作流页面 mark_interrupted，再清理"
                )
            else:
                item.reason = f"状态非终结（{status}），保留"
            items.append(item)
            continue

        terminal_candidates.append((ts, item, d))

    # 终结状态：先按 updated_at 倒序决定保留窗口
    terminal_candidates.sort(key=lambda p: p[0], reverse=True)
    for idx, (ts, item, d) in enumerate(terminal_candidates):
        if idx < keep_recent_count:
            item.reason = f"最近 {keep_recent_count} 个运行之一，保留"
        elif ts >= keep_days_cutoff:
            item.reason = f"在最近 {keep_recent_days} 天内，保留"
        else:
            item.action = "delete"
            item.reason = f"运行已结束（{item.status}）且超出保留窗口，可清理"
        # 只有要删除（或保护开关需要判断）时才计算大小与产出数
        if item.action == "delete" or protect_outputs:
            out_n = _count_files(d / "output") if (d / "output").is_dir() else 0
            item.output_files = out_n
            if protect_outputs and out_n and item.action == "delete":
                item.action = "keep"
                item.reason = f"含产出文件 {out_n} 个，受保护"
            elif item.action == "delete" and out_n:
                item.reason += f"（含产出文件 {out_n} 个，保护已关闭，将一并删除）"
        if item.action == "delete":
            item.size_bytes = _size(d)
        items.append(item)

    return items


def cleanup_workflow_runs(
    project_root,
    *,
    keep_recent_days: float = 30,
    keep_recent_count: int = 20,
    orphan_min_age_hours: float = 6.0,
    dry_run: bool = True,
    protect_outputs: bool = False,
) -> list[WorkflowRunItem]:
    """扫描 + （可选）执行。返回全部判定结果；失败项 action="failed"。"""
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.evolution.session_cleanup import _clear_raw_results

    items = scan_workflow_runs(
        project_root,
        keep_recent_days=keep_recent_days,
        keep_recent_count=keep_recent_count,
        orphan_min_age_hours=orphan_min_age_hours,
        protect_outputs=protect_outputs,
    )
    if not items:
        return items

    base = AgentPaths(Path(project_root)).workflow_sessions_dir

    for item in items:
        if item.action != "delete":
            continue
        run_dir = base / item.run_id
        names = _nested_dir_names(run_dir) if run_dir.is_dir() else set()
        if dry_run:
            item.raw_results_bytes = sum(
                _clear_raw_results(project_root, n, dry_run=True)[0] for n in names
            )
            continue
        try:
            from mini_agent.utils.protected_files_guard import is_protected as _is_protected
            if not run_dir.is_dir():
                item.action = "failed"
                item.reason += "；目录已不存在"
                continue
            if _is_protected(run_dir, project_root):
                item.action = "failed"
                item.reason += "；命中受保护文件清单，跳过删除"
                continue
            shutil.rmtree(run_dir, ignore_errors=False)
            freed = 0
            for n in names:
                freed += _clear_raw_results(project_root, n, dry_run=False)[0]
            item.raw_results_bytes = freed
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc,
                          where='mini_agent.evolution.workflow_run_cleanup.cleanup_workflow_runs')
            item.action = "failed"
            item.reason += "；删除失败"

    return items


__all__ = ["WorkflowRunItem", "scan_workflow_runs", "cleanup_workflow_runs"]
