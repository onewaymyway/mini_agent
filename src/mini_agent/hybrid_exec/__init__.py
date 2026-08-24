"""
mini_agent.hybrid_exec — 脚本/LLM/Agent 混合执行系统

设计文档：next_doc/hybrid_exec_design_plan.md

用法示例：
    from mini_agent.hybrid_exec import TaskSpec, default_executor

    executor = default_executor(project_root="/path/to/project")
    result = executor.run(TaskSpec(
        task_id="extract_entities_v1",
        description="从输入文本中抽取人名/机构名，返回 JSON 列表",
        input_data={"text": "..."},
    ))
    if result.ok:
        print(result.output)
"""

from .executor import HybridExecutor, default_executor
from .explorer import AgentExplorer, Explorer, LLMExplorer
from .fallback import FallbackExecutor
from .kanban_summary import build_kanban_summary
from .playbook_repository import PlaybookRecord, PlaybookRepository
from .playbook_runner import PlaybookInvalidError, PlaybookRunner
from .policy import ReexplorePolicy
from .recorder import RunRecorder
from .repairer import AgentRepairer, LLMRepairer, Repairer
from .repository import ScriptRecord, ScriptRepository
from .runner import RunnerAppConfig, ScriptRunner
from .spec import (
    AttemptRecord,
    ExecutionResult,
    ExecutionTier,
    OutputValidator,
    ScriptOutcome,
    TaskSpec,
    default_validator,
)

__all__ = [
    "HybridExecutor",
    "default_executor",
    "Explorer",
    "LLMExplorer",
    "AgentExplorer",
    "Repairer",
    "LLMRepairer",
    "AgentRepairer",
    "FallbackExecutor",
    "ScriptRepository",
    "ScriptRecord",
    "PlaybookRepository",
    "PlaybookRecord",
    "PlaybookRunner",
    "PlaybookInvalidError",
    "ScriptRunner",
    "RunnerAppConfig",
    "RunRecorder",
    "ReexplorePolicy",
    "build_kanban_summary",
    "TaskSpec",
    "ExecutionTier",
    "ExecutionResult",
    "AttemptRecord",
    "ScriptOutcome",
    "OutputValidator",
    "default_validator",
]
