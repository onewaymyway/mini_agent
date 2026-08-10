"""
cli/commands/goals.py — /agent goals slash 命令处理（Stage 9 第六节）

子命令：
  /agent goals                     — 列出所有 active goals 和 objectives
  /agent goals add <title>         — 添加 Goal
  /agent goals obj add <title> [--goal <id>] [--thread <id>]
                                   — 添加 Objective
  /agent goals done <id>           — 标记完成
  /agent goals abandon <id>        — 标记放弃
  /agent goals progress <id> <txt> — 更新进展记录
  /agent goals recur <id> <schedule> [task]  — 声明为周期性（见 goal_cron_bridge.py）
  /agent goals unrecur <id>        — 停止周期性（不删 Goal/cron job）
  /agent goals spec generate <id> [--template <id>] [--from-history]
                                   — 生成执行规范草稿（见 perception/goal_execution_spec.py）
  /agent goals spec confirm <id>   — 确认执行规范（冻结，下次触发生效）
  /agent goals spec show <id>      — 查看执行规范当前内容
  /agent goals status              — 显示 AutonomousLoop tick 状态
"""

from __future__ import annotations

from typing import Optional

import mini_agent.ui.renderer as R


def handle_goals_cmd(args: list[str], agent=None) -> None:
    """
    入口：处理 `/agent goals` 子命令。
    agent 参数用于获取 paths（与 /evolution 等命令风格一致）。
    """
    paths = _get_paths(agent)
    if paths is None:
        R.print_error("Cannot access project paths (agent not initialized).")
        return

    from mini_agent.perception.goal_backlog import load_goal_backlog
    gb = load_goal_backlog(paths)

    if not args or args[0] in ("list", "ls"):
        _cmd_list(gb)
        return

    subcmd = args[0]
    rest = args[1:]

    if subcmd == "add":
        if not rest:
            R.print_error("Usage: /agent goals add <title> [--priority N] [--tag tag1,tag2]")
            return
        _cmd_add_goal(gb, rest)

    elif subcmd == "obj":
        if not rest or rest[0] != "add":
            R.print_error("Usage: /agent goals obj add <title> [--goal <id>] [--thread <id>]")
            return
        _cmd_add_objective(gb, rest[1:])

    elif subcmd == "done":
        if not rest:
            R.print_error("Usage: /agent goals done <id>")
            return
        _cmd_set_status(gb, rest[0], "completed")

    elif subcmd == "abandon":
        if not rest:
            R.print_error("Usage: /agent goals abandon <id>")
            return
        _cmd_abandon(gb, rest[0], paths, agent)

    elif subcmd == "reject":
        # /goals reject <id> — 拒绝 agent_derived Goal（alias: abandon + 记录 rejected）
        if not rest:
            R.print_error("Usage: /agent goals reject <id>")
            return
        _cmd_abandon(gb, rest[0], paths, agent)

    elif subcmd == "accept":
        # /goals accept <id> — 接受 agent_derived Goal（激活，提升 priority）
        if not rest:
            R.print_error("Usage: /agent goals accept <id>")
            return
        _cmd_accept(gb, rest[0], paths=paths)

    elif subcmd == "pause":
        if not rest:
            R.print_error("Usage: /agent goals pause <id>")
            return
        _cmd_set_status(gb, rest[0], "paused")

    elif subcmd == "progress":
        if len(rest) < 2:
            R.print_error("Usage: /agent goals progress <id> <notes>")
            return
        _cmd_progress(gb, rest[0], " ".join(rest[1:]))

    elif subcmd == "feedback":
        # [goal_cron_feedback_and_output_policy_plan.md 3.4]
        # /agent goals feedback <id> <text> — 持久化提意见，此后所有基于这个
        # Goal/Objective 派生的执行都会带着这条意见，区别于一次性的
        # inject_guidance()。
        if len(rest) < 2:
            R.print_error("Usage: /agent goals feedback <id> <text>")
            return
        _cmd_feedback(gb, rest[0], " ".join(rest[1:]))

    elif subcmd == "recur":
        # [goal_cron_binding_plan.md Track D] /agent goals recur <id> <schedule> [task]
        if len(rest) < 2:
            R.print_error("Usage: /agent goals recur <id> <schedule> [task_template]")
            return
        _cmd_recur(gb, paths, rest[0], rest[1], " ".join(rest[2:]) if len(rest) > 2 else None)

    elif subcmd == "spec":
        # [goal_execution_spec_generation_plan.md §6.4] /agent goals spec ...
        if not rest:
            R.print_error(
                "Usage: /agent goals spec generate <goal_id> [--template <id>] "
                "| /agent goals spec confirm <goal_id> | /agent goals spec show <goal_id>"
            )
            return
        _cmd_spec(gb, paths, rest[0], rest[1:])

    elif subcmd == "unrecur":
        # /agent goals unrecur <id> — 停止周期性（不删 Goal/cron job）
        if not rest:
            R.print_error("Usage: /agent goals unrecur <id>")
            return
        _cmd_unrecur(gb, paths, rest[0])

    elif subcmd == "status":
        _cmd_loop_status(agent, paths)

    elif subcmd == "reset-step":
        # [daemon_autonomous_state_recovery_plan.md 阶段二]
        # /agent goals reset-step <exec_id> <step_idx> [reason...]
        if len(rest) < 2:
            R.print_error("Usage: /agent goals reset-step <exec_id> <step_idx> [reason]")
            return
        _cmd_reset_step(agent, paths, rest[0], rest[1], " ".join(rest[2:]) if len(rest) > 2 else "")

    else:
        R.print_error(f"Unknown subcommand: {subcmd!r}")
        R.print_info(
            "Available: list, add, obj add, done, abandon, accept, reject, pause, "
            "progress, feedback, recur, unrecur, spec, status, reset-step"
        )


