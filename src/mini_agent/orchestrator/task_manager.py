"""
orchestrator/task_manager.py — 并发任务调度器

负责：
  - 接收 Task 提交
  - 依赖关系解析（depends_on）
  - 并发执行上限控制（max_workers）
  - SubAgent 生命周期管理
  - 任务状态查询和取消

设计：
  - 纯线程模型（threading），不依赖 asyncio
  - 单后台调度线程持续轮询，将满足条件的 PENDING 任务投入执行
  - 外部线程安全：所有状态访问通过 _lock 保护
  - 任务完成后记录保留（可查询历史）
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Iterator, Optional

from mini_agent.config import AppConfig
from .task import Task, TaskRecord, TaskStatus
from .sub_agent import SubAgent, LogCallback


class TaskManager:
    """
    并发任务调度器。

    使用方式：
        mgr = TaskManager(base_cfg, max_workers=4)
        mgr.start()

        tid = mgr.submit(Task(prompt="Write tests for utils.py"))
        mgr.submit(Task(prompt="Fix the bug in parser.py", depends_on=[tid]))

        mgr.wait_all()
        for rec in mgr.list_records():
            print(rec.task_id, rec.status, rec.result.output[:80])

        mgr.stop()
    """

    def __init__(
        self,
        base_cfg: AppConfig,
        max_workers: int = 4,
        on_log: Optional[LogCallback] = None,
        on_status_change: Optional[Callable[[TaskRecord], None]] = None,
    ) -> None:
        self.base_cfg = base_cfg
        self.on_log = on_log
        self.on_status_change = on_status_change

        self._records: dict[str, TaskRecord] = {}   # task_id → TaskRecord
        self._agents:  dict[str, SubAgent] = {}     # task_id → SubAgent
        self._lock = threading.Lock()
        self._session_id: Optional[str] = None      # 由主 Agent 在 session 建立后注入
        self._scheduler_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._poll_interval = 0.3   # 调度间隔（秒）

        # [Phase E / 3.3] 跨 SubAgent 共享的 ToolResultCache（对应设计文档第 5 节
        # "SubAgent 信息继承"）。每个 SubAgent 默认各自新建一份缓存，意味着
        # SubAgent A 读过的文件，SubAgent B 还要重新读一次——并发跑多个子任务时
        # 这部分重复 token 消耗完全可以避免。TaskManager 持有唯一实例并通过
        # SubAgent 注入给各自的 Agent，ToolResultCache 内部已加 threading.Lock
        # 保护并发读写（见 perception/tool_cache.py）。仅在功能开关打开时创建，
        # 避免未启用 tool_cache 的场景下白白占用一份空缓存对象。
        self._shared_tool_cache = None
        if getattr(base_cfg, "tool_cache_enabled", False):
            from mini_agent.perception.tool_cache import ToolResultCache
            self._shared_tool_cache = ToolResultCache(
                max_entries=getattr(base_cfg.perception, "tool_cache_max_entries", 256)
            )

        # [Phase E / 3.3] 主 agent 的 memory backend 引用（由 Agent.__init__ 事后
        # 通过 set_memory_sinks() 注册），用于 SubAgent 结束时触发 reload()。
        self._main_memory = None
        self._main_global_memory = None

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """启动后台调度线程。"""
        self._stop_event.clear()
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            name="task-manager-scheduler",
            daemon=True,
        )
        self._scheduler_thread.start()

    def set_session_id(self, session_id: str) -> None:
        """由主 Agent 在 session 建立后调用，使后续 SubAgent 任务日志写到正确目录。"""
        self._session_id = session_id

    @property
    def max_workers(self) -> int:
        from .concurrency import get_task_sem
        try:
            return get_task_sem().limit
        except Exception:
            return 4

    @max_workers.setter
    def max_workers(self, value: int) -> None:
        from .concurrency import set_max_tasks
        try:
            set_max_tasks(value)
        except Exception:
            pass

    def stop(self, cancel_pending: bool = True) -> None:
        """
        停止调度器。
        cancel_pending=True 时取消所有未完成任务，并等待所有 SubAgent 线程结束。
        """
        self._stop_event.set()
        if cancel_pending:
            # 先取消所有任务
            with self._lock:
                for rec in self._records.values():
                    if not rec.is_terminal:
                        agent = self._agents.get(rec.task_id)
                        if agent:
                            agent.cancel()
                        elif rec.status == TaskStatus.PENDING:
                            rec.status = TaskStatus.CANCELLED
                            rec.finished_at = time.time()
            # 等待所有正在运行的 SubAgent 线程结束（最多 5 秒）
            with self._lock:
                agents = list(self._agents.values())
            for agent in agents:
                agent.join(timeout=1.0)
        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=5)

    # ── 任务提交 ──────────────────────────────────────────────────────────────

    def submit(self, task: Task) -> str:
        """
        提交一个任务，返回 task_id。
        任务立即进入 PENDING 状态，等待调度器分配 worker。
        """
        record = TaskRecord(task=task)
        with self._lock:
            self._records[task.id] = record
        # [SYS-HOOKS] TaskCreated：任务提交时触发
        try:
            from mini_agent.hooks import get_hook_manager as _ghm_tc
            _hm_tc = _ghm_tc()
            if _hm_tc is not None:
                _hm_tc.run("TaskCreated", {
                    "task_id": task.id,
                    "task_name": task.name,
                    "prompt": task.prompt[:200],
                    "tags": list(task.tags),
                })
        except Exception:
            pass
        return task.id

    def submit_many(self, tasks: list[Task]) -> list[str]:
        """批量提交，返回 task_id 列表（顺序与 tasks 一致）。"""
        return [self.submit(t) for t in tasks]

    # ── 取消与控制 ────────────────────────────────────────────────────────────

    def cancel(self, task_id: str) -> bool:
        """取消指定任务。已完成的任务无法取消，返回 False。"""
        with self._lock:
            rec = self._records.get(task_id)
            if rec is None or rec.is_terminal:
                return False
            agent = self._agents.get(task_id)
        if agent:
            agent.cancel()
        else:
            with self._lock:
                rec.status = TaskStatus.CANCELLED
                rec.finished_at = time.time()
        return True

    def cancel_all(self) -> int:
        """取消所有未完成任务，返回取消数量。"""
        task_ids = list(self._records.keys())
        return sum(1 for tid in task_ids if self.cancel(tid))

    def reset(self) -> None:
        """
        清空所有历史任务记录（_records）与 SubAgent 引用（_agents）。

        用于 `/session new`（Agent.new_session()）场景：新 session 不应该
        继续看到上一个 session 遗留下来的 SubAgent 任务状态/结果。

        注意：不应在 resume（load_session）场景调用本方法——resume 是回到
        一个已有 session，用户期望看到该 session 之前的任务记录。

        若存在仍在运行中的任务，会先尝试取消，避免其结束回调/日志写入
        混入新 session 的目录。
        """
        with self._lock:
            for rec in list(self._records.values()):
                if not rec.is_terminal:
                    agent = self._agents.get(rec.task_id)
                    if agent:
                        try:
                            agent.cancel()
                        except Exception:
                            pass
            self._records.clear()
            self._agents.clear()

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def get(self, task_id: str) -> Optional[TaskRecord]:
        with self._lock:
            return self._records.get(task_id)

    def list_records(
        self,
        status: Optional[TaskStatus] = None,
        tag: Optional[str] = None,
    ) -> list[TaskRecord]:
        with self._lock:
            recs = list(self._records.values())
        if status:
            recs = [r for r in recs if r.status == status]
        if tag:
            recs = [r for r in recs if tag in r.task.tags]
        return sorted(recs, key=lambda r: r.task.created_at)

    def running_count(self) -> int:
        with self._lock:
            return sum(
                1 for r in self._records.values()
                if r.status == TaskStatus.RUNNING
                or (r.status == TaskStatus.PENDING and r.task_id in self._agents)
            )

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for r in self._records.values() if r.status == TaskStatus.PENDING)

    def stats(self) -> dict:
        with self._lock:
            recs = list(self._records.values())
        counts = {s: 0 for s in TaskStatus}
        for r in recs:
            counts[r.status] += 1
        return {
            "total": len(recs),
            "pending": counts[TaskStatus.PENDING],
            "running": counts[TaskStatus.RUNNING],
            "done": counts[TaskStatus.DONE],
            "failed": counts[TaskStatus.FAILED],
            "cancelled": counts[TaskStatus.CANCELLED],
        }

    # ── 等待 ──────────────────────────────────────────────────────────────────

    def wait(self, task_id: str, timeout: Optional[float] = None) -> Optional[TaskRecord]:
        """阻塞等待单个任务完成（或超时），返回最终 TaskRecord。"""
        deadline = time.time() + timeout if timeout else None
        while True:
            rec = self.get(task_id)
            if rec and rec.is_terminal:
                return rec
            if deadline and time.time() > deadline:
                return rec
            time.sleep(0.2)

    def wait_all(self, timeout: Optional[float] = None) -> bool:
        """
        阻塞等待所有任务进入终态。
        返回 True 表示全部完成，False 表示超时。
        """
        deadline = time.time() + timeout if timeout else None
        while True:
            with self._lock:
                all_done = all(r.is_terminal for r in self._records.values())
            if all_done:
                return True
            if deadline and time.time() > deadline:
                return False
            time.sleep(0.3)

    # ── 调度循环 ──────────────────────────────────────────────────────────────

    def _scheduler_loop(self) -> None:
        while not self._stop_event.is_set():
            self._tick()
            time.sleep(self._poll_interval)
        # 最后再 tick 一次，处理剩余任务
        self._tick()

    def _tick(self) -> None:
        """一次调度周期：检查依赖、启动就绪任务。"""
        with self._lock:
            # 【修复】同时统计 RUNNING 状态的任务，以及已经分配了 SubAgent
            # 但仍处于 PENDING（线程刚启动、正在等信号量）的任务，避免重复调度。
            active = sum(
                1 for r in self._records.values()
                if r.status == TaskStatus.RUNNING
                or (r.status == TaskStatus.PENDING and r.task_id in self._agents)
            )
            from .concurrency import get_task_sem
            sem_limit = get_task_sem().limit
            if active >= sem_limit:
                return

            # 收集所有已完成任务的 id（用于依赖检查）
            done_ids = {
                r.task_id for r in self._records.values()
                if r.status == TaskStatus.DONE
            }
            failed_ids = {
                r.task_id for r in self._records.values()
                if r.status in (TaskStatus.FAILED, TaskStatus.CANCELLED)
            }

            # 找出所有可以启动的 PENDING 任务
            ready: list[TaskRecord] = []
            for rec in self._records.values():
                if rec.status != TaskStatus.PENDING:
                    continue
                deps = set(rec.task.depends_on)
                # 依赖有失败/取消 → 将本任务也取消
                if deps & failed_ids:
                    rec.status = TaskStatus.CANCELLED
                    rec.finished_at = time.time()
                    rec.append_log("Cancelled: dependency failed or cancelled")
                    self._notify_status(rec)
                    continue
                # 依赖全部完成 → 可以启动
                if deps <= done_ids:
                    ready.append(rec)

            # 按提交时间排序，填满 worker 槽位
            ready.sort(key=lambda r: r.task.created_at)
            slots = max(0, sem_limit - active)
            to_start = ready[:slots]

        # 在锁外启动，避免死锁
        for rec in to_start:
            self._launch(rec)

    def _launch(self, rec: TaskRecord) -> None:
        agent = SubAgent(
            record=rec,
            base_cfg=self.base_cfg,
            on_log=self._handle_log,
            on_terminal=self._handle_terminal,
            session_id=self._session_id,
            shared_tool_cache=self._shared_tool_cache,
        )
        with self._lock:
            self._agents[rec.task_id] = agent
            # 【修复】不在这里设置 RUNNING 状态。
            # 原来的代码在 agent.start() 之前就把状态改成 RUNNING，
            # 导致 SubAgent.start() 内部的 "status != PENDING" 检查提前触发，
            # 线程根本不会启动，_run() 永远不执行。
            # 正确做法：保持 PENDING，让 SubAgent._run_body() 在真正开始执行时
            # 再切换状态。
        agent.start()
        self._notify_status(rec)

    def _handle_log(self, task_id: str, line: str) -> None:
        if self.on_log:
            try:
                self.on_log(task_id, line)
            except Exception:
                pass

    def _handle_terminal(self, task_id: str, old_status: TaskStatus, new_status: TaskStatus) -> None:
        """处理 SubAgent 终态通知；FAILED 时尝试降级重试（13.2+15.3）。"""
        if new_status not in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
            return

        with self._lock:
            rec = self._records.get(task_id)

        if rec is None:
            return

        # [Stage 7 / 13.2+15.3] FAILED → 尝试降级重试链
        if new_status == TaskStatus.FAILED:
            if self._try_demotion(rec):
                # 已重新提交降级任务，不通知"最终失败"
                self._reload_main_memory_sinks()
                return

        # [SYS-HOOKS] TaskCompleted：任务进入终态时触发
        try:
            from mini_agent.hooks import get_hook_manager as _ghm_tcd
            _hm_tcd = _ghm_tcd()
            if _hm_tcd is not None:
                _status_val = rec.status.value if hasattr(rec.status, 'value') else str(rec.status)
                _hm_tcd.run("TaskCompleted", {
                    "task_id": rec.task_id,
                    "task_name": rec.task.name,
                    "status": _status_val,
                    "error": (rec.result.error if rec.result else "") or "",
                })
        except Exception:
            pass
        self._notify_status(rec)
        # [Phase E / 3.3] 重新加载主 agent memory
        self._reload_main_memory_sinks()

    def _try_demotion(self, rec: TaskRecord) -> bool:
        """
        [Stage 7 / 13.2+15.3] SubAgent 降级重试链实现。

        降级顺序（与设计文档 13.2+15.3 节对齐）：
          1. fallback_profiles 列表里按顺序切换 agent profile（换 SubAgent 角色）
          2. 所有 profile 都试过后，若设置了 demotion_scope，用更窄目标再试一次
          3. 超出 max_demotion_attempts 后放弃，留给外部判定为最终失败

        返回 True 表示已触发降级重试（任务重新入队），False 表示放弃。
        """
        task = rec.task
        if task.max_demotion_attempts <= 0:
            return False  # 未启用降级

        if rec.demotion_attempts >= task.max_demotion_attempts:
            return False  # 已耗尽降级次数

        # ── 阶段一：fallback_profiles 降级（换 profile）─────────────────────
        # 当前 profile 索引 = demotion_attempts（0=原始，1=fallback_profiles[0]，…）
        profile_idx = rec.demotion_attempts  # 这次失败前已用掉的 profile 序号
        if profile_idx < len(task.fallback_profiles):
            next_profile = task.fallback_profiles[profile_idx]
            rec.active_fallback_profile = next_profile
            rec.demotion_attempts += 1
            rec.append_log(
                f"[demotion] profile fallback → {next_profile!r} "
                f"(attempt {rec.demotion_attempts}/{task.max_demotion_attempts})"
            )
            self._resubmit_demoted(rec, profile_override=next_profile)
            return True

        # ── 阶段二：demotion_scope 降级（缩小任务目标）──────────────────────
        if task.demotion_scope and not rec.demoted_scope:
            rec.demoted_scope = True
            rec.demotion_attempts += 1
            rec.append_log(
                f"[demotion] scope demotion activated "
                f"(attempt {rec.demotion_attempts}/{task.max_demotion_attempts}): "
                f"{task.demotion_scope[:80]}"
            )
            self._resubmit_demoted(rec, scope_override=task.demotion_scope)
            return True

        return False  # 所有降级策略已耗尽

    def _resubmit_demoted(
        self,
        rec: TaskRecord,
        profile_override: str = "",
        scope_override: str = "",
    ) -> None:
        """
        把 TaskRecord 重置为 PENDING 并把对应的新 SubAgent 加入调度。

        不创建新的 TaskRecord / TaskRecord.task_id，复用原始 task_id，
        以便主 agent 的 depends_on 引用不失效。SubAgent 会被替换为新实例。
        """
        import copy

        # 构造降级后的 prompt
        demoted_prompt = rec.task.prompt
        if scope_override:
            demoted_prompt = demoted_prompt + "\n\n[降级约束] " + scope_override

        # 构造降级后的 system_extra（注入 profile 角色切换提示）
        system_extra = rec.task.system_extra or ""
        if profile_override:
            system_extra = (
                f"[fallback_profile={profile_override!r}] "
                f"你是一个 {profile_override} profile 的 SubAgent，正在重试上次失败的任务。\n"
                + system_extra
            )

        # 修改运行时状态，重置为 PENDING
        rec.status = TaskStatus.PENDING
        rec.result = None
        rec.started_at = None
        rec.finished_at = None

        # 用修改后的 prompt/system_extra 覆盖 task（浅拷贝后替换字段）
        # Task 是 dataclass，用 dataclasses.replace 最安全
        from dataclasses import replace as _dc_replace
        rec.task = _dc_replace(
            rec.task,
            prompt=demoted_prompt,
            system_extra=system_extra,
        )

        with self._lock:
            # 移除旧 SubAgent（已终止）
            self._agents.pop(rec.task_id, None)
            # 任务重新进入等待列表（_records 里已有此条目，status 已改 PENDING）

        # _tick() 会在下一个调度周期自动启动新 SubAgent

    def _reload_main_memory_sinks(self) -> None:
        for sink in (self._main_memory, self._main_global_memory):
            if sink is None:
                continue
            try:
                sink.reload()
            except Exception:
                pass

    def set_memory_sinks(self, memory=None, global_memory=None) -> None:
        """
        由主 Agent.__init__ 调用，登记自己的 memory / global_memory backend 实例。

        TaskManager 通常在主 Agent 构造之前就已经 init_task_manager() 创建好
        （见 cli/app.py 的初始化顺序），因此用"事后注册"而不是构造函数参数传入。
        """
        self._main_memory = memory
        self._main_global_memory = global_memory

    def _notify_status(self, rec: TaskRecord) -> None:
        if self.on_status_change:
            try:
                self.on_status_change(rec)
            except Exception:
                pass