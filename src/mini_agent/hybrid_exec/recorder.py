"""
hybrid_exec/recorder.py — RunRecorder：run 记录落盘 + 聚合统计

对应 next_doc/hybrid_exec_design_plan.md §6（存储与可观测性），P3 范围。

存储布局（默认位于 <project_root>/.agent/hybrid_exec/runs/<task_id>/）：
    summary.json      # 滚动聚合统计（总次数、成功次数、各 tier 命中次数、最近一次时间/结果）
    <run_id>.json      # 单次 run 的完整决策轨迹（ExecutionResult.to_dict()）

设计取舍：
  - summary.json 是为了"不用扫描全部 run 文件就能快速看一眼这个 task 目前
    跑得怎么样"（比如未来 P4 判断是否要触发重新探索、或 kanban 面板要展示
    时直接读这一个文件）。单条 run 文件仍然全部保留，供需要时深挖细节。
  - 不做文件锁/并发写保护——hybrid_exec 目前的调用场景（workflow 单步骤
    执行、daemon 单次调用）本身就是串行的，同一 task_id 并发写 summary.json
    的情况极少；真出现极端并发场景，最坏结果是 summary.json 的统计出现
    轻微丢更新，不影响单条 run 文件的完整性，也不影响脚本仓库
    ScriptRepository 自己的成功/失败计数（那部分是独立准确的）。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional

from .spec import ExecutionResult


class RunRecorder:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)

    def _task_dir(self, task_id: str) -> Path:
        d = self.base_dir / task_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _summary_path(self, task_id: str) -> Path:
        return self._task_dir(task_id) / "summary.json"

    def record(self, task_id: str, result: ExecutionResult) -> Path:
        """写一条单次 run 记录，并更新该 task 的滚动 summary。返回 run 文件路径。"""
        task_dir = self._task_dir(task_id)
        run_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{uuid.uuid4().hex[:8]}"
        run_path = task_dir / f"{run_id}.json"
        run_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

        self._update_summary(task_id, result)
        return run_path

    def _update_summary(self, task_id: str, result: ExecutionResult) -> None:
        summary_path = self._summary_path(task_id)
        summary = self._load_summary(task_id)

        summary["total_runs"] = summary.get("total_runs", 0) + 1
        if result.ok:
            summary["success_runs"] = summary.get("success_runs", 0) + 1
        else:
            summary["fail_runs"] = summary.get("fail_runs", 0) + 1

        tier_counts = summary.setdefault("tier_counts", {})
        tier_key = result.tier_used.value
        tier_counts[tier_key] = tier_counts.get(tier_key, 0) + 1

        summary["last_run_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        summary["last_run_ok"] = result.ok
        summary["last_tier_used"] = tier_key
        summary["last_duration"] = result.duration

        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_summary(self, task_id: str) -> dict:
        summary_path = self._summary_path(task_id)
        if not summary_path.exists():
            return {}
        try:
            return json.loads(summary_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def get_summary(self, task_id: str) -> Optional[dict]:
        """读取某个 task 的滚动聚合统计，没有记录过则返回 None。"""
        summary_path = self._summary_path(task_id)
        if not summary_path.exists():
            return None
        return self._load_summary(task_id)

    def list_run_ids(self, task_id: str) -> "list[str]":
        task_dir = self.base_dir / task_id
        if not task_dir.is_dir():
            return []
        return sorted(p.stem for p in task_dir.glob("*.json") if p.stem != "summary")

    def load_run(self, task_id: str, run_id: str) -> Optional[dict]:
        run_path = self.base_dir / task_id / f"{run_id}.json"
        if not run_path.exists():
            return None
        return json.loads(run_path.read_text(encoding="utf-8"))