# ── 子命令实现 ─────────────────────────────────────────────────────────────────

def _cmd_list(gb) -> None:
    """列出所有 Goals 和 Objectives。"""
    goals = gb.active_goals()
    objectives = gb.active_objectives()
    all_nodes = gb.all_nodes()

    if not all_nodes:
        R.print_info("Goal Backlog 为空。用 `/agent goals add <title>` 添加第一个目标。")
        return

    # 显示 active goals
    if goals:
        R.print_info(f"📌 Goals ({len(goals)} active):")
        for g in goals:
            priority_str = f" [priority={g.priority}]" if g.priority else ""
            tags_str = f" [{', '.join(g.tags)}]" if g.tags else ""
            R.print_info(f"  {g.id}  {g.title}{priority_str}{tags_str}")
            if g.children_ids:
                R.print_info(f"    └─ {len(g.children_ids)} objectives")

    # 显示 active objectives
    if objectives:
        R.print_info(f"\n🎯 Objectives ({len(objectives)} active):")
        for o in objectives:
            parent_str = f" [goal:{o.parent_id}]" if o.parent_id else ""
            thread_str = f" [thread:{o.work_thread_ref}]" if o.work_thread_ref else ""
            R.print_info(f"  {o.id}  {o.title}{parent_str}{thread_str}")
            if o.progress_notes:
                R.print_info(f"    ├─ 进展: {o.progress_notes[:80]}")

    # 显示非 active 节点摘要
    inactive = [n for n in all_nodes if not n.is_active]
    if inactive:
        completed = [n for n in inactive if n.status == "completed"]
        abandoned = [n for n in inactive if n.status == "abandoned"]
        parts = []
        if completed:
            parts.append(f"{len(completed)} completed")
        if abandoned:
            parts.append(f"{len(abandoned)} abandoned")
        if parts:
            R.print_info(f"\n（历史：{', '.join(parts)}）")


def _cmd_add_goal(gb, args: list[str]) -> None:
    """添加 Goal：/agent goals add <title> [--priority N] [--tag tag1,tag2]"""
    import argparse
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("title", nargs="+")
    p.add_argument("--priority", type=int, default=0)
    p.add_argument("--tag", default="")
    try:
        parsed = p.parse_args(args)
    except SystemExit:
        R.print_error("Usage: /agent goals add <title> [--priority N] [--tag tag1,tag2]")
        return

    title = " ".join(parsed.title)
    tags = [t.strip() for t in parsed.tag.split(",") if t.strip()] if parsed.tag else []

    # [goal-provenance-guide.md] 显式标记 —— 这是终端里手敲的 /agent goals
    # add 命令，无论当前线程是否残留着某轮对话的 turn_context，都应该记
    # 成用户本人操作，不依赖 thread-local 兜底值。
    node = gb.add_goal(title, source="user", priority=parsed.priority, tags=tags, source_initiator="user")
    R.print_success(f"Goal 已添加: {node.id} — {node.title}")


