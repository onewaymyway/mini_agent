"""
cli/repl.py — 交互式 REPL 主循环与 slash 命令路由

职责：
  - 启动 banner 与状态打印
  - 循环读取用户输入
  - 分发 slash 命令到对应 handler
  - 处理 agent.run_turn() 的异常和中断
"""

from __future__ import annotations

from mini_agent.agent import Agent
from mini_agent.skills import SkillLoader
from mini_agent.prompts import pm
import mini_agent.ui.renderer as R
from mini_agent.cli.commands import (
    handle_skills_list,
    handle_skill_cmd,
    handle_session_cmd,
    handle_tasks_cmd,
    handle_plan_cmd,
    handle_concurrency_cmd,
    handle_provider_cmd,
    handle_agents_cmd,
    handle_hooks_cmd,
    handle_evolution_cmd,
    handle_evolve_cmd,
)


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


def run_repl(agent: Agent, skill_loader: SkillLoader) -> None:
    """启动并运行交互式 REPL，直到用户退出。"""
    R.console.print(pm.fragment("cli_messages", "BANNER"), style="bold blue")
    R.print_info(pm.fragment("cli_messages", "REPL_STARTUP_MODEL", model=agent.cfg.model))
    R.print_info(pm.fragment("cli_messages", "REPL_STARTUP_PROJECT", project_root=agent.cfg.project_root))
    R.print_info(pm.fragment("cli_messages", "REPL_STARTUP_SKILLS", skill_count=len(skill_loader.available)))
    if agent.cfg.sandbox:
        R.print_warning(pm.fragment("cli_messages", "REPL_SANDBOX_WARNING"))
    if agent.session_id:
        R.print_info(f"Session: \\[{agent.session_id}] — /session list to browse history")

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
            except Exception:
                pass
        except Exception as e:
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
    except Exception:
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
        _handle_retry(agent)

    elif name == "rollback":
        _handle_rollback(agent)

    elif name == "skills":
        handle_skills_list(skill_loader)

    elif name == "skill":
        handle_skill_cmd(parts[1:], skill_loader, cfg=agent.cfg)

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

    elif name == "model" and len(parts) >= 2:
        _handle_model_cmd(parts[1], agent)

    elif name == "model":
        R.print_error("Usage: /model <name>")

    elif name == "compact":
        _compact_history(agent)

    elif name == "memory":
        agent.trigger_summary_and_profile(force=True)

    elif name == "profile":
        import threading
        threading.Thread(
            target=agent._maybe_refresh_profile,
            kwargs={"force": True},
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

    elif name in ("concurrency", "cc"):
        handle_concurrency_cmd(parts[1:])

    elif name == "ensemble":
        from mini_agent.cli.commands.ensemble import handle_ensemble_cmd
        handle_ensemble_cmd(parts[1:], agent)

    elif name == "provider":
        handle_provider_cmd(parts[1:], agent)

    elif name == "agents":
        handle_agents_cmd(parts[1:], agent)

    elif name == "hooks":
        handle_hooks_cmd(parts[1:], agent)

    elif name == "evolution":
        handle_evolution_cmd(parts[1:], agent)

    elif name == "evolve":
        handle_evolve_cmd(parts[1:], agent)

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
        except Exception:
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
        R.print_warning(f"无法读取 activity_digest: {e}")


def _handle_retry(agent: Agent) -> None:
    """/retry — 丢弃上一轮模型输出，用相同输入重新生成。"""
    if agent._turn_snapshot is None:
        R.print_warning("Nothing to retry — no previous turn in this session.")
        return
    try:
        agent.retry_last_turn()
    except KeyboardInterrupt:
        R.print_interrupt()
    except Exception as e:
        R.print_error(f"Retry failed: {e}")
        if agent.cfg.verbose:
            import traceback
            traceback.print_exc()


def _handle_rollback(agent: Agent) -> None:
    """/rollback — 完整撤销上一轮（用户消息 + 模型回复），同步 session 文件。"""
    if agent._turn_snapshot is None:
        R.print_warning("Nothing to rollback — no previous turn in this session.")
        return
    ok = agent.rollback_turn()
    if ok:
        R.print_success(
            f"Rollback complete. History now has {len(agent.history)} messages. "
            "Session saved."
        )
    else:
        R.print_error("Rollback failed unexpectedly.")


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
    except Exception:
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
        R.print_error(f"Failed to switch model: {e}")


def _compact_history(agent: Agent) -> None:
    """/compact — 压缩对话历史并重附 skill 上下文。"""
    if not agent.history:
        R.print_info(pm.fragment("cli_messages", "COMPACT_EMPTY"))
        return
    R.print_info(pm.fragment("cli_messages", "COMPACT_START"))
    try:
        agent.compact_with_skills()
    except Exception as e:
        R.print_error(f"Compact failed: {e}")


def _list_prompts() -> None:
    """/prompts — 列出所有 PromptManager 管理的 prompt 文件。"""
    R.console.print("\n[bold]Managed prompt files:[/bold]")
    for p_name in pm.list_prompts():
        R.console.print(f"  [cyan]{p_name}[/cyan]")
    R.console.print(f"\n[dim]Prompt root: {pm._root}[/dim]")