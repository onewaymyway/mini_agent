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

[goal_cron_unified_scheduler_improvement_plan.md P3]
  上面这段"运维/看板可以用……判断"此前只是一句文档建议——没有任何代码
  真的去做这个判断，用户必须点开面板细看两个时间戳做心算才能发现心跳假死。
  本阶段把这句建议升级为主动检测，但检测本身**不能**放在原来那条 tick
  循环线程里顺带做——如果真的卡在某次 tick() 里，那条线程自己也会跟着
  卡住，永远轮不到检测代码执行，等于用一个会被卡死的东西去检测自己是否
  卡死。因此新增一条完全独立的看门狗线程 `_watchdog_run()`，只负责按
  自己的轮询节奏调用 `_check_stuck()`，与是否发生 tick() 无关。

  判定条件与上面文档描述完全一致——当前确实处在"已经开始但还没结束"的
  一次 tick 里（`last_tick_started_at > last_tick_finished_at`，排除
  "从未 tick 过"和"tick 过但已经正常结束"这两种不该误报的情况），且这次
  tick 已经持续超过 `tick_interval_seconds * stuck_threshold_multiplier`
  （默认 2 倍）。

  命中时通过 `NotificationDispatcher` 告警一次，并置位
  `suspected_stuck` 供 `execution_model_status` 展示；命中期间不重复
  刷屏（同一次"卡住事件"只在刚检测到的那一刻告警一次），直到某次 tick()
  终于返回（`finally` 分支里 `last_tick_finished_at` 被刷新）才复位，
  下一次再卡住会重新告警——与 P2 的 `consecutive_skip_count == threshold`
  是同一节流思路。

[daemon_dual_signal_hang_detection_plan.md 阶段B]
  上面这些观测字段（`suspected_stuck` 等）此前只通过
  `/v1/self/execution_model_status` 这个 HTTP 端点对外暴露——如果
  daemon 的 event loop 被某个未经 `run_blocking()` 包装的慢请求整个
  占满，这个端点和 `/v1/health` 一样会无响应，导致"核心调度已经卡死
  但 HTTP 层还能勉强应答"这种更危险的场景完全检测不到。看门狗线程
  `_check_stuck()` 每轮轮询结束后新增一步：把观测状态写入
  `.agent/scheduler_heartbeat_status.json`（`_write_status_file()`），
  完全不经过 HTTP/asyncio，供外部的 `cli/daemon_supervisor.py` 直接
  读文件判定，与"核心调度心跳为主信号、HTTP 响应为辅助信号"的双信号
  判定矩阵配套使用。仅在 `scheduler_heartbeat_enabled=True`（本类被
  构造并启动）时才会产生这个文件；未开启心跳的部署没有该文件，
  supervisor 端自动退化为纯 HTTP 判定，向后兼容。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)


