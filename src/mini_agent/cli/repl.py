"""
cli/repl.py — 交互式 REPL 主循环与 slash 命令路由

职责：
  - 启动 banner 与状态打印
  - 循环读取用户输入
  - 分发 slash 命令到对应 handler
  - 处理 agent.run_turn() 的异常和中断
"""

from __future__ import annotations

from typing import Optional

from mini_agent.agent import Agent
from mini_agent.skills import SkillLoader
from mini_agent.prompts import pm
from mini_agent._version import get_version
import mini_agent.ui.renderer as R
from mini_agent.cli.commands import (
    handle_skills_list,
    handle_skill_cmd,
    handle_behavior_cmd,
    handle_session_cmd,
    handle_tasks_cmd,
    handle_plan_cmd,
    handle_notepad_cmd,
    handle_concurrency_cmd,
    handle_provider_cmd,
    handle_agents_cmd,
    handle_hooks_cmd,
    handle_platform_cmd,
    handle_quarantine_cmd,
    handle_commit_guard_cmd,
    handle_evolution_cmd,
    handle_evolve_cmd,
    handle_goal_cmd,
    handle_workflow_cmd,
    handle_debug_cmd,
    handle_role_cmd,
    handle_wiki_cmd,
    handle_recall_cmd,
    handle_digest_cmd,
    handle_next_action_cmd,
    handle_profile_cmd,
    handle_growth_cmd,
    handle_capability_cmd,)
from mini_agent.cli.commands.agent_value_profile_cmd import handle_agent_value_profile_cmd


def _print_resume_hint(agent: Agent) -> None:
    """在退出时打印 resume 提示，告知用户如何继续当前会话。"""
    session_id = getattr(agent, "session_id", None)
    if not session_id:
        return
    resume_cmd = f"mini-agent --resume {session_id}"
    R.console.print(
        f"\n[bold cyan]💡 提示：[/bold cyan] 你可以通过以下命令继续本次对话：\n"
        f"  [bold green]{resume_cmd}[/bold green]\n"
        f"  [dim]Session ID: {session_id}[/dim]",
    )


def _print_startup_digest_and_advisor(agent: Agent) -> None:
    """启动时打印一次未展示过的日报摘要 + 推荐摘要（各占一行，不合并，
    见 主动推荐与数字分身机制设计方案.md 第 4.3 节）。任何一步失败都静默跳过，
    不能因为这个附加提示影响正常启动。

    两条提示分别受 digest_advisor.daily_digest_startup_print_enabled /
    next_action_startup_print_enabled 独立开关控制（agent_config.json），
    关闭某一项只是不在启动时打扰用户，不影响对应 cron job 正常生成文件、
    也不影响 /digest daily 与 /next 手动查看。
    """
    paths = None
    digest_advisor_cfg = getattr(getattr(agent, "cfg", None), "digest_advisor", None)

    try:
        paths = getattr(agent, "_paths", None)
        if paths is None:
            from mini_agent.storage.paths import AgentPaths
            paths = AgentPaths(agent.cfg.project_root)

        if digest_advisor_cfg is None or digest_advisor_cfg.daily_digest_startup_print_enabled:
            from mini_agent.evolution.daily_digest import (
                load_pending_digest,
                render_startup_summary as render_digest_summary,
                mark_shown as mark_digest_shown,
            )
            digest_data = load_pending_digest(paths)
            if digest_data:
                line = render_digest_summary(digest_data)
                if line:
                    R.print_info(line)
                mark_digest_shown(paths, digest_data["day"])
    except Exception:
        pass

    if paths is None:
        return

    # [daemon_hang_detection_and_alert_escalation_plan.md §3.2"交互时
    # 顺带提示"] 本地 REPL 场景直接读文件（不像 daemon connected 客户端
    # 需要走 HTTP 端点），跟上面的日报/推荐提示共用同一层 try/except
    # 静默失败策略——一次会话只在这里打印一次，天然节流。
    try:
        from mini_agent.notification.daemon_crash_store import count_unacknowledged_crash_alerts
        pending = count_unacknowledged_crash_alerts(paths)
        if pending:
            R.print_info(
                f"⚠️ 有 {pending} 条未读的 daemon 崩溃/卡死记录，运行 `daemon status` 查看"
            )
    except Exception:
        pass

    if digest_advisor_cfg is not None and not digest_advisor_cfg.next_action_startup_print_enabled:
        return

    try:
        from mini_agent.evolution.next_action_advisor import (
            load_pending_next_actions,
            render_startup_summary as render_next_summary,
            mark_shown as mark_next_shown,
        )
        next_data = load_pending_next_actions(paths)
        if next_data:
            line = render_next_summary(next_data)
            if line:
                R.print_info(line)
            mark_next_shown(paths)
    except Exception:
        pass


