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
  /agent goals migrate-legacy <id> — 请求下一次触发时附加一次"历史数据迁移"
                                     任务：把旧模型（每轮一个 cycle_NNNN/
                                     目录）下的历史产出搬进新的固定四目录
                                     模型，见 evolution/output_workspace.py
                                     ::build_legacy_migration_directive()，
                                     next_doc/goal_output_directory_and_
                                     execution_phase_redesign_plan.md
                                     Stage 9。只打标记，daemon 模式下用
                                     `/cron run <job_id>` 立即触发一轮，
                                     或等待下一次自然触发。
  /agent goals spec generate <id> [--template <id>] [--from-history] [--mode llm|agent|auto]
                                   — 生成执行规范草稿（见 perception/goal_execution_spec.py）
                                     --mode 单次覆盖配置默认的 builder_mode
  /agent goals spec confirm <id>   — 确认执行规范（冻结，下次触发生效）
  /agent goals spec show <id>      — 查看执行规范当前内容
  /agent goals spec close-check <id> [--use-agent | --no-agent]
                                   — 手动（重新）触发一次"整体是否可以关闭"
                                     判定（见 §5 第二段，通常由子 Objective
                                     完成时自动触发，这里补一个手动入口）
  /agent goals status              — 显示 AutonomousLoop tick 状态
  /agent goals phase show <id>     — 查看当前执行阶段（见
                                     perception/execution_phase.py，
                                     next_doc/goal_execution_phase_improvement_plan.md）
  /agent goals phase set <id> explore|converge|stable|tidy|auto [--lock]
                                   — 手动切换执行阶段（非 auto 默认隐式锁定）
  /agent goals phase unlock <id>   — 解除锁定，交回自动判定
  /agent goals diagnose <id>       — 跨轮次诊断报告：这个 Goal 整体跑得
                                     怎么样（阶段/健康告警/cron 状态/最近
                                     轮次产出/机制说明一次性拼出来，见
                                     perception/cycle_diagnostics.py，
                                     next_doc/goal_cron_cycle_diagnostics_
                                     and_interactive_tuning_plan.md Stage 1）
  /agent goals tune <id> <param>=<value> [<param2>=<value2> ...] [--reason <text>]
                                   — 生成一份调优草案（白名单参数，见
                                     perception/cycle_tuning.py），打印 diff，
                                     不生效
  /agent goals tune suggest <id>  — 基于诊断报告规则触发的调优建议（不含
                                     LLM），命中信号时生成草案，未命中时
                                     明确告知"当前没有建议"
  /agent goals tune list <id>     — 列出该 Goal 的历史调优草案（含状态）
  /agent goals tune confirm <id> <proposal_id>
                                   — 确认草案（仍未生效，只是"这份草案本身
                                     被确认了"）
  /agent goals tune apply <id> <proposal_id>
                                   — 应用已确认的草案，逐项调用白名单参数
                                     对应的既有修改入口
  /agent goals tune reject <id> <proposal_id> [reason...]
                                   — 拒绝草案，作废，不产生任何实际改动

  以下两项为 Stage 3（可选增强，默认关闭，见 next_doc/goal_cron_cycle_
  diagnostics_and_interactive_tuning_plan.md §2.3/§3.2，配置开关
  `cycle_tuning.diagnostics_llm_summary_enabled` /
  `cycle_tuning.tuning_llm_parse_enabled`）：
  /agent goals diagnose <id> --summarize
                                   — 在结构化诊断报告末尾附一段 LLM 生成
                                     的自然语言总结；开关关闭或 LLM 不
                                     可用时静默跳过，不影响报告本身
  /agent goals tune <id> "<自然语言改进意见>"
                                   — 不含 `param=value` 时按自然语言意见
                                     处理，尝试用 LLM 解析成白名单参数
                                     改动；解析失败时提示改用具体命令

  以下三项为目标树系统（next_doc/goal_tree_system_plan.md）阶段一/二新增：
  /agent goals tree [root_id]     — 文本树形打印目标树（省略 root_id 时用
                                     全局根节点），候选分解以"待确认"前缀
                                     列在对应父节点下，⭐ 标记
                                     current_focus_ids 命中的节点
  /agent goals decompose <id> [--force]
                                   — 手动触发一次分解建议（§4.2 触发时机
                                     3），生成的候选落进该节点的
                                     decompose_candidates；节奏治理（间隔/
                                     已有未处理候选）拦下时给出提示，
                                     --force 跳过拦截
  /agent goals candidates <id> accept|reject <candidate_id>
                                   — 处理分解候选：accept 创建真正的子
                                     节点，reject 移除候选并记 30 天去重

  以下两项为目标树系统阶段三（§4.3 现阶段焦点）新增：
  /agent goals focus <id>         — 查看某节点当前的 current_focus_ids/
                                     focus_pinned_ids（配合直接子节点标题
                                     一并打印，省去再手动查一遍 tree）
  /agent goals focus pin|unpin <node_id> <child_id>
                                   — 手动 pin/unpin 某个直接子节点为
                                     "现阶段焦点"，立即重算该节点自身的
                                     current_focus_ids（不用等下一次
                                     sys:goal_tree_focus_recompute 巡检）
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
        _cmd_add_goal(gb, rest, paths=paths)

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
                "Usage: /agent goals spec generate <goal_id> [--template <id>] [--from-history] [--mode llm|agent|auto] "
                "| /agent goals spec confirm <goal_id> | /agent goals spec show <goal_id> "
                "| /agent goals spec close-check <goal_id> [--use-agent | --no-agent]"
            )
            return
        _cmd_spec(gb, paths, rest[0], rest[1:])

    elif subcmd == "phase":
        # [goal_execution_phase_improvement_plan.md §5]
        # /agent goals phase show <id> | set <id> <mode> [--lock] | unlock <id>
        if not rest:
            R.print_error(
                "Usage: /agent goals phase show <goal_id> "
                "| /agent goals phase set <goal_id> explore|converge|stable|tidy|auto [--lock] "
                "| /agent goals phase unlock <goal_id>"
            )
            return
        _cmd_phase(paths, rest[0], rest[1:])

    elif subcmd == "diagnose":
        # [goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md Stage 1/3]
        # /agent goals diagnose <goal_id> [--summarize]
        if not rest:
            R.print_error("Usage: /agent goals diagnose <goal_id> [--summarize]")
            return
        summarize = "--summarize" in rest[1:]
        goal_id_arg = rest[0]
        _cmd_diagnose(gb, paths, goal_id_arg, agent=agent, summarize=summarize)

    elif subcmd == "tune":
        # [goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md Stage 2]
        if not rest:
            R.print_error(
                "Usage: /agent goals tune <goal_id> <param>=<value> [...] [--reason <text>] "
                "| /agent goals tune suggest <goal_id> "
                "| /agent goals tune list <goal_id> "
                "| /agent goals tune confirm <goal_id> <proposal_id> "
                "| /agent goals tune apply <goal_id> <proposal_id> "
                "| /agent goals tune reject <goal_id> <proposal_id> [reason...]"
            )
            return
        _cmd_tune(gb, paths, rest[0], rest[1:], agent=agent)

    elif subcmd == "unrecur":
        # /agent goals unrecur <id> — 停止周期性（不删 Goal/cron job）
        if not rest:
            R.print_error("Usage: /agent goals unrecur <id>")
            return
        _cmd_unrecur(gb, paths, rest[0])

    elif subcmd == "migrate-legacy":
        # [goal_output_directory_and_execution_phase_redesign_plan.md Stage 9]
        # /agent goals migrate-legacy <id>
        if not rest:
            R.print_error("Usage: /agent goals migrate-legacy <goal_id>")
            return
        _cmd_migrate_legacy(gb, paths, rest[0])

    elif subcmd == "status":
        _cmd_loop_status(agent, paths)

    elif subcmd == "reset-step":
        # [daemon_autonomous_state_recovery_plan.md 阶段二]
        # /agent goals reset-step <exec_id> <step_idx> [reason...]
        if len(rest) < 2:
            R.print_error("Usage: /agent goals reset-step <exec_id> <step_idx> [reason]")
            return
        _cmd_reset_step(agent, paths, rest[0], rest[1], " ".join(rest[2:]) if len(rest) > 2 else "")

    elif subcmd == "judge-calibration":
        # [next_doc/autonomous_execution_stability_and_self_learning_integration_plan.md
        # 方案 D.4 后半段 / 阶段 4] /agent goals judge-calibration
        # 只读展示判官校准建议报告，不会自动修改任何配置或 prompt。
        _cmd_judge_calibration(paths)

    elif subcmd == "tree":
        # [next_doc/goal_tree_system_plan.md §4.5] /agent goals tree [root_id]
        # 文本树形打印，root_id 省略时用全局根节点（level=ultimate）。
        _cmd_tree(gb, rest[0] if rest else None)

    elif subcmd == "decompose":
        # [next_doc/goal_tree_system_plan.md §4.2/§4.5]
        # /agent goals decompose <id> [--force]
        if not rest:
            R.print_error("Usage: /agent goals decompose <id> [--force]")
            return
        force = "--force" in rest[1:]
        _cmd_decompose(gb, paths, rest[0], force=force)

    elif subcmd == "candidates":
        # /agent goals candidates <id> accept|reject <candidate_id>
        if len(rest) < 3 or rest[1] not in ("accept", "reject"):
            R.print_error("Usage: /agent goals candidates <id> accept|reject <candidate_id>")
            return
        _cmd_candidates(gb, paths, rest[0], rest[1], rest[2])

    elif subcmd == "focus":
        # [next_doc/goal_tree_system_plan.md §4.3 阶段三]
        # /agent goals focus <id> | /agent goals focus pin|unpin <node_id> <child_id>
        if not rest:
            R.print_error(
                "Usage: /agent goals focus <id> "
                "| /agent goals focus pin|unpin <node_id> <child_id>"
            )
            return
        if rest[0] in ("pin", "unpin"):
            if len(rest) < 3:
                R.print_error(f"Usage: /agent goals focus {rest[0]} <node_id> <child_id>")
                return
            _cmd_focus_pin(gb, rest[1], rest[2], pinned=(rest[0] == "pin"))
        else:
            _cmd_focus_show(gb, rest[0])

    else:
        R.print_error(f"Unknown subcommand: {subcmd!r}")
        R.print_info(
            "Available: list, add, obj add, done, abandon, accept, reject, pause, "
            "progress, feedback, recur, unrecur, migrate-legacy, spec, phase, diagnose, "
            "tune, status, reset-step, judge-calibration, tree, decompose, candidates, focus"
        )


