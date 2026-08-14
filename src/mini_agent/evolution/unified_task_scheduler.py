# -*- coding: utf-8 -*-
"""
[goal_cron_unified_scheduler_improvement_plan.md P5 · 长期目标 · 第 1-4 步]

本模块是"收敛到统一调度层"这一长期目标的地基，按方案原文的分步迁移
路径实现：

  1. 定义统一接口：`SchedulableTask`（最小字段：source/task_id/priority/
     due_at/resource_estimate）+ `TaskChannel` 协议
     （`poll_due() -> list[SchedulableTask]`、
     `execute(task) -> concurrent, non-blocking`）。
  2. 先适配只读部分：让 Goal（Objective）/普通 cron/goal_cycle 三条通道各自
     实现 `TaskChannel.poll_due()`，`UnifiedTaskScheduler` 先只做"聚合展示 +
     统一排序建议"，不接管真正的执行决策。
  3. 接管仲裁裁决：新增 `allocate_weighted_slots()` 纯函数，degraded 状态
     下 goal/cron 两通道的并发上限可选（`scheduler.unified_arbitration_
     enabled`，默认关闭）改由本函数按权重 + cron 保底统一计算。
  4. 接管实际派发：`CronChannelAdapter`/`GoalCycleChannelAdapter`/
     `ObjectiveChannelAdapter` 的 `execute()` 均已实现真正委托派发
     （详见下方范围边界）——[goal_cron_convergence_and_governance_
     improvement_plan.md Track 1] `ObjectiveChannelAdapter.execute()`
     的缺口已补上。

**明确的范围边界**：
- 本模块 **不修改** `ObjectiveExecutor`/`GoalBacklog` 任何一行既有代码，
  只是在它们之上包一层只读适配器；`CronScheduler` 在 P5 第 4 步做了一次
  行为保留的内部重构（把 `tick()` 循环体里"触发单个 job + 记账"的逻辑
  抽成 `_trigger_and_record()`/`trigger_job_now()`，供本模块的
  `execute()` 复用），`tick()` 自身的到期判断/排序/触发顺序完全不变；
  `AutonomousLoop` 在 Track 1 做了对等的重构（把 `_tick_maintenance()`
  排序循环体里"触发单个 Objective 候选 + 记账"的逻辑抽成
  `_trigger_objective_candidate()`/`trigger_objective_now()`），
  `_tick_maintenance()` 自身的排序/筛选/触发顺序同样完全不变。
- 三个适配器的 `execute()` 现在都是真正的委托派发，但**目前仍未被
  `UnifiedTaskScheduler` 自身或 `AutonomousLoop`/`CronScheduler` 的任何
  既有 tick 路径调用**——`CronScheduler.tick()`/`AutonomousLoop.
  _tick_maintenance()` 依然是当前唯一的实际触发入口，三个 `execute()`
  只是"已经可以安全调用，但还没有人在调用"。是否/何时切换到统一入口
  是 P5 第 5 步的范围，`scheduler.unified_dispatch_enabled` 默认值不
  在 Track 1 范围内调整。

[goal_cron_task_optimization_holistic_plan.md §5 调度联动子项 · 已实施]
`ObjectiveChannelAdapter.poll_due()` 新增阶段感知的 `resource_estimate`
——按该 Goal `ExecutionPhaseState` 的 `last_known_effective_mode()` 经
`execution_phase.phase_resource_multiplier()` 换算出的相对倍率
（explore/converge 更宽松，stable/tidy 更收紧），替代此前恒为 `1.0` 的
占位值。这一步仍然只是"只读预览"层面的可观测性增强，目前唯一的消费方是
`/self/unified_scheduler_preview` 诊断端点；`allocate_weighted_slots()`
接管的仲裁裁决仍只用 `channel_weights`（goal/cron 两通道整体权重），
尚未把单个 Goal 的 `resource_estimate` 纳入真正的槽位分配计算——这仍是
"具体权重该怎么用"需要先观察真实数据再排期的部分，见 §5 剩余记录。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, runtime_checkable

__all__ = [
    "SchedulableTask",
    "TaskChannel",
    "UnifiedTaskScheduler",
    "ObjectiveChannelAdapter",
    "CronChannelAdapter",
    "GoalCycleChannelAdapter",
    "allocate_weighted_slots",
    "dispatch_due_cron_jobs",
]


@dataclass
class SchedulableTask:
    """三条执行通道对"一个待执行任务"的统一最小描述。

    字段刻意保持最小——各通道领域特定的字段（比如 Goal 的
    `effective_priority`/cron 的 `run_mode`）放进 `extra` 字典里，不污染
    统一接口本身；`UnifiedTaskScheduler.suggest_order()` 的排序逻辑只依赖
    这几个通用字段，方便未来新增第四条通道时无需改动排序算法。
    """

    source: str            # "goal" | "cron" | "goal_cycle"
    task_id: str
    title: str = ""
    priority: float = 0.0
    due_at: Optional[float] = None   # None 表示"随时可跑，无明确到期时间"（如 Goal）
    resource_estimate: float = 1.0   # 相对资源消耗估算，暂无精细模型时统一取 1.0
    extra: dict = field(default_factory=dict)


@runtime_checkable
class TaskChannel(Protocol):
    """三条执行通道要接入统一调度层，需要满足的最小协议。

    `poll_due()` 必须是只读、非阻塞、不做任何 LLM 调用的规则化计算——与
    改进计划 §设计边界 第 3 条"本计划所有新增判断都是规则化计算……零
    LLM 成本"一致。`execute()` 是为 P5 第 3-4 步预留的接口占位，本轮
    所有实现均为 `NotImplementedError`（见模块头部说明）。
    """

    def poll_due(self) -> list[SchedulableTask]:
        """返回当前"值得被调度层看到"的任务列表。语义上不等同于
        "现在必须立刻执行"——是否执行仍由各通道自己的现有逻辑决定，这里
        只是提供一份只读快照供聚合/排序展示。"""
        ...

    def execute(self, task: SchedulableTask) -> bool:
        """[P5 第 4 步] cron/goal_cycle 两个适配器已实现真正委托派发（见
        `CronChannelAdapter`/`GoalCycleChannelAdapter`），`ObjectiveChannelAdapter`
        仍 `raise NotImplementedError`——见该类文档字符串说明原因。返回值
        `True` 表示确实触发成功，`False` 表示"这次没触发成功"（不算错误，
        是正常的仲裁/去重结果），与三条通道各自既有的"triggered/success"
        语义一致。`poll_due()` 必须是只读、非阻塞、不做任何 LLM 调用的
        规则化计算——与改进计划 §设计边界 第 3 条"本计划所有新增判断都是
        规则化计算……零 LLM 成本"一致；`execute()` 本身允许产生真实副作用
        （这正是它存在的意义），但内部委托的仍是三条通道各自原有的、已经
        过测试的触发路径，不引入新的执行逻辑。"""
        ...


class ObjectiveChannelAdapter:
    """Goal → Objective 通道的只读适配器。

    `poll_due()` 复用 `GoalBacklog.active_objectives_fair_ranked()`
    已经算好的"公平轮询"排序结果，不重新发明一套排序算法——与改进计划
    P5 验收标准第 3 条"现有公平轮询/老化补偿逻辑作为 UnifiedTaskScheduler
    内部候选排序算法保留，不重新发明"一致。`priority` 字段用
    `compute_aging_boost()` 还原出与该方法内部排序口径一致的
    effective_priority（`node.priority + aging_boost`），供
    `UnifiedTaskScheduler.suggest_order()` 跨通道比较用。`due_at` 恒为
    `None`（Goal 没有明确到期时间，"随时可跑，谁该被优先轮到"完全由
    effective_priority 决定）。

    [goal_cron_task_optimization_holistic_plan.md §5 调度联动子项]
    `resource_estimate` 不再恒为 `1.0`——若能读到该 Goal 的
    `ExecutionPhaseState`（`paths` 已注入且状态文件存在/可读），按
    `execution_phase.last_known_effective_mode()` 还原出的阶段名，经
    `execution_phase.phase_resource_multiplier()` 换算成相对倍率；读不到
    （`paths` 未注入、Goal 没有 recurring 阶段历史、任何异常）时保守回落
    到 `1.0`，与引入本机制之前的行为完全一致。这一步仍然只是“只读预览”
    ——`poll_due()` 不接管任何实际执行决策，`resource_estimate` 目前只
    出现在 `/self/unified_scheduler_preview` 这类诊断端点里，尚未被真正的
    资源分配逻辑消费（该逻辑的落地仍是 §5 记录的待办，本次只补上
    “阶段感知的资源估算”这一块可观测性）。
    """

    def __init__(self, goal_backlog: Any, paths: Any = None, autonomous_loop: Any = None) -> None:
        self._goal_backlog = goal_backlog
        # paths 优先用显式传入的值；未传入时尝试从 goal_backlog 自身取
        # （与 goal_cron_bridge.py 里 getattr(goal_backlog, "_paths", None)
        # 同一取法），两者都拿不到时阶段感知的资源估算整体降级为 1.0。
        self._paths = paths if paths is not None else getattr(goal_backlog, "_paths", None)
        # [goal_cron_convergence_and_governance_improvement_plan.md
        # Track 1] execute() 需要的安全入口——不传时 execute() 退化为
        # 返回 False（与 cron_scheduler/goal_cycle 两个适配器"依赖未注入
        # 时返回 False 不抛异常"的既有约定一致）。
        self._autonomous_loop = autonomous_loop

    def _resource_estimate_for(self, goal_id: str):
        """返回 (resource_estimate, phase_mode)，任何环节失败都回落到
        (1.0, "")——阶段感知的资源估算是纯诊断增强，不应该因为读取阶段
        状态失败而影响 poll_due() 本身的可用性。"""
        if self._paths is None:
            return 1.0, ""
        try:
            from mini_agent.perception import execution_phase as ep
            state = ep.load_phase(self._paths, goal_id)
            mode = ep.last_known_effective_mode(state)
            return ep.phase_resource_multiplier(mode), mode
        except Exception:
            return 1.0, ""

    def poll_due(self) -> list[SchedulableTask]:
        if self._goal_backlog is None:
            return []
        try:
            from mini_agent.perception.goal_backlog import compute_aging_boost
            now = time.time()
            nodes = self._goal_backlog.active_objectives_fair_ranked(now=now)
        except Exception:
            return []
        tasks: list[SchedulableTask] = []
        for node in nodes:
            try:
                effective_priority = node.priority + compute_aging_boost(node, now)
            except Exception:
                effective_priority = getattr(node, "priority", 0.0)
            task_id = getattr(node, "id", "")
            # 阶段状态挂在 recurring Goal（根节点）上，不是每个派生
            # Objective 各自维护一份——`parent_id` 为空（该节点本身就是
            # Goal 根节点，理论上不该出现在 active_objectives_fair_ranked()
            # 结果里，但兜底处理）时退回用它自身 id 查询，仍然安全（读不到
            # 就回落 1.0）。
            phase_goal_id = getattr(node, "parent_id", None) or task_id
            resource_estimate, phase_mode = self._resource_estimate_for(phase_goal_id)
            tasks.append(SchedulableTask(
                source="goal",
                task_id=task_id,
                title=getattr(node, "title", ""),
                priority=float(effective_priority),
                due_at=None,
                resource_estimate=resource_estimate,
                extra={
                    "last_scheduled_at": getattr(node, "last_scheduled_at", 0.0),
                    "phase_mode": phase_mode,
                },
            ))
        return tasks

    def execute(self, task: SchedulableTask) -> bool:
        """[goal_cron_convergence_and_governance_improvement_plan.md
        Track 1] 委托给 `AutonomousLoop.trigger_objective_now(objective_id)`
        ——与 `_tick_maintenance()` 排序循环体触发候选时走的是同一份
        `_trigger_objective_candidate()` 记账逻辑（`mark_scheduled` +
        digest 记录），不会出现"这里触发了但排序状态没同步更新"的记账
        错位，延续了 P5 第 4 步中 `CronChannelAdapter.execute()` 的设计
        原则：不在适配器这一层重新拼一份简化版调度逻辑。

        `autonomous_loop` 未注入、或 `task.task_id` 找不到对应节点/
        当前不满足触发条件（正在运行、用户暂停、并发上限已满）时返回
        `False`，不抛异常。

        **调用方注意**：与 `CronChannelAdapter.execute()` 当前状态一致
        ——本方法目前仍未被 `UnifiedTaskScheduler` 自身任何方法调用，
        也未接入 `AutonomousLoop` 既有的 tick 路径，`_tick_maintenance()`
        依然是当前唯一的实际触发入口。是否/何时切换到统一入口作为默认
        路径，留给后续单独评审（`scheduler.unified_dispatch_enabled`
        默认值不在本 Track 范围内调整）。
        """
        if self._autonomous_loop is None:
            return False
        try:
            return bool(self._autonomous_loop.trigger_objective_now(task.task_id))
        except Exception:
            return False


class CronChannelAdapter:
    """普通 cron 通道（`run_mode != \"goal_cycle\"`）的只读适配器。

    `poll_due()` 只挑选"已到期且启用"的 job（`enabled and next_run_at <=
    now`），`priority` 直接取 `CronJob.priority`（若字段不存在则退化为
    0.0），`due_at` 取 `next_run_at`——与 goal 通道"永远随时可跑"形成对比，
    这正是改进计划背景里强调的"cron 恰恰是三条通道里对时间确定性要求最高
    的一个"。
    """

    def __init__(self, cron_scheduler: Any) -> None:
        self._cron_scheduler = cron_scheduler

    def poll_due(self) -> list[SchedulableTask]:
        return _poll_cron_jobs(self._cron_scheduler, want_goal_cycle=False)

    def execute(self, task: SchedulableTask) -> bool:
        """[P5 第 4 步] 委托给 `CronScheduler.trigger_job_now(job_id)`——
        与 `CronScheduler.tick()` 内部触发到期 job 走的是同一份记账逻辑
        （`_trigger_and_record()`），不会出现"这里触发了但 next_run_at/
        consecutive_skip_count 没更新，导致下次 tick() 重复触发"的记账
        错位（这正是 P5 第 1-2 步决策 18 里指出的、提前实现 execute() 的
        风险——本轮通过让 `CronScheduler` 内部共用同一份记账函数解决了
        这个问题，而不是在适配器这一层重新拼一份记账逻辑）。

        `cron_scheduler` 未注入、或 `task.task_id` 找不到对应 job 时返回
        `False`（与 `CronScheduler.trigger_job_now()` 本身的失败语义
        一致），不抛异常。

        **调用方注意**：本方法目前仍未被 `UnifiedTaskScheduler` 自身的
        任何方法调用（`poll_all()`/`suggest_order()` 仍是纯读取），也
        未接入 `AutonomousLoop` 的既有 tick 路径——`CronScheduler.tick()`
        依然是当前唯一的实际触发入口。谁在什么条件下调用本方法（是否/
        何时切换到统一入口）留给 P5 第 5 步决定，本轮只保证"调用它是
        安全的、幂等记账正确的"这一前提已经成立。
        """
        if self._cron_scheduler is None:
            return False
        try:
            return bool(self._cron_scheduler.trigger_job_now(task.task_id))
        except Exception:
            return False


class GoalCycleChannelAdapter:
    """goal_cycle 通道（`run_mode == \"goal_cycle\"`）的只读适配器。

    与普通 cron 通道共用同一个 `CronScheduler.list_jobs()` 数据源（避免
    重复 IO，与 P4 后端"共用同一次 list_jobs() 调用"是同一节流思路），只是
    按 `run_mode` 过滤出另一半。
    """

    def __init__(self, cron_scheduler: Any) -> None:
        self._cron_scheduler = cron_scheduler

    def poll_due(self) -> list[SchedulableTask]:
        return _poll_cron_jobs(self._cron_scheduler, want_goal_cycle=True)

    def execute(self, task: SchedulableTask) -> bool:
        """[P5 第 4 步] 与 `CronChannelAdapter.execute()` 完全同构——
        goal_cycle job 一样是 `CronScheduler` 管理的 `CronJob`，只是
        `run_mode == "goal_cycle"`，`trigger_job_now()` 内部经
        `_fire()` 已经按 `run_mode` 正确分流到 `_goal_cycle_fn`（转发进
        `ObjectiveExecutor`），本方法不需要（也不应该）自己重新判断
        run_mode，直接委托同一个入口即可。"""
        if self._cron_scheduler is None:
            return False
        try:
            return bool(self._cron_scheduler.trigger_job_now(task.task_id))
        except Exception:
            return False


def _poll_cron_jobs(cron_scheduler: Any, *, want_goal_cycle: bool) -> list[SchedulableTask]:
    """两个 cron 侧适配器共用的抽取逻辑，只在 `want_goal_cycle` 上分叉。"""
    if cron_scheduler is None:
        return []
    try:
        jobs = cron_scheduler.list_jobs()
    except Exception:
        return []
    now = time.time()
    source = "goal_cycle" if want_goal_cycle else "cron"
    tasks: list[SchedulableTask] = []
    for j in jobs:
        run_mode = getattr(j, "run_mode", "message")
        is_goal_cycle = run_mode == "goal_cycle"
        if is_goal_cycle != want_goal_cycle:
            continue
        if not getattr(j, "enabled", True):
            continue
        next_run_at = getattr(j, "next_run_at", None)
        # [P5 第 5 步] `next_run_at <= 0` 是"尚未初始化"的哨兵值（见
        # `CronScheduler.tick()` 同一判断），不是"早已到期"——`0 > now`
        # 恒为假，之前这里会把未初始化的 job 误判成到期任务。P5 第 1-2
        # 步该字段只用于只读预览，这个误差不影响任何实际执行；但 P5 第
        # 5 步 `dispatch_due_cron_jobs()` 会真正调用 `execute()` 触发，
        # 必须与 `tick()` 的到期判断口径完全一致，因此在这里补上同一个
        # 排除条件。
        if next_run_at is None or next_run_at <= 0 or next_run_at > now:
            continue
        tasks.append(SchedulableTask(
            source=source,
            task_id=getattr(j, "id", ""),
            title=getattr(j, "name", ""),
            priority=float(getattr(j, "priority", 0.0) or 0.0),
            due_at=next_run_at,
            resource_estimate=1.0,
            extra={
                "consecutive_skip_count": getattr(j, "consecutive_skip_count", 0),
                "run_mode": run_mode,
            },
        ))
    return tasks


class UnifiedTaskScheduler:
    """[P5 第 1-2 步] 三条通道的只读聚合 + 排序建议层。

    本轮 **不接管任何执行决策**——`poll_all()`/`suggest_order()` 都是纯
    读取，调用多少次、什么时候调用都不会改变任何通道的实际运行结果。
    定位等价于改进计划原文对第 2 步的描述："等价于 P4 的数据源升级版，
    风险为零，可以先上线观察排序结果是否符合预期"。
    """

    def __init__(self) -> None:
        self._channels: dict[str, TaskChannel] = {}

    def register_channel(self, name: str, channel: TaskChannel) -> None:
        self._channels[name] = channel

    def poll_all(self) -> dict[str, list[SchedulableTask]]:
        """按通道名分组返回每条通道当前的任务快照，任一通道 `poll_due()`
        抛异常时该通道降级为空列表，不影响其它通道（与项目一贯的"非核心
        信息降级不影响主链路"风格一致）。"""
        result: dict[str, list[SchedulableTask]] = {}
        for name, channel in self._channels.items():
            try:
                result[name] = channel.poll_due()
            except Exception:
                result[name] = []
        return result

    def suggest_order(
        self,
        *,
        channel_weights: Optional[dict[str, float]] = None,
    ) -> list[SchedulableTask]:
        """把所有通道当前到期/可跑的任务合并成一份"建议执行顺序"。

        排序键（降序）为 `weight * priority`，`due_at` 更早的任务在同权重/
        同优先级时排在前面（cron 到期时间比 Goal 的抽象 priority 更具体，
        平手时用它做 tie-break 更符合直觉）。`channel_weights` 默认全部
        为 1.0（不偏向任何通道）——方案原文 P5 第 3 步才会引入真正生效的
        `channel_weights` 配置来分配执行槽位，这里提前暴露同名参数只是为
        了让"排序建议"能提前观察不同权重假设下的效果，本身不产生任何
        实际调度后果。
        """
        weights = channel_weights or {}
        all_tasks: list[SchedulableTask] = []
        for tasks in self.poll_all().values():
            all_tasks.extend(tasks)

        def _sort_key(t: SchedulableTask):
            w = weights.get(t.source, 1.0)
            due = t.due_at if t.due_at is not None else float("inf")
            return (-(w * t.priority), due)

        return sorted(all_tasks, key=_sort_key)


