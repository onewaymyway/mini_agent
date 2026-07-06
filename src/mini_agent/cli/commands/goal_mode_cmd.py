"""
cli/commands/goal_mode_cmd.py — /goal slash 命令处理

子命令：
  /goal <目标文本>       — 开始一次新的 Goal 协商（生成验收标准草案，进入确认子对话）
  /goal resume [sid]     — 恢复上次未完成的 goal（sid 可省略，省略时自动找最近一个）
  /goal list             — 列出所有可恢复的 goal 任务（status==running，可能不止一个）
  /goal status           — 查看当前 session 是否有 goal 状态记录
  /goal cancel           — 取消当前 session 记录的 goal 状态（不会中断正在运行的循环，
                            仅用于清理一个已经卡住/不想再恢复的记录）

协商子对话内部命令（在 "/goal <文本>" 触发的确认循环里输入）：
  /confirm               — 确认当前版本的验收标准，冻结并开始执行
  /cancel                — 放弃本次协商
  其他任意文本            — 视为对当前版本的修改意见，重新生成下一版
"""

from __future__ import annotations

from typing import Optional

import mini_agent.ui.renderer as R


def handle_goal_cmd(args: list[str], agent) -> None:
    if not getattr(agent.cfg, "goal_mode", None) or not agent.cfg.goal_mode.enabled:
        R.print_error(
            "Goal 模式未启用。请在 agent_config.json 中设置 goal_mode.enabled=true 后重启。"
        )
        return

    if not args:
        R.print_error(
            "用法：\n"
            "  /goal <目标文本>     开始一个新目标\n"
            "  /goal resume [sid]  恢复未完成的目标\n"
            "  /goal list          列出所有可恢复的目标（可能不止一个）\n"
            "  /goal status        查看当前 goal 状态\n"
            "  /goal cancel        清理当前 session 的 goal 状态记录"
        )
        return

    sub = args[0].lower()
    if sub == "resume":
        _handle_resume(args[1:], agent)
        return
    if sub == "status":
        _handle_status(agent)
        return
    if sub == "list":
        _handle_list(agent)
        return
    if sub == "cancel":
        _handle_cancel(agent)
        return

    # 否则整句都是目标文本
    goal_text = " ".join(args)
    _handle_new_goal(goal_text, agent)


# ── 新目标：验收标准协商 ────────────────────────────────────────────────────

def _handle_new_goal(goal_text: str, agent) -> None:
    from mini_agent.goal_mode.spec import GoalSpecBuilder

    builder = GoalSpecBuilder(agent.cfg)
    R.print_info("[Goal 模式] 正在根据你的描述生成验收标准草案…")
    try:
        spec = builder.build_initial(goal_text)
    except Exception as e:
        R.print_error(f"生成验收标准失败：{e}")
        return

    spec = _negotiate_loop(builder, spec, agent)
    if spec is None:
        R.print_info("[Goal 模式] 已放弃本次目标协商。")
        return

    _run_goal(agent, spec)


def _negotiate_loop(builder, spec, agent):
    """展示 GoalSpec 草案，循环收集用户反馈直到 /confirm 或 /cancel。

    返回确认后的 GoalSpec，或 None（用户取消）。
    这是一个独立的会话态子循环，不会把协商过程写入主 Agent 历史。
    """
    from mini_agent.ui.terminal import term as _term

    while True:
        R.console.print(spec.render_summary_for_user())
        R.print_info(
            "输入 [bold]/confirm[/bold] 确认并开始执行，"
            "输入修改意见继续调整草案，输入 [bold]/cancel[/bold] 放弃。"
        )
        try:
            user_input = _term.prompt_user()
        except (KeyboardInterrupt, EOFError):
            return None

        user_input = (user_input or "").strip()
        if not user_input:
            continue
        if user_input in ("/confirm", "confirm", "确认"):
            spec.confirmed = True
            return spec
        if user_input in ("/cancel", "cancel", "取消", "算了"):
            return None

        R.print_info("[Goal 模式] 正在根据你的反馈修订验收标准…")
        try:
            new_spec = builder.revise(spec, user_input)
        except Exception as e:
            R.print_error(f"修订失败：{e}，请重试或输入 /cancel 放弃。")
            continue

        R.console.print(builder.diff_summary(spec, new_spec))
        spec = new_spec


# ── 执行 ─────────────────────────────────────────────────────────────────

def _run_goal(agent, spec) -> None:
    from mini_agent.goal_mode.runner import GoalRunner

    runner = GoalRunner(agent=agent, cfg=agent.cfg, goal_spec=spec)
    R.print_info(
        f"[Goal 模式] 开始执行（最多 {agent.cfg.goal_mode.max_rounds} 轮），"
        "过程中可以 Ctrl-C 中断（会保留状态，之后可用 /goal resume 续跑）。"
    )
    try:
        result = runner.run()
    except KeyboardInterrupt:
        runner.pause()
        R.print_warning("[Goal 模式] 已中断，状态已保存，可用 /goal resume 继续。")
        return
    except Exception as e:
        R.print_error(f"[Goal 模式] 执行异常终止：{e}")
        if agent.cfg.verbose:
            import traceback
            traceback.print_exc()
        return

    R.console.print(f"\n[bold]Goal 执行结果：[/bold] {result.status}")
    R.console.print(f"轮次：{result.rounds_used}  compact 次数：{result.compacts_done}")
    R.console.print(result.final_report)


# ── resume ───────────────────────────────────────────────────────────────

