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
"""

from __future__ import annotations

import logging
import threading
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

    def stop(self) -> None:
        """请求心跳线程退出。非阻塞——不等待当前正在进行的 tick() 跑完。
        daemon 关停流程如需确认线程已退出，可自行调用 self.join(timeout=...)。"""
        self._stop_evt.set()

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
        try:
            with self._lock:
                self._autonomous_loop.tick()
        except Exception as exc:
            # 与 AutonomousLoop 既有的"非核心子系统静默降级"原则一致：
            # 心跳线程本身绝不应该因为一次 tick() 内部异常而整体退出，
            # 下一轮轮询会自然重试。
            log.warning("SchedulerHeartbeat.tick() raised: %s", exc)