def _cmd_add_objective(gb, args: list[str]) -> None:
    """添加 Objective：/agent goals obj add <title> [--goal <id>] [--thread <id>]"""
    import argparse
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("title", nargs="+")
    p.add_argument("--goal", default=None)
    p.add_argument("--thread", default=None)
    p.add_argument("--priority", type=int, default=0)
    try:
        parsed = p.parse_args(args)
    except SystemExit:
        R.print_error("Usage: /agent goals obj add <title> [--goal <id>] [--thread <id>]")
        return

    title = " ".join(parsed.title)

    # 验证 parent goal 存在
    if parsed.goal:
        parent_node = gb.get(parsed.goal)
        if not parent_node:
            R.print_error(f"Goal not found: {parsed.goal!r}")
            return
        if not parent_node.is_goal:
            R.print_error(f"{parsed.goal!r} 是 Objective，不是 Goal（objective 不能嵌套）")
            return

    node = gb.add_objective(
        title,
        parent_id=parsed.goal,
        work_thread_ref=parsed.thread,
        source="user",
        priority=parsed.priority,
    )
    parent_str = f" (under {parsed.goal})" if parsed.goal else ""
    thread_str = f" [thread:{parsed.thread}]" if parsed.thread else ""
    R.print_success(f"Objective 已添加: {node.id} — {node.title}{parent_str}{thread_str}")


def _cmd_set_status(gb, node_id: str, status: str) -> None:
    """更新节点状态。"""
    node = gb.get(node_id)
    if not node:
        R.print_error(f"Not found: {node_id!r}")
        return
    old_status = node.status
    gb.set_status(node_id, status)
    emoji = {"completed": "✅", "abandoned": "🗑", "paused": "⏸"}.get(status, "")
    R.print_success(f"{emoji} {node_id} 状态: {old_status} → {status}")


def _cmd_accept(gb, node_id: str, paths=None) -> None:
    """
    接受 agent_derived Goal：激活并提升优先级到用户 Goal 默认值（50）。
    对非 agent_derived Goal 也有效（等同于把 paused Goal 重新激活）。
    """
    node = gb.get(node_id)
    if not node:
        R.print_error(f"Not found: {node_id!r}")
        return
    if node.status == "active":
        R.print_warning(f"{node_id} 已经是 active 状态")
        return
    fields = {"status": "active"}
    if getattr(node, "source", "") == "agent_derived" and node.priority < 50:
        fields["priority"] = 50
    updated = gb.update_fields(node_id, **fields)
    if not updated:
        R.print_error(f"Not found: {node_id!r}")
        return
    R.print_success(f"✅ 已接受 Goal：{updated.title}")

    # [系统关联性断点改进方案 F3] agent_derived Goal 被接受时记入反馈账本，
    # 供 soft_goal_deriver 后续对同类高采纳率的方向做小幅加成。
    if getattr(node, "source", "") == "agent_derived" and paths is not None:
        try:
            from mini_agent.evolution.soft_goal_deriver import _DeriveCandidate
            from mini_agent.evolution.suggestion_feedback_ledger import record_outcome
            key = _DeriveCandidate(title=node.title, description="", source_tag="").dedupe_key()
            record_outcome(paths, key, "accepted")
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.cli.commands.goals._cmd_accept')
    if getattr(updated, "source", "") == "agent_derived":
        R.print_info("提示：使用 /goals obj add <步骤描述> --goal " + node_id + " 为此 Goal 添加 Objective")


def _cmd_abandon(gb, node_id: str, paths=None, agent=None) -> None:
    """
    放弃/拒绝 Goal：设置 abandoned 状态，若是 agent_derived 则记录 rejected 历史。
    """
    node = gb.get(node_id)
    if not node:
        R.print_error(f"Not found: {node_id!r}")
        return
    old_status = node.status
    gb.set_status(node_id, "abandoned")

    # agent_derived Goal 被拒绝时，通知 SoftGoalDeriver 记录 30 天去重
    if getattr(node, "source", "") == "agent_derived":
        try:
            from mini_agent.evolution.soft_goal_deriver import SoftGoalDeriver
            cfg = getattr(agent, "cfg", None) if agent else None
            if paths and cfg:
                SoftGoalDeriver(paths, cfg).record_rejected(node.title)
                R.print_success(f"🗑 已拒绝并记录：{node.title}（30 天内不再建议相同主题）")
                return
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.cli.commands.goals')
            pass

    R.print_success(f"🗑 {node_id} 状态: {old_status} → abandoned")