# ── 子命令实现 ─────────────────────────────────────────────────────────────────

def _cmd_judge_calibration(paths) -> None:
    """[next_doc/autonomous_execution_stability_and_self_learning_integration_plan.md
    方案 D.4 后半段 / 阶段 4] 展示判官校准建议报告——只读，纯统计视角，
    不会自动修改任何配置或 prompt。"""
    from mini_agent.role_agents.judge_calibration import generate_calibration_suggestions
    report = generate_calibration_suggestions(paths)
    R.console.print(report)


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


def _cmd_add_goal(gb, args: list[str], paths=None) -> None:
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

    # [personal_researcher_and_coach_capability_gap_plan.md C2] 命中高
    # 置信度价值取向模式时的参照提示，只提示不阻断；画像不存在（最常见
    # 情况，见 sys:decision_profile_update 默认关闭）时静默跳过。
    if paths is not None:
        try:
            from mini_agent.evolution.decision_profile_builder import match_goal_against_profile
            hint = match_goal_against_profile(paths, title)
            if hint:
                R.print_info(f"💡 这个方向和你过去反复表现出的「{hint.get('pattern', '')}」倾向一致（仅供参考）。")
        except Exception:
            pass


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
    """更新节点状态。

    [goal_cron_status_integrity_and_self_healing_plan.md] 写入前先校验：
    周期性 Goal 不允许通过本命令（`/agent goals done|pause`）直接写成
    `active`/`paused`/`abandoned` 以外的状态，防止重蹈"agent 误把父 Goal
    标记为 completed，导致周期性从此静默停摆"的覆辙。
    """
    node = gb.get(node_id)
    if not node:
        R.print_error(f"Not found: {node_id!r}")
        return
    from mini_agent.perception.goal_backlog import validate_status_write_for_recurring_goal
    reject_reason = validate_status_write_for_recurring_goal(node, status)
    if reject_reason:
        R.print_error(reject_reason)
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