def run_repl(agent: Agent, skill_loader: SkillLoader) -> None:
    """启动并运行交互式 REPL，直到用户退出。"""
    R.console.print(pm.fragment("cli_messages", "BANNER", version=get_version()), style="bold blue")
    R.print_info(pm.fragment("cli_messages", "REPL_STARTUP_MODEL", model=agent.cfg.model))
    R.print_info(pm.fragment("cli_messages", "REPL_STARTUP_PROJECT", project_root=agent.cfg.project_root))
    R.print_info(pm.fragment("cli_messages", "REPL_STARTUP_SKILLS", skill_count=len(skill_loader.available)))
    if agent.cfg.sandbox:
        R.print_warning(pm.fragment("cli_messages", "REPL_SANDBOX_WARNING"))
    if agent.session_id:
        R.print_info(f"Session: \\[{agent.session_id}] — /session list to browse history")

    _print_startup_digest_and_advisor(agent)

    from mini_agent.ui.terminal import term as _term, prime_model_completions as _prime_models
    _prime_models(getattr(agent, "_client_pool", None))

    while True:
        # ── HTTP 模式：等待 AgentRunner 处理完才进入输入 ──────────────────
        # 若 bridge 存在且 agent 正在被 HTTP 线程驱动，则不抢占 term 队列，
        # 避免 prompt_user() 里的 q.join() 与 AgentRunner 的 push 死锁。
        _http_bridge = _get_http_bridge()
        if _http_bridge is not None:
            _bridge_state = _http_bridge.get_state()
            if _bridge_state.get("state") in ("running", "waiting_permission"):
                import time as _time
                _time.sleep(0.3)
                continue
            # 队列中可能还有 AgentRunner finally 块里发出的最后几条 print 消息，
            # 让渲染线程先把它们消费完，再进入 prompt_user() 的 q.join() 等待，
            # 否则 q.join() 会等那些消息，反而更慢。
            # 短暂 yield 给渲染线程即可（不需要精确同步）。
            import time as _time
            _time.sleep(0.05)

        try:
            user_input = _term.prompt_user()
        except KeyboardInterrupt:
            R.print_interrupt()
            continue
        except EOFError:
            R.print_info(pm.fragment("cli_messages", "BYE_MSG"))
            agent.trigger_session_end()
            _print_resume_hint(agent)
            _term.stop()
            break

        if not user_input:
            continue

        # ptk 的输入行现在配置成 erase_when_done=True（见
        # ui/terminal.py::_build_ptk_session），提交后会自己把那行擦掉，
        # 这里补打印一次，走统一渲染队列，避免游离在 _bar_drawn 记账之外
        # 被后续重绘误伤（daemon connected 模式已经用同样的方式修复过，
        # 这里是本地非 daemon 模式的对应修复）。
        from rich.markup import escape as _esc_echo_local
        _term.print(
            f"[bold green]You[/bold green][bold cyan] \u276f [/bold cyan]{_esc_echo_local(user_input)}"
        )

        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            R.print_stats(agent.stats.summary())
            R.print_info(pm.fragment("cli_messages", "BYE_MSG"))
            agent.trigger_session_end()
            _print_resume_hint(agent)
            _term.stop()
            break

        if user_input.startswith("/"):
            _handle_slash(user_input, agent, skill_loader)
            continue

        try:
            from mini_agent.ui.raw_key_listener import get_listener as _get_key_listener
            _key_listener = _get_key_listener()
            _key_listener.start()
            try:
                agent.run_turn(user_input)
            finally:
                _key_listener.stop()

            # [SYS-HOOKS] TurnEnd：若 hook 返回了替代用户输入，直接注入下一轮，
            # 跳过真实用户输入等待。
            _injected = getattr(agent, "_turn_end_user_input", None)
            if _injected:
                agent._turn_end_user_input = None
                user_input = _injected
                # 直接 goto 本循环的 run_turn，跳过 prompt_user()
                try:
                    _key_listener2 = _get_key_listener()
                    _key_listener2.start()
                    try:
                        while user_input:
                            # 显示注入的输入，视觉上与真实用户输入保持一致
                            R.console.print(
                                f"\n[bold green]You[/bold green][cyan] ❯ [/cyan]"
                                f"[dim]{user_input}[/dim]"
                            )
                            agent.run_turn(user_input)
                            _injected2 = getattr(agent, "_turn_end_user_input", None)
                            agent._turn_end_user_input = None
                            user_input = _injected2 or ""
                    finally:
                        _key_listener2.stop()
                except KeyboardInterrupt:
                    _term.force_end_stream()
                    _cancel_running_tasks()
                    R.print_interrupt()
                except Exception as e:
                    from mini_agent.errors import log_exception
                    log_exception(e, where='mini_agent.cli.repl.run_repl')
                    _term.force_end_stream()
                    R.print_error(f"API error (injected turn): {e}")
                    if agent.cfg.verbose:
                        import traceback
                        traceback.print_exc()
        except KeyboardInterrupt:
            _term.force_end_stream()
            _cancel_running_tasks()
            R.print_interrupt()
            # [具身改进 C3] 用户明确打断当前任务——尝试生成认知锚点，
            # 失败静默降级，不影响中断流程本身。
            try:
                agent._save_cognitive_anchor()
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.cli.repl')
                pass
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.cli.repl.run_repl')
            _term.force_end_stream()
            R.print_error(f"API error: {e}")
            if agent.cfg.verbose:
                import traceback
                traceback.print_exc()


