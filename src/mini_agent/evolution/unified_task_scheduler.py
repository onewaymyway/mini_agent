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
  4. 接管实际派发（部分）：`CronChannelAdapter`/`GoalCycleChannelAdapter`
     的 `execute()` 已实现真正委托派发（详见下方范围边界）；
     `ObjectiveChannelAdapter.execute()` 仍未实现，见该类文档字符串。

**明确的范围边界**：
- 本模块 **不修改** `ObjectiveExecutor`/`GoalBacklog` 任何一行既有代码，
  只是在它们之上包一层只读适配器；`CronScheduler` 在 P5 第 4 步做了一次
  行为保留的内部重构（把 `tick()` 循环体里"触发单个 job + 记账"的逻辑
  抽成 `_trigger_and_record()`/`trigger_job_now()`，供本模块的
  `execute()` 复用），`tick()` 自身的到期判断/排序/触发顺序完全不变。
- `CronChannelAdapter`/`GoalCycleChannelAdapter.execute()` 在 P5 第 4 步
  已经是真正的委托派发（内部调用 `CronScheduler.trigger_job_now()`），
  但**目前仍未被 `UnifiedTaskScheduler` 自身或 `AutonomousLoop` 的任何
  既有 tick 路径调用**——`CronScheduler.tick()` 依然是当前唯一的实际
  触发入口，这两个 `execute()` 只是"已经可以安全调用，但还没有人在
  调用"。是否/何时切换到统一入口是 P5 第 5 步的范围。
- `ObjectiveChannelAdapter.execute()` 仍 `raise NotImplementedError`——
  Goal 通道的实际派发逻辑（公平排序/per-Goal 并发上限/pause 状态检查等）
  深度耦合 `AutonomousLoop` 自身持有的运行时状态，还没有一个类似
  `CronScheduler.trigger_job_now()` 那样的安全公开入口，见该类文档
  字符串的详细说明。
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
    """

    def __init__(self, goal_backlog: Any) -> None:
        self._goal_backlog = goal_backlog

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
            tasks.append(SchedulableTask(
                source="goal",
                task_id=getattr(node, "id", ""),
                title=getattr(node, "title", ""),
                priority=float(effective_priority),
                due_at=None,
                resource_estimate=1.0,
                extra={"last_scheduled_at": getattr(node, "last_scheduled_at", 0.0)},
            ))
        return tasks

    def execute(self, task: SchedulableTask) -> None:
        raise NotImplementedError(
            "ObjectiveChannelAdapter.execute() 仍未实现——Goal 通道的实际"
            "派发（AutonomousLoop._tick_maintenance() 里公平排序/per-Goal"
            "并发上限/paused 状态检查/resume_fairness 等一整套逻辑，见"
            "autonomous_loop.py 相关代码段）比 cron/goal_cycle 通道复杂"
            "得多，且深度耦合 AutonomousLoop 自身持有的运行时状态"
            "（fairness_paused_objective_ids 等），在没有一个安全的公开"
            "入口（类似 CronScheduler.trigger_job_now() 那样，把'触发 +"
            "记账'封装成一次调用）之前，贸然实现 execute() 要么重新拼一份"
            "简化版调度逻辑（引入与 AutonomousLoop 不一致的风险），要么"
            "需要先重构 AutonomousLoop 抽出对应的公开方法——两者都超出"
            "本轮范围，留给后续评估。见 unified_task_scheduler.py 模块"
            "头部说明。"
        )


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
        if next_run_at is None or next_run_at > now:
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


def build_default_scheduler(
    *,
    goal_backlog: Any = None,
    cron_scheduler: Any = None,
) -> UnifiedTaskScheduler:
    """便捷构造函数：按现有三条通道的既有对象构造一个已注册好全部
    Channel 的 `UnifiedTaskScheduler`。任一依赖为 `None` 时对应 Channel
    仍会注册，只是 `poll_due()` 会返回空列表（不影响其它通道），与
    P4 后端端点"任一子系统数据缺失时对应字段返回空/占位"是同一风格。
    """
    scheduler = UnifiedTaskScheduler()
    scheduler.register_channel("goal", ObjectiveChannelAdapter(goal_backlog))
    scheduler.register_channel("cron", CronChannelAdapter(cron_scheduler))
    scheduler.register_channel("goal_cycle", GoalCycleChannelAdapter(cron_scheduler))
    return scheduler