def _cmd_migrate_legacy(gb, paths, goal_id: str) -> None:
    """[goal_output_directory_and_execution_phase_redesign_plan.md Stage 9]
    请求下一次触发时附加一次"历史数据迁移"任务，只打一次性标记，不立即
    执行——实际迁移工作由下一次真正触发（自然到期，或 daemon 模式下用
    `/cron run <job_id>` 手动立即触发）时的 agent 完成。
    """
    node = gb.get(goal_id)
    if node is None or not node.is_goal:
        R.print_error(f"Goal 不存在：{goal_id}")
        return
    if not node.recurring:
        R.print_error(f"Goal {goal_id} 不是周期性 Goal，历史数据迁移任务只对周期性 Goal 生效。")
        return

    from mini_agent.evolution import output_workspace as ow
    if not ow.has_legacy_cycle_dirs(paths, goal_id):
        R.print_info(f"未检测到 Goal {goal_id} 名下有旧模型（cycle_NNNN/）遗留目录，无需迁移。")
        return

    gb.update_fields(goal_id, legacy_migration_requested=True)
    R.print_success(f"已标记：Goal {goal_id} 下一次触发时会附加一次历史数据迁移任务。")
    if node.recurrence_cron_job_id:
        R.print_info(f"daemon 模式下可用 /cron run {node.recurrence_cron_job_id} 立即触发这一轮，或等待下次自然触发。")


def _cmd_spec(gb, paths, action: str, rest: list[str]) -> None:
    """[goal_execution_spec_generation_plan.md §6.4]
    /agent goals spec generate <goal_id> [--template <id>] [--from-history]
    /agent goals spec confirm <goal_id>
    /agent goals spec show <goal_id>
    /agent goals spec close-check <goal_id>
    """
    if action == "generate":
        if not rest:
            R.print_error("Usage: /agent goals spec generate <goal_id> [--template <id>] [--from-history] [--mode llm|agent|auto]")
            return
        import argparse
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("goal_id")
        p.add_argument("--template", default=None)
        p.add_argument("--from-history", action="store_true")
        p.add_argument("--mode", default=None, choices=["llm", "agent", "auto"])
        try:
            parsed = p.parse_args(rest)
        except SystemExit:
            R.print_error("Usage: /agent goals spec generate <goal_id> [--template <id>] [--from-history] [--mode llm|agent|auto]")
            return
        _cmd_spec_generate(gb, paths, parsed.goal_id, parsed.template, parsed.from_history, parsed.mode)

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

    elif action == "close-check":
        if not rest:
            R.print_error("Usage: /agent goals spec close-check <goal_id> [--use-agent | --no-agent]")
            return
        import argparse
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("goal_id")
        p.add_argument("--use-agent", dest="use_agent", action="store_true", default=None)
        p.add_argument("--no-agent", dest="use_agent", action="store_false")
        try:
            parsed = p.parse_args(rest)
        except SystemExit:
            R.print_error("Usage: /agent goals spec close-check <goal_id> [--use-agent | --no-agent]")
            return
        _cmd_spec_close_check(gb, paths, parsed.goal_id, parsed.use_agent)

    else:
        R.print_error(f"Unknown spec subcommand: {action!r}")
        R.print_info("Available: generate, confirm, show, close-check")


