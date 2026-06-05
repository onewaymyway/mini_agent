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
"""

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

__all__ = [
    "Task", "TaskRecord", "TaskResult", "TaskStatus",
    "TaskManager", "SubAgent",
    "TaskDashboard", "print_task_table", "print_task_log",
    "CountingSemaphore", "init_concurrency",
    "get_task_sem", "get_llm_sem", "set_max_tasks", "set_max_llm_calls",
    "concurrency_snapshot", "StatusBar", "start_status_bar", "stop_status_bar", "suppress_bar", "unsuppress_bar",
]
