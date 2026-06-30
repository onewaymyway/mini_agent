"""
workflow/schema.py — 工作流数据模型

WorkflowDef   工作流定义（由 YAML/JSON 反序列化而来）
WorkflowStep  单个步骤定义
StepStatus    步骤运行时状态
StepResult    步骤执行结果

YAML 格式示例：
  name: code_review
  description: 代码审查完整流程
  version: "1.0"
  steps:
    - id: analyze
      name: 静态分析
      prompt: |
        分析以下代码的结构和潜在问题：
        {code}
      role: null        # null = 主 Agent 执行

    - id: evaluate
      name: 质量评估
      prompt: |
        对分析结果评分：{analyze.output}
      depends_on: [analyze]
      role: evaluator   # 指定角色 Agent

    - id: report
      name: 生成报告
      prompt: 综合以上内容生成报告。分析：{analyze.output}
      depends_on: [evaluate]
      condition: "evaluate.score >= 6"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class StepStatus(str, Enum):
    PENDING      = "pending"
    RUNNING      = "running"
    DONE         = "done"
    SKIPPED      = "skipped"      # condition 不满足
    FAILED       = "failed"
    GATE_FAILED  = "gate_failed"  # evaluator 评分不达标（质检门未通过）


@dataclass
class WorkflowStep:
    """工作流中的单个步骤（定义层）。"""
    id: str                              # 步骤唯一标识，用于依赖引用
    name: str                            # 可读名称
    prompt: str                          # Prompt 模板，支持 {step_id.output} 占位符
    role: Optional[str] = None           # 指定角色 Agent name；None = 主 Agent
    depends_on: list[str] = field(default_factory=list)  # 依赖的步骤 id 列表
    condition: Optional[str] = None      # 执行条件表达式，如 "evaluate.score >= 6"
    max_turns: int = 10                  # 该步骤允许的最大 LLM 轮数
    model: Optional[str] = None          # 覆盖 model（None = 继承全局）
    timeout: Optional[float] = None      # 步骤超时（秒）
    retry_on_gate_fail: int = 0          # evaluator 不达标时，重跑前序步骤的最大次数（0=不重跑）
    # [具身改进 B3] 是否允许与同一拓扑层的其他步骤并发执行。
    # 默认 True；若某步骤有 depends_on 未声明的隐式副作用（如读写同一外部文件/
    # 状态），可显式设为 False 强制串行，保留人工对并发风险的控制权。
    allow_parallel: bool = True


@dataclass
class WorkflowDef:
    """完整的工作流定义。"""
    name: str
    steps: list[WorkflowStep]
    description: str = ""
    version: str = "1.0"

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowDef":
        """从字典（YAML 解析结果）反序列化。"""
        raw_steps = data.get("steps", [])
        steps = []
        for s in raw_steps:
            if not isinstance(s, dict):
                continue
            steps.append(WorkflowStep(
                id=str(s.get("id", "")),
                name=str(s.get("name", s.get("id", ""))),
                prompt=str(s.get("prompt", "")),
                role=s.get("role"),   # None 保持 None
                depends_on=list(s.get("depends_on", [])),
                condition=s.get("condition"),
                max_turns=int(s.get("max_turns", 10)),
                model=s.get("model"),
                timeout=float(s["timeout"]) if s.get("timeout") else None,
                retry_on_gate_fail=int(s.get("retry_on_gate_fail", 0)),
                allow_parallel=bool(s.get("allow_parallel", True)),
            ))
        return cls(
            name=str(data.get("name", "unnamed")),
            steps=steps,
            description=str(data.get("description", "")),
            version=str(data.get("version", "1.0")),
        )

    def to_dict(self) -> dict:
        """序列化为字典（用于 YAML 存储）。"""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "steps": [
                {
                    "id": s.id,
                    "name": s.name,
                    "prompt": s.prompt,
                    **({"role": s.role} if s.role else {}),
                    **({"depends_on": s.depends_on} if s.depends_on else {}),
                    **({"condition": s.condition} if s.condition else {}),
                    **({"max_turns": s.max_turns} if s.max_turns != 10 else {}),
                    **({"model": s.model} if s.model else {}),
                    **({"timeout": s.timeout} if s.timeout else {}),
                    **({"retry_on_gate_fail": s.retry_on_gate_fail} if s.retry_on_gate_fail else {}),
                    **({"allow_parallel": s.allow_parallel} if not s.allow_parallel else {}),
                }
                for s in self.steps
            ],
        }

    def validate(self) -> list[str]:
        """校验工作流定义，返回错误列表（空列表=合法）。"""
        errors: list[str] = []
        seen_ids: set[str] = set()

        for step in self.steps:
            if not step.id:
                errors.append("存在 id 为空的步骤")
            elif step.id in seen_ids:
                errors.append(f"步骤 id 重复：{step.id!r}")
            else:
                seen_ids.add(step.id)

            if not step.prompt.strip():
                errors.append(f"步骤 {step.id!r} 的 prompt 为空")

        # 检查依赖是否存在
        for step in self.steps:
            for dep in step.depends_on:
                if dep not in seen_ids:
                    errors.append(f"步骤 {step.id!r} 依赖不存在的步骤 {dep!r}")

        return errors


@dataclass
class StepResult:
    """步骤执行结果（运行时产生）。"""
    step_id: str
    status: StepStatus
    output: str = ""
    score: Optional[float] = None    # evaluator 角色才会填写
    error: Optional[str] = None
    duration_seconds: float = 0.0