def _cmd_spec_generate(gb, paths, goal_id: str, template_id: Optional[str], from_history: bool,
                        mode: Optional[str] = None) -> None:
    """`mode` 支持单次覆盖配置文件里的 `goal_execution_spec.builder_mode`
    （不传时回退配置默认值 "auto"），对应方案 §6.4 与
    implementation_record.md §7.5/§9 未实施清单第 2 条"CLI/看板未暴露单次
    覆盖 mode 的入口"——现已补上。"""
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

        builder = ges.GoalExecutionSpecBuilder(cfg, mode=mode)
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
        path_label = {"llm": "纯 LLM", "agent": "只读探索 Agent"}.get(builder.last_effective_path, builder.last_effective_path)
        R.print_success(f"已生成执行规范草稿（第 {spec.version} 版，走 {path_label} 路径），未确认，不影响执行。")
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


def _cmd_spec_close_check(gb, paths, goal_id: str, use_agent: Optional[bool] = None) -> None:
    """[goal_execution_spec_generation_plan.md §5 第二段] 手动（重新）触发一次
    "整体是否可以关闭"判定。正常情况下这个判定在最后一个子 Objective 正常
    完成时由 `ObjectiveExecutor._maybe_close_parent_goal()` 自动触发一次；
    本命令是补充入口，用于：
      - 上一次自动判定结果是 `continue`，用户后续手动补充了材料/调整了
        Goal 描述，想不新增子 Objective 就重新判一次；
      - 排查"为什么这个 Goal 一直没有自动关闭"。

    `GoalBacklog.maybe_close_goal_by_overall_criteria()` 自己会重新校验全部
    前置条件（是否一次性 Goal、子节点是否全部终态、规范是否已确认且
    `overall_completion_criteria` 非空），条件不满足时直接告知原因，不会
    误触发。

    use_agent：[implementation_record.md §11 后续建议顺序第 2 条] 单次覆盖
    是否走受限 Agent 路径，`--use-agent`/`--no-agent` 对应 `True`/`False`，
    都不传则为 `None`，回退配置文件 `overall_completion_use_agent`（与
    Stage 8 `--mode` 单次覆盖同一风格，不修改配置文件）。
    """
    node = gb.get(goal_id)
    if node is None or not node.is_goal:
        R.print_error(f"Goal 不存在：{goal_id}")
        return
    if node.status != "active":
        R.print_info(f"Goal 当前状态为 {node.status!r}，不是 active，跳过判定。")
        return
    try:
        from mini_agent.config import load_config
        cfg = load_config()
        outcome = gb.maybe_close_goal_by_overall_criteria(goal_id, cfg, use_agent=use_agent)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.commands.goals._cmd_spec_close_check')
        R.print_error(f"整体完成判定失败：{e}")
        return

    if outcome is None:
        R.print_info(
            "未触发判定：可能不是一次性 Goal、还有子 Objective 未进入终态、"
            "执行规范未确认，或 overall_completion_criteria 为空。"
        )
        return

    updated = gb.get(goal_id)
    last_check = getattr(updated, "overall_completion_last_check", None) if updated else None
    path_label = "只读探索 Agent" if (last_check or {}).get("used_agent") else "纯 LLM"
    if outcome == "closed":
        R.print_success(f"判定为整体已完成（走 {path_label} 路径），Goal 已标记为 completed：{goal_id}")
    else:
        R.print_info(f"判定为暂不关闭（走 {path_label} 路径，继续保持 active）：{goal_id}，详见 progress notes。")


def _cmd_phase(paths, action: str, rest: list[str]) -> None:
    """[goal_execution_phase_improvement_plan.md §5] /agent goals phase 子命令。"""
    from mini_agent.perception import execution_phase as ep

    if action == "show":
        if not rest:
            R.print_error("Usage: /agent goals phase show <goal_id>")
            return
        state = ep.load_phase(paths, rest[0])
        lock_txt = "locked" if state.locked else "unlocked"
        R.print_info(
            f"Goal {state.goal_id} execution phase: {state.mode} ({lock_txt}), "
            f"stability_score={state.stability_score:.2f}, cycles_in_mode={state.cycles_in_mode}"
        )
        if state.mode_history:
            R.print_info("Recent transitions:")
            for m in state.mode_history[-5:]:
                R.print_info(f"  {m.from_mode} -> {m.to_mode} ({m.reason})")
        return

    if action == "set":
        if len(rest) < 2:
            R.print_error("Usage: /agent goals phase set <goal_id> explore|converge|stable|tidy|auto [--lock]")
            return
        goal_id, mode = rest[0], rest[1]
        lock_flag = "--lock" in rest[2:]
        try:
            state = ep.set_mode(paths, goal_id, mode, lock=True if lock_flag else None, reason="cli_set")
        except ValueError as e:
            R.print_error(str(e))
            return
        R.print_success(f"Goal {goal_id} execution phase set to {state.mode} (locked={state.locked})")
        return

    if action == "unlock":
        if not rest:
            R.print_error("Usage: /agent goals phase unlock <goal_id>")
            return
        state = ep.unlock_mode(paths, rest[0])
        R.print_success(f"Goal {rest[0]} execution phase unlocked (mode={state.mode})")
        return

    R.print_error(f"Unknown phase action: {action!r}. Use show/set/unlock.")


