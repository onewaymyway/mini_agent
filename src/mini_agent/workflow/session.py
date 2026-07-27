"""
workflow/session.py — WorkflowSession 执行会话（workflow机制改进计划.md P2）

一次 run_workflow 执行 = 一个 WorkflowSession，落盘在
`.agent/workflow_sessions/<workflow_session_id>/session.json`，
每完成一个 step 就增量写回，使得：
  - 进程崩溃后可通过 resume_workflow_run(workflow_session_id) 跳过已 DONE
    的步骤，只重跑未完成部分
  - 外部（CLI / 看板 / 另一次工具调用）可随时读取该文件查看实时进度，
    不依赖 stdout
  - pause/cancel/approve/reject 等控制信号通过 control_flags 字段传递，
    同进程内由 workflow/registry.py 的内存态优先响应，写盘只是为了在
    resume 场景下也能看到"上一次运行结束前的最终状态"
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from .schema import StepResult

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


class WorkflowRunStatus(str, Enum):
    RUNNING            = "running"
    PAUSED             = "paused"
    AWAITING_APPROVAL  = "awaiting_approval"
    DONE               = "done"
    FAILED             = "failed"
    PARTIAL            = "partial"
    CANCELLED          = "cancelled"


@dataclass
class WorkflowSession:
    """一次工作流执行的运行时状态（可增量落盘 / 可从磁盘恢复）。"""

    workflow_session_id: str
    workflow_name: str
    status: WorkflowRunStatus = WorkflowRunStatus.RUNNING
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    current_batch_index: int = 0
    inputs: dict = field(default_factory=dict)
    step_results: dict[str, StepResult] = field(default_factory=dict)
    # 控制信号：由外部工具（pause/resume/cancel/approve/reject）写入，
    # runner/watchdog 轮询响应。
    control_flags: dict = field(default_factory=lambda: {
        "pause_requested": False,
        "cancel_requested": False,
        "approved_steps": [],
        "rejected_steps": [],
    })
    pending_approval_step: Optional[str] = None
    error: Optional[str] = None
    # [workflow_mechanism_improvement_plan_p10.md §2] 记录最近一次
    # resume_workflow_run(step_overrides=...) 使用过的一次性覆盖内容，
    # 形如 {"step_id": {"timeout": 120}}。只影响本次 resume 执行、不写回
    # WorkflowStore 持久化的定义——这里落盘只是为了 get_workflow_run_status
    # 能提示"这次结果里有临时覆盖，不是定义本身的行为"，避免误读。
    # 空 dict 表示这次（或迄今为止）没有使用过 step_overrides。
    last_step_overrides: dict = field(default_factory=dict)
    # [output_export 功能] 用户在启动执行时可选传入的外部导出目录：
    # workflow 到达终态（done/failed/partial/cancelled）时，把本次执行
    # `.agent/workflow_sessions/<id>/output/` 目录下的所有文件复制到这里。
    # 不设置（None/空字符串）则跳过复制这一步，行为与新增前完全一致。
    # 落盘在 session 里而不是只作为 run() 的临时参数，是为了 resume 场景
    # （pause→resume、force_rerun_from 续跑）依然记得要复制到哪、不需要
    # 调用方每次都重新传一遍。
    output_export_dir: Optional[str] = None

    # ── 序列化 ──────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "workflow_session_id": self.workflow_session_id,
            "workflow_name": self.workflow_name,
            "status": self.status.value,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "current_batch_index": self.current_batch_index,
            "inputs": self.inputs,
            "step_results": {k: v.to_dict() for k, v in self.step_results.items()},
            "control_flags": self.control_flags,
            "pending_approval_step": self.pending_approval_step,
            "error": self.error,
            "last_step_overrides": self.last_step_overrides,
            "output_export_dir": self.output_export_dir,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowSession":
        return cls(
            workflow_session_id=str(data["workflow_session_id"]),
            workflow_name=str(data.get("workflow_name", "")),
            status=WorkflowRunStatus(data.get("status", "running")),
            started_at=float(data.get("started_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            current_batch_index=int(data.get("current_batch_index", 0)),
            inputs=dict(data.get("inputs", {})),
            step_results={
                k: StepResult.from_dict(v) for k, v in (data.get("step_results") or {}).items()
            },
            control_flags=dict(data.get("control_flags") or {
                "pause_requested": False,
                "cancel_requested": False,
                "approved_steps": [],
                "rejected_steps": [],
            }),
            pending_approval_step=data.get("pending_approval_step"),
            error=data.get("error"),
            last_step_overrides=dict(data.get("last_step_overrides") or {}),
            output_export_dir=data.get("output_export_dir"),
        )

    # ── 落盘 / 加载 ────────────────────────────────────────────────────────

    def save(self, paths: "AgentPaths") -> Path:
        self.updated_at = time.time()
        paths.ensure_workflow_session_dir(self.workflow_session_id)
        p = paths.workflow_session_meta(self.workflow_session_id)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    @classmethod
    def load(cls, paths: "AgentPaths", workflow_session_id: str) -> Optional["WorkflowSession"]:
        p = paths.workflow_session_meta(workflow_session_id)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.workflow.session.WorkflowSession.load")
            return None

    # ── 事件流 ──────────────────────────────────────────────────────────────

    def append_event(self, paths: "AgentPaths", event_type: str, data: Optional[dict] = None) -> None:
        """向 events.jsonl 追加一条结构化事件（只追加，不影响 session.json 本身）。"""
        paths.ensure_workflow_session_dir(self.workflow_session_id)
        p = paths.workflow_session_events(self.workflow_session_id)
        record = {
            "ts": time.time(),
            "event": event_type,
            "workflow_session_id": self.workflow_session_id,
            **(data or {}),
        }
        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.workflow.session.WorkflowSession.append_event")

    # ── 便捷查询 ────────────────────────────────────────────────────────────

    def summary_line(self) -> str:
        done = sum(1 for sr in self.step_results.values() if sr.status.value == "done")
        total = len(self.step_results)
        return (
            f"[{self.workflow_session_id}] {self.workflow_name} "
            f"status={self.status.value} steps={done}/{total or '?'}"
        )

    def export_output_files(self, paths: "AgentPaths") -> Optional[dict]:
        """把本次执行 `output/` 目录下的所有文件复制到 `self.output_export_dir`。

        未设置 `output_export_dir` 时直接返回 None（不做任何事，也不报错）。
        目标目录不存在会自动创建；复制失败（权限/磁盘等问题）不抛异常、
        不影响 workflow 本身已经产出的结果——只在返回值里带上 error 供
        调用方决定是否展示给用户，因为"导出产物"是锦上添花的收尾动作，
        不应该让一次已经跑完的 workflow 因为这一步失败而被判定为异常。
        """
        if not self.output_export_dir:
            return None

        import shutil

        src_dir = paths.workflow_session_output_dir(self.workflow_session_id)
        dest_dir = Path(self.output_export_dir)
        result = {"dest_dir": str(dest_dir), "copied_files": [], "error": None}

        try:
            if not src_dir.exists():
                return result  # 没有产出文件，视为"复制了 0 个"，不算错误
            dest_dir.mkdir(parents=True, exist_ok=True)
            for item in sorted(src_dir.rglob("*")):
                if item.is_dir():
                    continue
                rel = item.relative_to(src_dir)
                target = dest_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
                result["copied_files"].append(str(rel))
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.workflow.session.WorkflowSession.export_output_files")
            result["error"] = str(e)

        return result
