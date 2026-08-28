"""evolution/cron_context.py — 当前线程正在执行的 cron job_id 的 thread-local 透传

设计背景见 next_doc/cron_async_user_feedback_mechanism_plan.md。

跟 `perception/turn_context.py::set_current_turn_initiator()` 完全相同的
thread-local 模式：`CronJobExecutor.run_job()` 在开始执行一次 job 之前，
把 `job.id` 写进 thread-local；`tools/ask_user_async.py` 里的
`ask_user_async` 工具在被调用时读取这个 thread-local，用来判断"这个问题
属于哪个 cron job"，从而写入 `questions_store` 时带上正确的 `job_id`，
下次该 job 触发时 `CronJobWorkspace.render_prompt()` 才能按 job_id 查到
对应的已回答/待回答问题。

`CronJobExecutor.run_job()` 是同步阻塞调用，在 `AutonomousLoop._tick_passive()`
所在的 cron 执行专属线程上跑完整个 job（含内部多次 submit_step_fn 调用），
同一线程内构造/运行的 cron 专用 Agent（`cron_agent_bridge.build_cron_agent()`）
调用工具时读到的就是这里写入的值——与 notepad.py/evolution.py 等模块的
thread-local provider 遵循同一套"并发单元是线程"的既有假设。

非 cron 场景（交互式对话里直接调用 `ask_user_async`）下这个 thread-local
从未被设置，`get_current_cron_job_id()` 返回默认值 `"adhoc"`——问题仍会
被正常创建和通知，只是不会被任何 `render_prompt()` 自动续接消费，看板历史
记录里仍然可见。
"""

from __future__ import annotations

import threading as _threading

_cron_ctx_local = _threading.local()

DEFAULT_JOB_ID = "adhoc"


def set_current_cron_job_id(job_id: str) -> None:
    """由 `CronJobExecutor.run_job()` 在开始执行一次 job 之前调用。必须在
    执行这个 job 的同一条线程上调用——job 内部构造的 cron 专用 Agent 及其
    工具调用运行在同一线程，读到的就是这里写入的值。"""
    _cron_ctx_local.job_id = job_id or DEFAULT_JOB_ID


def clear_current_cron_job_id() -> None:
    """一次 job 执行结束后调用（`run_job()` 的 `finally` 块），避免
    thread-local 残留到下一次非 cron 触发的调用（同一条 cron 执行线程之后
    可能被复用去做别的事）。"""
    _cron_ctx_local.job_id = DEFAULT_JOB_ID


def get_current_cron_job_id() -> str:
    """返回当前线程正在执行的 cron job 的 id；未设置（交互式对话/CLI/测试
    等非 cron 场景）时返回 `DEFAULT_JOB_ID`（`"adhoc"`）。"""
    return getattr(_cron_ctx_local, "job_id", DEFAULT_JOB_ID) or DEFAULT_JOB_ID


__all__ = [
    "set_current_cron_job_id",
    "clear_current_cron_job_id",
    "get_current_cron_job_id",
    "DEFAULT_JOB_ID",
]