def _get_llm_ask(agent):
    """[Stage 3] 把 `agent.llm_helper.ask()` 包成 `Callable[[str], str]`，
    与 `growth_cmd.py::_get_llm_helper` 同一约定。拿不到 agent/helper 时
    返回 None，调用方据此走"无 LLM"的默认降级路径。
    """
    if agent is None:
        return None
    helper = getattr(agent, "llm_helper", None)
    if helper is None:
        return None
    return lambda prompt: helper.ask(prompt)


def _cycle_tuning_cfg(agent):
    """[Stage 3] 拿 `cfg.cycle_tuning`（两个 LLM 增强层开关）。拿不到 cfg
    时返回 `CycleTuningConfig()` 默认值（两个开关都是 False，等价于
    "未显式开启"）。"""
    from mini_agent.config.models import CycleTuningConfig
    cfg = getattr(agent, "cfg", None)
    return getattr(cfg, "cycle_tuning", None) or CycleTuningConfig()


def _cmd_diagnose(gb, paths, goal_id: str, *, agent=None, summarize: bool = False) -> None:
    """[goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md Stage 1/3]
    /agent goals diagnose <goal_id> [--summarize] — 打印跨轮次诊断报告。
    纯只读展示；`--summarize` 是 Stage 3 的可选 LLM 摘要层，需要配置开关
    `cycle_tuning.diagnostics_llm_summary_enabled=True` 才会真正调用 LLM，
    否则明确提示"该功能未开启"而不是静默忽略这个 flag。
    """
    from mini_agent.perception.cycle_diagnostics import build_cycle_diagnostics

    report = build_cycle_diagnostics(paths, gb, goal_id)
    if not report.found:
        R.print_error(report.error or f"Goal '{goal_id}' not found")
        return

    R.print_info(f"📋 诊断报告：{report.goal_title} ({report.goal_id})")
    recur_txt = f"周期性 (schedule={report.schedule})" if report.recurring else "一次性"
    R.print_info(f"  状态: {report.status}  |  {recur_txt}  |  已完成轮次: {report.cycle_count}")

    lock_txt = "locked" if report.execution_phase_locked else "unlocked"
    R.print_info(f"  执行阶段: {report.execution_phase_mode} ({lock_txt})")
    if report.phase_history_summary:
        R.print_info("  最近阶段变迁:")
        for m in report.phase_history_summary[-5:]:
            R.print_info(f"    {m['from']} -> {m['to']} ({m['reason']})")

    if report.cron_health:
        ch = report.cron_health
        R.print_info(
            f"  Cron 健康: run_count={ch.get('run_count')}, "
            f"consecutive_skip_count={ch.get('consecutive_skip_count')}, "
            f"enabled={ch.get('enabled')}"
        )

    # [诊断展示补全] task_template 是白名单调优参数之一，但此前诊断报告
    # 打印时完全没展示——用户想调优前得先知道"现在生效的到底是哪段文本"，
    # 不然只能凭记忆或者去翻 cron_jobs.json。report.task_template 字段
    # 后端早就有（build_cycle_diagnostics 里从绑定的 cron job 读出），
    # 只是没被打印过；这里补上，不新增任何后端逻辑。
    if report.task_template:
        R.print_info("  当前 task_template（cron 触发时注入的任务描述）:")
        for line in report.task_template.splitlines():
            R.print_info(f"    {line}")

    if report.recent_health_alerts:
        R.print_info("  ⚠️  健康告警:")
        for a in report.recent_health_alerts:
            R.print_info(f"    {a['message']}")

    if report.recent_cycle_summaries:
        R.print_info(f"  最近轮次（最多显示 {len(report.recent_cycle_summaries)} 条）:")
        for s in report.recent_cycle_summaries[-10:]:
            tag = " [archived]" if s.get("archived") else ""
            R.print_info(
                f"    cycle={s.get('cycle')} status={s.get('status')} "
                f"artifacts={s.get('artifact_count')}{tag}  {s.get('task_summary', '')[:60]}"
            )

    R.print_info(f"  产出目录: {report.output_dir}")
    if report.progress_notes_tail:
        R.print_info("  最近进展记录:")
        for line in report.progress_notes_tail.splitlines()[-5:]:
            R.print_info(f"    {line}")

    if report.mechanism_notes:
        R.print_info("  机制说明:")
        for note in report.mechanism_notes:
            R.print_info(f"    - {note}")

    if summarize:
        cfg = _cycle_tuning_cfg(agent)
        if not getattr(cfg, "diagnostics_llm_summary_enabled", False):
            R.print_info(
                "  （--summarize 需要先在配置里开启 "
                "cycle_tuning.diagnostics_llm_summary_enabled，当前未开启，跳过。）"
            )
        else:
            from mini_agent.perception.cycle_diagnostics import summarize_report_with_llm
            llm_ask = _get_llm_ask(agent)
            summary = summarize_report_with_llm(report, llm_ask)
            if summary:
                R.print_info("  🤖 自然语言总结:")
                R.print_info(f"    {summary}")
            else:
                R.print_info("  （未能生成自然语言总结，仅展示以上结构化字段。）")


