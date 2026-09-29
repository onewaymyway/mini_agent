"""
evolution/cron_skip_reasons.py — cron job "到点但未能触发"的原因码

背景：`CronScheduler._fire()` 返回 False 时只有一个布尔值，"为什么没触发"
在返回那一刻就丢了，`cron_skip_alert` 通知只能写一句猜测性文案。本模块提供
一个很小的共享词表 + 记录函数，让每个返回 False 的分支都能留下具体原因：

- `set_skip_reason(job, code, detail="")`：写入 `job.last_skip_reason` /
  `job.last_skip_detail`。**先到先得**：同一次触发内如果被调用方（比如
  goal_cycle 处理函数）已经写过更具体的原因，外层（`_fire()`）的兜底调用
  不会覆盖它。
- `describe_skip_reason(code)`：原因码 → 人类可读说明，供告警正文使用。

原因码是自由字符串，词表里没有的码会原样展示（不抛错），方便日后扩展。
"""

from __future__ import annotations

from typing import Any

SKIP_REASONS: dict[str, str] = {
    # ── CronJobRunner.submit() ──
    "arbiter_blocked": "资源仲裁处于 blocked 状态（预算耗尽/挫败感过高/用户活跃），本次不触发",
    "already_running": "该 job 上一次执行还没结束（仍在运行或被判定为长期未结束）",
    # ── CronScheduler._fire() ──
    "goal_cycle_handler_missing": "goal_cycle 处理函数未注册（非 daemon 场景，或功能尚未接线）",
    "goal_cycle_handler_exception": "goal_cycle 处理函数抛出异常",
    "goal_cycle_returned_false": "goal_cycle 处理函数返回 False，且未给出具体原因",
    "local_handler_exception": "本地 handler 抛出异常",
    "local_handler_returned_false": "本地 handler 返回 False，且未给出具体原因",
    "job_runner_exception": "job_runner.submit() 抛出异常",
    "job_runner_returned_false": "job_runner.submit() 返回 False，且未给出具体原因",
    "no_submit_fn": "没有可用的 submit_fn / job_runner，无处投递",
    "submit_fn_exception": "submit_fn 抛出异常",
    "submit_fn_rejected": "submit_fn 拒绝了本次投递（返回 False）",
    "unknown": "未记录到具体原因",
    # ── goal_cron_bridge._fire_goal_cycle() ──
    "goal_cycle_no_goal_id": "goal_cycle job 没有绑定 goal_id",
    "goal_cycle_passive_autonomy": "autonomy_level 为 passive，周期性 Goal 不会自动续期（需调到 maintenance/autonomous）",
    "goal_cycle_goal_missing": "绑定的 Goal 已不存在（僵尸绑定，可在 Cron 任务面板手动清理该 job）",
    "goal_cycle_goal_abandoned": "绑定的 Goal 已被放弃（abandoned），但 cron job 仍处于启用状态",
    "goal_cycle_goal_paused": "绑定的 Goal 已被暂停（paused），但 cron job 仍处于启用状态",
    "goal_cycle_user_skip": "用户手动要求跳过这一轮（skip_next_cycle）",
    "goal_cycle_prev_cycle_running": "上一轮 Objective 还没跑完，本轮不叠加并发",
    "goal_cycle_objective_start_failed": "objective_executor.start() 返回 None（拆解失败/第一步提交失败等）",
}


def set_skip_reason(job: Any, code: str, detail: str = "") -> None:
    """记录 job 本次未触发的原因。先到先得：已有原因码时不覆盖。"""
    try:
        if getattr(job, "last_skip_reason", ""):
            return
        job.last_skip_reason = code
        job.last_skip_detail = (detail or "")[:300]
    except Exception:
        # 记录原因只是诊断用途，绝不能影响触发主流程。
        pass


def describe_skip_reason(code: str) -> str:
    """原因码 → 说明；未知码原样返回，空码返回 unknown 的说明。"""
    if not code:
        return SKIP_REASONS["unknown"]
    return SKIP_REASONS.get(code, code)
