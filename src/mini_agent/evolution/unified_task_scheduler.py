# -*- coding: utf-8 -*-
"""
[goal_cron_unified_scheduler_improvement_plan.md P5 · 长期目标 · 第 1-2 步]

本模块是"收敛到统一调度层"这一长期目标的第一块地基，按方案原文的分步迁移
路径实现前两步：

  1. 定义统一接口：`SchedulableTask`（最小字段：source/task_id/priority/
     due_at/resource_estimate）+ `TaskChannel` 协议
     （`poll_due() -> list[SchedulableTask]`、
     `execute(task) -> concurrent, non-blocking`）。
  2. 先适配只读部分：让 Goal（Objective）/普通 cron/goal_cycle 三条通道各自
     实现 `TaskChannel.poll_due()`，`UnifiedTaskScheduler` 先只做"聚合展示 +
     统一排序建议"，不接管真正的执行决策。

**明确的范围边界（与方案原文一致）**：
- 本模块 **不修改** `ObjectiveExecutor`/`CronScheduler`/`CronJobRunner`/
  `GoalBacklog` 任何一行既有代码，只是在它们之上包一层只读适配器——三条
  通道各自的"内部"调度逻辑（公平轮询/老化补偿、去重+watchdog 回收）完全
  不变，本轮只新增一份"从外部只读观察这些通道当前有哪些任务到期"的统一
  视图。
- `TaskChannel.execute()` 在本轮 **不会被 `UnifiedTaskScheduler` 调用**
  （`UnifiedTaskScheduler` 目前只有 `poll_all()`/`suggest_order()` 两个
  只读方法），三条通道各自现有的触发路径
  （`AutonomousLoop._tick_maintenance()` 直接调 `ObjectiveExecutor`/
  `CronScheduler.tick()`）继续独立运作，不受本模块影响、也不会被本模块
  重复触发。各适配器的 `execute()` 目前直接 `raise NotImplementedError`
  并在文档字符串里说明原因，等到 P5 第 3 步"接管仲裁裁决"时才需要真正
  实现，提前实现一个"看起来能跑但从未被真正调用过"的执行路径反而增加
  测试盲区，不如显式声明"尚未实现"更安全。
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

    def execute(self, task: SchedulableTask) -> None:
        """[P5 第 3-4 步预留] 由统一调度层直接派发执行。本轮所有实现均
        raise NotImplementedError——见模块头部'范围边界'说明。"""
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
            "ObjectiveChannelAdapter.execute() 在 P5 第 1-2 步尚未实现——"
            "Goal 通道的实际派发仍由 ObjectiveExecutor/AutonomousLoop 自行"
            "负责，见 unified_task_scheduler.py 模块头部说明。"
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

    def execute(self, task: SchedulableTask) -> None:
        raise NotImplementedError(
            "CronChannelAdapter.execute() 在 P5 第 1-2 步尚未实现——普通 "
            "cron 通道的实际派发仍由 CronScheduler.tick()/CronJobRunner "
            "自行负责，见 unified_task_scheduler.py 模块头部说明。"
        )


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

    def execute(self, task: SchedulableTask) -> None:
        raise NotImplementedError(
            "GoalCycleChannelAdapter.execute() 在 P5 第 1-2 步尚未实现——"
            "goal_cycle 通道的实际派发仍借道 CronScheduler._fire_goal_cycle "
            "→ ObjectiveExecutor 自行负责，见 unified_task_scheduler.py "
            "模块头部说明。"
        )


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
