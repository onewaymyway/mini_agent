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
        R.print_info(f"Session: [{agent.session_id}] — /session list to browse history")

    from mini_agent.ui.terminal import term as _term

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
            _term.stop()
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            R.print_stats(agent.stats.summary())
            R.print_info(pm.fragment("cli_messages", "BYE_MSG"))
            _term.stop()
            break

        if user_input.startswith("/"):
            _handle_slash(user_input, agent, skill_loader)
            continue

        try:
            agent.run_turn(user_input)
        except KeyboardInterrupt:
            _term.force_end_stream()
            # 尝试取消所有运行的 sub-agents
            _cancel_running_tasks()
            R.print_interrupt()
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
        handle_skill_cmd(parts[1:], skill_loader)

    elif name == "stats":
        R.print_stats(agent.stats.summary())

    elif name == "verbose":
        agent.cfg.verbose = not agent.cfg.verbose
        key = "VERBOSE_ON" if agent.cfg.verbose else "VERBOSE_OFF"
        R.print_info(pm.fragment("cli_messages", key))

    elif name == "model" and len(parts) >= 2:
        agent.cfg.model = parts[1]
        R.print_info(pm.fragment("cli_messages", "MODEL_SWITCHED", model=parts[1]))

    elif name == "compact":
        _compact_history(agent)

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

    elif name == "provider":
        handle_provider_cmd(parts[1:], agent)

    else:
        R.print_error(pm.fragment("cli_messages", "UNKNOWN_COMMAND", cmd=cmd))


# ── 内联命令实现（行为简单、无独立拆分价值）────────────────────────────────

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
