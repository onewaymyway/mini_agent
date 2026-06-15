"""
workflow — 工作流系统

核心能力：
  1. 定义：YAML/JSON 描述工作流，每个步骤有固定的 id/prompt/role/依赖关系
  2. 存储：保存到 .agent/workflows/，可列举、加载、版本管理
  3. 执行：WorkflowRunner 按依赖顺序执行步骤，支持条件分支和步骤间数据传递
  4. 生成：让 LLM 根据用户描述自动生成工作流定义

使用方式（主 Agent 调用工具）：
  generate_workflow("做一个代码审查流程")  → 生成 YAML，用户确认后保存
  run_workflow("code_review", {"code": "..."})  → 执行工作流
  list_workflows()  → 列举所有已保存工作流
"""

from .schema import WorkflowDef, WorkflowStep, StepStatus
from .store import WorkflowStore
from .runner import WorkflowRunner

__all__ = ["WorkflowDef", "WorkflowStep", "StepStatus", "WorkflowStore", "WorkflowRunner"]
