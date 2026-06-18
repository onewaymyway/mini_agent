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

import sys
from pathlib import Path

import mini_agent.ui.renderer as R


def main() -> None:
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


def _main_inner() -> None:
    """实际的主逻辑，分离出来以便全局异常捕获。"""
    # ── 注册内置工具（side-effect import，必须在 Agent 构造前执行）─────────
    import mini_agent.tools.builtin       # noqa: F401
    import mini_agent.tools.orchestration # noqa: F401
    import mini_agent.tools.plan          # noqa: F401
    import mini_agent.tools.user_input    # noqa: F401
    # tools.skill_manager 由 Agent.__init__ 懒注册（需要 SkillLoader 实例）

    from mini_agent.cli.parser import build_parser
    from mini_agent.config import load_config
    from mini_agent.permissions import PermissionGuard
    from mini_agent.skills import SkillLoader
    from mini_agent.agent import Agent

    parser = build_parser()
    args   = parser.parse_args()

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

            http_server = HttpServer(
                agent            = agent,
                project_root     = cfg.project_root,
                host             = http_host,
                port             = http_port,
                configured_token = http_token,
                allowed_ips      = allowed_ips,
                cors_origins     = cfg.http_cors_origins,
                fs_readonly      = fs_readonly,
                fs_excludes      = cfg.http_fs_excludes or [],
                ring_maxlen      = cfg.http_ring_maxlen,
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

    # ── 交互 REPL ─────────────────────────────────────────────────────────────
    import anthropic  # noqa: F401  延迟导入，使 API key 校验错误有更清晰的信息
    from mini_agent.cli.repl import run_repl
    try:
        run_repl(agent, skill_loader)
    finally:
        if http_server:
            http_server.stop()