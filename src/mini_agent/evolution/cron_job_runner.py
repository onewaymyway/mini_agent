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

[daemon_task_hang_recovery_and_watchdog_hardening_plan.md 阶段一]
  上面的记账（_running_job_ids/semaphore 许可）此前完全依赖线程**正常
  返回**——如果 executor.run_job() 内部卡死不返回（网络请求挂起/工具调用
  阻塞在某个系统调用上等情况），job.id 会永久留在 _running_job_ids 里，
  这个 job 之后所有的定时触发都会被 submit() 静默拒绝；同时对应的
  semaphore 许可也永久不释放，攒够 max_concurrent_jobs 个卡死 job 后，
  其它所有 cron job 会永久阻塞在排队上，cron 功能实质性全局瘫痪。

  reap_stale_jobs() 是这个问题的外部存活性回收：用一个每次 submit() 生成
  的唯一 token 判定"谁是这个 job 当前合法的执行者"，回收判定为卡死的
  job 时代替永远不会执行到的 finally 释放一次 semaphore、清空记账、把
  workspace 标记为 needs_human_review；真正卡死的旧线程本身会作为孤儿
  线程继续在后台跑（Python 无法强制杀死线程），但迟到收尾时会发现自己
  持有的 token 已经不是当前合法 token，从而跳过重复释放/清理，不会
  和 watchdog 的回收互相踩踏。