def _print_tuning_proposal(proposal) -> None:
    R.print_info(f"调优草案 {proposal.id}（status={proposal.status}, source={proposal.source}）")
    for c in proposal.proposed_changes:
        R.print_info(f"  {c.param}: {c.from_value!r} -> {c.to_value!r}")
        if c.reason:
            R.print_info(f"    原因: {c.reason}")
    if proposal.status == "applied" and proposal.apply_results:
        R.print_info("  应用结果:")
        for r in proposal.apply_results:
            mark = "✓" if r["ok"] else "✗"
            R.print_info(f"    {mark} {r['param']} -> {r['to']}: {r['detail']}")
    if proposal.status == "rejected" and proposal.reject_reason:
        R.print_info(f"  拒绝原因: {proposal.reject_reason}")


def _cmd_tune(gb, paths, goal_id: str, rest: list[str], *, agent=None) -> None:
    """[goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md Stage 2/3]
    /agent goals tune <goal_id> <param>=<value> [...] [--reason <text>]
    /agent goals tune <goal_id> "<自然语言改进意见>"  （Stage 3，无 '=' 时）
    /agent goals tune suggest <goal_id>
    /agent goals tune list <goal_id>
    /agent goals tune confirm <goal_id> <proposal_id>
    /agent goals tune apply <goal_id> <proposal_id>
    /agent goals tune reject <goal_id> <proposal_id> [reason...]

    注意第一个位置参数的双重语义：既可能是子动作（suggest/list/confirm/
    apply/reject），也可能直接是 goal_id（生成草案的默认形式）。用是否
    匹配已知子动作名来消歧，与项目里其它"动词可省略"的命令风格一致。
    """
    from mini_agent.perception import cycle_tuning as ct

    known_actions = ("suggest", "list", "confirm", "apply", "reject")
    if goal_id in known_actions:
        action = goal_id
        if not rest:
            R.print_error(f"Usage: /agent goals tune {action} <goal_id> [...]")
            return
        target_goal_id = rest[0]
        action_rest = rest[1:]
    else:
        action = "create"
        target_goal_id = goal_id
        action_rest = rest

    node = gb.get(target_goal_id)
    if node is None or not node.is_goal:
        R.print_error(f"Goal 不存在：{target_goal_id}")
        return

    if action == "suggest":
        from mini_agent.perception.cycle_diagnostics import build_cycle_diagnostics
        report = build_cycle_diagnostics(paths, gb, target_goal_id)
        suggestion = ct.suggest_tuning_from_diagnostics(report)
        if suggestion is None:
            R.print_info("当前没有基于诊断报告规则触发的调优建议。")
            return
        ct.save_proposal(paths, suggestion)
        R.print_success("已生成规则触发的调优草案：")
        _print_tuning_proposal(suggestion)
        R.print_info(f"确认：/agent goals tune confirm {target_goal_id} {suggestion.id}")
        return

    if action == "list":
        proposals = ct.list_proposals(paths, target_goal_id)
        if not proposals:
            R.print_info("该 Goal 还没有任何调优草案。")
            return
        for p in proposals:
            R.print_info(f"  {p.id}  status={p.status}  source={p.source}  changes={len(p.proposed_changes)}")
        return

    if action == "confirm":
        if not action_rest:
            R.print_error(f"Usage: /agent goals tune confirm {target_goal_id} <proposal_id>")
            return
        try:
            proposal = ct.confirm_tuning_proposal(paths, target_goal_id, action_rest[0])
        except ValueError as e:
            R.print_error(str(e))
            return
        R.print_success(f"草案 {proposal.id} 已确认（仍未生效）。")
        R.print_info(f"应用：/agent goals tune apply {target_goal_id} {proposal.id}")
        return

    if action == "apply":
        if not action_rest:
            R.print_error(f"Usage: /agent goals tune apply {target_goal_id} <proposal_id>")
            return
        try:
            from mini_agent.evolution.cron_scheduler import load_cron_scheduler
            cs = load_cron_scheduler(paths)
            spec_builder_cfg = None
            try:
                from mini_agent.config import load_config
                spec_builder_cfg = load_config()
            except Exception:
                spec_builder_cfg = None
            proposal = ct.apply_tuning_proposal(
                paths, gb, cs, target_goal_id, action_rest[0], spec_builder_cfg=spec_builder_cfg,
            )
        except ValueError as e:
            R.print_error(str(e))
            return
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.cli.commands.goals._cmd_tune.apply')
            R.print_error(f"应用失败：{e}")
            return
        R.print_success(f"草案 {proposal.id} 已应用。")
        _print_tuning_proposal(proposal)
        return

    if action == "reject":
        if not action_rest:
            R.print_error(f"Usage: /agent goals tune reject {target_goal_id} <proposal_id> [reason...]")
            return
        reason = " ".join(action_rest[1:]) if len(action_rest) > 1 else ""
        try:
            proposal = ct.reject_tuning_proposal(paths, gb, target_goal_id, action_rest[0], reason)
        except ValueError as e:
            R.print_error(str(e))
            return
        R.print_success(f"草案 {proposal.id} 已拒绝。")
        return

    # action == "create"：解析 <param>=<value> [...] [--reason <text>]
    reason = ""
    kv_args = []
    i = 0
    while i < len(action_rest):
        tok = action_rest[i]
        if tok == "--reason":
            reason = " ".join(action_rest[i + 1:])
            break
        kv_args.append(tok)
        i += 1
    if not kv_args:
        R.print_error(
            f"Usage: /agent goals tune {target_goal_id} <param>=<value> [...] [--reason <text>]\n"
            f"可选参数：{', '.join(ct.WHITELIST_PARAMS)}"
        )
        return

    # [Stage 3] 没有任何一项包含 '='，按自然语言改进意见处理（不是格式
    # 错误）——只要开关开启且能拿到 LLM，就尝试解析成白名单参数改动；
    # 解析失败/开关未开启则明确提示改用 param=value，不静默失败。
    if all("=" not in kv for kv in kv_args):
        nl_text = " ".join(kv_args)
        cfg = _cycle_tuning_cfg(agent)
        llm_ask = _get_llm_ask(agent) if getattr(cfg, "tuning_llm_parse_enabled", False) else None
        if llm_ask is None:
            R.print_error(
                "未识别到 param=value 格式，也无法按自然语言解析（需要先开启 "
                "cycle_tuning.tuning_llm_parse_enabled 配置且有可用的 LLM）。\n"
                f"请改用：/agent goals tune {target_goal_id} <param>=<value> [...]\n"
                f"可选参数：{', '.join(ct.WHITELIST_PARAMS)}"
            )
            return
        from mini_agent.perception.cycle_diagnostics import build_cycle_diagnostics
        report = build_cycle_diagnostics(paths, gb, target_goal_id)
        proposal = ct.build_tuning_proposal_from_nl(target_goal_id, nl_text, report, llm_ask)
        if proposal is None:
            R.print_error(
                "无法把这条意见解析成白名单参数改动，请改用具体命令："
                f"/agent goals tune {target_goal_id} <param>=<value>"
            )
            return
        ct.save_proposal(paths, proposal)
        R.print_success("已根据自然语言意见生成调优草案（请仔细核对 diff 后再确认）：")
        _print_tuning_proposal(proposal)
        R.print_info(f"确认：/agent goals tune confirm {target_goal_id} {proposal.id}")
        return

    changes = []
    for kv in kv_args:
        if "=" not in kv:
            R.print_error(f"格式错误（需要 param=value）：{kv!r}")
            return
        param, value = kv.split("=", 1)
        changes.append({"param": param, "to": value, "reason": reason})
    try:
        proposal = ct.build_tuning_proposal(target_goal_id, changes, source="user_request")
    except ct.WhitelistViolation as e:
        R.print_error(str(e))
        return
    except ValueError as e:
        R.print_error(str(e))
        return
    ct.save_proposal(paths, proposal)
    R.print_success("已生成调优草案：")
    _print_tuning_proposal(proposal)
    R.print_info(f"确认：/agent goals tune confirm {target_goal_id} {proposal.id}")