def _report_no_resumable(project_root) -> None:
    """没找到可恢复的 goal 时，打印具体原因，而不是只说一句"没找到"。"""
    from mini_agent.goal_mode.state import scan_goal_states
    from mini_agent.storage.paths import AgentPaths

    paths = AgentPaths(project_root=project_root)
    R.print_info(
        f"没有找到可恢复的 goal 任务。（扫描目录：{paths.sessions_dir}）"
    )
    records = scan_goal_states(project_root)
    if not records:
        R.print_info(
            "该目录下没有任何 goal_state.json 记录——如果你确定之前跑过 goal，"
            "请检查是否在跟当时相同的项目目录下启动（`--project` 参数 / 当前工作目录是否一致）。"
        )
        return
    R.print_info(f"找到 {len(records)} 条 goal_state.json 记录，但状态都不是 running：")
    for r in records:
        if r.get("error"):
            R.print_warning(f"  session={r.get('session_id')}  {r['error']}")
        else:
            R.console.print(
                f"  session={r['session_id']}  status={r['status']}  round={r['round']}"
            )


def _handle_resume(args: list[str], agent) -> None:
    from mini_agent.goal_mode.state import find_resumable_session, GoalStateStore, scan_goal_states
    from mini_agent.goal_mode.spec import GoalSpec
    from mini_agent.goal_mode.runner import GoalRunner
    from mini_agent.storage.paths import AgentPaths

    force = "--force" in args
    positional = [a for a in args if a != "--force"]

    project_root = agent.cfg.project_root
    sid = positional[0] if positional else find_resumable_session(project_root)
    if not sid:
        _report_no_resumable(project_root)
        return

    paths = AgentPaths(project_root=project_root)
    store = GoalStateStore(paths, sid)
    state = store.load()
    if state is None:
        R.print_error(f"session {sid} 的 goal_state.json 不存在或已损坏，无法恢复。")
        return
    if state.status != "running" and not force:
        R.print_info(
            f"session {sid} 的 goal 状态是 {state.status}（不是 running），默认不恢复。"
        )
        R.print_info(
            f"如果确认要恢复（比如这是旧版本 Ctrl-C 中断时被误存成 cancelled 的记录），"
            f"加 --force 强制恢复：/goal resume {sid} --force"
        )
        return

    # 若当前 session 不是目标 session，先加载对应历史
    if agent.session_id != sid:
        try:
            agent.load_session(sid)
        except Exception as e:
            R.print_error(f"加载 session {sid} 历史失败：{e}")
            return

    spec = GoalSpec.from_dict(state.goal_spec)
    spec.confirmed = True  # 恢复的 spec 一定是之前已确认过的

    R.print_info(
        f"[Goal 模式] 恢复目标（session={sid}，已完成 {state.round} 轮）："
    )
    R.console.print(spec.render_summary_for_user())

    runner = GoalRunner(
        agent=agent,
        cfg=agent.cfg,
        goal_spec=spec,
        state_store=store,
        resume_state=state,
    )
    try:
        result = runner.run()
    except KeyboardInterrupt:
        runner.pause()
        R.print_warning("[Goal 模式] 已中断，状态已保存，可再次 /goal resume 继续。")
        return
    except Exception as e:
        R.print_error(f"[Goal 模式] 执行异常终止：{e}")
        return

    R.console.print(f"\n[bold]Goal 执行结果：[/bold] {result.status}")
    R.console.print(result.final_report)


def _handle_status(agent) -> None:
    from mini_agent.goal_mode.state import GoalStateStore
    from mini_agent.storage.paths import AgentPaths

    if not agent.session_id:
        R.print_info("当前 session 没有 id，无 goal 状态记录。")
        return
    paths = AgentPaths(project_root=agent.cfg.project_root)
    store = GoalStateStore(paths, agent.session_id)
    state = store.load()
    if state is None:
        R.print_info("当前 session 没有 goal 状态记录。")
        return
    R.console.print(
        f"status={state.status}  round={state.round}  "
        f"compacts_done={state.compacts_done}\n"
        f"最后一次判定：{state.last_judge_status or '(无)'}"
    )


def _handle_cancel(agent) -> None:
    from mini_agent.goal_mode.state import GoalStateStore
    from mini_agent.storage.paths import AgentPaths

    if not agent.session_id:
        R.print_info("当前 session 没有 id，无需清理。")
        return
    paths = AgentPaths(project_root=agent.cfg.project_root)
    store = GoalStateStore(paths, agent.session_id)
    if not store.exists():
        R.print_info("当前 session 没有 goal 状态记录。")
        return
    store.clear()
    R.print_success("已清理当前 session 的 goal 状态记录。")


def _handle_list(agent) -> None:
    """列出所有 status=="running" 的 goal 会话（可能不止一个——比如多个进程各自
    /goal 了不同目标、都被意外杀死的场景），避免只能看到"最近一个"就以为其他的丢了。
    """
    from mini_agent.goal_mode.state import list_resumable_sessions

    sessions = list_resumable_sessions(agent.cfg.project_root)
    if not sessions:
        R.print_info("没有检测到可恢复的 goal 任务（status==running）。")
        return

    R.console.print(f"[bold]检测到 {len(sessions)} 个可恢复的目标任务：[/bold]")
    for s in sessions:
        current_mark = "  [dim](当前 session)[/dim]" if s["session_id"] == agent.session_id else ""
        R.console.print(
            f"  - session: {s['session_id']}  round={s['round']}  "
            f"updated_at={s['updated_at']}{current_mark}"
        )
    R.console.print(
        "\n输入 [bold]/goal resume <session_id>[/bold] 恢复对应的目标。"
    )