"""

from __future__ import annotations

import threading
import time
import uuid
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
        # [next_doc/kanban_execution_visibility_and_control_plan.md
        # 阶段 B] job_id -> 是否已经真正拿到 semaphore 开始执行（而不是
        # 还在排队）。用于区分 is_running()==True 时到底是"正在跑"还是
        # "卡在排队"——此前两者完全无法从外部区分。
        self._sem_acquired: set[str] = set()
        # [阶段一] job_id -> 当前合法执行者的 token（submit() 时生成）。
        # reap_stale_jobs() 强制回收时会清空对应条目；线程体收尾前会比对
        # 自己拿到的 token 与这里当前的值，不相等说明自己已经是"迟到的
        # 孤儿"，不能再触碰共享状态或重复释放 semaphore。
        self._tokens: dict[str, str] = {}
        self._started_at: dict[str, float] = {}
        # [阶段三·顺带做] 被 reap_stale_jobs() 强制回收过的 job 次数，
        # 进程内累计，不持久化——只用于观测"卡死回收发生的频率"。
        self._reaped_job_count: int = 0
        # [next_doc/scheduling_unification_and_kanban_visibility_improvement_plan.md
        # P1] 因 ResourceArbiter 仲裁未通过而被跳过本次触发的次数，进程内
        # 累计，不持久化——只用于观测"cron 通道有多少次因为仲裁被挡"，
        # 供看板 P3 展示。
        self._arbiter_skipped_count: int = 0

    # ── 查询 ──────────────────────────────────────────────────────────────

    def is_running(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._running_job_ids

    @property
    def running_count(self) -> int:
        with self._lock:
            return len(self._running_job_ids)

    @property
    def reaped_job_count(self) -> int:
        """[阶段三·顺带做] 进程内累计的"被 watchdog 强制回收"次数。"""
        with self._lock:
            return self._reaped_job_count

    @property
    def arbiter_skipped_count(self) -> int:
        """[P1] 进程内累计的"因资源仲裁未通过被跳过"次数。"""
        with self._lock:
            return self._arbiter_skipped_count

    def execution_phase(self, job_id: str) -> str:
        """[阶段 B] 返回 job 当前的执行阶段：
        "not_running"（没在跑）/ "queued"（已提交但还在排队等 semaphore）
        / "running"（已经真正开始执行）。看板据此区分"正在执行"和
        "排队等待"两栏，不再把两者混为一谈。"""
        with self._lock:
            if job_id not in self._running_job_ids:
                return "not_running"
            if job_id in self._sem_acquired:
                return "running"
            return "queued"

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
        # [P1：接入 ResourceArbiter] 只对非 "sys:" 系统维护类 job 做仲裁
        # 检查——sys: job（sys:digest_trim / sys:session_cleanup 等）本身
        # 低频、轻量、以只读扫描为主，设计上就不应该因为用户在场而永远
        # 排不上，维持现状不检查。
        #
        # 注意：不能用 job.initiator 来区分"用户自定义 job"——读码确认
        # CronScheduler.add_job()（用户手动创建 job 的唯一入口）把
        # initiator 硬编码成 "cron"（cron_scheduler.py::add_job），
        # 这个字段实际语义是"提交到 InputQueue/job_runner 时打的来源
        # 标签"，不是"谁创建的这条 job"，用它做门控条件会导致本检查
        # 永远不生效。真正能区分的只有 job_id 前缀（is_system），
        # goal_cron_bridge 绑定的 run_mode="goal_cycle" job 走的是
        # CronScheduler._fire() 里的另一条分支（_goal_cycle_fn），根本
        # 不会到达这里，因此这里只需要判断 is_system 即可覆盖到达
        # submit() 的所有用户自定义 message 类 job。
        if not job.is_system:
            try:
                from mini_agent.evolution.resource_arbiter import ResourceArbiter
                arbiter = ResourceArbiter(self._paths, self._base_cfg)
                state = arbiter.gating_state().get("state")
            except Exception:
                # 仲裁模块本身异常：保守放行，不能因为仲裁检查失败导致
                # 所有用户 cron job 停摆（与 ResourceArbiter 自身各 _check_*
                # 方法"异常时保守放行"的既有风格保持一致）。
                state = "full"
            if state == "blocked":
                with self._lock:
                    self._arbiter_skipped_count += 1
                # 不触发，等同于"这次没触发成功"：不占用 semaphore、不记账，
                # CronScheduler.tick() 不会推进 last_run_at/next_run_at，
                # 下次 tick 会再次尝试，行为与"job 已有一次执行在跑"时
                # 返回 False 的既有语义一致。
                return False

        token = uuid.uuid4().hex
        with self._lock:
            if job.id in self._running_job_ids:
                return False
            self._running_job_ids.add(job.id)
            self._tokens[job.id] = token
            self._started_at[job.id] = time.time()

        t = threading.Thread(
            target=self._run_job_thread,
            args=(job, token),
            name=f"cron-job-{job.id}",
            daemon=True,
        )
        with self._lock:
            self._threads[job.id] = t
        t.start()
        return True

    # ── 存活性回收（watchdog） ───────────────────────────────────────────────

    def reap_stale_jobs(self, now: Optional[float] = None) -> list[str]:
        """
        [daemon_task_hang_recovery_and_watchdog_hardening_plan.md 阶段一]
        扫描当前 _running_job_ids，对每个 job 计算"有效超时阈值" = 该 job
        自己 .agent/cron_jobs/<id>/config.json 里的 timeout_seconds（读不到
        则回退 cfg.cron.default_timeout_seconds）+
        cfg.cron.stale_job_watchdog_grace_seconds。超过
        started_at + 有效阈值仍未收到线程正常收尾，判定为卡死：清空该
        job_id 的全部记账、代替永远不会执行到的 finally 释放一次
        semaphore、把 workspace 状态标记为 needs_human_review。

        返回本次被回收的 job_id 列表，供上层日志/计数。每个 job 独立
        try/except，一个 job 的回收逻辑异常不影响其它 job。
        """
        now = time.time() if now is None else now
        with self._lock:
            snapshot = list(self._running_job_ids)

        reaped: list[str] = []
        for job_id in snapshot:
            try:
                if self._reap_one_if_stale(job_id, now):
                    reaped.append(job_id)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(
                    _mini_agent_exc,
                    where="mini_agent.evolution.cron_job_runner.CronJobRunner.reap_stale_jobs",
                )
        return reaped

    def _effective_timeout_seconds(self, job_id: str) -> float:
        """job 自己 config.json 的 timeout_seconds（读不到则回退全局
        default_timeout_seconds）+ 全局 grace 余量。"""
        cron_cfg = getattr(self._base_cfg, "cron", None)
        default_timeout = getattr(cron_cfg, "default_timeout_seconds", 20 * 60) if cron_cfg is not None else 20 * 60
        grace = getattr(cron_cfg, "stale_job_watchdog_grace_seconds", 5 * 60) if cron_cfg is not None else 5 * 60

        try:
            from mini_agent.evolution.cron_job_workspace import CronJobWorkspace, CronJobConfig
            ws = CronJobWorkspace(self._paths, job_id)
            default = CronJobConfig(timeout_seconds=default_timeout)
            job_cfg = ws.read_config(default=default)
            timeout = job_cfg.timeout_seconds
        except Exception:
            timeout = default_timeout

        return float(timeout) + float(grace)

    def _reap_one_if_stale(self, job_id: str, now: float) -> bool:
        with self._lock:
            if job_id not in self._running_job_ids:
                return False
            started_at = self._started_at.get(job_id, 0.0)
            token = self._tokens.get(job_id)

        if started_at <= 0:
            return False

        effective_timeout = self._effective_timeout_seconds(job_id)
        if (now - started_at) < effective_timeout:
            return False

        with self._lock:
            # 双重确认：released between 读取快照和这里之间（极小概率的
            # 竞态，正常收尾恰好在这一瞬间发生）不应该被误回收。
            if job_id not in self._running_job_ids:
                return False
            if self._tokens.get(job_id) != token:
                return False
            self._running_job_ids.discard(job_id)
            self._threads.pop(job_id, None)
            self._started_at.pop(job_id, None)
            self._tokens.pop(job_id, None)
            self._sem_acquired.discard(job_id)
            self._reaped_job_count += 1

        # 代替永远不会执行到的 finally 释放一次 semaphore——线程体收尾时
        # 会发现自己的 token 已经不是当前合法 token（上面已经 pop 掉），
        # 从而跳过它自己的 release()，两者互斥，不会重复释放。
        self._sem.release()

        try:
            from mini_agent.evolution.cron_job_workspace import (
                CronJobWorkspace, STATUS_NEEDS_REVIEW,
            )
            ws = CronJobWorkspace(self._paths, job_id)
            ws.ensure()
            state = ws.read_state()
            state.status = STATUS_NEEDS_REVIEW
            state.last_error = (
                f"cron job 判定为卡死（超过 {effective_timeout:.0f}s 未收到执行结果），"
                "已被 watchdog 强制回收，可重新触发"
            )
            state.last_run_finished_at = now
            ws.write_state(state)
        except Exception:
            pass

        import logging
        logging.getLogger(__name__).warning(
            "CronJobRunner.reap_stale_jobs: job_id=%s 判定为卡死（超过 %.0fs），已强制回收",
            job_id, effective_timeout,
        )
        try:
            from mini_agent.evolution.recovery_event_log import record_recovery_event
            record_recovery_event(
                "cron_job", job_id,
                f"超过 {effective_timeout:.0f}s 未收到执行结果，已强制回收",
                now=now,
                paths=self._paths,
            )
        except Exception:
            pass
        return True

    # ── 线程体 ────────────────────────────────────────────────────────────

    def _run_job_thread(self, job: "CronJob", token: str) -> None:
        self._sem.acquire()
        with self._lock:
            # 迟到的孤儿线程（已经被 reap_stale_jobs() 回收过）不应该
            # 把自己标记为"正在运行"——只有仍持有当前合法 token 才标记。
            if self._tokens.get(job.id) == token:
                self._sem_acquired.add(job.id)
        try:
            from mini_agent.evolution.cron_agent_bridge import (
                build_cron_agent, make_submit_step_fn,
            )
            from mini_agent.evolution.cron_job_executor import CronJobExecutor
            from mini_agent.evolution.cron_job_workspace import CronJobConfig

            agent = build_cron_agent(self._base_cfg, job)
            step_fn = make_submit_step_fn(agent)
            executor = CronJobExecutor(self._paths)

            # 根据全局 AppConfig.cron 构造"该 job 若是首次运行"时应写入的
            # config.json 默认值；job 若已经有自己的 config.json（用户手动
            # 编辑过，或非首次运行），这个默认值不会生效——见
            # CronJobWorkspace.ensure() 的说明。
            cron_cfg = getattr(self._base_cfg, "cron", None)
            default_config = CronJobConfig(
                timeout_seconds=getattr(cron_cfg, "default_timeout_seconds", 20 * 60),
                max_steps=getattr(cron_cfg, "default_max_steps", 60),
            ) if cron_cfg is not None else None

            outcome = executor.run_job(job, submit_step_fn=step_fn, default_config=default_config)

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
            # [阶段一] 只有自己仍是这个 job 当前合法的执行者（没有被
            # reap_stale_jobs() 强制回收过）才清理共享状态、释放 semaphore；
            # 否则说明自己是一个"迟到的孤儿"——watchdog 已经代为清理并释放
            # 过一次 semaphore 了，这里绝不能重复释放，也不能 touch 任何
            # 可能已经属于下一轮重新提交的共享状态。
            released = False
            with self._lock:
                if self._tokens.get(job.id) == token:
                    self._running_job_ids.discard(job.id)
                    self._threads.pop(job.id, None)
                    self._started_at.pop(job.id, None)
                    self._tokens.pop(job.id, None)
                    self._sem_acquired.discard(job.id)
                    released = True
            if released:
                self._sem.release()


__all__ = ["CronJobRunner"]
