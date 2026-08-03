"""
evolution/goal_cron_bridge.py — Goal ⇄ Cron 绑定桥接层
（next_doc/goal_cron_binding_plan.md Track B/C）

背景：`GoalBacklog`（perception/goal_backlog.py）和 `CronScheduler`
（evolution/cron_scheduler.py）此前是完全独立的两套体系——GoalNode 没有周期性字段，
CronJob 触发只会把 task_template 当一条裸消息塞进 InputQueue，互不感知。本模块
不改动两者各自的核心职责（GoalBacklog 管状态机，CronScheduler 管定时），只加一层
绑定/触发/回收逻辑，让一个 Goal 可以声明"我需要被周期性推进"。

对外入口三个：
  register_goal_cycle_handler(cron_scheduler, goal_backlog, objective_executor)
      — daemon 启动时调用一次，把触发逻辑接进 CronScheduler。
  make_goal_recurring(goal_backlog, cron_scheduler, goal_id, schedule, task_template)
      — 把一个已存在的 Goal 声明为周期性。
  stop_goal_recurrence(goal_backlog, cron_scheduler, goal_id)
      — 反向操作，停止自动续期（不删 Goal/cron job）。

以及一个供 AutonomousLoop 被动 tick 调用的收尾函数：
  reap_finished_cycles(goal_backlog) — 扫描所有 recurring Goal，把本轮已进入终态
      的子 Objective 计入 cycle_count/progress_notes。
"""

from __future__ import annotations

import time
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode
    from mini_agent.evolution.cron_scheduler import CronScheduler, CronJob
    from mini_agent.evolution.objective_executor import ObjectiveExecutor


_TERMINAL_STATUSES = ("completed", "failed", "cancelled")


# ── 触发逻辑（Track B） ────────────────────────────────────────────────────────

def register_goal_cycle_handler(
    cron_scheduler: "CronScheduler",
    goal_backlog: "GoalBacklog",
    objective_executor: "ObjectiveExecutor",
) -> None:
    """把 goal_cycle 触发逻辑挂到 cron_scheduler。daemon 启动时（构建完
    GoalBacklog/CronScheduler/ObjectiveExecutor 三者之后）调用一次，见
    api/server.py::HttpServer._build_autonomous_loop()。
    """

    def _handler(job: "CronJob") -> bool:
        return _fire_goal_cycle(job, goal_backlog, objective_executor)

    cron_scheduler.set_goal_cycle_handler(_handler)


def _goal_has_active_cycle(goal: "GoalNode", goal_backlog: "GoalBacklog",
                            objective_executor: "ObjectiveExecutor") -> bool:
    """幂等检查：Goal 下是否已有一轮子 Objective 仍在跑。

    只看"仍是 active 状态、且 ObjectiveExecutor 认为它正在跑"的子节点——
    Objective 完成/失败/取消后，`ObjectiveExecutor._sync_goal_status()` 会把
    子节点自己的 status 改成终态，此时不再算"活跃"，允许开下一轮。
    """
    for child_id in goal.children_ids:
        child = goal_backlog.get(child_id)
        if child is None or not child.is_objective:
            continue
        if child.status == "active" and objective_executor.is_running(child.id):
            return True
    return False


def _autonomy_level(paths) -> str:
    """读取当前 autonomy_level，逻辑与 AutonomousLoop._get_autonomy_level() 保持
    一致（读取失败时保守降级为 passive）。之所以在这里重复一份小函数而不是
    直接依赖 AutonomousLoop 的方法，是为了不引入 goal_cron_bridge → autonomous_loop
    的反向依赖（后者本来就要 import 本模块）。
    """
    try:
        from mini_agent.perception.global_knowledge import load_self_profile
        profile = load_self_profile(paths)
        if profile:
            return profile.operating_state.autonomy_level
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.goal_cron_bridge._autonomy_level')
    return "passive"