def heartbeat_status_file_path(project_root):
    """[daemon_dual_signal_hang_detection_plan.md 阶段B] 调度心跳磁盘旁路
    状态文件路径：`<project_root>/.agent/scheduler_heartbeat_status.json`。

    独立于本模块定义（而不是依赖调用方自己拼字符串），供
    `cli/daemon_supervisor.py`（读取方，外部进程）与本模块（写入方）
    共享同一份路径约定，避免两处各写一份字符串产生不一致。
    """
    from pathlib import Path
    return Path(project_root) / ".agent" / "scheduler_heartbeat_status.json"


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
        tick_interval_seconds: float = 60.0,
        paths=None,
        stuck_threshold_multiplier: float = 2.0,
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
        # [P3] tick() 执行看门狗：以下字段只在 `_check_stuck()` 内读写，
        # 复用 self._stats_lock 一并保护，不额外新增一把锁。
        self._tick_interval_seconds = max(0.01, float(tick_interval_seconds))
        self._stuck_threshold_multiplier = max(1.0, float(stuck_threshold_multiplier))
        self._paths = paths
        self._suspected_stuck: bool = False
        self._stuck_alert_sent: bool = False
        # [P3] 看门狗必须是一条独立于主 tick 循环的线程——如果只是在
        # run() 的主循环里"tick 完之后顺带检查"，一旦真的卡在某次
        # tick() 里，主循环会阻塞在 `self._maybe_tick()` 那一行出不来，
        # 检查代码自己也永远没有机会被执行到（这正是本阶段要解决的
        # 故障场景本身，绝不能让检测机制依赖同一条会被卡住的线程）。
        self._watchdog_stop_evt = threading.Event()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_poll_interval = min(self._interval, 2.0)

    def start(self) -> None:  # type: ignore[override]
        super().start()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_run, daemon=True, name=f"{self.name}-watchdog",
        )
        self._watchdog_thread.start()

    def stop(self) -> None:
        """请求心跳线程退出。非阻塞——不等待当前正在进行的 tick() 跑完。
        daemon 关停流程如需确认线程已退出，可自行调用 self.join(timeout=...)。"""
        self._stop_evt.set()
        self._watchdog_stop_evt.set()

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

    @property
    def suspected_stuck(self) -> bool:
        """[P3] 当前是否怀疑心跳线程卡在某次未返回的 tick() 里
        （alive=True 但已经很久没有产生新的 tick）。"""
        with self._stats_lock:
            return self._suspected_stuck

    def set_tick_interval_seconds(self, tick_interval_seconds: float) -> None:
        """[P3] AutonomousLoop 的 tick_interval 可能在运行期间被配置/灰度
        调整，允许调用方（api/server.py）随时刷新看门狗判定用的基准值，
        不强制要求构造时就拿到最终值。"""
        with self._stats_lock:
            self._tick_interval_seconds = max(0.01, float(tick_interval_seconds))

    def run(self) -> None:
        log.info("SchedulerHeartbeat started (poll_interval=%.1fs)", self._interval)
        while not self._stop_evt.is_set():
            # 用 Event.wait() 代替 time.sleep()，这样 stop() 之后能立刻
            # 从这次等待里醒来退出，而不是最多要等满一个 interval。
            if self._stop_evt.wait(self._interval):
                break
            self._maybe_tick()
        log.info("SchedulerHeartbeat stopped")

    def _watchdog_run(self) -> None:
        """[P3] 独立看门狗线程：不参与 tick 触发，只按自己的轮询间隔
        （不超过 2 秒，避免用户配置很长的 poll_interval 时看门狗反应
        也跟着变慢）检查主线程是否卡在某次未返回的 tick() 里。"""
        while not self._watchdog_stop_evt.is_set():
            if self._watchdog_stop_evt.wait(self._watchdog_poll_interval):
                break
            self._check_stuck()

    def _check_stuck(self) -> None:
        """[P3] tick() 执行看门狗：把模块 docstring 里"运维/看板可以用
        ……判断心跳假死"这句建议升级为主动检测。每次心跳轮询醒来（不管
        这一轮是否真的触发了 tick()）都做一次判定，命中时告警一次并
        置位 suspected_stuck，直到卡住的那次 tick() 终于返回才复位。
        本方法自身绝不抛出异常影响心跳线程存活。"""
        try:
            with self._stats_lock:
                started_at = self._last_tick_started_at
                finished_at = self._last_tick_finished_at
                threshold_seconds = self._tick_interval_seconds * self._stuck_threshold_multiplier
            # 从未 tick 过（started_at == 0）不判定；tick 过但已经正常
            # 结束（finished_at >= started_at）也不判定——只有"已经开始
            # 但还没结束"才是我们要抓的"卡在某次 tick() 里"这种情况。用
            # `time.time() - started_at`（本次 tick 已经跑了多久）而不是
            # `- finished_at`（上一次成功 tick 距今多久）来比较阈值——
            # 后者在"第一次 tick 就卡住、finished_at 还停在 0.0"这种场景
            # 下会立刻算出一个巨大的差值（距 Unix 纪元），误判为卡死。
            is_currently_stuck = (
                started_at > 0.0
                and started_at > finished_at
                and (time.time() - started_at) > threshold_seconds
            )
            with self._stats_lock:
                if is_currently_stuck:
                    self._suspected_stuck = True
                    if not self._stuck_alert_sent:
                        self._stuck_alert_sent = True
                        stuck_seconds = time.time() - started_at
                        should_alert = True
                    else:
                        should_alert = False
                else:
                    # 卡住的那次 tick() 已经返回（或从未发生过卡死），
                    # 复位，允许下一次卡住重新告警一次。
                    self._suspected_stuck = False
                    self._stuck_alert_sent = False
                    should_alert = False
                    stuck_seconds = 0.0
            if should_alert:
                log.warning(
                    "SchedulerHeartbeat suspected stuck: tick() has been running for "
                    "%.1fs (threshold=%.1fs)", stuck_seconds, threshold_seconds,
                )
                self._alert_stuck(stuck_seconds)
        except Exception as exc:
            log.warning("SchedulerHeartbeat._check_stuck() raised: %s", exc)
        # [daemon_dual_signal_hang_detection_plan.md 阶段B] 无论本轮是否
        # 命中卡死判定，都顺带写一次磁盘旁路状态文件——写入本身必须与上面
        # 的判定逻辑解耦（各自 try/except），避免文件写入失败反过来影响
        # 看门狗线程自身的存活或告警节流状态。
        self._write_status_file()

    def _write_status_file(self) -> None:
        """[daemon_dual_signal_hang_detection_plan.md 阶段B] 把当前心跳
        观测状态写入 `.agent/scheduler_heartbeat_status.json`，完全不经过
        HTTP/asyncio——即使 event loop 被某个未包 `run_blocking()` 的慢
        请求整个占满，这份状态依然能被外部的 supervisor 进程直接读到，
        用来判断"核心调度是否真的卡死"，不必依赖 `/v1/health` 或
        `/v1/self/execution_model_status` 这两个可能同样被堵住的 HTTP
        端点。`self._paths` 为 None（未能构造 AgentPaths，理论上不该
        发生但不排除极端环境）时直接跳过，不影响心跳线程本身。

        用"临时文件 + 原子 rename"写入，避免读取方（supervisor）在写入
        过程中读到半截 JSON；任何异常都吞掉，绝不能让状态文件写入失败
        影响看门狗线程存活。
        """
        if self._paths is None:
            return
        try:
            with self._stats_lock:
                payload = {
                    "written_at": time.time(),
                    "last_tick_started_at": self._last_tick_started_at,
                    "last_tick_finished_at": self._last_tick_finished_at,
                    "last_tick_duration_seconds": self._last_tick_duration_seconds,
                    "tick_interval_seconds": self._tick_interval_seconds,
                    "suspected_stuck": self._suspected_stuck,
                    "pid": os.getpid(),
                }
            target = heartbeat_status_file_path(self._paths.project_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = target.with_suffix(target.suffix + f".tmp{os.getpid()}")
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(tmp_path, target)
        except Exception as exc:
            log.warning("SchedulerHeartbeat._write_status_file() raised: %s", exc)

    def _alert_stuck(self, stuck_seconds: float) -> None:
        """告警失败静默降级，不影响心跳线程本身。"""
        if self._paths is None:
            return
        try:
            from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
            NotificationDispatcher(self._paths).dispatch(NotificationMessage(
                title="调度心跳疑似卡死",
                body=(
                    f"SchedulerHeartbeat 已连续 {stuck_seconds:.0f} 秒未能完成一次 "
                    "tick()，线程仍存活（alive=True）但可能卡在某次同步调用里，"
                    "建议检查是否有新代码违反了 tick() 内部'决策+提交、不做耗时"
                    "调用'的约束"
                )[:200],
                source="scheduler_heartbeat_stuck",
                meta={"stuck_seconds": round(stuck_seconds, 1)},
            ))
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where="mini_agent.evolution.scheduler_heartbeat.SchedulerHeartbeat._alert_stuck")

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
