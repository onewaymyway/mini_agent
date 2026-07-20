"""
cli/commands/goal_mode_cmd.py — /goal slash 命令处理

子命令：
  /goal <目标文本>       — 开始一次新的 Goal 协商（生成验收标准草案，进入确认子对话）
  /goal from-history      — 根据当前 session 的历史对话自动归纳目标，
                            生成验收标准草案后进入和 /goal <文本> 相同的确认子对话
  /goal resume [sid]     — 恢复未完成的 goal（sid 可省略：省略时优先继续本 session
                            自己的 goal；本 session 没有可恢复的才全局找最近一个）
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
from datetime import datetime

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
            "  /goal from-history   根据当前 session 历史自动归纳目标并开始协商\n"
            "  /goal resume [sid]  恢复未完成的目标\n"
            "  /goal revise [sid]  基于已冻结的目标（以及上次终止时的重规划提议，若有）重新协商\n"
            "  /goal list          列出所有可恢复的目标（可能不止一个）\n"
            "  /goal status        查看当前 goal 状态\n"
            "  /goal cancel        清理当前 session 的 goal 状态记录"
        )
        return

    sub = args[0].lower()
    if sub == "resume":
        _handle_resume(args[1:], agent)
        return
    if sub == "revise":
        _handle_revise(args[1:], agent)
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
    if sub == "from-history":
        _handle_from_history(agent)
        return

    # 否则整句都是目标文本
    goal_text = " ".join(args)
    _handle_new_goal(goal_text, agent)


# ── 新目标：验收标准协商 ────────────────────────────────────────────────────

def _handle_new_goal(goal_text: str, agent) -> None:
    from mini_agent.goal_mode.spec import GoalSpecBuilder

    builder = GoalSpecBuilder(agent.cfg, parent_session_id=getattr(agent, "session_id", None))
    R.print_info("[Goal 模式] 正在根据你的描述生成验收标准草案…")
    try:
        spec = builder.build_initial(goal_text)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.commands.goal_mode_cmd._handle_new_goal')
        R.print_error(f"生成验收标准失败：{e}")
        return

    spec = _negotiate_loop(builder, spec, agent)
    if spec is None:
        R.print_info("[Goal 模式] 已放弃本次目标协商。")
        return

    _run_goal(agent, spec)


def _handle_from_history(agent) -> None:
    """`/goal from-history`：根据当前 session 的历史对话自动归纳目标。

    复用 GoalSpecBuilder，只是草案来源从"用户这次输入的一句话"换成"从
    agent.history 里归纳"，生成后走和 `_handle_new_goal` 完全相同的
    协商/确认循环，避免两条命令的后续行为出现分叉。
    """
    from mini_agent.goal_mode.spec import GoalSpecBuilder, GoalSpecBuildError

    history = agent.history
    if not history:
        R.print_error(
            "当前 session 还没有任何历史记录，无法自动归纳目标。\n"
            "请先进行一些对话，或改用 /goal <目标文本> 手动指定。"
        )
        return

    builder = GoalSpecBuilder(agent.cfg, parent_session_id=getattr(agent, "session_id", None))
    R.print_info("[Goal 模式] 正在根据当前 session 历史归纳目标…")
    try:
        spec = builder.build_from_history(history)
    except GoalSpecBuildError as e:
        R.print_error(f"根据历史生成目标草案失败：{e}\n请重试一次，或改用 /goal <目标文本> 手动指定。")
        return
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.commands.goal_mode_cmd._handle_from_history')
        R.print_error(f"根据历史归纳目标失败：{e}")
        return

    if not spec.goal_text:
        R.print_error(
            "无法从当前历史中归纳出明确的目标（可能对话内容还不够、或者都是闲聊）。\n"
            "请改用 /goal <目标文本> 手动指定目标。"
        )
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

    daemon 适配：之前这里直接调用 `_term.prompt_user()` 阻塞读本地终端——
    daemon 模式下这段代码是在服务端 AgentRunner 线程里执行的，服务端进程
    通常没有真正连着的本地终端，会导致整个协商永久卡死、远程客户端完全
    看不到这个问题。现在改为通过 mini_agent.interaction.ask() 走双路
    （本地终端 + HTTP 远程客户端），谁先回答就用谁的。

    排查记录：这里"本地"那一路一度直接 sys.stdout.write("\n> ") +
    独立读 stdin，完全绕开了 Terminal 的 _enter_input_mode()/
    _refresh_paused 协调机制——状态栏刷新线程仍在按周期擦除/重绘，
    而这行"裸写"的提示符和用户输入完全不在 Terminal 的 _bar_drawn
    记账范围内，于是被状态栏刷新反复覆盖，表现为"看不到提示符""输入
    内容一闪而过被冲掉"。现在改用 term.interruptible_prompt()——
    和 permissions.py 的 confirm(interrupt_event=...) 走同一套
    enter/exit input mode，行为对称，不再绕开状态栏协调。
    """
    from mini_agent import interaction
    from mini_agent.ui.terminal import get_terminal

    term = get_terminal()

    while True:
        R.console.print(spec.render_summary_for_user())
        R.print_info(
            "输入 [bold]/confirm[/bold] 确认并开始执行，"
            "输入修改意见继续调整草案，输入 [bold]/cancel[/bold] 放弃。"
        )

        def _local_read(interrupt_event):
            line = term.interruptible_prompt("\n> ", interrupt_event)
            if line is None:
                return None
            return {"answer": line}

        result = interaction.ask(
            "goal_negotiation",
            {"summary": spec.render_summary_for_user()},
            _local_read,
        )
        user_input = (result or {}).get("answer") or ""

        user_input = user_input.strip()
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
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.cli.commands.goal_mode_cmd._negotiate_loop')
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
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.commands.goal_mode_cmd._run_goal')
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
    sid = positional[0] if positional else find_resumable_session(
        project_root, from_session_id=agent.session_id
    )
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
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.cli.commands.goal_mode_cmd._handle_resume')
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
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.commands.goal_mode_cmd._handle_resume')
        R.print_error(f"[Goal 模式] 执行异常终止：{e}")
        return

    R.console.print(f"\n[bold]Goal 执行结果：[/bold] {result.status}")
    R.console.print(result.final_report)