def _fire_goal_cycle(
    job: "CronJob",
    goal_backlog: "GoalBacklog",
    objective_executor: "ObjectiveExecutor",
) -> bool:
    """CronScheduler 到期触发一个 run_mode="goal_cycle" 的 job 时调用。

    返回 False 时 CronScheduler.tick() 不会推进 last_run_at/next_run_at，
    等于"这次没算数"，下次 tick 会再次尝试——用来实现"Goal 非 active 时挂起
    等待"和"上一轮还没跑完时跳过"两种场景，都不需要用户手动介入。

    档位边界：`AutonomousLoop._tick_passive()` 的既有约定是"方法体内不引用
    GoalBacklog 任何方法"，但 CronScheduler.tick() 恰好是在 passive 档位下
    被调用的（cron job 本身不分档位）。goal_cycle job 的触发逻辑本质上就是
    读写 GoalBacklog，为了不悄悄破坏这条边界，这里显式检查：autonomy_level
    为 "passive" 时直接跳过（不触碰 goal_backlog），等用户把档位调到
    maintenance/autonomous 才会真正生效。这也符合直觉——"周期性 Goal 自动
    续期"本来就是一种自主行为，不该在最保守的 passive 档位下发生。
    """
    if not job.goal_id:
        return False

    paths = getattr(goal_backlog, "_paths", None)
    if paths is not None and _autonomy_level(paths) == "passive":
        return False

    goal_backlog.load()
    goal = goal_backlog.get(job.goal_id)
    if goal is None or not goal.is_goal:
        # Goal 已经不存在了（比如被彻底删除，虽然当前 GoalBacklog 没有硬删除
        # 接口，但预留这个分支防御未来变化）——job 本身不自动删除，只是
        # 每次触发都跳过，用户可以用 /cron remove 清理。
        return False

    if goal.status != "active":
        # Goal 被暂停/放弃/取消：不触发，也不报错。这正是 P3 要解决的问题——
        # 用户只需要管 Goal 的状态，不需要额外记得去 disable 对应 cron job。
        return False

    if goal.skip_next_cycle:
        # [goal_cron_visibility_and_intervention_improvement_plan.md Track B]
        # 用户主动请求跳过这一轮，但保持 recurring=True——跟"Goal 未 active"
        # "上一轮未完成"两种系统级跳过不同，这是用户的主动决策，需要留痕在
        # progress_notes 里，方便回看"这一轮为什么没跑"。跳过后清零标记，
        # 只影响下一次触发这一次，不会一直跳过。
        goal_backlog.update_fields(goal.id, skip_next_cycle=False)
        goal_backlog.append_progress_note(goal.id, "本轮由用户手动跳过（跳过后周期性照常继续）")
        return False

    if _goal_has_active_cycle(goal, goal_backlog, objective_executor):
        # 上一轮还没跑完，本轮跳过，不叠加并发。
        return False

    cycle_no = goal.cycle_count + 1
    objective = goal_backlog.add_objective(
        title=f"{goal.title}（第 {cycle_no} 轮）",
        parent_id=goal.id,
        source="cron",
        description=job.task_template or goal.description,
    )

    exec_id = objective_executor.start(objective)
    if exec_id is None:
        # 启动失败（拆解失败/第一步提交失败等）：把这个子节点标终态，避免
        # 留下一个 status="active" 但没有对应 execution 的幽灵节点，卡住
        # 下一次 tick 的幂等检查（_goal_has_active_cycle 会一直认为"活跃"，
        # 因为 objective_executor.is_running() 对它返回 False，反而不会拦住——
        # 但节点本身语义上应该反映"这轮没跑起来"，所以仍需显式标记）。
        goal_backlog.set_status(objective.id, "failed")
        goal_backlog.update_fields(objective.id, progress_notes="本轮启动失败：objective_executor.start() 返回 None")
        return False

    return True


# ── 绑定/解绑（用户操作入口） ───────────────────────────────────────────────────

def make_goal_recurring(
    goal_backlog: "GoalBacklog",
    cron_scheduler: "CronScheduler",
    goal_id: str,
    schedule: str,
    task_template: Optional[str] = None,
) -> "CronJob":
    """把一个已存在的 Goal 声明为周期性。

    - Goal 不存在或不是 level="goal" 的节点 → 抛 ValueError（调用方通常是
      CLI/REST，交由上层转成用户可读的错误提示）。
      注：为什么这里选择抛异常而不是返回 None——`add_job()` 本身没有"失败"
      语义（总是成功建 job），如果本函数也吞掉错误静默返回 None，用户会看到
      "命令执行了但什么都没发生"，比报错更难排查。
    - 已经绑定过 → 复用旧 job（更新 schedule/task_template），不会重复创建
      多个 job 绑在同一个 Goal 上（对应 plan 里"一对一绑定"的约束）。
    """
    goal_backlog.load()
    goal = goal_backlog.get(goal_id)
    if goal is None or not goal.is_goal:
        raise ValueError(f"Goal 不存在或不是有效的 goal 节点：{goal_id}")

    template = task_template or goal.description or goal.title

    if goal.recurring and goal.recurrence_cron_job_id:
        existing = cron_scheduler.get(goal.recurrence_cron_job_id)
        if existing is not None:
            existing.schedule = schedule
            existing.task_template = template
            existing.enabled = True
            from mini_agent.evolution.cron_scheduler import compute_next_run
            existing.next_run_at = compute_next_run(schedule, 0.0)
            cron_scheduler.save()
            return existing

    job = cron_scheduler.add_job(
        name=f"goal-cycle:{goal.title[:24]}",
        schedule=schedule,
        task_template=template,
        description=f"周期性推进 Goal {goal.id}：{goal.title}",
        tags=["goal_cycle"],
        enabled=True,
        goal_id=goal.id,
        run_mode="goal_cycle",
    )
    goal_backlog.set_recurrence(goal.id, recurring=True, cron_job_id=job.id)
    return job


