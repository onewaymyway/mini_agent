"""
cli/commands/cron.py — /cron 命令处理器

子命令：
  /cron list [--all]            — 列出 cron job（默认只显示 enabled）
  /cron status                  — 所有 job 的下次触发时间总览
  /cron enable <id>             — 启用 job
  /cron disable <id>            — 禁用 job
  /cron run <id>                — 立即触发一次（不改变 next_run_at）
  /cron add <name> <schedule> <task>  — 添加用户 job
  /cron remove <id>             — 删除用户 job（sys: 前缀不可删）
  /cron set-schedule <id> <schedule>  — 修改触发时间
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.cli.repl import ReplContext


async def handle_cron(args: list[str], ctx: "ReplContext") -> str:
    """
    /cron 命令主处理器。
    ctx.cron_scheduler 由 AgentRunner/ReplContext 在 daemon 模式下注入。
    非 daemon 模式（cron_scheduler 为 None）时返回提示。
    """
    cs = getattr(ctx, "cron_scheduler", None)
    if cs is None:
        return (
            "[cron] Cron scheduler 仅在 daemon 模式下可用。\n"
            "启动方式：mini-agent daemon start --detach"
        )

    if not args or args[0] == "list":
        return _cmd_list(cs, show_all="--all" in args)

    sub = args[0]
    rest = args[1:]

    if sub == "status":
        return _cmd_status(cs)

    if sub == "enable":
        if not rest:
            return "[cron] 用法：/cron enable <job_id>"
        return _cmd_enable(cs, rest[0])

    if sub == "disable":
        if not rest:
            return "[cron] 用法：/cron disable <job_id>"
        return _cmd_disable(cs, rest[0])

    if sub == "run":
        if not rest:
            return "[cron] 用法：/cron run <job_id>"
        return _cmd_run_now(cs, rest[0])

    if sub == "remove":
        if not rest:
            return "[cron] 用法：/cron remove <job_id>"
        return _cmd_remove(cs, rest[0])

    if sub == "set-schedule":
        if len(rest) < 2:
            return "[cron] 用法：/cron set-schedule <job_id> <schedule>"
        return _cmd_set_schedule(cs, rest[0], rest[1])

    if sub == "add":
        # /cron add <name> <schedule> <task_template...>
        if len(rest) < 3:
            return (
                "[cron] 用法：/cron add <name> <schedule> <task_template>\n"
                "schedule 格式：interval:<秒> 或 cron:<分 时 日 月 周>\n"
                "示例：/cron add weekly-backup interval:604800 备份项目文件到 ./backups/"
            )
        name = rest[0]
        schedule = rest[1]
        task_template = " ".join(rest[2:])
        return _cmd_add(cs, name, schedule, task_template)

    return (
        "[cron] 未知子命令。可用子命令：\n"
        "  list [--all]  status  enable  disable  run  add  remove  set-schedule"
    )


# ── 子命令实现 ────────────────────────────────────────────────────────────────

def _cmd_list(cs, show_all: bool = False) -> str:
    jobs = cs.list_jobs(enabled_only=not show_all)
    if not jobs:
        return "[cron] 没有" + ("" if show_all else "启用的") + " cron job。"

    lines = [
        f"{'ID':<24}  {'名称':<16}  {'状态':<6}  {'下次触发':<14}  {'已运行':<6}  说明"
    ]
    lines.append("─" * 90)
    for j in jobs:
        status_str = "✓ on " if j.enabled else "✗ off"
        lines.append(
            f"{j.id:<24}  {j.name:<16}  {status_str}  "
            f"{j.next_run_str():<14}  {j.run_count:<6}  {j.description[:30]}"
        )

    tip = "\n提示：/cron run <id> 立即触发 | /cron disable <id> 暂停"
    if not show_all:
        tip += " | /cron list --all 显示全部"
    return "\n".join(lines) + tip


def _cmd_status(cs) -> str:
    return "[cron] 下次触发预览：\n" + cs.next_run_summary()


def _cmd_enable(cs, job_id: str) -> str:
    if cs.enable(job_id):
        job = cs.get(job_id)
        nxt = job.next_run_str() if job else "unknown"
        return f"[cron] ✓ {job_id} 已启用，下次触发：{nxt}"
    return f"[cron] ✗ Job '{job_id}' 不存在"


def _cmd_disable(cs, job_id: str) -> str:
    if cs.disable(job_id):
        return f"[cron] ✓ {job_id} 已禁用"
    return f"[cron] ✗ Job '{job_id}' 不存在"


def _cmd_run_now(cs, job_id: str) -> str:
    job = cs.get(job_id)
    if not job:
        return f"[cron] ✗ Job '{job_id}' 不存在"
    if not job.enabled:
        return f"[cron] ✗ Job '{job_id}' 已禁用，请先 /cron enable {job_id}"
    success = cs.run_now(job_id)
    if success:
        return f"[cron] ✓ Job '{job_id}' 已触发（任务已提交到队列）"
    return f"[cron] ✗ Job '{job_id}' 触发失败（submit_fn 未就绪或返回失败）"


def _cmd_remove(cs, job_id: str) -> str:
    job = cs.get(job_id)
    if not job:
        return f"[cron] ✗ Job '{job_id}' 不存在"
    if job.is_system:
        return (
            f"[cron] ✗ 系统 Job '{job_id}' 不可删除。\n"
            f"如需停用，请使用：/cron disable {job_id}"
        )
    if cs.remove_job(job_id):
        return f"[cron] ✓ Job '{job_id}' 已删除"
    return f"[cron] ✗ 删除失败"


def _cmd_set_schedule(cs, job_id: str, schedule: str) -> str:
    if not cs.get(job_id):
        return f"[cron] ✗ Job '{job_id}' 不存在"
    # 简单格式校验
    if not (schedule.startswith("interval:") or schedule.startswith("cron:")):
        return (
            f"[cron] ✗ schedule 格式错误。\n"
            f"  interval 格式：interval:<秒>     例：interval:3600\n"
            f"  cron 格式：   cron:<分 时 日 月 周>  例：cron:0 */6 * * *"
        )
    if cs.update_schedule(job_id, schedule):
        job = cs.get(job_id)
        nxt = job.next_run_str() if job else "unknown"
        return f"[cron] ✓ {job_id} schedule 已更新为 {schedule!r}，下次触发：{nxt}"
    return f"[cron] ✗ 更新失败"


def _cmd_add(cs, name: str, schedule: str, task_template: str) -> str:
    if not (schedule.startswith("interval:") or schedule.startswith("cron:")):
        return (
            f"[cron] ✗ schedule 格式错误。\n"
            f"  interval 格式：interval:<秒>     例：interval:3600\n"
            f"  cron 格式：   cron:<分 时 日 月 周>  例：cron:0 9 * * 1"
        )
    job = cs.add_job(name=name, schedule=schedule, task_template=task_template)
    return (
        f"[cron] ✓ 已添加 Job：{job.id}\n"
        f"  名称：{job.name}\n"
        f"  触发：{schedule}\n"
        f"  任务：{task_template[:80]}\n"
        f"  下次：{job.next_run_str()}"
    )


__all__ = ["handle_cron"]