def _format_ago(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}m"
    else:
        return f"{seconds/3600:.1f}h"


_LEVEL_ICON = {
    "ultimate": "🌍", "domain": "🧭", "stage": "📅", "goal": "🎯", "objective": "📌",
}


def _cmd_tree(gb, root_id: Optional[str]) -> None:
    """[next_doc/goal_tree_system_plan.md §4.5] 文本树形打印，root_id 省略
    时用全局根节点。缩进表达层级深度，节点标题前带 level 图标 + 状态，
    候选分解（decompose_candidates）以 "待确认" 前缀单独列在子节点下面。
    """
    tree = gb.get_tree(root_id)
    if tree is None:
        if root_id is None:
            R.print_info("目标树还没有根节点，用 /agent goals add 创建第一个 Goal 后，"
                          "或直接创建 ultimate 节点后即可查看。")
        else:
            R.print_error(f"节点不存在：{root_id}")
        return

    def _print(entry: dict, depth: int) -> None:
        node = entry["node"]
        icon = _LEVEL_ICON.get(node.level, "•")
        indent = "  " * depth
        focus_mark = " ⭐" if node.current_focus_ids else ""
        R.console.print(f"{indent}{icon} [{node.status}] {node.title} ({node.id}){focus_mark}")
        for cand in node.decompose_candidates:
            R.console.print(f"{indent}  ┊ 待确认候选：{cand.get('title', '')} "
                             f"[{cand.get('level', '?')}] ({cand.get('id', '')})")
        for child in entry["children"]:
            _print(child, depth + 1)

    _print(tree, 0)


