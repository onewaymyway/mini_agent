"""
cli/app.py — 应用启动装配

职责：
  - 解析 CLI 参数
  - 构建 AppConfig
  - 初始化 SkillLoader、PermissionGuard、并发控制、TaskManager
  - 构建 Agent
  - 单次模式 / 交互 REPL 分支
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import mini_agent.ui.renderer as R


def _extract_project_root(argv: list[str]) -> tuple[Path, list[str]]:
    """
    从子命令的 argv 里取出 --project/-p 的值，并返回"去掉这两个 token 之后"的
    剩余 argv。

    修复一个真实存在的 bug：之前 daemon/user/self 三处子命令短路各自写了一份
    几乎一样的"扫描 --project，找到就记下值"的代码，但只是**读取**，从来没有
    把这两个 token 从转发给子命令处理函数的 argv 里**去掉**。
    `run_daemon_cli` 的 `start`/`stop`/`status` 子命令凑巧没有用严格的
    argparse（`stop`/`status` 根本不解析 rest，`start` 用 parse_known_args
    能容忍多余参数），这个 bug 一直没暴露。但 `run_user_cli`/`run_self_cli`
    用的是标准的 `argparse.ArgumentParser`（不认识 `--project` 这个选项），
    一旦 argv 里残留了 `--project <path>` 没被消费掉，就会被当成"多余的
    位置参数"直接报错拒绝——也就是说，`mini-agent user list --project <path>`
    这种最基本的用法，从 `user`/`self` 子命令加上的那天起就没有真正可用过，
    只有"在当前目录运行、不显式传 --project"这一种用法是好的。

    现在统一在这里既扫描又剔除，三处短路逻辑都改成调用这个函数，从根上
    避免同一个 bug 在未来新增的子命令里再出现一次。
    """
    project_root = Path.cwd()
    rest: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] in ("--project", "-p"):
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                # 正常情况：--project <path>
                val = argv[i + 1].strip()
                if val:  # 非空才覆盖
                    project_root = Path(val).expanduser()
                i += 2
            else:
                # --project 是末尾孤立 token，或后面紧跟另一个 flag：
                # PowerShell 里 "$TESTPROJ"（未定义变量）会被展开成空串后
                # 整个参数被 shell 丢弃，导致 --project 没有值。
                # 静默跳过，不把它放进 rest，避免下游 argparse 报
                # "unrecognized arguments: --project"。
                i += 1
            continue
        rest.append(argv[i])
        i += 1
    return project_root, rest


def main() -> int:
    # ── eval 子命令短路：在进入主 argparse 流程之前优先处理 ───────────────────
    # 对应 self_evolution_implementation_plan.md Stage 3.2。`mini-agent eval ...`
    # 与主入口的位置参数 `prompt`（cli/parser.py）共存：argparse 不支持
    # "位置参数 + 互斥子命令"两者兼得，所以在这里按 argv[1] 整体短路，
    # 不进入 build_parser() 解析，与现有单命令体系互不干扰。
    if len(sys.argv) > 1 and sys.argv[1] == "eval":
        from mini_agent.cli.commands.eval_cmd import run_eval_cli
        return run_eval_cli(sys.argv[2:])

    # ── Stage 9 §3.3: daemon 子命令短路 ──────────────────────────────────────
    # `mini-agent daemon start|stop|status` 不进入主 argparse 流程
    if len(sys.argv) > 1 and sys.argv[1] == "daemon":
        from mini_agent.cli.daemon import run_daemon_cli
        project_root, rest = _extract_project_root(sys.argv[2:])
        return run_daemon_cli(rest, project_root)

    # ── daemon 多用户架构 Phase 1：user 子命令短路 ───────────────────────────
    # `mini-agent user list|add|remove|role|token` 同样不进入主 argparse 流程，
    # 写法与上面的 daemon 子命令完全一致（--project 扫描 + 短路转发）。
    if len(sys.argv) > 1 and sys.argv[1] == "user":
        from mini_agent.cli.commands.user_cmd import run_user_cli
        project_root, rest = _extract_project_root(sys.argv[2:])
        return run_user_cli(rest, project_root)

    # ── daemon 多用户架构 Phase 4：self 子命令短路 ───────────────────────────
    # `mini-agent self status` 同样不进入主 argparse 流程，写法与上面的
    # user 子命令完全一致。
    if len(sys.argv) > 1 and sys.argv[1] == "self":
        from mini_agent.cli.commands.self_cmd import run_self_cli
        project_root, rest = _extract_project_root(sys.argv[2:])
        return run_self_cli(rest, project_root)

    # ── 全局异常捕获：确保任何启动错误都能显示 ────────────────────────────────
    try:
        _main_inner()
    except Exception as e:
        # 使用最直接的方式输出错误，不依赖任何库
        import traceback
        print("\n" + "=" * 50, file=sys.stderr)
        print("ERROR: mini-agent startup failed", file=sys.stderr)
        print("=" * 50, file=sys.stderr)
        print(f"\n{type(e).__name__}: {e}", file=sys.stderr)
        print("\nFull traceback:", file=sys.stderr)
        traceback.print_exc()
        print("=" * 50, file=sys.stderr)
        sys.exit(1)
    return 0


def _main_inner() -> None:
    """实际的主逻辑，分离出来以便全局异常捕获。"""
    # ── 注册内置工具（side-effect import，必须在 Agent 构造前执行）─────────
    import mini_agent.tools.builtin       # noqa: F401
    import mini_agent.tools.orchestration # noqa: F401
    import mini_agent.tools.plan          # noqa: F401
    import mini_agent.tools.user_input    # noqa: F401
    import mini_agent.tools.evolution     # noqa: F401  [Phase C / 3.1] skill_propose
    import mini_agent.tools.workdir_knowledge  # noqa: F401  [W2 / Stage 4] add_open_thread/update_work_thread/update_knowledge/search_knowledge
    # tools.skill_manager 由 Agent.__init__ 懒注册（需要 SkillLoader 实例）

    from mini_agent.cli.parser import build_parser
    from mini_agent.config import load_config
    from mini_agent.permissions import PermissionGuard
    from mini_agent.skills import SkillLoader
    from mini_agent.agent import Agent

    parser = build_parser()
    args   = parser.parse_args()

    # ── simple-mode：尽早设置，确保启动阶段的所有输出（包括下面 print("cfg:", cfg)
    # 以及 R.print_info 等）都遵循该模式。CLI 参数优先于 MINI_AGENT_SIMPLE_MODE
    # 环境变量（Terminal 构造时已经读取过环境变量作为默认值，这里只在用户
    # 显式传了 --simple-mode 时才覆盖，不传则保留 Terminal 自己的默认判断）。
    if getattr(args, "simple_mode", None):
        from mini_agent.ui.terminal import term as _term_early
        _term_early.set_simple_mode(True)

    if getattr(args, "raw_output", None):
        from mini_agent.ui.terminal import term as _term_early_raw
        _term_early_raw.set_raw_output(True)

    # ── 配置构建 ─────────────────────────────────────────────────────────────
    project_root  = Path(args.project).expanduser() if args.project else Path.cwd()
    debug_console = getattr(args, "debug_llm_console", False)
    config_file   = Path(args.config).expanduser() if getattr(args, "config", None) else None
    providers_config_file = (
        Path(args.providers_config).expanduser()
        if getattr(args, "providers_config", None)
        else None
    )

    def _flag(name, default=None):
        v = getattr(args, name, default)
        return v if v else default

    cfg = load_config(
        project_root=project_root,
        extra_system=args.system,
        verbose=args.verbose,
        sandbox=args.sandbox,
        simple_mode=getattr(args, "simple_mode", None),
        raw_output=getattr(args, "raw_output", None),
        auto_approve=args.yes,
        model=args.model,
        llm_provider=getattr(args, "provider", None),
        llm_base_url=getattr(args, "base_url", None),
        use_system_tool_call=getattr(args, "system_tool_call", False),
        debug_llm=getattr(args, "debug_llm", False) or debug_console,
        debug_llm_console=debug_console,
        max_llm_calls=getattr(args, "max_llm_calls", 8),
        session_dir=Path(args.session_dir) if getattr(args, "session_dir", None) else None,
        session_fmt=getattr(args, "session_fmt", "json"),
        auto_save_session=not getattr(args, "no_save_session", False),
        agent_name=getattr(args, "agent_name", None),
        system_message_format=getattr(args, "system_msg_format", None),
        config_file=config_file,
        providers_config_file=providers_config_file,
        claude_md_file=getattr(args, "claude_md_file", None),
        # 感知与记忆开关
        memory_enabled=_flag("memory"),
        memory_top_k=_flag("memory_top_k"),
        session_summary_enabled=_flag("session_summary"),
        session_summary_min_turns=_flag("session_summary_min_turns"),
        session_search_enabled=_flag("session_search"),
        auto_compress_enabled=_flag("auto_compress"),
        auto_compress_threshold=_flag("auto_compress_threshold"),
        tool_result_trim_enabled=_flag("tool_result_trim"),
        tool_result_trim_threshold=_flag("tool_result_trim_threshold"),
        forget_policy_enabled=_flag("forget_policy"),
        skill_semantic_enabled=_flag("skill_semantic"),
        skill_semantic_threshold=_flag("skill_semantic_threshold"),
        skill_tracking_enabled=_flag("skill_tracking"),
        skill_chunking_enabled=_flag("skill_chunking"),
        skill_compact_budget=_flag("skill_compact_budget"),
        skill_compact_per_skill=_flag("skill_compact_per_skill"),
        project_scan_enabled=_flag("project_scan"),
        file_watch_enabled=_flag("file_watch"),
        tool_cache_enabled=_flag("tool_cache"),
        token_estimate_enabled=_flag("token_estimate"),
        token_warn_threshold=_flag("token_warn_threshold"),
        tool_stats_enabled=_flag("tool_stats"),
        # reminder 系统
        reminder_enabled=not _flag("no_reminders"),
        reminders_dir=Path(args.reminders_dir).expanduser() if getattr(args, "reminders_dir", None) else None,
        reminder_verbose=_flag("reminder_verbose"),
        # role agent 系统
        role_agent_enabled=_flag("role_agents"),
        role_agent_allow=getattr(args, "role_agents_allow", None),
        role_agent_block=getattr(args, "role_agents_block", None),
        role_agent_dir=Path(args.role_agents_dir).expanduser() if getattr(args, "role_agents_dir", None) else None,
        # 重试退避策略
        llm_retry_backoff_mode=getattr(args, "retry_backoff", None),
        llm_retry_backoff_step=getattr(args, "retry_backoff_step", None),
        llm_retry_backoff_max_delay=getattr(args, "retry_backoff_max", None),
    )

    # ── simple-mode 最终同步 ─────────────────────────────────────────────────
    # 上面在解析完 args 后已经处理了 --simple-mode 显式传参的情况；这里用
    # load_config() 算出的最终值（可能来自 agent_config.json 里的
    # "simple_mode": true，CLI 未传参时也要生效）再同步一次，确保两者
    # 不会因为来源不同而不一致。
    from mini_agent.ui.terminal import term as _term
    if cfg.simple_mode and not _term.is_simple_mode():
        _term.set_simple_mode(True)

    # ── raw-output 最终同步 ──────────────────────────────────────────────────
    # 同 simple-mode：CLI 未显式传参时，agent_config.json 里的
    # "raw_output": true 也要生效。
    if cfg.raw_output and not _term.is_raw_output():
        _term.set_raw_output(True)

    print("cfg:",cfg)

    if not cfg.api_key:
        # 使用最直接的方式输出错误
        print("\nERROR: ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        print("Please set it via:", file=sys.stderr)
        print("  Windows: $env:ANTHROPIC_API_KEY=\"sk-...\"", file=sys.stderr)
        print("  Linux/Mac: export ANTHROPIC_API_KEY=\"sk-...\"", file=sys.stderr)
        sys.exit(1)

    if args.max_turns is not None:
        cfg.max_turns = args.max_turns
    if args.no_stream:
        cfg.stream = False

    # ── Prompts ─────────────────────────────────────────────────────────────
    if args.prompts_dir:
        cfg.prompts_dir = Path(args.prompts_dir).expanduser()
    if cfg.prompts_dir:
        from mini_agent.prompts import pm
        pm.set_custom_dir(cfg.prompts_dir)

    # ── Skills ───────────────────────────────────────────────────────────────
    skill_dirs: list[Path] = []
    if cfg.skills_dir:
        skill_dirs.append(cfg.skills_dir)
    if args.skills_dir:
        skill_dirs.append(Path(args.skills_dir).expanduser())
    skill_loader = SkillLoader(
        skill_dirs,
        per_skill_tokens=getattr(cfg, "skill_compact_per_skill", 5_000),
        total_budget=getattr(cfg, "skill_compact_budget", 25_000),
    )

    # ── PermissionGuard ──────────────────────────────────────────────────────
    guard = PermissionGuard(
        auto_approve=cfg.auto_approve,
        sandbox=cfg.sandbox,
        project_root=cfg.project_root,
    )

    # ── 并发控制 + 状态栏 + TaskManager ──────────────────────────────────────
    from mini_agent.orchestrator.concurrency import init_concurrency
    from mini_agent.orchestrator.status_bar import start_status_bar
    from mini_agent.tools.orchestration import init_task_manager

    max_workers   = getattr(args, "workers", 4)
    max_llm_calls = getattr(args, "max_llm_calls", 8)
    init_concurrency(max_tasks=max_workers, max_llm_calls=max_llm_calls)
    start_status_bar()
    init_task_manager(cfg, max_workers=max_workers)
    R.print_info(f"Task manager ready (max {max_workers} concurrent workers)")

    # ── RPM 限速初始化 ────────────────────────────────────────────────────────
    from mini_agent.orchestrator.concurrency import set_rpm_limit
    _rpm = getattr(args, "rpm", 0) or 0
    if _rpm > 0:
        set_rpm_limit(_rpm)
        R.print_info(f"LLM rate limit: {_rpm} requests/minute")

    # ── 自定义子 agent profiles + hooks ──────────────────────────────────────
    from mini_agent.orchestrator.agent_profiles import init_agent_profiles
    profile_loader = init_agent_profiles(cfg)
    if profile_loader.available:
        R.print_info(f"Custom sub-agents loaded: {profile_loader.available}")

    # ── Role Agent 系统 ───────────────────────────────────────────────────────
    from mini_agent.role_agents import init_role_agent_system
    if cfg.role_agent.enabled:
        role_sys = init_role_agent_system(cfg, profile_loader)
        if role_sys.has_output_roles or role_sys.has_tool_roles:
            R.print_info(f"Role agents ready: {role_sys.summary}")
    else:
        R.print_info("[RoleAgent] 未启用（使用 --role-agents 参数开启）")

    # ── 工作流系统 ────────────────────────────────────────────────────────────
    from mini_agent.workflow.tools import register_workflow_tools
    register_workflow_tools(cfg)
    R.print_info("Workflow tools registered (generate/save/run/list/show/delete_workflow)")

    from mini_agent.hooks import init_hooks
    hook_mgr = init_hooks(cfg.project_root)
    if hook_mgr.has_any:
        R.print_info("Hooks loaded from .agent/hooks.json")

    # ── Stage 9: 连接模式 —— 在构建 Agent 之前检查 daemon ───────────────────
    # 条件：非 --no-daemon、非 --daemon-mode（daemon 自身不去连自己）、非单次 --prompt
    # 若 daemon 存活，直接用 HTTP API 连接，跳过本进程的 Agent 构建（节省资源）
    if (
        not getattr(args, "no_daemon", False)
        and not getattr(args, "daemon_mode", False)
        and not args.prompt
    ):
        from mini_agent.cli.daemon import _read_daemon_info, run_connected_repl, DaemonClient
        _daemon_info = _read_daemon_info(project_root)
        if _daemon_info:
            # 额外探活：PID 存在 + HTTP 服务真正就绪才走连接模式
            _client = DaemonClient(
                _daemon_info["http_port"],
                project_root=project_root,
            )
            if _client.health_check():
                # daemon HTTP 就绪，走连接模式（不构建本进程 Agent）。
                # 注意：不再在这里 stop_status_bar()。
                # run_connected_repl() 内部会把 provider 替换为 connected 模式专用的
                # _connected_status_bar_provider，显示 daemon session/state 信息，
                # 退出时再清除（set_statusbar_provider(None)）。
                run_connected_repl(_daemon_info)
                return
            else:
                # PID 存在但 HTTP 不通：daemon 可能刚启动或已崩溃
                # 等待最多 3 秒再试一次
                import time as _time
                _time.sleep(3)
                if _client.health_check():
                    run_connected_repl(_daemon_info)
                    return
                # 仍不通：回退到独立进程模式，打印提示
                R.print_warning(
                    f"[daemon] Daemon PID={_daemon_info['pid']} found but HTTP not responding. "
                    "Starting in standalone mode. Use 'mini-agent daemon status' to check."
                )

    # ── Agent ─────────────────────────────────────────────────────────────────
    agent = Agent(cfg=cfg, skill_loader=skill_loader, guard=guard)

    # ── Resume session ────────────────────────────────────────────────────────
    if getattr(args, "resume", None):
        if agent.load_session(args.resume):
            R.print_success(
                f"Resumed session [{agent.session_id}] — {len(agent.history)} messages loaded"
            )
        else:
            R.print_error(f"Session '{args.resume}' not found. Starting fresh.")

    # ── HTTP API 服务（可选）──────────────────────────────────────────────────
    http_server = None
    # 优先级：CLI 参数 > config 文件
    http_enabled = getattr(args, "http", None) or cfg.http_enabled
    if http_enabled:
        try:
            from mini_agent.api.server import HttpServer

            # CLI 参数覆盖 config 文件中的值
            http_host  = getattr(args, "http_host", None)  or cfg.http_host
            http_port  = getattr(args, "http_port", None)  or cfg.http_port
            http_token = getattr(args, "http_token", None) or cfg.http_api_token

            # IP 白名单：CLI 用逗号分隔字符串，config 用 list
            raw_ips = getattr(args, "http_allow_ip", None)
            if raw_ips:
                allowed_ips = [ip.strip() for ip in raw_ips.split(",") if ip.strip()]
            else:
                allowed_ips = cfg.http_allowed_ips

            fs_readonly = getattr(args, "http_fs_readonly", None) or cfg.http_fs_readonly
            multi_user_enabled = getattr(args, "http_multi_user", None)
            if multi_user_enabled is None:
                multi_user_enabled = cfg.http_multi_user_enabled

            http_server = HttpServer(
                agent              = agent,
                project_root       = cfg.project_root,
                host               = http_host,
                port               = http_port,
                configured_token   = http_token,
                allowed_ips        = allowed_ips,
                cors_origins       = cfg.http_cors_origins,
                fs_readonly        = fs_readonly,
                fs_excludes        = cfg.http_fs_excludes or [],
                ring_maxlen        = cfg.http_ring_maxlen,
                multi_user_enabled = multi_user_enabled,
            )
            http_server.start()

        except ImportError as e:
            R.print_warning(
                f"HTTP server dependencies not installed: {e}\n"
                "  Run: pip install fastapi uvicorn sse-starlette"
            )

    # ── 单次模式 ──────────────────────────────────────────────────────────────
    if args.prompt:
        try:
            agent.run_turn(args.prompt)
            R.print_stats(agent.stats.summary())
        except KeyboardInterrupt:
            R.print_interrupt()
        except Exception as e:
            R.print_error(str(e))
            sys.exit(1)
        finally:
            if http_server:
                http_server.stop()
        return

    # ── Stage 9: daemon 模式（--daemon-mode）────────────────────────────────
    # daemon 模式：HTTP 服务已启动，进程持续存活，不启动交互 REPL
    # 这是 `mini-agent daemon start` 内部调用的路径
    if getattr(args, "daemon_mode", False):
        import signal as _signal
        import threading as _threading

        # 写入 PID 文件
        try:
            from mini_agent.cli.daemon import _write_pid
            http_port = getattr(args, "http_port", None) or 8765
            _agent_name = getattr(cfg, "agent_name", None) or None
            _write_pid(project_root, os.getpid(), http_port, agent_name=_agent_name)
            R.print_info(f"[daemon] Running in daemon mode, PID={os.getpid()}, port={http_port}")
        except Exception as e:
            R.print_warning(f"[daemon] Failed to write PID file: {e}")

        stop_event = _threading.Event()

        def _shutdown_handler(signum, frame):
            R.print_info("[daemon] Received shutdown signal, stopping...")
            stop_event.set()

        try:
            _signal.signal(_signal.SIGTERM, _shutdown_handler)
            _signal.signal(_signal.SIGINT, _shutdown_handler)
        except Exception:
            pass

        R.print_info("[daemon] Daemon ready. Ctrl-C or SIGTERM to stop.")

        try:
            # 持续等待，直到收到停止信号
            while not stop_event.is_set():
                stop_event.wait(timeout=5.0)
        finally:
            R.print_info("[daemon] Shutting down daemon...")
            if http_server:
                http_server.stop()
            # 清理 PID 文件
            try:
                from mini_agent.cli.daemon import _cleanup_pid_files
                _cleanup_pid_files(project_root)
            except Exception:
                pass
        return

    # ── 交互 REPL ─────────────────────────────────────────────────────────────
    import anthropic  # noqa: F401  延迟导入，使 API key 校验错误有更清晰的信息
    from mini_agent.cli.repl import run_repl
    try:
        run_repl(agent, skill_loader)
    finally:
        if http_server:
            http_server.stop()