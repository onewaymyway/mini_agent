"""
orchestrator — 并发任务调度与 Sub-Agent 管理

公共 API：
    from orchestrator import Task, TaskManager, TaskDashboard
    from orchestrator import TaskStatus, TaskResult, TaskRecord

快速使用：
    mgr = TaskManager(cfg, max_workers=4)
    mgr.start()
    t1 = mgr.submit(Task(prompt="Write unit tests for parser.py"))
    t2 = mgr.submit(Task(prompt="Fix the bug in utils.py", depends_on=[t1]))
    dashboard = TaskDashboard(mgr)
    dashboard.run_until_done()
    mgr.stop()

加载方式（Phase 10 · orchestrator 拆分评估 Step 1）：
    本包的再导出是**惰性**的（PEP 562 模块级 ``__getattr__``）——``__all__`` 与
    原有 21 个名字的用法完全不变，但只有真正访问某个名字时才会加载它所在的子模块。

    原因：旧写法在 ``import mini_agent.orchestrator`` 时急切加载 ``sub_agent``，
    而 ``sub_agent`` 顶层会 ``from mini_agent.agent import Agent``，导致 LLM 层
    （``llm/providers/_base_mixin.py``、``llm/retry.py`` 只为用 ``concurrency``）
    间接拉起整个 Agent/工具层。详见
    next_doc/refactor_plan/15-phase10-orchestrator-split-assessment.md。

    回退方式：把本文件还原为急切 import 版本即可（单文件改动，没有调用方依赖本改动）。
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

# 名字 -> 所在子模块（相对本包）。新增再导出名字时只需在这里加一行，
# 并同步 __all__；tests/test_orchestrator_lazy_init.py 会校验两者一致。
_LAZY_EXPORTS: dict[str, str] = {
    # task
    "Task": ".task",
    "TaskRecord": ".task",
    "TaskResult": ".task",
    "TaskStatus": ".task",
    # task_manager / sub_agent
    "TaskManager": ".task_manager",
    "SubAgent": ".sub_agent",
    # task_display
    "TaskDashboard": ".task_display",
    "print_task_table": ".task_display",
    "print_task_log": ".task_display",
    # concurrency
    "CountingSemaphore": ".concurrency",
    "init_concurrency": ".concurrency",
    "get_task_sem": ".concurrency",
    "get_llm_sem": ".concurrency",
    "set_max_tasks": ".concurrency",
    "set_max_llm_calls": ".concurrency",
    "concurrency_snapshot": ".concurrency",
    # status_bar
    "StatusBar": ".status_bar",
    "start_status_bar": ".status_bar",
    "stop_status_bar": ".status_bar",
    "suppress_bar": ".status_bar",
    "unsuppress_bar": ".status_bar",
}

__all__ = [
    "Task", "TaskRecord", "TaskResult", "TaskStatus",
    "TaskManager", "SubAgent",
    "TaskDashboard", "print_task_table", "print_task_log",
    "CountingSemaphore", "init_concurrency",
    "get_task_sem", "get_llm_sem", "set_max_tasks", "set_max_llm_calls",
    "concurrency_snapshot", "StatusBar", "start_status_bar", "stop_status_bar", "suppress_bar", "unsuppress_bar",
]


def __getattr__(name: str):
    """PEP 562：首次访问再导出名字时才加载对应子模块，并缓存到本模块命名空间。"""
    submodule = _LAZY_EXPORTS.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(submodule, __name__), name)
    globals()[name] = value  # 缓存：后续访问不再走 __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


if TYPE_CHECKING:  # 仅供类型检查器/IDE 识别，运行时不执行
    from .task import Task, TaskRecord, TaskResult, TaskStatus
    from .task_manager import TaskManager
    from .sub_agent import SubAgent
    from .task_display import TaskDashboard, print_task_table, print_task_log
    from .concurrency import (
        CountingSemaphore, init_concurrency,
        get_task_sem, get_llm_sem,
        set_max_tasks, set_max_llm_calls, concurrency_snapshot,
    )
    from .status_bar import StatusBar, start_status_bar, stop_status_bar, suppress_bar, unsuppress_bar
