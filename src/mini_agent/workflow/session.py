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