def _cmd_progress(gb, node_id: str, notes: str) -> None:
    """更新进展记录。"""
    node = gb.get(node_id)
    if not node:
        R.print_error(f"Not found: {node_id!r}")
        return
    gb.update_progress(node_id, notes)
    R.print_success(f"进展已更新: {node_id}")


def _cmd_feedback(gb, node_id: str, text: str) -> None:
    """[goal_cron_feedback_and_output_policy_plan.md 3.4] 持久化提意见，
    合入该节点的 description，此后所有基于这个 Goal/Objective 派生的执行
    都会带着这条意见。若节点是绑定了周期性 CronJob 的 Goal，会自动双向
    同步到对应 CronJob。"""
    node = gb.get(node_id)
    if not node:
        R.print_error(f"Not found: {node_id!r}")
        return
    ok = gb.add_user_feedback(node_id, text)
    if ok:
        R.print_success(f"意见已记录并合入说明: {node_id}")
    else:
        R.print_error(f"记录意见失败: {node_id}")


def _cmd_loop_status(agent, paths) -> None:
    """显示 AutonomousLoop tick 状态（连接到 daemon 才有意义）。"""
    # 优先通过 HTTP API 查询 daemon status
    try:
        from mini_agent.cli.daemon import _read_daemon_info, DaemonClient
        info = _read_daemon_info(paths.workdir_dir.parent if hasattr(paths, "workdir_dir") else paths.workdir_dir)
        if not info:
            # 尝试用当前 workdir
            info = _read_daemon_info(paths.workdir_dir)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.cli.commands.goals._cmd_loop_status')
        info = None

    if info:
        client = DaemonClient(info["http_port"])
        status = client.get_status()
        if status:
            R.print_info(f"🔄 AutonomousLoop 状态（daemon PID={info['pid']}）:")
            R.print_info(f"  autonomy_level  : {status.get('autonomy_level', 'unknown')}")
            last_tick = status.get("last_autonomous_tick_at")
            if last_tick:
                import time
                ago = time.time() - last_tick
                R.print_info(f"  上次 tick       : {_format_ago(ago)}前")
            R.print_info(f"  tick 次数       : {status.get('tick_count', 0)}")
            return

    # Fallback：读取 activity_digest.jsonl
    try:
        from mini_agent.evolution.resource_arbiter import read_activity_digest
        records = read_activity_digest(paths)
        if records:
            R.print_info(f"📋 activity_digest.jsonl 最近 {len(records)} 条（无 daemon 连接，显示日志）:")
            for r in records[-5:]:
                import time
                ago = time.time() - r.get("at", 0)
                R.print_info(f"  [{_format_ago(ago)}前] {r.get('type', '?')}: "
                             f"{r.get('summary', r.get('task_desc', ''))[:60]}")
        else:
            R.print_info("暂无自主活动记录（daemon 未启动或尚未执行过 tick）")
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.commands.goals._cmd_loop_status')
        R.print_warning(f"无法读取 activity_digest.jsonl: {e}")