def _cancel_running_tasks() -> None:
    """取消所有正在运行的 sub-agent 任务。"""
    try:
        from mini_agent.tools.orchestration import get_task_manager
        mgr = get_task_manager()
        if mgr:
            cancelled = mgr.cancel_all()
            if cancelled > 0:
                R.print_info(f"Cancelled {cancelled} running task(s).")
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.cli.repl')
        pass


def _handle_slash(cmd: str, agent: Agent, skill_loader: SkillLoader) -> None:
    """解析并分发 slash 命令到对应 handler。"""
    from mini_agent.cli.parser import build_parser

    parts = cmd.lstrip("/").split()
    name  = parts[0].lower() if parts else ""

    if name == "help":
        R.console.print(build_parser().format_help())

    elif name == "clear":
        agent.clear_history()
        R.print_success(pm.fragment("cli_messages", "HISTORY_CLEARED"))

    elif name == "retry":
        _handle_retry(agent, parts[1:])

    elif name == "rollback":
        _handle_rollback(agent, parts[1:])

    elif name == "skills":
        handle_skills_list(skill_loader)

    elif name == "skill":
        handle_skill_cmd(parts[1:], skill_loader, cfg=agent.cfg)

    elif name == "behavior":
        handle_behavior_cmd(parts[1:], cfg=agent.cfg)

    elif name == "reload":
        # 手动强制热重载：跳过 debounce，立即重新扫描所有监视目录
        if not agent._hot_reloader.has_watches:
            R.print_info("[reload] 没有注册任何热重载监视目录")
        else:
            reports = agent._hot_reloader.force_reload()
            any_change = False
            for r in reports:
                if r.has_changes:
                    any_change = True
                    R.print_success(f"[reload:{r.category}] {r.summary()}")
                else:
                    R.print_info(f"[reload:{r.category}] reloaded (no file changes)")
            if any_change:
                # 使 system prompt 缓存失效
                agent._cached_system = None

    elif name == "stats":
        R.print_stats(agent.stats.summary())

    elif name == "verbose":
        agent.cfg.verbose = not agent.cfg.verbose
        key = "VERBOSE_ON" if agent.cfg.verbose else "VERBOSE_OFF"
        R.print_info(pm.fragment("cli_messages", key))

    elif name in ("raw-output", "raw_output", "rawoutput"):
        from mini_agent.ui.terminal import term as _term
        new_state = not _term.is_raw_output()
        _term.set_raw_output(new_state)
        agent.cfg.raw_output = new_state
        key = "RAW_OUTPUT_ON" if new_state else "RAW_OUTPUT_OFF"
        R.print_info(pm.fragment("cli_messages", key))

    elif name in ("reasoning", "show-reasoning", "show_reasoning"):
        agent.cfg.show_reasoning = not agent.cfg.show_reasoning
        key = "REASONING_ON" if agent.cfg.show_reasoning else "REASONING_OFF"
        R.print_info(pm.fragment("cli_messages", key))

    elif name in ("turnjudge", "turn-judge", "turn_judge"):
        _handle_turn_judge_cmd(parts[1:], agent)

    elif name == "model" and len(parts) >= 2:
        _handle_model_cmd(parts[1], agent)

    elif name == "model":
        R.print_error("Usage: /model <name>")

    elif name == "compact":
        _compact_history(agent)

    elif name == "compact_continue":
        _compact_and_continue(agent)

    elif name == "goal":
        handle_goal_cmd(parts[1:], agent)

    elif name == "workflow":
        handle_workflow_cmd(parts[1:], agent)

    elif name == "memory":
        from mini_agent.cli.commands.memory_cmd import handle_memory_cmd
        handle_memory_cmd(parts[1:], agent)

    elif name == "profile" and len(parts) >= 2 and parts[1] == "rebuild":
        import threading
        threading.Thread(
            target=agent._maybe_refresh_profile,
            kwargs={"force": True, "rebuild": True},
            daemon=True,
            name="mini-agent-profile",
        ).start()

    elif name == "profile":
        # [next_doc/memory_backfill_and_profile_update_plan.md 3.3 节]
        # 默认行为改为"立即刷新，走增量更新"（不丢弃已有画像）；
        # 想从头重建请用 `/profile rebuild`。
        import threading
        threading.Thread(
            target=agent._maybe_refresh_profile,
            kwargs={"force": True, "rebuild": False},
            daemon=True,
            name="mini-agent-profile",
        ).start()

    elif name == "prompts":
        _list_prompts()

    elif name in ("session", "sessions"):
        handle_session_cmd(parts[1:], agent)

    elif name == "tasks":
        handle_tasks_cmd(parts[1:], agent)

    elif name == "plan":
        handle_plan_cmd(parts[1:])

    elif name == "notepad":
        handle_notepad_cmd(parts[1:])

    elif name == "recall":
        handle_recall_cmd(parts[1:])

    elif name in ("concurrency", "cc"):
        handle_concurrency_cmd(parts[1:])

    elif name == "ensemble":
        from mini_agent.cli.commands.ensemble import handle_ensemble_cmd
        handle_ensemble_cmd(parts[1:], agent)

    elif name == "provider":
        handle_provider_cmd(parts[1:], agent)

    elif name == "agents":
        handle_agents_cmd(parts[1:], agent)

    elif name == "role":
        handle_role_cmd(parts[1:], agent)

    elif name == "proxy":
        from mini_agent.cli.commands.proxy import handle_proxy_cmd
        handle_proxy_cmd(parts[1:], agent)

    elif name == "hooks":
        handle_hooks_cmd(parts[1:], agent)

    elif name == "platform":
        handle_platform_cmd(parts[1:], agent)

    elif name == "quarantine":
        handle_quarantine_cmd(parts[1:], agent)

    elif name == "commit-guard":
        handle_commit_guard_cmd(parts[1:], agent)

    elif name == "evolution":
        handle_evolution_cmd(parts[1:], agent)

    elif name == "evolve":
        handle_evolve_cmd(parts[1:], agent)

    elif name == "wiki":
        handle_wiki_cmd(parts[1:], agent)

    elif name == "digest" and parts[1:2] == ["daily"]:
        # 主动推荐与数字分身机制设计方案.md 第 4.1 节（阶段一）
        # [BUGFIX] 之前这里是无条件的独立 `elif name == "digest"` 分支，排在
        # 下面"/digest 显示自主活动摘要"这个既有分支之前，导致既有 /digest
        # （无参数）分支永远走不到；这里改成只在显式带 `daily` 子命令时才
        # 分流到日报逻辑，不带参数时继续走下面既有的活动摘要分支。
        handle_digest_cmd(parts[1:], agent)

    elif name == "next":
        # 同上文档第 4.2 节（阶段二）
        handle_next_action_cmd(parts[1:], agent)

    elif name == "decision_profile":
        # 同上文档第 4.4 节（阶段三）。
        # [BUGFIX] 最初这里也叫 `/profile`，与既有的"强制刷新用户画像"命令
        # （见下方 `elif name == "profile"` 分支）重名，导致本命令因为分支
        # 顺序靠后而永远无法触发。改名为 `/decision_profile`，与本方案
        # 已经在用的 `sys:decision_profile_update` cron job 命名保持一致，
        # 避免语义混淆的同时也不需要再抢占既有的 /profile 语义。
        handle_profile_cmd(parts[1:], agent)

    elif name == "agent_value_profile":
        # [next_doc/self_awareness_identity_evolution_plan.md §2.1] 姊妹
        # 命令：/decision_profile 归纳用户决策画像，这里归纳 agent 自己的
        # 历史选择行为。
        handle_agent_value_profile_cmd(parts[1:], agent)

    elif name == "growth":
        # [next_doc/growth_advisor_design.md] 成长顾问：见
        # cli/commands/growth_cmd.py 顶部子命令说明。
        handle_growth_cmd(parts[1:], agent)

    elif name == "capability":
        # [next_doc/persona_capability_learning_design.md] 人设能力自主
        # 学习：见 cli/commands/capability_cmd.py 顶部子命令说明。
        handle_capability_cmd(parts[1:], agent)

    elif name == "debug":
        # /debug system|history [full] [n]|all [n]|save [path]
        # 打印/导出当前 system prompt 与 history，便于分析调试
        handle_debug_cmd(parts[1:], agent)

    # ── Stage 9: Goal Backlog + Activity Digest ───────────────────────────
    elif name == "agent":
        # /agent goals ... | /agent digest | /agent daemon ...
        _handle_agent_subcmd(parts[1:], agent)

    elif name == "goals":
        # 快捷方式：/goals 等同于 /agent goals
        from mini_agent.cli.commands.goals import handle_goals_cmd
        handle_goals_cmd(parts[1:], agent)

    elif name == "digest":
        # /digest — 显示自上次交互以来的自主活动摘要
        _handle_digest_cmd(agent)

    elif name == "cron":
        # /cron — 定时任务管理（daemon 模式）
        import asyncio
        from mini_agent.cli.commands.cron import handle_cron
        # 构造轻量 ctx 供 handle_cron 使用（注入 cron_scheduler）
        class _Ctx:
            cron_scheduler = getattr(agent, "_cron_scheduler", None)
        _Ctx.agent = agent
        result = asyncio.run(handle_cron(parts[1:], _Ctx()))
        if result:
            R.print_info(result)

    else:
        R.print_error(pm.fragment("cli_messages", "UNKNOWN_COMMAND", cmd=cmd))


