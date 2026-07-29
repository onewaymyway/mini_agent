"""
evolution/cron_job_runner.py — cron 任务的后台线程调度器

背景（daemon 单任务阻塞问题）：
  AgentRunner（api/server.py）是一条单线程循环：dequeue InputQueue → 空闲时
  调用 AutonomousLoop.tick() → _tick_passive() → CronScheduler.tick()。
  如果 cron job 的执行（CronJobExecutor.run_job()）直接同步跑在这条调用链
  里，一个耗时的 cron job 会独占这条唯一的线程：
    - 期间新到达的用户消息只能排队等着，daemon 对用户"卡住不回复"
    - 其它到期的 cron job 也无法被触发（tick() 本身就没跑完）
  这正是"避免某个任务一直执行导致其他任务没有执行"要解决的问题——
  必须让 cron job 的实际执行离开这条主线程。

设计：
  CronJobRunner 维护一个小的线程池语义（不用 ThreadPoolExecutor 是因为
  需要按 job_id 去重 + 主动查询"某 job 是否正在跑"，用显式的
  threading.Thread + 计数信号量更直观，量级也小，不需要引入线程池）。

  CronScheduler._fire(job) 只需要调用 CronJobRunner.submit(job)，
  该方法立即返回（不阻塞），真正的执行在独立线程里进行。
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.evolution.cron_scheduler import CronJob

# 完成一次 run_job() 后的回调类型：(job_id, RunOutcome) -> None
RunOutcomeCallback = Callable[[str, "object"], None]


class CronJobRunner:
    """
    后台线程管理器：把 cron job 的实际执行从 AgentRunner 主线程搬到独立线程，
    同时限制全局并发数，避免大量 cron job 同时到期时把系统资源（LLM 并发
    请求数、CPU）打满，与 orchestrator/concurrency.py 里 SubAgent 的
    task 信号量是同一个思路，但故意不共用同一个信号量池——cron 任务和
    用户显式派发的 Task 语义不同，独立控制互不干扰。
    """

    def __init__(
        self,
        base_cfg: "AppConfig",
        paths: "AgentPaths",
        max_concurrent: int = 2,
        on_finished: Optional[RunOutcomeCallback] = None,
    ) -> None:
        self._base_cfg = base_cfg
        self._paths = paths
        self._sem = threading.Semaphore(max(1, max_concurrent))
        self._on_finished = on_finished
        self._lock = threading.Lock()
        self._running_job_ids: set[str] = set()
        self._threads: dict[str, threading.Thread] = {}

    # ── 查询 ──────────────────────────────────────────────────────────────

    def is_running(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._running_job_ids

    @property
    def running_count(self) -> int:
        with self._lock:
            return len(self._running_job_ids)

    # ── 提交 ──────────────────────────────────────────────────────────────

    def submit(self, job: "CronJob") -> bool:
        """
        提交一个 job 到后台线程执行。立即返回，不阻塞调用方（CronScheduler.tick()
        所在的 AgentRunner 主线程）。

        返回 False 的情况：
          - 该 job 已经有一个执行实例在跑（避免同一个 job 被并发触发两次，
            比如 schedule 间隔比单次执行耗时还短的极端配置）
        并发上限（max_concurrent）不在这里拒绝——而是让线程在真正开始执行前
        阻塞在 semaphore.acquire() 上排队，这样"到期但暂时排不上"的 job 不会
        丢失触发记录，只是延后开始，行为上更接近"资源紧张时排队"而不是
        "直接丢弃"。
        """
        with self._lock:
            if job.id in self._running_job_ids:
                return False
            self._running_job_ids.add(job.id)

        t = threading.Thread(
            target=self._run_job_thread,
            args=(job,),
            name=f"cron-job-{job.id}",
            daemon=True,
        )
        with self._lock:
            self._threads[job.id] = t
        t.start()
        return True

    # ── 线程体 ────────────────────────────────────────────────────────────

    def _run_job_thread(self, job: "CronJob") -> None:
        self._sem.acquire()
        try:
            from mini_agent.evolution.cron_agent_bridge import (
                build_cron_agent, make_submit_step_fn,
            )
            from mini_agent.evolution.cron_job_executor import CronJobExecutor

            agent = build_cron_agent(self._base_cfg, job)
            step_fn = make_submit_step_fn(agent)
            executor = CronJobExecutor(self._paths)
            outcome = executor.run_job(job, submit_step_fn=step_fn)

            if self._on_finished is not None:
                try:
                    self._on_finished(job.id, outcome)
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where="mini_agent.evolution.cron_job_runner.on_finished")
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where="mini_agent.evolution.cron_job_runner.CronJobRunner._run_job_thread")
            # 兜底：即使 build_cron_agent/executor 本身抛异常（比如 LLM client
            # 构造失败），也要把 workspace 状态标记出来，不能让它悄悄消失、
            # 看板里既不显示 running 也不显示失败。
            try:
                from mini_agent.evolution.cron_job_workspace import (
                    CronJobWorkspace, STATUS_NEEDS_REVIEW,
                )
                ws = CronJobWorkspace(self._paths, job.id)
                ws.ensure(default_task_template=job.task_template)
                state = ws.read_state()
                state.status = STATUS_NEEDS_REVIEW
                state.last_error = str(_mini_agent_exc)
                state.last_run_finished_at = time.time()
                ws.write_state(state)
            except Exception:
                pass
        finally:
            with self._lock:
                self._running_job_ids.discard(job.id)
                self._threads.pop(job.id, None)
            self._sem.release()


__all__ = ["CronJobRunner"]
