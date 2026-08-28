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
    _cron_ctx_local.run_id = None


def get_current_cron_job_id() -> str:
    """返回当前线程正在执行的 cron job 的 id；未设置（交互式对话/CLI/测试
    等非 cron 场景）时返回 `DEFAULT_JOB_ID`（`"adhoc"`）。"""
    return getattr(_cron_ctx_local, "job_id", DEFAULT_JOB_ID) or DEFAULT_JOB_ID


def set_current_cron_run_id(run_id: str) -> None:
    """[cron_async_feedback_hardening_plan.md D6] 由 `CronJobExecutor.
    run_job()` 在生成本次触发的 `run_id` 之后调用，跟 `job_id` 一样写进
    thread-local。

    背景：`cron_job_runner.py` 的 watchdog（`reap_stale_jobs()`）判定某次
    run 卡死后会代替它释放并发槽位，但**卡死的旧线程本身杀不掉**，可能
    成为孤儿线程继续跑，事后才执行到 `ask_user_async`——这次调用写入
    `questions_store` 时，`job.id` 依然读得到（同一线程），但这次 run 早
    已经被判定"放弃"了。单靠 `job_id` 无法区分"这是当前这次触发问的"还是
    "上一次已经被放弃的那次触发迟到问的"。

    把 `run_id` 也一并透传，写入问题记录的 `run_id` 字段（仅用于事后
    审计识别，不做写入时的拦截——拦截需要 `ask_user_async` 反查
    `CronJobRunner` 当前合法 run 的状态，跨模块耦合成本高，本轮不做）。
    调用方（看板/审计工具）可以拿这个字段跟对应
    `CronJobWorkspace.read_state().last_run_id` 比较：不一致就说明这条
    问题来自一次已经不是"当前最新"的 run，可能是孤儿线程迟到写入的。
    """
    _cron_ctx_local.run_id = run_id or ""


def get_current_cron_run_id() -> str:
    """返回当前线程正在执行的 cron run 的 run_id；未设置时返回空字符串
    （非 cron 场景、或 cron 场景下调用方还没来得及设置）。"""
    return getattr(_cron_ctx_local, "run_id", "") or ""


__all__ = [
    "set_current_cron_job_id",
    "clear_current_cron_job_id",
    "get_current_cron_job_id",
    "set_current_cron_run_id",
    "get_current_cron_run_id",
    "DEFAULT_JOB_ID",
]