# ── 内联命令实现（行为简单、无独立拆分价值）────────────────────────────────

def _handle_agent_subcmd(parts: list[str], agent) -> None:
    """`/agent <subcmd>` 路由：goals / digest / daemon。"""
    if not parts:
        R.print_info("Usage: /agent <goals|digest|daemon> [args...]")
        return
    sub = parts[0].lower()
    rest = parts[1:]
    if sub == "goals":
        from mini_agent.cli.commands.goals import handle_goals_cmd
        handle_goals_cmd(rest, agent)
    elif sub == "digest":
        _handle_digest_cmd(agent)
    elif sub == "daemon":
        # daemon 子命令（在 REPL 中仅支持 status）
        if not rest or rest[0] == "status":
            from mini_agent.cli.commands.goals import handle_goals_cmd
            handle_goals_cmd(["status"], agent)
        else:
            R.print_info("In REPL, only '/agent daemon status' is supported. "
                         "Use 'mini-agent daemon start|stop|status' in a separate terminal.")
    else:
        R.print_error(f"Unknown /agent subcommand: {sub!r}")
        R.print_info("Available: goals, digest, daemon")


def _handle_digest_cmd(agent) -> None:
    """`/digest` — 显示自上次交互以来的自主活动摘要。"""
    import time
    paths = getattr(agent, "_paths", None)
    if paths is None:
        try:
            from mini_agent.storage.paths import AgentPaths
            paths = AgentPaths(agent.cfg.project_root)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.cli.repl._handle_digest_cmd')
            R.print_error("Cannot access project paths.")
            return

    try:
        from mini_agent.evolution.resource_arbiter import (
            read_activity_digest, build_digest_summary,
        )
        # 读取最近 24h 的记录
        since = time.time() - 86400
        records = read_activity_digest(paths, since_ts=since)
        summary = build_digest_summary(records)
        R.print_info(summary)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.repl._handle_digest_cmd')
        R.print_warning(f"无法读取 activity_digest: {e}")