def stop_goal_recurrence(
    goal_backlog: "GoalBacklog",
    cron_scheduler: "CronScheduler",
    goal_id: str,
) -> bool:
    """停止周期性推进：disable 绑定的 cron job，goal.recurring 置回 False。
    不删除 Goal，也不删除 cron job（用户随时可以再次 make_goal_recurring 复用
    同一个 job，或手动 /cron remove 彻底清掉）。
    """
    goal_backlog.load()
    goal = goal_backlog.get(goal_id)
    if goal is None or not goal.is_goal:
        return False

    job_id = goal.recurrence_cron_job_id
    if job_id:
        cron_scheduler.disable(job_id)

    return goal_backlog.set_recurrence(goal.id, recurring=False, cron_job_id=None)


# ── 完成计数回收（Track C） ────────────────────────────────────────────────────

def reap_finished_cycles(goal_backlog: "GoalBacklog") -> int:
    """扫描所有 recurring=True 的 Goal，把本轮新出现的终态子 Objective 计入
    cycle_count/progress_notes。由 AutonomousLoop 被动 tick 周期性调用
    （只读遍历 + 命中才写，开销可控，不需要单独起线程/订阅机制）。

    返回本次新计数的子节点数量（用于日志/测试断言，正常 tick 大多数时候是 0）。
    """
    goal_backlog.load()
    reaped = 0
    for goal in goal_backlog.all_nodes():
        if not goal.is_goal or not goal.recurring:
            continue
        for child_id in goal.children_ids:
            child = goal_backlog.get(child_id)
            if child is None or not child.is_objective:
                continue
            if child.status not in _TERMINAL_STATUSES:
                continue
            if child.id in goal.reaped_cycle_child_ids:
                continue
            note = child.progress_notes or f"状态：{child.status}"
            if goal_backlog.record_cycle_completed(goal.id, child.id, note=note):
                reaped += 1
                if child.status == "failed":
                    # [goal_cron_visibility_and_intervention_improvement_plan.md
                    # Track C] 只在失败时推通知——completed/cancelled 不打扰
                    # 用户，正常跑完的周期性任务不需要每轮都推送。发送失败
                    # （比如渠道配置有问题）不影响本次计数结果，只是记录到
                    # dispatcher 自己的日志里。
                    _notify_cycle_failed(goal_backlog, goal, note)
        # [Track D] 归档已跑过多轮、已经计过数的旧子节点，避免 goals.json
        # 随轮数无限增长。只读遍历+命中才写，跟本函数其余部分同一种开销
        # 可控的设计哲学。
        try:
            goal_backlog.archive_finished_cycle_children(goal.id)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.goal_cron_bridge.reap_finished_cycles.archive')
    return reaped


def _notify_cycle_failed(goal_backlog: "GoalBacklog", goal: "GoalNode", note: str) -> None:
    """周期性 Goal 某一轮以 failed 收尾时推一条通知。复用已有的通知网关
    （notification/dispatcher.py，见 watchlist_notification_goal_design.md），
    不新增渠道实现；kanban 渠道恒真兜底，用户至少能在看板"全局待办中心"/
    通知记录里看到。异常整体吞掉——通知是感知增强，不能反过来影响
    reap_finished_cycles() 的计数主流程。
    """
    try:
        paths = getattr(goal_backlog, "_paths", None)
        if paths is None:
            return
        from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
        NotificationDispatcher(paths).dispatch(NotificationMessage(
            title=f"周期性目标「{goal.title}」第 {goal.cycle_count} 轮执行失败",
            body=(note or "")[:200],
            source="goal_cycle",
            meta={"goal_id": goal.id, "cycle": goal.cycle_count},
        ))
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.goal_cron_bridge._notify_cycle_failed')


__all__ = [
    "register_goal_cycle_handler",
    "make_goal_recurring",
    "stop_goal_recurrence",
    "reap_finished_cycles",
]
