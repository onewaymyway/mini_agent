"""
hybrid_exec/spec.py — 混合执行系统的核心数据结构

对应 next_doc/hybrid_exec_design_plan.md §3.1/§3.2/§3.8。

设计取舍（已与用户确认，见方案文档 §9）：
  - task_id 粒度：一个 task_id 只对应仓库里一个 active 版本，不按输入结构
    指纹再细分分支版本（MVP 范围）。
  - output_validator 不强制要求调用方传：不传时，脚本/LLM/Agent 执行过程中
    不抛异常即视为成功；传了则以校验结果为准。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


class ExecutionTier(str, Enum):
    """三种执行手段，按执行成本从低到高排列（同时也是泛化能力从低到高）。"""

    SCRIPT = "script"
    LLM = "llm"
    AGENT = "agent"


# 校验器：入参是本次执行产出的 output，返回 (是否通过, 原因说明)。
OutputValidator = Callable[[Any], "tuple[bool, str]"]


def default_validator(_output: Any) -> "tuple[bool, str]":
    """默认校验器：不抛异常即视为成功（§9 确认）。"""
    return True, "未提供 output_validator，默认按“未抛异常”判定成功"


@dataclass
class TaskSpec:
    """一次混合执行任务的定义，由调用方构造并传给 HybridExecutor.run()。"""

    task_id: str
    description: str
    input_data: dict = field(default_factory=dict)
    output_validator: Optional[OutputValidator] = None
    context_files: list = field(default_factory=list)
    allow_tiers: "tuple[ExecutionTier, ...]" = (
        ExecutionTier.SCRIPT,
        ExecutionTier.LLM,
        ExecutionTier.AGENT,
    )
    max_script_repair_attempts: int = 2
    force_reexplore: bool = False
    # Explorer/Repairer 拉起的 Agent 是否允许写文件系统，默认关闭（§9 确认）。
    # 关闭时 Agent 仍可探索/修复，但走只读沙箱（P2 结合 PermissionGuard 细化）。
    agent_fs_write_enabled: bool = False
    # 单次脚本执行（含 dry-run）超时时间，秒。
    script_timeout_seconds: float = 60.0

    def run_validator(self, output: Any) -> "tuple[bool, str]":
        validator = self.output_validator or default_validator
        return validator(output)


@dataclass
class ScriptOutcome:
    """ScriptRunner.run() 的返回值，对应 py_step_runner.py 的结果包协议。"""

    ok: bool
    output: Any = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    traceback: Optional[str] = None
    duration: float = 0.0
    stdout_tail: str = ""
    stderr_tail: str = ""


@dataclass
class AttemptRecord:
    """HybridExecutor 决策轨迹里的一条记录，用于事后复盘。"""

    stage: str  # 如 "script_run" / "script_repair#1" / "explore_llm" / "fallback_agent"
    tier: ExecutionTier
    ok: bool
    detail: str = ""
    duration: float = 0.0


@dataclass
class ExecutionResult:
    """HybridExecutor.run() 的最终返回值。"""

    ok: bool
    output: Any
    tier_used: ExecutionTier
    script_version: Optional[int]
    attempts: "list[AttemptRecord]" = field(default_factory=list)
    duration: float = 0.0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "output": self.output,
            "tier_used": self.tier_used.value,
            "script_version": self.script_version,
            "duration": self.duration,
            "attempts": [
                {
                    "stage": a.stage,
                    "tier": a.tier.value,
                    "ok": a.ok,
                    "detail": a.detail,
                    "duration": a.duration,
                }
                for a in self.attempts
            ],
        }
