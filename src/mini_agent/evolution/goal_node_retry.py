"""
evolution/goal_node_retry.py — 目标树节点失败自动重试

用户明确需求（对话内确认，未落盘为独立 next_doc 方案文档——改动范围
集中在"检测失败 + 拉回重试 + 阈值后汇报"这一处胶水逻辑，不涉及新的
存储模型或呈现层重构，直接在本文件说明设计取舍即可）：

  - 范围：只考虑目标树下的节点（Goal/Objective 分解出来的 Objective），
    不区分是否绑定周期性 CronJob——不是只有 `goal_cron_bridge.py` 里
    "周期性 Goal 的某一轮"这种失败才重试，任何目标树下失败的 Objective
    都应该被自动重试。
  - 重试次数：不设上限，可以一直自动尝试。
  - 阈值行为：连续失败次数超过阈值后，**不是叫停**，而是额外推一条
    通知，把决定权交给用户自己判断要不要暂停/调整/放弃——重试仍然继续。

设计要点：
  - 只处理 `level=="objective"` 的节点。Goal 本身不会被置为
    `status=="failed"`——`objective_executor.py::_sync_goal_status()`
    只对 `objective_id` 调用 `goal_backlog.set_status()`，Goal 节点的
    终态只有由 `maybe_close_goal_by_overall_criteria()` 判定的
    completed/kept_open 语义，没有"Goal 本身失败"这个状态。
  - 只处理 `status=="failed"`，不处理 `"cancelled"`——cancelled 是用户
    主动取消，是有意为之的终止；混进自动重试范围会违背用户的取消意图。
    两者在 `_TERMINAL_STATUSES`（`goal_cron_bridge.py`）里是并列的两种
    终态，但对"要不要自动重试"这件事语义完全相反。
  - 重试的实现是把 `status` 拉回 `"active"`：`GoalBacklog.
    active_objectives()`/`active_objectives_fair_ranked()` 只认
    `status=="active"`，拉回去之后下一次 `AutonomousLoop._tick_
    maintenance()` 的候选调度（`_trigger_objective_candidate()`）会把
    它当成一个普通候选重新排队执行——不需要重新实现一遍触发逻辑，也
    不需要新建一个新节点：`ObjectiveExecutor.start()` 本身就是"用当前
    title/description 重新拆解 + 提交第一步"，对"之前失败过"没有任何
    特殊记忆，天然适合原地重试，不会重复消耗节点 id/树结构。
  - 连续失败次数（`GoalNode.consecutive_failures`，见 `goal_backlog.py`）
    每达到一次 `threshold` 的整数倍就推一条通知（复用
    `notification/dispatcher.py`，跟 `goal_cron_bridge._notify_cycle_
    failed()` 同一套通道）——不是每次重试都通知（会刷屏），也不是只
    通知一次就沉默（用户可能没看到，后续应该被继续提醒），"每
    threshold 次提醒一次"是两者的折中。该字段在节点某一轮成功完成时
    （`GoalBacklog.set_status(node_id, "completed")`）清零，见该方法
    实现里的说明。
  - 由 `AutonomousLoop._tick_maintenance()` 在 `reap_finished_cycles()`
    之后同一 tick 里调用，档位边界跟 `reap_finished_cycles()` 一致——
    两者都需要读写 `GoalBacklog`，只能在 maintenance/autonomous 档位
    触发，不能放进 `_tick_passive()`（该方法体内按既有约定不引用
    `GoalBacklog` 任何方法）。只读遍历 + 命中才写，开销可控，不需要
    单独起线程/订阅机制。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode

# 连续失败多少次推一次"要不要人工介入"的通知。不是重试次数上限——重试
# 本身不限次数，这个阈值只控制"多久提醒一次用户"。
DEFAULT_RETRY_ESCALATION_THRESHOLD = 3


def _notify_retry_escalated(goal_backlog: "GoalBacklog", node: "GoalNode", threshold: int) -> None:
    """连续失败次数达到 threshold 整数倍时推一条通知，把决定权交给用户。
    kanban 通知渠道恒真兜底，用户至少能在看板"全局待办中心"/通知记录里
    看到。异常整体吞掉——通知是感知增强，不能影响重试主流程。"""
    try:
        paths = getattr(goal_backlog, "_paths", None)
        if paths is None:
            return
        from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
        last_line = ""
        if node.progress_notes:
            lines = node.progress_notes.strip().splitlines()
            if lines:
                last_line = lines[-1]
        NotificationDispatcher(paths).dispatch(NotificationMessage(
            title=f"目标节点「{node.title}」已连续失败 {node.consecutive_failures} 次",
            body=(
                f"仍在自动重试中，但已连续失败 {node.consecutive_failures} 次，"
                f"可能需要人工介入（调整任务描述/暂停/放弃）。"
                + (f"最近一条记录：{last_line[:150]}" if last_line else "")
            ),
            source="goal_node_retry",
            meta={"goal_id": node.id, "consecutive_failures": node.consecutive_failures},
        ))
    except Exception:
        pass


def retry_failed_goal_tree_nodes(
    goal_backlog: "GoalBacklog",
    *,
    threshold: int = DEFAULT_RETRY_ESCALATION_THRESHOLD,
) -> dict:
    """扫描目标树里所有 `status=="failed"` 的 Objective 节点，自动拉回
    `"active"` 让下一次 tick 的正常调度重新捡起来执行；连续失败次数每
    达到一次 `threshold` 的整数倍时额外推一条通知。不限重试次数。

    返回 `{"retried": [node_id, ...], "escalated": [node_id, ...]}`，
    供日志/测试断言；正常 tick 大多数时候两个列表都是空的。
    """
    goal_backlog.load()
    retried: list[str] = []
    escalated: list[str] = []
    for node in goal_backlog.all_nodes():
        if not node.is_objective or node.status != "failed":
            continue
        new_count = goal_backlog.bump_consecutive_failures(node.id)
        if new_count is None:
            continue
        try:
            goal_backlog.append_progress_note(node.id, f"第 {new_count} 次连续失败后自动重试")
        except Exception:
            pass
        goal_backlog.set_status(node.id, "active")
        retried.append(node.id)
        if threshold > 0 and new_count % threshold == 0:
            refreshed = goal_backlog.get(node.id)
            if refreshed is not None:
                _notify_retry_escalated(goal_backlog, refreshed, threshold)
                escalated.append(node.id)
    return {"retried": retried, "escalated": escalated}


__all__ = ["retry_failed_goal_tree_nodes", "DEFAULT_RETRY_ESCALATION_THRESHOLD"]