def _cmd_reset_step(agent, paths, exec_id: str, step_idx_str: str, reason: str) -> None:
    """[daemon_autonomous_state_recovery_plan.md 阶段二]
    /agent goals reset-step <exec_id> <step_idx> [reason]

    把某个自主任务执行（exec_id）的第 step_idx 步（0-based）打回 pending 重做，
    并清空其之后所有步骤的既有进度——用于人工发现某个 objective/cron 任务已经
    进入错误状态（比如把畸形工具调用输出当成了步骤结果）时手动纠正。

    优先直接调用本进程内的 ObjectiveExecutor；本地没有可用实例时，回退到通过
    HTTP 连接的 daemon 发起请求。
    """
    try:
        step_idx = int(step_idx_str)
    except ValueError:
        R.print_error(f"step_idx 必须是整数，收到: {step_idx_str!r}")
        return

    oe = getattr(agent, "_objective_executor", None) if agent is not None else None
    if oe is not None:
        ok = oe.reset_step(exec_id, step_idx, reason)
        if ok:
            R.print_info(f"✅ 已重置 {exec_id} 的步骤 {step_idx+1}（{reason or '未说明原因'}）")
        else:
            R.print_error(f"重置失败：execution {exec_id!r} 不存在或 step_idx {step_idx} 越界")
        return

    # 回退：通过 HTTP 连接的 daemon
    try:
        from mini_agent.cli.daemon import _read_daemon_info, DaemonClient
        info = _read_daemon_info(paths.workdir_dir.parent if hasattr(paths, "workdir_dir") else paths.workdir_dir)
        if not info:
            info = _read_daemon_info(paths.workdir_dir)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.cli.commands.goals._cmd_reset_step")
        info = None

    if not info:
        R.print_error("未找到本地 ObjectiveExecutor，也未连接到 daemon，无法执行重置。")
        return

    client = DaemonClient(info["http_port"])
    result = client._post_json(
        f"/v1/objectives/{exec_id}/steps/{step_idx}/reset",
        {"reason": reason} if reason else {},
    )
    if result and result.get("ok"):
        R.print_info(f"✅ 已通过 daemon 重置 {exec_id} 的步骤 {step_idx+1}（{reason or '未说明原因'}）")
    else:
        R.print_error(f"重置失败：daemon 返回 {result!r}")


def _cmd_recur(gb, paths, goal_id: str, schedule: str, task_template: Optional[str]) -> None:
    """[goal_cron_binding_plan.md Track D] 把已有 Goal 声明为周期性。
    这里新建一个独立的 CronScheduler 实例做纯 CRUD（不传 submit_fn/job_runner，
    与 daemon 内运行的那个实例是同一份 cron_jobs.json，靠文件落盘同步，
    不需要跨进程通信）——与 `/agent goals` 系列命令一贯"每次现读现写" 的风格一致。
    """
    if not (schedule.startswith("interval:") or schedule.startswith("cron:")):
        R.print_error(
            "schedule 格式错误。interval:<秒>（如 interval:86400）或 "
            "cron:<分 时 日 月 周>（如 cron:0 9 * * 1）"
        )
        return
    try:
        from mini_agent.evolution.cron_scheduler import load_cron_scheduler
        from mini_agent.evolution.goal_cron_bridge import make_goal_recurring
        cs = load_cron_scheduler(paths)
        job = make_goal_recurring(gb, cs, goal_id, schedule, task_template)
    except ValueError as e:
        R.print_error(str(e))
        return
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.commands.goals._cmd_recur')
        R.print_error(f"绑定失败：{e}")
        return

    R.print_success(f"Goal {goal_id} 已声明为周期性，绑定 Job {job.id}，下次触发：{job.next_run_str()}")
    R.print_info(f"停止周期性：/agent goals unrecur {goal_id}")

    # [goal_execution_spec_generation_plan.md §6.4] recur 本身不强制依赖
    # 执行规范存在——只是没有已确认规范时提示一句，跟看板"跳过"选项的
    # 非目标声明保持一致：规范生成是可选增强，不是必经关卡。
    try:
        from mini_agent.perception import goal_execution_spec as ges
        spec = ges.load_spec(paths, goal_id)
        if spec is None or not spec.confirmed:
            R.print_info(
                f"该 Goal 还没有已确认的执行规范，可以先 "
                f"/agent goals spec generate {goal_id} 想清楚细节，或直接继续。"
            )
    except Exception:
        pass


def _cmd_unrecur(gb, paths, goal_id: str) -> None:
    """停止周期性推进（不删 Goal/cron job）。"""
    try:
        from mini_agent.evolution.cron_scheduler import load_cron_scheduler
        from mini_agent.evolution.goal_cron_bridge import stop_goal_recurrence
        cs = load_cron_scheduler(paths)
        ok = stop_goal_recurrence(gb, cs, goal_id)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.commands.goals._cmd_unrecur')
        R.print_error(f"解绑失败：{e}")
        return

    if ok:
        R.print_success(f"Goal {goal_id} 已停止周期性推进（绑定的 cron job 已 disable，未删除）")
    else:
        R.print_error(f"Goal 不存在或不是有效的 goal 节点：{goal_id}")