def _handle_retry(agent: Agent, args: Optional[list[str]] = None) -> None:
    """/retry [N] — 丢弃最近 N 轮（默认 1 轮）模型输出，用相同输入重新生成。
    基于 history 中的用户输入轮次边界定位，可以重试 resume 之前 session
    历史中的任意一轮（只要该轮还没被 /compact 折叠掉）。"""
    n = 1
    if args:
        try:
            n = int(args[0])
        except ValueError:
            R.print_error(f"Invalid retry count: {args[0]!r}. Usage: /retry [N]")
            return

    try:
        ok, msg, _ = agent.retry_to_turn(n)
        if not ok:
            R.print_warning(msg)
    except KeyboardInterrupt:
        R.print_interrupt()
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.repl._handle_retry')
        R.print_error(f"Retry failed: {e}")
        if agent.cfg.verbose:
            import traceback
            traceback.print_exc()


def _handle_rollback(agent: Agent, args: Optional[list[str]] = None) -> None:
    """/rollback [N] — 回退最近 N 轮（默认 1 轮），基于 history 中的用户输入
    轮次边界定位，可以回退到 resume 之前 session 历史中的任意一轮（只要
    该轮次还没被 /compact 折叠掉）。"""
    n = 1
    if args:
        try:
            n = int(args[0])
        except ValueError:
            R.print_error(f"Invalid rollback count: {args[0]!r}. Usage: /rollback [N]")
            return

    ok, msg = agent.rollback_to_turn(n)
    if ok:
        R.print_success(f"{msg} Session saved.")
    else:
        R.print_warning(msg)