def allocate_weighted_slots(
    total_slots: int,
    weights: dict[str, float],
    *,
    reserved_min: Optional[dict[str, int]] = None,
) -> dict[str, int]:
    """[P5 第 3 步 · 接管仲裁裁决] 按权重把 `total_slots` 个执行槽位分配给\n    各通道，供 `degraded` 状态下的并发上限裁决使用。

    纯函数、零 IO、零 LLM 成本——与改进计划 §设计边界第 3 条一致。设计\n    要点：

    1. **保底优先**：`reserved_min` 里声明的通道，无论权重多低，先分到\n       `min(reserved_min[name], total_slots)`（多个通道保底总和超过\n       `total_slots` 时，按声明顺序依次满足，直到槽位耗尽——这是一个\n       明确的降级行为，不是常态，真正配置合理时不应触发）。这正是\n       改进计划待讨论问题 2 提到的\"cron 保底并发数\"这一更直观的配置\n       思路的落地。\n    2. **剩余槽位按权重比例分配**，用最大余数法（largest remainder）\n       取整，保证 `sum(allocation.values()) == total_slots`（不会因为\n       四舍五入丢失或多出槽位）。\n    3. 权重缺失的通道视为权重 0（不参与剩余槽位的比例分配，但仍享有\n       `reserved_min` 保底）。\n    4. `total_slots <= 0` 或 `weights` 为空时，所有已知通道（`weights`\n       与 `reserved_min` 的并集）分配 0。

    这是本轮新增的**纯计算**能力，尚未接管任何通道的实际执行——见\n    `objective_executor.py`/`cron_job_runner.py` 里 `scheduler.\n    unified_arbitration_enabled` 开关的说明：默认关闭，配置未升级的\n    用户行为完全不变（改进计划 §设计边界第 4 条）。\n    """
    reserved_min = reserved_min or {}
    names = list(dict.fromkeys(list(weights.keys()) + list(reserved_min.keys())))
    allocation: dict[str, int] = {name: 0 for name in names}

    if total_slots <= 0 or not names:
        return allocation

    remaining = total_slots
    for name in names:
        want = max(0, int(reserved_min.get(name, 0)))
        give = min(want, remaining)
        allocation[name] = give
        remaining -= give

    if remaining <= 0:
        return allocation

    positive_weights = {n: max(0.0, float(weights.get(n, 0.0))) for n in names}
    total_weight = sum(positive_weights.values())

    if total_weight <= 0:
        # 没有正权重可参考——剩余槽位平均分给所有通道（largest remainder）。
        positive_weights = {n: 1.0 for n in names}
        total_weight = float(len(names))

    raw_shares = {n: remaining * (positive_weights[n] / total_weight) for n in names}
    floor_shares = {n: int(raw_shares[n]) for n in names}
    distributed = sum(floor_shares.values())
    leftover = remaining - distributed

    # 按小数部分从大到小依次 +1，直到分完 leftover（最大余数法）。
    remainders = sorted(names, key=lambda n: raw_shares[n] - floor_shares[n], reverse=True)
    for i in range(leftover):
        floor_shares[remainders[i % len(remainders)]] += 1

    for name in names:
        allocation[name] += floor_shares[name]

    return allocation