def _cmd_decompose(gb, paths, node_id: str, *, force: bool = False) -> None:
    """[next_doc/goal_tree_system_plan.md §4.2/§4.5] /agent goals decompose <id>

    手动触发一次分解建议（§4.2 触发时机 3）。生成的候选落进节点的
    decompose_candidates，通过 `/agent goals candidates <id> accept|reject
    <candidate_id>` 处理。
    """
    node = gb.get(node_id)
    if node is None:
        R.print_error(f"节点不存在：{node_id}")
        return
    try:
        from mini_agent.perception.goal_tree_decomposer import GoalTreeDecomposer
        decomposer = GoalTreeDecomposer(paths, gb)
        if not force:
            skip_reason = decomposer.should_decompose(node)
            if skip_reason:
                R.print_warning(f"跳过：{skip_reason}（可加 --force 强制触发）")
                return
        candidates = decomposer.decompose(node_id, force=force)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.commands.goals._cmd_decompose')
        R.print_error(f"分解失败：{e}")
        return

    if not candidates:
        R.print_info("本次没有生成新的候选（可能是 LLM 判断该节点已经足够具体）。")
        return
    R.print_success(f"生成了 {len(candidates)} 个候选，等待确认：")
    for cand in candidates:
        R.print_info(f"  [{cand['level']}] {cand['title']} —— {cand.get('description', '')} "
                      f"(id={cand['id']})")
    R.print_info(f"确认：/agent goals candidates {node_id} accept <candidate_id>")
    R.print_info(f"忽略：/agent goals candidates {node_id} reject <candidate_id>")


def _cmd_candidates(gb, paths, node_id: str, action: str, candidate_id: str) -> None:
    """/agent goals candidates <id> accept|reject <candidate_id>"""
    if action == "accept":
        new_node = gb.accept_candidate(node_id, candidate_id)
        if new_node is None:
            R.print_error(f"候选不存在，或节点/层级不合法：node={node_id} candidate={candidate_id}")
            return
        R.print_success(f"已采纳，创建了新节点 [{new_node.level}] {new_node.title} ({new_node.id})")
        return

    # reject
    try:
        from mini_agent.perception.goal_tree_decomposer import GoalTreeDecomposer
        decomposer = GoalTreeDecomposer(paths, gb)
        ok = decomposer.reject_candidate(node_id, candidate_id)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.commands.goals._cmd_candidates')
        R.print_error(f"忽略失败：{e}")
        return
    if not ok:
        R.print_error(f"候选不存在：node={node_id} candidate={candidate_id}")
        return
    R.print_success("已忽略，30 天内不会再对该节点生成同主题候选。")


def _cmd_focus_show(gb, node_id: str) -> None:
    """[next_doc/goal_tree_system_plan.md §4.3/§4.5] /agent goals focus <id>

    展示某节点当前的 current_focus_ids/focus_pinned_ids，附上直接子节点
    标题（避免用户拿到一串 id 还要再手动查一遍）。
    """
    node = gb.get(node_id)
    if node is None:
        R.print_error(f"节点不存在：{node_id}")
        return
    if node.level not in ("ultimate", "domain", "stage"):
        R.print_warning(
            f"[{node.level}] 节点不参与 current_focus_ids 计算"
            "（只有 ultimate/domain/stage 三层非叶子节点才有此字段）。"
        )

    def _title(cid: str) -> str:
        child = gb.get(cid)
        return child.title if child is not None else "（节点已不存在）"

    R.print_info(f"[{node.level}] {node.title} ({node.id})")
    if node.current_focus_ids:
        R.print_info("现阶段焦点（current_focus_ids）：")
        for cid in node.current_focus_ids:
            pin_mark = " 📌" if cid in node.focus_pinned_ids else ""
            R.console.print(f"  - {_title(cid)} ({cid}){pin_mark}")
    else:
        R.print_info("现阶段焦点为空（没有子节点，或子节点已全部进入终态——"
                      "该节点可能会被停滞巡检捕获去生成新的分解候选）。")
    if node.focus_pinned_ids:
        R.print_info("已手动 pin（📌 上面已标出，此处不重复列出坐标）：" +
                      "、".join(_title(cid) for cid in node.focus_pinned_ids))


def _cmd_focus_pin(gb, node_id: str, child_id: str, *, pinned: bool) -> None:
    """/agent goals focus pin|unpin <node_id> <child_id>"""
    ok = gb.set_focus_pin(node_id, child_id, pinned)
    if not ok:
        R.print_error(
            f"操作失败：节点不存在，或 {child_id} 不是 {node_id} 的直接子节点。"
        )
        return
    action = "pin" if pinned else "unpin"
    R.print_success(f"已{'📌 pin' if pinned else '取消 pin'}：{child_id} "
                     f"({action})，current_focus_ids 已立即重算。")


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