def _get_http_bridge():
    """
    尝试获取 HTTP bridge 单例。
    未启动 HTTP 服务时返回 None，不影响纯命令行模式。
    """
    try:
        from mini_agent.api.bridge import get_bridge
        bridge = get_bridge()
        # 只有 agent 已注入（即 HttpServer 真正启动了）才视为 HTTP 模式
        return bridge if bridge.agent is not None else None
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.cli.repl._get_http_bridge')
        return None


def _handle_model_cmd(model_name: str, agent: Agent) -> None:
    """
    /model <name> — 运行时切换模型。

    委托给 agent.switch_model()：
      - 若该模型已存在于 fallback chain 中，直接切换过去（连带正确的
        provider 和 api key）；
      - 否则在当前 provider 下用新模型名创建一个新 client 并追加进 chain。
    与旧实现的区别：这里会真正生效于后续的 LLM 调用，而不是只改一个
    不会被读取的配置字符串。
    """
    try:
        entry = agent.switch_model(model_name)
        R.print_info(
            pm.fragment("cli_messages", "MODEL_SWITCHED", model=entry.config.model)
            + f"  (provider: {entry.config.provider})"
        )
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.repl._handle_model_cmd')
        R.print_error(f"Failed to switch model: {e}")


def _handle_turn_judge_cmd(args: list[str], agent: Agent) -> None:
    """/turnjudge [on|off|status] — 运行时开关轮次守门员（TurnJudge）。

    不带参数时等同于 toggle（与 /verbose 一致的交互习惯）；
    显式传 on/off/status 时按指定值设置或仅查询当前状态。
    """
    tj_cfg = getattr(agent.cfg, "turn_judge", None)
    if tj_cfg is None:
        R.print_error("当前配置不支持 turn_judge（未找到 cfg.turn_judge，可能是旧版本 AppConfig）。")
        return

    sub = args[0].lower() if args else ""

    if sub == "status":
        state = "ON" if tj_cfg.enabled else "OFF"
        R.print_info(
            f"[TurnJudge] 当前状态：{state}"
            f"（max_auto_rounds={tj_cfg.max_auto_rounds}，"
            f"judge_model={tj_cfg.judge_model or '(复用主模型)'}）"
        )
        return

    if sub in ("on", "enable", "true", "1"):
        new_state = True
    elif sub in ("off", "disable", "false", "0"):
        new_state = False
    elif sub == "":
        new_state = not tj_cfg.enabled
    else:
        R.print_error(f"Unknown /turnjudge argument: {sub!r}. Usage: /turnjudge [on|off|status]")
        return

    tj_cfg.enabled = new_state
    # 关闭时清零自动接管计数，避免残留计数影响下次重新开启后的判断
    if not new_state:
        agent._turn_judge_auto_count = 0

    # [BUGFIX] RoleAgentDispatcher 的 turn_judge 内建 profile 只在
    # _discover()（启动时跑一次，或磁盘 agent-profile 热重载触发）时
    # 按当时的 cfg.turn_judge.enabled 合成——这里只是运行时翻转了配置
    # 标志位，如果不主动同步一次，dispatcher 的注册表不会更新：
    # - 开启前 enabled=False 时启动的进程，_discover() 根本没合成过
    #   turn_judge profile，/turnjudge on 之后 get_turn_end_review_roles()
    #   仍然是空列表，_maybe_run_turn_judge() 会一直误判成"被
    #   role_agent.block 屏蔽"（实际上只是从未注册过）。
    # - 反过来 /turnjudge off 后，dispatcher 里仍留着旧的 turn_judge
    #   profile，也需要重新发现一次才能把它摘掉。
    # - 更极端的情况：启动时 role_agent.enabled / goal_mode.enabled /
    #   turn_judge.enabled 全为 False，app.py 里根本没调用
    #   init_role_agent_system()，全局 dispatcher 单例是 None——单纯
    #   rediscover() 也无济于事，这里需要现场把它初始化出来。
    from mini_agent.role_agents import get_dispatcher, init_role_agent_system
    _dispatcher = get_dispatcher()
    if _dispatcher is None:
        from mini_agent.orchestrator.agent_profiles import get_profile_loader, AgentProfileLoader
        _profile_loader = get_profile_loader()
        if _profile_loader is None:
            # 理论上 app.py 启动时总会调用一次 init_agent_profiles()；
            # 这里只是兜底，避免因为找不到 loader 而彻底放弃同步。
            _profile_loader = AgentProfileLoader([])
        init_role_agent_system(agent.cfg, _profile_loader)
    else:
        _dispatcher.rediscover()

    key = "TURN_JUDGE_ON" if new_state else "TURN_JUDGE_OFF"
    R.print_info(pm.fragment("cli_messages", key))