def dispatch_due_cron_jobs(cron_scheduler: Any) -> list[str]:
    """[P5 第 5 步 · 灰度接入统一入口] 通过 `CronChannelAdapter`/
    `GoalCycleChannelAdapter` 真正派发本轮到期的普通 cron + goal_cycle
    job，返回触发成功的 job_id 列表——返回值语义与 `CronScheduler.tick()`
    完全一致，供 `AutonomousLoop._tick_passive()` 在
    `scheduler.unified_dispatch_enabled=True` 时直接替换 `tick()` 调用。

    **与 `tick()` 的关系**：本函数不重新实现"到期判断 + 触发 + 记账"，
    到期判断复用 `poll_due()`（P5 第 1-2 步已有、本轮修复了 `next_run_at
    <= 0` 的边界条件，见 `_poll_cron_jobs()`），触发 + 记账复用
    `execute()` → `CronScheduler.trigger_job_now()` → `_trigger_and_
    record()`（P5 第 4 步已有、与 `tick()` 内部共用同一份实现）。本函数
    只新增了一件 `tick()` 内部也在做、但 `poll_due()`/`execute()` 各自
    分离后需要重新组装的事——**把两条 cron 侧通道的到期任务合并后按
    `priority` 降序统一触发一次**，排序口径与 `CronScheduler.tick()`
    内部 `due_jobs.sort(key=lambda j: j.priority, reverse=True)` 保持
    一致（同优先级时维持合并后的相对顺序，与 `tick()` 用稳定排序的
    效果等价）。

    **已知的行为差异（不影响正确性，写清楚避免误解）**：`tick()` 一次
    调用只在触发列表非空或有状态变化时 `save()` 一次；本函数通过
    `trigger_job_now()` 派发，每个 job 各自触发一次 `save()`——多次
    落盘换来"两条路径共用同一份记账函数、不会出现记账口径漂移"这个
    更重要的正确性保证（P5 第 4 步决策 28 的延伸），本函数不通过自己
    重新拼一份"只 save 一次"的批量逻辑来换取这点 IO 优化，避免引入
    第三份记账代码。

    `cron_scheduler` 为 `None` 时返回空列表，不抛异常（与三条通道一贯的
    降级风格一致）。
    """
    if cron_scheduler is None:
        return []
    cron_adapter = CronChannelAdapter(cron_scheduler)
    goal_cycle_adapter = GoalCycleChannelAdapter(cron_scheduler)

    try:
        due_tasks = cron_adapter.poll_due() + goal_cycle_adapter.poll_due()
    except Exception:
        return []
    due_tasks.sort(key=lambda t: t.priority, reverse=True)

    triggered: list[str] = []
    for task in due_tasks:
        adapter = cron_adapter if task.source == "cron" else goal_cycle_adapter
        try:
            ok = adapter.execute(task)
        except Exception:
            ok = False
        if ok:
            triggered.append(task.task_id)
    return triggered