def _cmd_spec(gb, paths, action: str, rest: list[str]) -> None:
    """[goal_execution_spec_generation_plan.md §6.4]
    /agent goals spec generate <goal_id> [--template <id>] [--from-history]
    /agent goals spec confirm <goal_id>
    /agent goals spec show <goal_id>
    """
    if action == "generate":
        if not rest:
            R.print_error("Usage: /agent goals spec generate <goal_id> [--template <id>] [--from-history]")
            return
        import argparse
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("goal_id")
        p.add_argument("--template", default=None)
        p.add_argument("--from-history", action="store_true")
        try:
            parsed = p.parse_args(rest)
        except SystemExit:
            R.print_error("Usage: /agent goals spec generate <goal_id> [--template <id>] [--from-history]")
            return
        _cmd_spec_generate(gb, paths, parsed.goal_id, parsed.template, parsed.from_history)

    elif action == "confirm":
        if not rest:
            R.print_error("Usage: /agent goals spec confirm <goal_id>")
            return
        _cmd_spec_confirm(gb, paths, rest[0])

    elif action == "show":
        if not rest:
            R.print_error("Usage: /agent goals spec show <goal_id>")
            return
        _cmd_spec_show(paths, rest[0])

    else:
        R.print_error(f"Unknown spec subcommand: {action!r}")
        R.print_info("Available: generate, confirm, show")


def _cmd_spec_generate(gb, paths, goal_id: str, template_id: Optional[str], from_history: bool) -> None:
    node = gb.get(goal_id)
    if node is None or not node.is_goal:
        R.print_error(f"Goal 不存在：{goal_id}")
        return

    try:
        from mini_agent.config import load_config
        from mini_agent.perception import goal_execution_spec as ges
        from mini_agent.evolution import output_workspace

        cfg = load_config()
        history_manifests = None
        if from_history:
            base_dir = output_workspace.goal_output_base_dir(paths, goal_id)
            m = output_workspace.read_latest_manifest(base_dir)
            history_manifests = [m] if m else None

        builder = ges.GoalExecutionSpecBuilder(cfg)
        spec = builder.build_draft(
            goal_id,
            node.title,
            node.description,
            template_id=template_id,
            history_manifests=history_manifests,
        )
        ges.save_spec(paths, goal_id, spec)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.commands.goals._cmd_spec_generate')
        R.print_error(f"生成执行规范失败：{e}")
        return

    if spec.generation_error:
        R.print_warning(f"生成失败，已保存为空草稿（可手动编辑或重试）：{spec.generation_error}")
    else:
        R.print_success(f"已生成执行规范草稿（第 {spec.version} 版），未确认，不影响执行。")
    R.print_info(spec.render_summary_for_user())
    R.print_info(f"确认：/agent goals spec confirm {goal_id}")


def _cmd_spec_confirm(gb, paths, goal_id: str) -> None:
    node = gb.get(goal_id)
    if node is None or not node.is_goal:
        R.print_error(f"Goal 不存在：{goal_id}")
        return
    try:
        from mini_agent.perception import goal_execution_spec as ges
        spec = ges.load_spec(paths, goal_id)
        if spec is None:
            R.print_error(f"该 Goal 还没有生成过执行规范草稿：{goal_id}")
            return
        ges.GoalExecutionSpecBuilder.confirm(spec)
        ges.save_spec(paths, goal_id, spec)
        gb.update_fields(goal_id, execution_spec_confirmed=True)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.commands.goals._cmd_spec_confirm')
        R.print_error(f"确认失败：{e}")
        return
    R.print_success(f"执行规范已确认并冻结（第 {spec.version} 版），下次触发即生效。")


def _cmd_spec_show(paths, goal_id: str) -> None:
    try:
        from mini_agent.perception import goal_execution_spec as ges
        spec = ges.load_spec(paths, goal_id)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.commands.goals._cmd_spec_show')
        R.print_error(f"读取执行规范失败：{e}")
        return
    if spec is None:
        R.print_info(f"该 Goal 还没有执行规范：{goal_id}")
        return
    R.print_info(spec.render_summary_for_user())


def _format_ago(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}m"
    else:
        return f"{seconds/3600:.1f}h"


def _get_paths(agent):
    """从 agent 对象获取 AgentPaths。"""
    if agent is None:
        return None
    paths = getattr(agent, "_paths", None)
    if paths is not None:
        return paths
    # fallback: 从 cfg 推断
    cfg = getattr(agent, "cfg", None)
    if cfg is None:
        return None
    try:
        from mini_agent.storage.paths import AgentPaths
        return AgentPaths(cfg.project_root)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.cli.commands.goals._get_paths')
        return None