def _compact_history(agent: Agent) -> None:
    """/compact — 压缩对话历史并重附 skill 上下文。"""
    if not agent.history:
        R.print_info(pm.fragment("cli_messages", "COMPACT_EMPTY"))
        return
    R.print_info(pm.fragment("cli_messages", "COMPACT_START"))
    try:
        summary = agent.compact_with_skills()
        if summary:
            R.print_markdown(summary)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.repl._compact_history')
        R.print_error(f"Compact failed: {e}")


def _compact_and_continue(agent: Agent) -> None:
    """/compact_continue — 压缩历史后自动发送"继续"，无需人工等待压缩结束再手动续接。"""
    if not agent.history:
        R.print_info(pm.fragment("cli_messages", "COMPACT_EMPTY"))
        return

    R.print_info(pm.fragment("cli_messages", "COMPACT_CONTINUE_START"))
    try:
        summary = agent.compact_with_skills()
        if summary:
            R.print_markdown(summary)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.repl._compact_and_continue')
        R.print_error(f"Compact failed: {e}")
        R.print_error(pm.fragment("cli_messages", "COMPACT_CONTINUE_FAILED"))
        return

    from mini_agent.ui.terminal import term as _term
    from mini_agent.ui.raw_key_listener import get_listener as _get_key_listener

    user_input = "继续"
    try:
        _key_listener = _get_key_listener()
        _key_listener.start()
        try:
            while user_input:
                # 显示自动续接的输入，视觉上与真实用户输入保持一致
                R.console.print(
                    f"\n[bold green]You[/bold green][cyan] \u276f [/cyan]"
                    f"[dim]{user_input}[/dim]"
                )
                agent.run_turn(user_input)
                # 若 turn 内部（例如 TurnEnd hook）又产生了替代输入，继续续接；
                # 否则结束，交还给主 REPL 循环等待真人输入。
                _injected = getattr(agent, "_turn_end_user_input", None)
                agent._turn_end_user_input = None
                user_input = _injected or ""
        finally:
            _key_listener.stop()
    except KeyboardInterrupt:
        _term.force_end_stream()
        _cancel_running_tasks()
        R.print_interrupt()
        try:
            agent._save_cognitive_anchor()
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.cli.repl')
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.repl._compact_and_continue')
        _term.force_end_stream()
        R.print_error(f"API error (compact_continue): {e}")
        if agent.cfg.verbose:
            import traceback
            traceback.print_exc()


def _list_prompts() -> None:
    """/prompts — 列出所有 PromptManager 管理的 prompt 文件。"""
    R.console.print("\n[bold]Managed prompt files:[/bold]")
    for p_name in pm.list_prompts():
        R.console.print(f"  [cyan]{p_name}[/cyan]")
    R.console.print(f"\n[dim]Prompt root: {pm._root}[/dim]")