def build_default_scheduler(
    *,
    goal_backlog: Any = None,
    cron_scheduler: Any = None,
    paths: Any = None,
    autonomous_loop: Any = None,
) -> UnifiedTaskScheduler:
    """便捷构造函数：按现有三条通道的既有对象构造一个已注册好全部
    Channel 的 `UnifiedTaskScheduler`。任一依赖为 `None` 时对应 Channel
    仍会注册，只是 `poll_due()` 会返回空列表（不影响其它通道），与
    P4 后端端点"任一子系统数据缺失时对应字段返回空/占位"是同一风格。

    `paths` 转发给 `ObjectiveChannelAdapter`，用于 §5 调度联动子项的
    阶段感知资源估算；不传时 `ObjectiveChannelAdapter` 会退而尝试
    `goal_backlog._paths`，仍拿不到则该功能整体降级为 `1.0`（见该类
    文档字符串），不影响调用方既有用法。

    `autonomous_loop` 转发给 `ObjectiveChannelAdapter`，用于 Track 1
    的 `execute()` 真正委托派发；不传时 `execute()` 恒返回 `False`
    （与其它依赖缺失时的降级风格一致），不影响调用方既有用法。
    """
    scheduler = UnifiedTaskScheduler()
    scheduler.register_channel(
        "goal", ObjectiveChannelAdapter(goal_backlog, paths=paths, autonomous_loop=autonomous_loop),
    )
    scheduler.register_channel("cron", CronChannelAdapter(cron_scheduler))
    scheduler.register_channel("goal_cycle", GoalCycleChannelAdapter(cron_scheduler))
    return scheduler
