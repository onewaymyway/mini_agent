"""
evolution/objective_agent_bridge.py — [daemon_autonomous_state_recovery_plan.md
阶段三 / P1] 为 autonomous Objective 的每个 step 构建独立上下文的 Agent 实例。

背景：在这个模块出现之前，`autonomous`/`cron` 的 turn 都通过
`ObjectiveExecutor._submit_fn` → `bridge.input_queue.enqueue()` 提交，最终跑在
Self 共用的那一个 `bridge.agent` 上——与真人交互、与其它自主任务共享同一段
对话历史，容易互相污染上下文（见计划文档"根因回顾"第 4 点）。cron 任务已经
在更早的改造里通过 `cron_agent_bridge.py` + `CronJobExecutor` 拿到了独立的
Agent 实例；这个模块把同样的模式补给 `autonomous` Objective 的 step。

设计要点（与 cron_agent_bridge.py 保持同构，便于对照阅读）：
  - 每次 step 提交都重新构建一个全新 Agent（不复用、不保留跨 step 的对话
    历史）。"上一步做到哪了"完全靠 ObjectiveExecutor 自己在 prompt 里拼接的
    结构化摘要（`[前序步骤结果]`/`[前序步骤产出文件]`）传递，这一点在
    `_submit_step()` 里已经实现，本模块不需要、也不应该额外做什么来"记住"
    上一个 Agent 实例的状态。
  - 全量继承主 Agent 的工具（registry 留空 → 回退到全局默认 registry），
    与 cron 任务、以及未设置工具限制的普通 SubAgent 是同一套已验证过的
    "thread-local 状态按构造 Agent 的线程隔离"模式——只要 Agent 在它将要
    运行的那条线程上构造，就是安全的。`ObjectiveIsolatedRunner` 保证了这
    一点：每个 step 都在专属的后台线程里构造 + 运行，不跨线程。
  - `auto_approve=True`：自主任务无人值守，必须自动批准工具调用（与 cron
    一致）。
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import logging
import os
import threading
import time
import uuid
from typing import Any, Callable, Optional

from mini_agent.config import AppConfig, load_config
from mini_agent.agent import Agent
from mini_agent.llm.base import LLMConfig
from mini_agent.llm.factory import create_client
from mini_agent.permissions import PermissionGuard

log = logging.getLogger("mini_agent.objective_agent_bridge")

# 与 cron_agent_bridge.CRON_INNER_MAX_TURNS_DEFAULT 同一档位；实际生效值
# 由 cfg.autonomy.objective_isolated_inner_max_turns 决定，这里只是兜底。
OBJECTIVE_INNER_MAX_TURNS_DEFAULT = 15


def build_objective_agent(
    base_cfg: AppConfig,
    objective_title: str,
    execution_id: str,
    inner_max_turns: Optional[int] = None,
    persistent: bool = False,
) -> Agent:
    """
    为一次 Objective 执行构建一个全新的、全量继承主 Agent 工具集的独立
    Agent 实例。不携带真人交互或其它 Objective 的历史，只携带任务描述本身
    （由调用方传入 run_turn() 的 message 参数）。

    与 cron_agent_bridge.build_cron_agent() 的结构保持一致，便于对照维护。

    persistent — [daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md
        阶段一] 由 ObjectivePersistentRunner 调用时传 True：这个 Agent 实例会
        在同一个 execution 的多个 step 之间被复用（不是跑完一个 step 就丢弃），
        因此注入的说明文案不同——不再声称"每次都是独立会话、不记得任何未在
        消息中出现的内容"，而是如实告知"你的会话历史会在本次 Objective 执行
        期间保留"。仅影响 system_extra 文案，不影响其它任何行为。
    """
    if inner_max_turns is None:
        inner_max_turns = getattr(
            getattr(base_cfg, "autonomy", None),
            "objective_isolated_inner_max_turns",
            OBJECTIVE_INNER_MAX_TURNS_DEFAULT,
        )

    cfg = load_config(
        project_root=base_cfg.project_root,
        verbose=False,
        sandbox=base_cfg.sandbox,
        auto_approve=True,               # 自主任务无人值守，必须自动批准工具调用
        model=base_cfg.model,
        llm_provider=base_cfg.llm_provider,
        llm_base_url=base_cfg.llm_base_url,
        use_system_tool_call=base_cfg.use_system_tool_call,
        debug_llm=base_cfg.debug_llm,
        tool_cache_enabled=base_cfg.tool_cache_enabled,
    )
    if not cfg.api_key:
        cfg.api_key = base_cfg.api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    cfg.max_turns = inner_max_turns
    cfg.stream = False
    if persistent:
        # [daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md
        # §7.3 修复] 持久 Worker 的 Agent 实例跨 step 复用会话历史，理论上
        # 可能一直累积到撑爆 context window——只在项目全局没有开启
        # cfg.compress.enabled 时才介入（尊重用户已有配置，不覆盖），强制
        # 打开 token 阈值 compact 触发器，复用现有的 compact_with_skills
        # 实现（cfg.compress.strategy 默认值），不是重新发明一套简易摘要。
        _auto_compact_floor = getattr(
            getattr(base_cfg, "autonomy", None),
            "objective_persistent_worker_auto_compact_enabled", True,
        )
        if _auto_compact_floor and not cfg.compress.enabled:
            cfg.compress.enabled = True
            cfg.compress.threshold = getattr(
                getattr(base_cfg, "autonomy", None),
                "objective_persistent_worker_auto_compact_threshold", 0.75,
            )
        cfg.system_extra = (
            (base_cfg.system_extra or "") +
            f"\n\n[自主任务 - 持久化 Worker] 你正在以 daemon 后台自主任务身份"
            f"持续执行「{objective_title}」（execution_id={execution_id}）。"
            f"这是无人值守执行；与一次性独立会话不同，你的对话历史会在本次"
            f"Objective 的多个步骤之间保留——你可以记得自己之前做过什么。"
            f"每条消息仍然会额外附带结构化的「前序步骤结果」/「前序步骤"
            f"产出文件」摘要，以此为准核对进展，如果信息不足，做出合理假设"
            f"并在输出中说明，而不是等待澄清。"
        )
    else:
        cfg.system_extra = (
            (base_cfg.system_extra or "") +
            f"\n\n[自主任务 - 独立上下文] 你正在以 daemon 后台自主任务身份执行"
            f"「{objective_title}」（execution_id={execution_id}）。这是无人值守"
            f"执行，本次 turn 使用一个专属的、不携带其它对话历史的独立会话——"
            f"如果需要了解此前步骤的进展，请以本条消息里附带的"
            f"「前序步骤结果」/「前序步骤产出文件」为准，不要假设自己记得任何"
            f"未在本条消息中出现的内容。如果信息不足，做出合理假设并在输出中"
            f"说明，而不是等待澄清。"
        )

    llm_cfg = LLMConfig.from_app_config(cfg)
    guard = PermissionGuard(
        auto_approve=True,
        sandbox=base_cfg.sandbox,
        project_root=base_cfg.project_root,
    )

    # registry 留空 → Agent.__init__ 回退到 get_default_registry()，
    # 即全量继承主 Agent 可用的工具集合，与 cron 任务同一模式。
    return Agent(
        cfg=cfg,
        guard=guard,
        llm_client=create_client(llm_cfg),
        registry=None,
        skill_loader=None,
        tool_cache=None,
        is_subagent=True,
    )


class ObjectiveIsolatedRunner:
    """
    [P1] 可以直接替换 `ObjectiveExecutor._submit_fn` 的独立上下文 runner。

    与共享 bridge.input_queue 的默认提交路径相比：每次 `submit()` 调用都在
    一个专属的后台线程里构建全新 Agent + 执行 `run_turn()`，执行完毕后立即
    丢弃这个 Agent 实例（不保留、不复用），并通过 `on_done`/`on_failed`
    回调把结果交回给 ObjectiveExecutor——回调签名与
    `ObjectiveExecutor.on_turn_done(turn_id, result_summary, valid)` /
    `on_turn_failed(turn_id, error)` 完全一致，可以直接传方法引用。

    结果健全性校验（P0-A）在这里同样生效：复用
    `perception/format_correction_detector.py::is_valid_final_result()`，
    与 api/server.py 里共享路径的判定逻辑保持一致，不需要
    ObjectiveExecutor/on_turn_done 关心"这个 turn 是不是隔离上下文跑的"。
    """

    def __init__(
        self,
        base_cfg: AppConfig,
        on_done: Callable[..., Any],
        on_failed: Callable[[str, str], Any],
        max_workers: Optional[int] = None,
        inner_max_turns: Optional[int] = None,
        sched_lock: Optional[threading.Lock] = None,
    ) -> None:
        self._base_cfg = base_cfg
        self._on_done = on_done
        self._on_failed = on_failed
        self._inner_max_turns = inner_max_turns
        # [daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md
        # §7.1 修复] 与 AgentRunner._maybe_sched_lock() 同一套模式：
        # sched_lock 为 None（默认，未开启心跳解耦）时 _maybe_sched_lock()
        # 返回 no-op 上下文管理器，行为与改造前完全一致；心跳模式开启时，
        # 这把锁与 SchedulerHeartbeat 线程持锁调用 tick() 互斥，避免本
        # runner 的回调线程与心跳线程并发读写 ObjectiveExecutor 内部状态。
        self._sched_lock = sched_lock
        if max_workers is None:
            max_workers = getattr(
                getattr(base_cfg, "autonomy", None), "objective_isolated_max_workers", 4
            )
        self._max_workers = max(1, int(max_workers))
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="obj-isolated",
        )
        self._lock = threading.Lock()
        self._stopped = False
        # [daemon_task_hang_recovery_and_watchdog_hardening_plan.md §5 后续
        # 补做] 共享线程池没有"按 execution 精细回收单个卡死 worker"的能力
        # （不像 CronJobRunner/ObjectivePersistentRunner 那样自己维护
        # id -> 状态的记账，可以针对性清零）——ThreadPoolExecutor 不暴露
        # "强制释放某个 future 占用的槽位"的官方接口。这里改用"整体健康
        # 检查 + 达到临界条件才整体重建"的更粗粒度方案：
        #   _inflight: turn_id -> started_at，submit() 时登记，_run_step()
        #   的 finally 里摘除。check_health() 扫描其中运行超过有效阈值的
        #   条目，只有当"卡死数 >= max_workers"（池子事实上已经整体瘫痪，
        #   不再可能接受新提交后及时被执行）才整体丢弃旧池子、换一个全新
        #   的 ThreadPoolExecutor；卡死数 > 0 但 < max_workers 时只计数，
        #   不重建（池子还有空闲槽位可以正常工作，重建成本大于收益）。
        self._inflight: dict[str, float] = {}
        self._pool_rebuild_count: int = 0
        self._stale_turn_count: int = 0

    @property
    def pool_rebuild_count(self) -> int:
        """进程内累计的整体线程池重建次数（不持久化）。"""
        with self._lock:
            return self._pool_rebuild_count

    @property
    def stale_turn_count(self) -> int:
        """进程内累计检测到的卡死 turn 数（含未触发整体重建的部分计数，
        用于观测趋势——即使还没到"整体重建"的临界条件，频繁出现也是
        需要关注的信号）。"""
        with self._lock:
            return self._stale_turn_count

    def _effective_timeout_seconds(self) -> float:
        autonomy_cfg = getattr(self._base_cfg, "autonomy", None)
        default_timeout = 600
        try:
            from mini_agent.evolution.objective_executor import DEFAULT_STEP_TIMEOUT_SECONDS
            default_timeout = DEFAULT_STEP_TIMEOUT_SECONDS
        except Exception:
            pass
        timeout_override = getattr(autonomy_cfg, "objective_step_stale_timeout_seconds", None) \
            if autonomy_cfg is not None else None
        timeout = timeout_override if timeout_override is not None else default_timeout
        grace = getattr(
            autonomy_cfg, "objective_isolated_pool_rebuild_grace_seconds", 5 * 60
        ) if autonomy_cfg is not None else 5 * 60
        return float(timeout) + float(grace)

    def check_health(self, now: Optional[float] = None, force: bool = False) -> dict:
        """[阶段四] 供 AutonomousLoop._tick_maintenance() 周期性调用（也可
        由看板"立即回收"按钮以 force=True 触发，跳过超时判定，直接按当前
        in-flight 数量判断是否需要重建）。

        返回 {"stale_turn_ids": [...], "rebuilt": bool}，纯观测 + 必要时
        自愈，不影响调用方其它逻辑；异常发生时静默吞掉，不向上抛出
        （与 reap_stale_jobs()/reap_stale_steps() 同一降级风格）。
        """
        now = time.time() if now is None else now
        effective_timeout = self._effective_timeout_seconds()
        with self._lock:
            snapshot = dict(self._inflight)
        stale_ids = [
            tid for tid, started_at in snapshot.items()
            if force or (now - started_at) >= effective_timeout
        ]
        rebuilt = False
        if stale_ids:
            with self._lock:
                self._stale_turn_count += len(stale_ids)
                if len(stale_ids) >= self._max_workers or force:
                    old_executor = self._executor
                    self._executor = concurrent.futures.ThreadPoolExecutor(
                        max_workers=self._max_workers,
                        thread_name_prefix="obj-isolated",
                    )
                    # 旧池子里真正卡死的线程本身作为孤儿继续跑（Python
                    # 无法强制杀死线程），但不再阻塞任何后续提交；
                    # cancel_futures=True 让还没真正开始执行的排队 future
                    # 尽快放弃，wait=False 不阻塞本次健康检查调用。
                    self._pool_rebuild_count += 1
                    rebuilt = True
                    for tid in stale_ids:
                        self._inflight.pop(tid, None)
            if rebuilt:
                try:
                    old_executor.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass
                log.warning(
                    "ObjectiveIsolatedRunner.check_health: 检测到 %d 个卡死 turn"
                    "（>= max_workers=%d），已整体重建线程池",
                    len(stale_ids), self._max_workers,
                )
                try:
                    from mini_agent.evolution.recovery_event_log import record_recovery_event
                    from mini_agent.storage.paths import AgentPaths
                    record_recovery_event(
                        "isolated_pool", "",
                        f"检测到 {len(stale_ids)} 个卡死 turn，已整体重建共享线程池",
                        now=now,
                        paths=AgentPaths(getattr(self._base_cfg, "project_root", None)),
                    )
                except Exception:
                    pass
        return {"stale_turn_ids": stale_ids, "rebuilt": rebuilt}

    def _maybe_sched_lock(self):
        if self._sched_lock is None:
            return contextlib.nullcontext()
        return self._sched_lock

    def submit(self, message: str, initiator: str, meta: dict) -> Optional[str]:
        """与 `submit_fn(message, initiator, meta) -> turn_id` 签名一致，
        可直接赋给 `ObjectiveExecutor._submit_fn`。"""
        with self._lock:
            if self._stopped:
                return None
        turn_id = f"obj-iso-{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._inflight[turn_id] = time.time()
        try:
            self._executor.submit(self._run_step, turn_id, message, meta)
        except RuntimeError:
            # executor 已 shutdown（daemon 正在退出），当作提交失败处理，
            # ObjectiveExecutor 会按现有"submit 返回 None"的既有路径降级。
            with self._lock:
                self._inflight.pop(turn_id, None)
            return None
        return turn_id

    def _run_step(self, turn_id: str, message: str, meta: dict) -> None:
        try:
            self._run_step_inner(turn_id, message, meta)
        finally:
            # [阶段四] 无论正常完成、异常，还是（迟到的）被 check_health()
            # 判定为卡死后整体重建了线程池，都要摘除 in-flight 记录——
            # 如果自己已经被重建清理过（pop 无副作用），这里也不会出错。
            with self._lock:
                self._inflight.pop(turn_id, None)

    def _run_step_inner(self, turn_id: str, message: str, meta: dict) -> None:
        from mini_agent.perception.format_correction_detector import is_valid_final_result

        objective_title = meta.get("objective_id", "") or "(unknown)"
        execution_id = meta.get("execution_id", "") or "(unknown)"

        try:
            agent = build_objective_agent(
                self._base_cfg, objective_title, execution_id,
                inner_max_turns=self._inner_max_turns,
            )
        except Exception as exc:
            log.warning("build_objective_agent failed for turn_id=%s: %s", turn_id, exc)
            with self._maybe_sched_lock():
                self._safe_on_failed(turn_id, f"独立上下文 Agent 构建失败: {exc}")
            return

        try:
            result = agent.run_turn(message)
        except Exception as exc:
            log.warning("isolated run_turn failed for turn_id=%s: %s", turn_id, exc)
            with self._maybe_sched_lock():
                self._safe_on_failed(turn_id, str(exc))
            return

        summary = (result or "").strip()
        summary = summary.split("\n")[0][:200]
        result_valid = not getattr(agent, "_last_turn_result_invalid", False)
        if not result_valid and not is_valid_final_result(result or ""):
            # 双重确认（agent 自身已经在 run_turn 末尾判过一次，这里再校验
            # 一遍是防御性的，避免未来接入不经过 run_turn() 内部校验路径的
            # 场景时静默漏判）——两者任一判定无效就认为无效。
            result_valid = False

        # [§7.1 修复] 与 AgentRunner 里两处 on_turn_done/on_turn_failed 调用
        # 用同一把共享锁互斥，避免与 SchedulerHeartbeat 线程并发读写
        # ObjectiveExecutor 内部状态字典。
        with self._maybe_sched_lock():
            self._safe_on_done(turn_id, summary, result_valid)

    def _safe_on_done(self, turn_id: str, summary: str, valid: bool) -> None:
        try:
            self._on_done(turn_id, summary, valid=valid)
        except Exception as exc:
            log.warning("on_done callback failed for turn_id=%s: %s", turn_id, exc)

    def _safe_on_failed(self, turn_id: str, error: str) -> None:
        try:
            self._on_failed(turn_id, error)
        except Exception as exc:
            log.warning("on_failed callback failed for turn_id=%s: %s", turn_id, exc)

    def shutdown(self, wait: bool = False) -> None:
        """daemon 退出时调用：停止接受新 step，不强行打断正在跑的线程
        （wait=False 时不阻塞退出流程，与其它子系统的关停风格一致）。"""
        with self._lock:
            self._stopped = True
        self._executor.shutdown(wait=wait, cancel_futures=not wait)


class ObjectivePersistentRunner:
    """
    [daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md 阶段一]
    "目标级持久 Worker"：与 ObjectiveIsolatedRunner 接口完全一致（可直接替换
    `ObjectiveExecutor._submit_fn`，不需要改动 objective_executor.py 的 step
    提交/状态机逻辑本身），但内部实现不同——每个 execution_id 独占一个专属的
    单线程 ThreadPoolExecutor，第一个 step 提交时惰性构建一个 Agent 实例并
    缓存，同一 execution 后续所有 step 都复用这一个 Agent 实例、在同一条
    专属线程上执行。

    这与 build_objective_agent() docstring 里强调的"Agent 的 thread-local
    状态只在构造它的那条线程上安全"这一前提严格对齐：因为该 execution 的
    所有 step 永远只在它自己的专属线程上跑，不会跨线程复用 Agent 实例。

    不同 execution_id 之间各自的专属线程互相独立，因此天然并行——某一时刻
    存在几个活跃 execution，就有几条线程在真正同时执行 run_turn()，不再像
    共享 bridge.input_queue 的默认提交路径那样排队等一个单线程队列轮到自己。
    真正的并发数上限仍然由 ObjectiveExecutor 既有的
    max_concurrent_objectives_cap/adaptive_concurrency_* 机制约束，本类不
    新增独立的并发上限判断，只负责"某个 execution 该在哪条线程、哪个 Agent
    实例上跑"。
    """

    def __init__(
        self,
        base_cfg: AppConfig,
        on_done: Callable[..., Any],
        on_failed: Callable[[str, str], Any],
        inner_max_turns: Optional[int] = None,
        idle_ttl_seconds: float = 1800.0,
        sched_lock: Optional[threading.Lock] = None,
    ) -> None:
        self._base_cfg = base_cfg
        self._on_done = on_done
        self._on_failed = on_failed
        self._inner_max_turns = inner_max_turns
        self._idle_ttl = max(1.0, float(idle_ttl_seconds))
        # [daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md
        # §7.1 修复] 同 ObjectiveIsolatedRunner：默认 None，行为不变；心跳
        # 模式开启时与 SchedulerHeartbeat 线程互斥，见 _maybe_sched_lock()。
        self._sched_lock = sched_lock

        self._lock = threading.Lock()
        self._executors: dict[str, concurrent.futures.ThreadPoolExecutor] = {}
        self._agents: dict[str, Agent] = {}
        self._last_used_at: dict[str, float] = {}
        self._stopped = False
        # [daemon_task_hang_recovery_and_watchdog_hardening_plan.md 阶段三·
        # 顺带做] release()/_evict_idle_locked() 实际丢弃过一个专属线程池
        # 的次数，进程内累计，不持久化——包含正常终止收尾（Objective
        # completed/failed/cancelled）和 idle 兜底回收两类，不特指"卡死
        # 回收"这一种；单独看这个数字不能断定"发生了多少次卡死"，但长期
        # 运行下的频率变化仍是值得关注的观测信号（详见
        # execution_model_status 里同一字段的说明）。
        self._discarded_worker_count: int = 0

    def _maybe_sched_lock(self):
        if self._sched_lock is None:
            return contextlib.nullcontext()
        return self._sched_lock

    def submit(self, message: str, initiator: str, meta: dict) -> Optional[str]:
        """与 `submit_fn(message, initiator, meta) -> turn_id` 签名一致，
        可直接赋给 `ObjectiveExecutor._submit_fn`。"""
        execution_id = meta.get("execution_id") or f"unknown-{uuid.uuid4().hex[:8]}"
        with self._lock:
            if self._stopped:
                return None
            self._evict_idle_locked(exclude=execution_id)
            executor = self._executors.get(execution_id)
            if executor is None:
                executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix=f"obj-worker-{execution_id[:12]}",
                )
                self._executors[execution_id] = executor
            self._last_used_at[execution_id] = time.time()

        turn_id = f"obj-persist-{uuid.uuid4().hex[:12]}"
        try:
            executor.submit(self._run_step, turn_id, execution_id, message, meta)
        except RuntimeError:
            # executor 已 shutdown（该 execution 已被 release()，或 daemon
            # 正在退出），当作提交失败处理，ObjectiveExecutor 会按现有
            # "submit 返回 None"的既有路径降级。
            return None
        return turn_id

    def _run_step(self, turn_id: str, execution_id: str, message: str, meta: dict) -> None:
        from mini_agent.perception.format_correction_detector import is_valid_final_result

        objective_title = meta.get("objective_id", "") or "(unknown)"

        agent = self._agents.get(execution_id)
        if agent is None:
            try:
                agent = build_objective_agent(
                    self._base_cfg, objective_title, execution_id,
                    inner_max_turns=self._inner_max_turns, persistent=True,
                )
            except Exception as exc:
                log.warning("build_objective_agent(persistent) failed for turn_id=%s: %s", turn_id, exc)
                with self._maybe_sched_lock():
                    self._safe_on_failed(turn_id, f"持久 Worker Agent 构建失败: {exc}")
                return
            self._agents[execution_id] = agent

        try:
            result = agent.run_turn(message)
        except Exception as exc:
            log.warning("persistent run_turn failed for turn_id=%s: %s", turn_id, exc)
            with self._maybe_sched_lock():
                self._safe_on_failed(turn_id, str(exc))
            return

        summary = (result or "").strip()
        summary = summary.split("\n")[0][:200]
        result_valid = not getattr(agent, "_last_turn_result_invalid", False)
        if not result_valid and not is_valid_final_result(result or ""):
            result_valid = False

        with self._lock:
            if execution_id in self._last_used_at:
                self._last_used_at[execution_id] = time.time()

        # [§7.1 修复] 与 AgentRunner 里两处 on_turn_done/on_turn_failed 调用
        # 用同一把共享锁互斥，避免与 SchedulerHeartbeat 线程并发读写
        # ObjectiveExecutor 内部状态字典——这是本次修复要解决的核心问题：
        # 阶段一新增的持久 Worker 回调路径此前完全没有经过这把锁。
        with self._maybe_sched_lock():
            self._safe_on_done(turn_id, summary, result_valid)

    def release(self, execution_id: str) -> None:
        """Objective 到达终止状态（completed/failed/cancelled）时调用：立即
        关闭该 execution 的专属线程、丢弃 Agent 实例。用作
        `ObjectiveExecutor(release_worker_fn=...)` 的回调。同时也是
        reap_stale_steps() 判定 step 卡死时的回调（见该方法说明）。"""
        with self._lock:
            executor = self._executors.pop(execution_id, None)
            self._agents.pop(execution_id, None)
            self._last_used_at.pop(execution_id, None)
            if executor is not None:
                self._discarded_worker_count += 1
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    @property
    def discarded_worker_count(self) -> int:
        """[阶段三·顺带做] 见 __init__ 里的说明：release()/idle 兜底回收
        累计丢弃过的专属线程池次数（不特指卡死，含正常终止收尾）。"""
        with self._lock:
            return self._discarded_worker_count

    def _evict_idle_locked(self, exclude: Optional[str] = None) -> None:
        """调用方已持有 self._lock。清理超过 idle_ttl 未使用的 execution
        专属线程——release() 是主要的清理路径（由 ObjectiveExecutor 在
        终止时主动调用），这里是兜底（比如 daemon 异常重启前的孤儿
        execution，某次终止回调没有触发到），避免线程/Agent 泄漏。"""
        now = time.time()
        stale = [
            eid for eid, ts in self._last_used_at.items()
            if eid != exclude and (now - ts) >= self._idle_ttl
        ]
        for eid in stale:
            executor = self._executors.pop(eid, None)
            self._agents.pop(eid, None)
            self._last_used_at.pop(eid, None)
            if executor is not None:
                self._discarded_worker_count += 1
                executor.shutdown(wait=False, cancel_futures=True)

    def _safe_on_done(self, turn_id: str, summary: str, valid: bool) -> None:
        try:
            self._on_done(turn_id, summary, valid=valid)
        except Exception as exc:
            log.warning("on_done callback failed for turn_id=%s: %s", turn_id, exc)

    def _safe_on_failed(self, turn_id: str, error: str) -> None:
        try:
            self._on_failed(turn_id, error)
        except Exception as exc:
            log.warning("on_failed callback failed for turn_id=%s: %s", turn_id, exc)

    def active_execution_ids(self) -> list[str]:
        """仅供测试/可观测性使用：当前仍持有专属线程的 execution_id 列表。"""
        with self._lock:
            return list(self._executors.keys())

    def shutdown(self, wait: bool = False) -> None:
        """daemon 退出时调用：停止接受新 step，不强行打断正在跑的线程
        （wait=False 时不阻塞退出流程，与 ObjectiveIsolatedRunner 一致）。"""
        with self._lock:
            self._stopped = True
            executors = list(self._executors.values())
            self._executors.clear()
            self._agents.clear()
            self._last_used_at.clear()
        for executor in executors:
            executor.shutdown(wait=wait, cancel_futures=not wait)