def _handle_revise(args: list[str], agent) -> None:
    """[goal_mode_stuck_compact_plan.md §5] `/goal revise [sid]`：基于一个
    已经冻结过的 GoalSpec（不要求 status==running——stuck/max_rounds_exhausted
    终止的目标也可以修订）重新走一遍协商子对话，用户 /confirm 之后重新开始
    执行。

    如果对应 session 落盘的 GoalState 里带有非空 replan_proposal（"confirm"
    档位下 agent 在最后一次卡住恢复机会提出的重规划建议），会先用它作为
    起点调用一次 `GoalSpecBuilder.revise()` 生成一份新草案再进入协商循环——
    用户只需要"确认/继续调整/拒绝"，不需要凭记忆重新组织语言描述问题。
    没有提议时和直接对旧 spec 手动输入修改意见没有区别，进入协商循环后
    用户自己输入反馈即可。
    """
    from mini_agent.goal_mode.state import GoalStateStore, find_resumable_session
    from mini_agent.goal_mode.spec import GoalSpec, GoalSpecBuilder
    from mini_agent.goal_mode.runner import render_replan_proposal
    from mini_agent.storage.paths import AgentPaths

    project_root = agent.cfg.project_root
    sid = args[0] if args else (
        agent.session_id or find_resumable_session(project_root, from_session_id=agent.session_id)
    )
    if not sid:
        R.print_error("没有找到可修订的 goal 状态记录，请指定 session id：/goal revise <session_id>")
        return

    paths = AgentPaths(project_root=project_root)
    store = GoalStateStore(paths, sid)
    state = store.load()
    if state is None:
        R.print_error(f"session {sid} 的 goal_state.json 不存在或已损坏，无法修订。")
        return
    if not state.goal_spec:
        R.print_error(f"session {sid} 没有已冻结的 goal_spec，无法修订。")
        return

    spec = GoalSpec.from_dict(state.goal_spec)
    spec.confirmed = False  # 重新进入协商，须重新走一遍 /confirm
    builder = GoalSpecBuilder(agent.cfg, parent_session_id=sid)

    if state.replan_proposal:
        proposal_text = render_replan_proposal(state.replan_proposal)
        R.console.print("[bold]— 上次终止时 Agent 提出的重规划提议 —[/bold]")
        R.console.print(proposal_text)
        R.print_info("[Goal 模式] 正在基于以上提议生成新草案…")
        try:
            spec = builder.revise(spec, proposal_text)
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.cli.commands.goal_mode_cmd._handle_revise')
            R.print_error(f"基于提议生成新草案失败：{e}，将展示原草案供你手动修改。")
            spec = GoalSpec.from_dict(state.goal_spec)
            spec.confirmed = False

    new_spec = _negotiate_loop(builder, spec, agent)
    if new_spec is None:
        R.print_info("[Goal 模式] 已放弃本次修订。")
        return

    if agent.session_id != sid:
        try:
            agent.load_session(sid)
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.cli.commands.goal_mode_cmd._handle_revise')
            R.print_error(f"加载 session {sid} 历史失败：{e}")
            return

    _run_goal(agent, new_spec)


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
    """列出所有 goal 会话，不论状态（running / done / stuck /
    max_rounds_exhausted / cancelled / 其他），按状态分组展示。

    [BUGFIX/需求变更] 此前只列 `status==running`（后来加了 `stuck`），
    `done`/`cancelled`/`max_rounds_exhausted` 等状态的记录完全看不到，用户
    没有一个"查看全部历史 goal"的入口，只能自己去翻 sessions_dir 下的
    goal_state.json。现在改为 `include_all=True`，展示全部状态，并按状态
    分组、分别给出对应的操作提示（running 可直接 resume，其余状态需要
    `--force`）。
    """
    from mini_agent.goal_mode.state import list_resumable_sessions

    sessions = list_resumable_sessions(agent.cfg.project_root, include_all=True)
    if not sessions:
        R.print_info("没有检测到任何 goal 任务记录。")
        return

    def _first_line(s: dict) -> str:
        goal_text = s.get("goal_text") or "(无目标描述，可能是旧版本数据)"
        # 目标描述可能很长（多行/几百字），命令行里整段甩出来反而看不清哪行是
        # 哪个 session 的，所以只取第一行 + 截断，完整内容还是原样存在
        # goal_state.json 里，需要的话可以自己去翻文件。
        line = goal_text.splitlines()[0] if goal_text else ""
        return line[:77] + "..." if len(line) > 80 else line

    def _updated_str(s: dict) -> str:
        updated_at = s.get("updated_at")
        return (
            datetime.fromtimestamp(updated_at).strftime("%Y-%m-%d %H:%M:%S")
            if updated_at else "未知"
        )

    # status 分组的展示元数据：分组标题、是否需要 --force、额外提示。
    # "running" 条目本身不带 status 字段（见 list_resumable_sessions），统一
    # 归一成 "running" 分组 key。
    _GROUP_META = {
        "running": ("[bold]可直接恢复的目标任务[/bold]", False, None),
        "stuck": (
            "[bold yellow]因判定为\"卡住\"而终止的目标任务[/bold yellow]", True,
            "恢复时会重新给予一次完整的卡住检测额度",
        ),
        "max_rounds_exhausted": (
            "[bold yellow]达到最大轮次仍未完成的目标任务[/bold yellow]", True, None,
        ),
        "done": ("[bold green]已达成的目标任务[/bold green]", True, None),
        "cancelled": ("[dim]已取消的目标任务[/dim]", True, None),
    }

    groups: dict[str, list[dict]] = {}
    for s in sessions:
        key = s.get("status") or "running"
        groups.setdefault(key, []).append(s)

    # 展示顺序：running 优先，其余按 _GROUP_META 声明顺序，未知状态兜底放最后。
    ordered_keys = [k for k in _GROUP_META if k in groups]
    ordered_keys += [k for k in groups if k not in _GROUP_META]

    first_block = True
    for key in ordered_keys:
        group_sessions = groups[key]
        title, needs_force, extra_hint = _GROUP_META.get(
            key, (f"[bold]状态为 {key} 的目标任务[/bold]", True, None),
        )
        if not first_block:
            R.console.print()
        first_block = False

        R.console.print(f"{title}（{len(group_sessions)} 个）：")
        for s in group_sessions:
            current_mark = "  [dim](当前 session)[/dim]" if s["session_id"] == agent.session_id else ""
            lines = [
                f"  - session: {s['session_id']}  round={s['round']}  "
                f"更新时间={_updated_str(s)}{current_mark}",
                f"    目标：{_first_line(s)}",
            ]
            if key != "running":
                report = s.get("final_report") or ""
                report_first_line = report.splitlines()[0] if report else ""
                if len(report_first_line) > 100:
                    report_first_line = report_first_line[:97] + "..."
                if report_first_line:
                    lines.append(f"    结果：{report_first_line}")
            R.console.print("\n".join(lines))

        resume_cmd = "/goal resume <session_id>" + (" --force" if needs_force else "")
        hint = f"输入 [bold]{resume_cmd}[/bold] 恢复对应的目标。"
        if extra_hint:
            hint += f"（{extra_hint}）"
        R.console.print(f"\n{hint}")

