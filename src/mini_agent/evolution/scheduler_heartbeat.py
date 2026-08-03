"""
[daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md 阶段二]

调度心跳独立化：不再依赖 AgentRunner 主循环"dequeue 超时后顺带检查"这种
协作式调度方式触发 AutonomousLoop.tick()，而是由一条独立的后台线程按自己
的轮询间隔主动检查、主动触发。

线程安全边界（这是本模块设计的核心，不是简单"另起一个线程调 tick()"）：
  - AutonomousLoop.tick() 内部只做"决策 + 提交"（判断该不该跑、调用
    submit_fn 把 step 交给执行层），不做真正耗时的 LLM 调用本身，因此持锁
    时间很短。
  - 真正耗时的 agent.run_turn()（无论是用户交互还是自主任务的 step）不
    持有这把锁——锁只在 AgentRunner 主循环处理完一个 turn、回调
    objective_executor.on_turn_done()/on_turn_failed() 那一小段状态更新
    代码上短暂持有（由调用方在那两处调用点自行加锁，本模块不负责）。
  - 因此本线程和 AgentRunner 主循环之间只在"状态更新"这个短暂窗口互斥，
    不会因为一次长 turn 而让心跳整体停摆。

本模块只负责"独立心跳线程"本身；共享锁对象由调用方（api/server.py）创建
并同时传给 AgentRunner 和本类，本类不创建、也不管理锁的生命周期。

[daemon_task_hang_recovery_and_watchdog_hardening_plan.md 阶段二]
  `_maybe_tick()` 此前只处理了"抛异常"这一种失败模式——如果
  `autonomous_loop.tick()`（或它间接调用到的任何一段同步代码）卡住不
  返回而不抛异常，心跳线程会永久停在 `with self._lock: ...` 里出不来，
  `stop()`/`join()` 对一个已经阻塞在业务逻辑里的线程没有任何作用，而
  `is_alive()` 在这种情况下仍然是 True——"看起来一切正常，实际上心跳
  早就停摆了"，是一个比"线程真的死了"更隐蔽的故障模式。

  Python 线程本身无法被强制中断（与 daemon_execution_model_and_
  scheduler_heartbeat_improvement_plan.md §7.5 的结论一致），本模块能做
  的只是**观测**：记录 last_tick_started_at/last_tick_finished_at，暴露
  给 execution_model_status。运维/看板可以用
  `now - last_tick_finished_at > 2 * tick_interval_seconds`（或类似阈值）
  判断"心跳线程虽然 alive=True，但已经不再产生新的 tick"，作为心跳假死
  的间接信号。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)


class SchedulerHeartbeat(threading.Thread):
    """独立的调度心跳线程：按自己的轮询间隔检查
    `autonomous_loop.should_tick()`，到期则在持有共享锁的情况下调用
    `autonomous_loop.tick()`。

    与 AgentRunner 主循环原有的"dequeue 超时后顺带 tick"路径互斥——两者
    不应该同时启用，否则同一个 tick_interval 周期内可能被触发两次。由
    调用方（api/server.py）负责保证：开启本线程时，把 AgentRunner 的
    `heartbeat_owns_tick` 置为 True，让主循环不再自己触发 tick()。
    """

    def __init__(
        self,
        autonomous_loop,
        lock: threading.Lock,
        interval_seconds: float = 5.0,
        name: str = "scheduler-heartbeat",
    ) -> None:
        super().__init__(daemon=True, name=name)
        self._autonomous_loop = autonomous_loop
        self._lock = lock
        self._interval = max(0.5, float(interval_seconds))
        self._stop_evt = threading.Event()
        # [阶段二] 用独立的小锁保护这三个观测字段，不复用 self._lock——
        # self._lock 是与 AgentRunner 共享的业务锁，读取观测状态不应该
        # 依赖它是否被占用（尤其是心跳线程本身正卡在它上面等锁的时候，
        # 外部仍然应该能读到"上一次成功 tick 是什么时候"）。
        self._stats_lock = threading.Lock()
        self._last_tick_started_at: float = 0.0
        self._last_tick_finished_at: float = 0.0
        self._last_tick_duration_seconds: float = 0.0

    def stop(self) -> None:
        """请求心跳线程退出。非阻塞——不等待当前正在进行的 tick() 跑完。
        daemon 关停流程如需确认线程已退出，可自行调用 self.join(timeout=...)。"""
        self._stop_evt.set()

    # ── 观测（阶段二） ────────────────────────────────────────────────────

    @property
    def last_tick_started_at(self) -> float:
        """上一次真正开始执行 tick()（进入 self._lock 临界区）的时间戳，
        0.0 表示尚未发生过。"""
        with self._stats_lock:
            return self._last_tick_started_at

    @property
    def last_tick_finished_at(self) -> float:
        """上一次 tick() 结束（正常返回或抛异常）的时间戳，0.0 表示尚未
        发生过。与 last_tick_started_at 一起使用：
        `now - last_tick_finished_at > 2 * tick_interval_seconds` 可以
        作为"心跳虽然 alive=True 但已经假死"的间接信号。"""
        with self._stats_lock:
            return self._last_tick_finished_at

    @property
    def last_tick_duration_seconds(self) -> float:
        """上一次 tick() 实际耗时（秒）。"""
        with self._stats_lock:
            return self._last_tick_duration_seconds

    def run(self) -> None:
        log.info("SchedulerHeartbeat started (poll_interval=%.1fs)", self._interval)
        while not self._stop_evt.is_set():
            # 用 Event.wait() 代替 time.sleep()，这样 stop() 之后能立刻
            # 从这次等待里醒来退出，而不是最多要等满一个 interval。
            if self._stop_evt.wait(self._interval):
                break
            self._maybe_tick()
        log.info("SchedulerHeartbeat stopped")

    def _maybe_tick(self) -> None:
        try:
            if not self._autonomous_loop.should_tick():
                return
        except Exception as exc:
            log.warning("SchedulerHeartbeat.should_tick() raised: %s", exc)
            return

        started_at = time.time()
        with self._stats_lock:
            self._last_tick_started_at = started_at
        try:
            with self._lock:
                self._autonomous_loop.tick()
        except Exception as exc:
            # 与 AutonomousLoop 既有的"非核心子系统静默降级"原则一致：
            # 心跳线程本身绝不应该因为一次 tick() 内部异常而整体退出，
            # 下一轮轮询会自然重试。
            log.warning("SchedulerHeartbeat.tick() raised: %s", exc)
        finally:
            # [阶段二] 放在 finally 里——即使 tick() 抛异常，也要能看出
            # "心跳还在正常轮转，只是这一次业务失败了"，与"心跳彻底停摆"
            # 区分开：后者的表现是 last_tick_finished_at 长期不再更新。
            finished_at = time.time()
            with self._stats_lock:
                self._last_tick_finished_at = finished_at
                self._last_tick_duration_seconds = max(0.0, finished_at - started_at)
