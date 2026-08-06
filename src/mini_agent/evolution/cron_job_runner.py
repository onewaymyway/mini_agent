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

[goal_cron_unified_scheduler_improvement_plan.md P0/P1/P2]
  P0：原来的固定容量 `threading.Semaphore(max_concurrent)` 只能表达一个不变的
  并发上限，`ResourceArbiter` 判定为 degraded 时无法临时收紧。改为用
  `threading.Condition` 实现的"可变容量槽位"（`_acquire_slot`/`_release_slot`），
  effective_max_concurrent() 在 degraded 时降到 `cron.degraded_max_concurrent`
  （默认 1），full 时恢复到构造时传入的 `max_concurrent`——语义与
  `ObjectiveExecutor.effective_max_concurrent()` 对齐，"只降不升"，不引入新的
  排队丢弃语义（原来 semaphore 打满时是阻塞排队，现在容量收紧后行为一致，
  只是排队会更久）。
  P1：每次 job 执行完毕后，把 Agent 本次消耗的 token（`agent.stats.
  input_tokens + output_tokens`，cron 用的是一次性构造的独立 Agent，累计值
  即为本次 job 的总消耗）计入 `ResourceArbiter.record_autonomous_token_usage
  (usage_type="cron")`，与 Goal/探索三条通道共用同一份 `used_today` 记账，
  不再是"cron 消耗不计入任何预算计数器"的不对称状态。
  P2：`CronJob.consecutive_skip_count` 由 `CronScheduler.tick()` 维护（不在本
  文件内），本文件只需要在 submit() 因仲裁被跳过、以及正常触发成功时，
  通过返回值告知调用方结果（沿用已有 True/False 返回值，无需新增接口）。

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
        # [P0] 固定容量的 threading.Semaphore 换成"容量可变"的槽位实现：
        # _slot_cond 保护 _held_slots（当前真正持有槽位、在跑的 job 数），
        # effective_max_concurrent() 决定当前容量上限，degraded 时可以
        # 临时收紧而不需要重新构造 Semaphore（Semaphore 的容量在构造后
        # 不可缩小）。max_concurrent 仍是 full 状态下的容量天花板。
        self._max_concurrent = max(1, max_concurrent)
        self._slot_cond = threading.Condition()
        self._held_slots = 0
        self._gating_degraded = False
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
        # [daemon_stability_and_ux_improvement_plan.md 第 1 项 / P2-1]
        # 跨 cron job 广度熔断：scope_id 用 job_id，与
        # ObjectiveExecutor/workflow watchdog 共用同一份
        # `CircuitBreakerCore` 实现。CronJobRunner 是长期持有的单例（不像
        # CronJobExecutor 是每次触发临时构造），所以熔断状态放在这里，
        # 每次 `_run_job_thread()` 构造 CronJobExecutor 时把这个共享实例
        # 传进去。触发后果同 ObjectiveExecutor：只记录 + 主动告警，不阻断
        # 新 job 的调度。
        from mini_agent.evolution.circuit_breaker_core import CircuitBreakerCore
        cron_cfg_for_breaker = getattr(base_cfg, "cron", None)
        self._circuit_breaker = CircuitBreakerCore(
            distinct_scope_threshold=getattr(
                cron_cfg_for_breaker, "circuit_breaker_distinct_threshold", None,
            ),
            on_trip=self._on_circuit_breaker_tripped,
            log_fn=None,
        )

    def _on_circuit_breaker_tripped(self, error_type: str, distinct_job_ids: list[str]) -> None:
        """[P2-1] 同一粗分类 error_type 已在多个不同 cron job 上失败，
        判定为系统性问题，主动告警（不阻断调度）。"""
        try:
            from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
            NotificationDispatcher(self._paths).dispatch(NotificationMessage(
                title="检测到跨 cron job 的系统性失败",
                body=(
                    f"error_type={error_type!r} 已在 {len(distinct_job_ids)} 个不同 cron job"
                    f"（{distinct_job_ids}）上失败，可能是某个工具/API 全局失效，建议排查"
                )[:200],
                source="cron_circuit_breaker",
                meta={"error_type": error_type, "distinct_job_ids": distinct_job_ids},
            ))
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where="mini_agent.evolution.cron_job_runner._on_circuit_breaker_tripped")

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

    def effective_max_concurrent(self) -> int:
        """[P0] 当前生效的并发上限。degraded 且未关闭
        `autonomy.resource_gating_degraded_enabled` 时收紧到
        `cron.degraded_max_concurrent`（默认 1），否则为构造时传入的
        `max_concurrent`——语义、命名都与
        `ObjectiveExecutor.effective_max_concurrent()` 对齐。异常时保守
        返回天花板值，不因为读配置失败导致 cron 被误收紧。"""
        cap = self._max_concurrent
        if not self._gating_degraded:
            return cap
        try:
            autonomy_cfg = getattr(self._base_cfg, "autonomy", None)
            if autonomy_cfg is not None and not getattr(
                autonomy_cfg, "resource_gating_degraded_enabled", True
            ):
                return cap
            cron_cfg = getattr(self._base_cfg, "cron", None)
            degraded_cap = getattr(cron_cfg, "degraded_max_concurrent", 1) if cron_cfg is not None else 1
            return max(1, min(cap, int(degraded_cap)))
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where="mini_agent.evolution.cron_job_runner.CronJobRunner.effective_max_concurrent")
            return cap

    def set_gating_degraded(self, degraded: bool) -> None:
        """[P0] 由 AutonomousLoop 每次 tick 调用，反映
        ResourceArbiter.gating_state() 的最新结果是否为 "degraded"。
        不做任何 I/O，纯内存标志位；下一次有 job 排队等待槽位（或槽位
        释放时唤醒等待者）都会用最新值重新计算 effective_max_concurrent()。
        """
        with self._slot_cond:
            self._gating_degraded = bool(degraded)
            # 状态变化（尤其是 degraded → full）可能让容量变大，唤醒所有
            # 正在等待槽位的线程重新检查是否能拿到槽位。
            self._slot_cond.notify_all()

    def _acquire_slot(self) -> None:
        """[P0] 等待直到当前持有槽位数 < effective_max_concurrent()，
        然后占用一个槽位。取代原来的 `self._sem.acquire()`——容量可以在
        等待期间因为 degraded 状态变化而升降，每次被唤醒都会用最新容量
        重新判断，而不是一次性固定住。"""
        with self._slot_cond:
            while self._held_slots >= self.effective_max_concurrent():
                self._slot_cond.wait(timeout=1.0)
            self._held_slots += 1

    def _release_slot(self) -> None:
        """[P0] 归还一个槽位，取代原来的 `self._sem.release()`。"""
        with self._slot_cond:
            self._held_slots = max(0, self._held_slots - 1)
            self._slot_cond.notify_all()

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

        # 代替永远不会执行到的 finally 释放一个槽位——线程体收尾时
        # 会发现自己的 token 已经不是当前合法 token（上面已经 pop 掉），
        # 从而跳过它自己的 release()，两者互斥，不会重复释放。
        self._release_slot()

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
        self._acquire_slot()
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
            # [P2-1] 用属性赋值而不是构造参数传入共享熔断内核——保持
            # CronJobExecutor(paths) 的构造签名不变，不影响任何已有的
            # 直接实例化/测试替身写法；executor.py 内部把它当可选属性
            # 处理（None 时不启用广度熔断，行为与改造前一致）。
            executor.circuit_breaker = self._circuit_breaker

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

            # [P1：cron 消耗统一记账] agent 是本次 job 独占的一次性实例
            # （build_cron_agent 每次触发都重新构造，不跨触发复用），
            # agent.stats 的累计值就是本次 job 的总消耗，不需要额外做
            # "本次 - 上次"的差值计算。失败静默：记账失败不能影响 job
            # 本身已经产出的结果。
            try:
                tokens_used = (
                    getattr(agent.stats, "input_tokens", 0)
                    + getattr(agent.stats, "output_tokens", 0)
                )
                if tokens_used > 0:
                    from mini_agent.evolution.resource_arbiter import ResourceArbiter
                    ResourceArbiter(self._paths, self._base_cfg).record_autonomous_token_usage(
                        tokens_used, usage_type="cron",
                    )
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where="mini_agent.evolution.cron_job_runner.CronJobRunner._run_job_thread.token_accounting")

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
                self._release_slot()


__all__ = ["CronJobRunner"]
