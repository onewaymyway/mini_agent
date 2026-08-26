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
    从子命令的 argv 里取出 --project/-p（或别名 --workspace/-w）的值，
    并返回"去掉这两个 token 之后"的剩余 argv。

    [external_projects_workspace_plan.md 阶段 2] `--workspace`/`-w` 是
    `--project`/`-p` 的别名，语义完全相同（都是"以哪个目录为根加载
    配置"），只是外部项目场景下用 `--workspace` 更贴合
    `mini_agent.workspace.Workspace` 的概念，不强制用户改用这个更偏
    "交互式项目根"历史命名的 `--project`。两个别名可以互换，同一次
    调用只需要传其中一个。

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
        if argv[i] in ("--project", "-p", "--workspace", "-w"):
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
    # ── 全局异常日志：进程一启动就安装，覆盖后面所有短路分支 ──────────────────
    # 见 mini_agent/errors.py。安装后：root logger 的 error/exception 调用、
    # 主线程/子线程未捕获异常都会额外落盘到 ~/.agent/logs/error.jsonl。
    from mini_agent.errors import install_global_error_logging
    install_global_error_logging()

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

    # ── workflow 子命令短路：`mini-agent workflow ...` 独立命令行入口 ────────
    # 与 daemon/user/self 短路方式一致：不进入 build_parser() 主流程、不
    # 需要先起一整个交互式 Agent（只需要 load_config()）。之前只能在交互
    # REPL 里用 `/workflow run <name>` 触发，脚本/cron/systemd 场景下要求
    # 用户先进入交互模式很别扭；现在两条路径共用同一套
    # cli/commands/workflow_cmd.py 实现，见该文件顶部 run_workflow_cli()
    # 的说明。
    if len(sys.argv) > 1 and sys.argv[1] == "workflow":
        from mini_agent.cli.commands.workflow_cmd import run_workflow_cli
        project_root, rest = _extract_project_root(sys.argv[2:])
        return run_workflow_cli(rest, project_root)

    # ── hybrid-exec 子命令短路：`mini-agent hybrid-exec ...` 独立命令行入口 ──
    # 与 workflow 短路方式完全一致：不进入 build_parser() 主流程、不需要先
    # 构造交互式 Agent，只 load_config() 即可，适合脚本/cron/systemd 场景
    # 直接触发一次 hybrid_exec 任务（已有 active 脚本会优先复用）。
    # 见 hybrid_exec/cli.py 头部说明。
    if len(sys.argv) > 1 and sys.argv[1] == "hybrid-exec":
        from mini_agent.hybrid_exec.cli import run_hybrid_exec_cli
        project_root, rest = _extract_project_root(sys.argv[2:])
        return run_hybrid_exec_cli(rest, project_root)

    # ── projects 子命令短路：`mini-agent projects ...` 外部项目注册表 ────────
    # 对应 next_doc/external_projects_workspace_plan.md 阶段 3。与
    # workflow/hybrid-exec 短路方式完全一致：不构造 Agent，只操作本地
    # 注册表文件 + 按需触发一次 entrypoint 子进程执行。见
    # cli/commands/projects_cmd.py 顶部说明。
    if len(sys.argv) > 1 and sys.argv[1] == "projects":
        from mini_agent.cli.commands.projects_cmd import run_projects_cli
        project_root, rest = _extract_project_root(sys.argv[2:])
        return run_projects_cli(rest, project_root)

    # ── 全局异常捕获：确保任何启动错误都能显示 ────────────────────────────────
    try:
        _main_inner()
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where="cli.app.main")
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
    import mini_agent.tools.external_projects  # noqa: F401  [external_projects_workspace_plan 阶段5] list_projects/inspect_project/trigger_run/propose_fix
    import mini_agent.tools.workdir_knowledge  # noqa: F401  [W2 / Stage 4] add_open_thread/update_work_thread/update_knowledge/search_knowledge
    import mini_agent.tools.notepad       # noqa: F401  [记事本] notepad_add/update/remove/list/summarize
    import mini_agent.tools.recall_history  # noqa: F401  [compact_mechanism_improvement_plan P2-B] recall_from_raw_history
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
        show_reasoning=getattr(args, "show_reasoning", None),
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

    # ── 可加载对象的平台/tag 过滤策略 ─────────────────────────────────────────
    # 必须在 skill / agent profile / hooks / tool 的发现与注册之前初始化，
    # 否则这些发现逻辑拿到的会是懒加载出的默认（cwd 而非 cfg.project_root）单例。
    # 读取 <project_root>/platform_policy.json，不存在则完全不限制（no-op）。
    from mini_agent.platform_filter import init_load_policy
    init_load_policy(cfg.project_root)

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

    # ── 角色扮演（Persona）系统 ────────────────────────────────────────────────
    from mini_agent.orchestrator.persona_profiles import init_personas
    persona_loader = init_personas(cfg)
    if persona_loader.available:
        R.print_info(f"Personas available: {persona_loader.available}")

    # ── Role Agent 系统 ───────────────────────────────────────────────────────
    # [判官接线统一 阶段六] dispatcher 从"role_agent 专属对象"升级为"判官
    # 触发的公共基础设施"：只要 goal_mode/turn_judge 任一子系统开启，即使
    # role_agent.enabled=False，也需要构造 dispatcher 才能让内建的
    # goal_judge/turn_judge profile 被统一注册（详见 role_agents/dispatcher.py
    # 的 _discover() 两段式逻辑）。role_agent.enabled 只影响是否额外加载磁盘上
    # 的自定义 evaluator/coach 等 profile，不再是 dispatcher 是否存在的唯一条件。
    from mini_agent.role_agents import init_role_agent_system
    if cfg.role_agent.enabled or cfg.goal_mode.enabled or cfg.turn_judge.enabled:
        role_sys = init_role_agent_system(cfg, profile_loader)
        if role_sys.has_output_roles or role_sys.has_tool_roles:
            R.print_info(f"Role agents ready: {role_sys.summary}")
        if not cfg.role_agent.enabled:
            R.print_info("[RoleAgent] role_agent.enabled=False（未加载磁盘自定义角色 Agent），"
                          "dispatcher 仅用于内建判官（goal_judge/turn_judge）接线")
    else:
        R.print_info("[RoleAgent] 未启用（使用 --role-agents 参数开启）")

    # ── 工作流系统 ────────────────────────────────────────────────────────────
    from mini_agent.workflow.tools import register_workflow_tools
    register_workflow_tools(cfg)
    R.print_info("Workflow tools registered (generate/save/run/list/show/delete_workflow)")

    # ── hybrid_exec 工具（脚本/LLM/Agent 混合执行）───────────────────────────
    # 放在 workflow 工具之后、myplugins 之前：hybrid_exec 独立于 workflow，
    # 不依赖 workflow 子系统是否就绪；myplugins 里的 hybrid_step 插件是
    # workflow 场景下的接入方式，这里注册的是给主 Agent 直接用的工具函数，
    # 两者互不影响、可以同时存在。
    from mini_agent.hybrid_exec.tools import register_hybrid_exec_tools
    register_hybrid_exec_tools(cfg)
    R.print_info("hybrid_exec tools registered (run/list/show_hybrid_exec_task)")

    # ── myplugins/ 插件发现（P7-④2） ─────────────────────────────────────────
    # 放在 register_workflow_tools 之后：插件的 register(cfg) 里若要调用
    # workflow.executors.register_step_executor() 注册自定义 step 类型，
    # 此时 workflow 子系统（executors 模块）已可用。
    from mini_agent.plugins import discover_and_register_plugins
    loaded_plugins = discover_and_register_plugins(cfg)
    if loaded_plugins:
        R.print_info(f"Plugins loaded from myplugins/: {loaded_plugins}")

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
            # [BUGFIX] 已知坑：连接到一个"已经在跑"的 daemon 时，本次命令行带的
            # --debug-llm / --debug-llm-console 完全不会生效——因为这里根本不会
            # 在当前（客户端）进程构建 Agent/cfg，实际处理 LLM 调用的是那个更早
            # 启动、且启动时未必带了这个 flag 的 daemon 进程。这个坑排查成本很高
            # （现象是"加了 --debug-llm 但 llm_debug.jsonl 一直是空的"），这里
            # 提前给出明确提示，而不是让用户去猜。
            if getattr(args, "debug_llm", False) or getattr(args, "debug_llm_console", False):
                R.print_warning(
                    "[daemon] 检测到已有 daemon 在运行，本次连接会直接复用它的进程，"
                    "这次命令行带的 --debug-llm/--debug-llm-console 对它不会生效"
                    "（该 flag 只在启动 daemon 那一刻起作用）。如果需要调试日志，"
                    "请先 `mini-agent daemon stop`，再用 "
                    "`mini-agent daemon start --debug-llm` 重新启动 daemon。"
                )
            # 额外探活：PID 存在 + HTTP 服务真正就绪才走连接模式
            # --token（-T）：显式指定连接身份用的 token，多用户 daemon 下
            # 用它决定"以哪个用户连接"；不传则 DaemonClient 内部按原有优先级
            # 回退到 .agent/agent_api.key 文件（单用户/owner 行为不变）。
            _cli_token = getattr(args, "token", None)
            _client = DaemonClient(
                _daemon_info["http_port"],
                token=_cli_token,
                project_root=project_root,
            )
            if _client.health_check():
                # daemon HTTP 就绪，走连接模式（不构建本进程 Agent）。
                # 注意：不再在这里 stop_status_bar()。
                # run_connected_repl() 内部会把 provider 替换为 connected 模式专用的
                # _connected_status_bar_provider，显示 daemon session/state 信息，
                # 退出时再清除（set_statusbar_provider(None)）。
                run_connected_repl(_daemon_info, token=_cli_token)
                return
            else:
                # PID 存在但 HTTP 不通：daemon 可能刚启动或已崩溃
                # 等待最多 3 秒再试一次
                import time as _time
                _time.sleep(3)
                if _client.health_check():
                    run_connected_repl(_daemon_info, token=_cli_token)
                    return
                # 仍不通：回退到独立进程模式，打印提示
                R.print_warning(
                    f"[daemon] Daemon PID={_daemon_info['pid']} found but HTTP not responding. "
                    "Starting in standalone mode. Use 'mini-agent daemon status' to check."
                )

    # ── Agent ─────────────────────────────────────────────────────────────────
    agent = Agent(cfg=cfg, skill_loader=skill_loader, guard=guard)

    # [具身改进 B4 本地路径接入] 与 daemon 多用户路径（api/session_pool.py::
    # SessionAgentPool._create_entry()）共用同一实现，消除"AffordanceMap 只在
    # daemon 多用户模式生效"的已知不对称。失败静默降级，不阻断 REPL 启动。
    # 详见 docs/embodied-agent-guide.md §8、next_doc/priority_improvements_implementation_plan.md 方案一。
    try:
        from mini_agent.perception.affordance_analyzer import inject_affordance_map
        inject_affordance_map(agent, cfg)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.cli.app._main_inner')
        pass

    # ── Resume session ────────────────────────────────────────────────────────
    if getattr(args, "resume", None):
        if agent.load_session(args.resume):
            R.print_success(
                f"Resumed session [{agent.session_id}] — {len(agent.history)} messages loaded"
            )
            # 保存一份"resume 时刻"的快照，使得在本次进程内尚未执行任何新
            # run_turn 之前，/rollback 也能回到刚 resume 完的状态（而不是
            # 报 "Nothing to rollback" —— _turn_snapshot 是纯内存态，不随
            # session 文件持久化，resume 后默认是 None）。
            # /retry 语义上需要"上一轮的用户输入"来重新发送，resume 时刻没有
            # 这个信息，所以 /retry 在这里仍然无法工作，需要用户先手动发一轮。
            try:
                agent._save_turn_snapshot()
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.cli.app._main_inner')
                pass
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
                access_log_enabled = cfg.http.access_log_enabled,
                access_log_path    = cfg.http.access_log_path,
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
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.cli.app._main_inner')
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

        # [FIX] 之前这里无条件 set_headless_mode(True)，理由（后台 detach
        # 进程没有真正终端）只对 detach 模式成立。--daemon-attach-console
        # （只有前台、不带 --detach 时才会被 cmd_daemon_start 附加）意味着
        # 当前进程有一个真实 tty：permissions.py::_prompt_with_http() 和
        # interaction.py::ask() 本来就设计成"本地 CLI 输入 + HTTP 双路
        # 竞速"，这条本地路径靠的正是 sys.stdin.isatty()／_HEADLESS_MODE
        # 判断——foreground 场景下应该保持 False，让本地终端直接参与审批/
        # 回答交互，而不是被强制退化成"只能等其他客户端处理"。
        # detach（真正的后台进程，stdout/stderr 重定向到 daemon.log，没有
        # 可用 stdin）继续保持 headless，避免白白卡到超时。
        attach_console = getattr(args, "daemon_attach_console", False)
        from mini_agent.permissions import set_headless_mode
        set_headless_mode(not attach_console)

        # 写入 PID 文件
        try:
            from mini_agent.cli.daemon import _write_pid
            http_port = getattr(args, "http_port", None) or 8765
            _agent_name = getattr(cfg, "agent_name", None) or None
            _write_pid(project_root, os.getpid(), http_port, agent_name=_agent_name)
            R.print_info(f"[daemon] Running in daemon mode, PID={os.getpid()}, port={http_port}")
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.cli.app._main_inner')
            R.print_warning(f"[daemon] Failed to write PID file: {e}")

        stop_event = _threading.Event()

        def _shutdown_handler(signum, frame):
            R.print_info("[daemon] Received shutdown signal, stopping...")
            stop_event.set()

        # [daemon-stop-graceful-fix] 把这个 daemon 自己的 stop_event 暴露
        # 给 HTTP 层的 POST /v1/shutdown（api/routes.py::request_shutdown），
        # 让 `mini-agent daemon stop` 能通过一条与平台无关的通道（HTTP，
        # 而不是跨进程信号/TerminateProcess）触发下面同一套优雅关停 +
        # finally 清理逻辑。Windows 上之前 `daemon stop` 直接
        # TerminateProcess 硬杀该进程，绕过了这里的 finally，导致
        # --daemon-attach-console 场景下 prompt_toolkit 改过的控制台输入
        # 模式来不及恢复（回车无响应、方向键历史失效）。
        if http_server is not None:
            http_server.shutdown_event = stop_event

        try:
            _signal.signal(_signal.SIGTERM, _shutdown_handler)
            _signal.signal(_signal.SIGINT, _shutdown_handler)
            # [daemon-stop-graceful-fix] Windows 没有 SIGTERM；
            # cli/daemon.py::cmd_daemon_stop 的第 2 级兜底改用
            # GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT)，Python 会把它
            # 转换成 signal.SIGBREAK 交给这里同一个 handler 处理，
            # 与 HTTP 优雅关停（第 1 级）走的是同一套 finally 清理路径。
            if sys.platform == "win32" and hasattr(_signal, "SIGBREAK"):
                _signal.signal(_signal.SIGBREAK, _shutdown_handler)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.cli.app')
            pass

        R.print_info("[daemon] Daemon ready. Ctrl-C or SIGTERM to stop.")

        # [FIX #1] 前台（非 --detach）daemon 进程之前只是在这里裸等信号：
        # 不读用户输入。已解决。
        #
        # [FIX #2 / 本次修正] 第一版修复直接复用了 run_connected_repl()——
        # 那一整套是为"从另一个终端 connect 上来的外部客户端"设计的，
        # 会另起一个 SSE 观察者线程，把同一个 session 的所有事件重新渲染
        # 一遍。但 api/server.py::_install_output_hook() 这个 monkey-patch
        # 本来就是"daemon 进程自己（这条终端）先原样 print 一遍，再顺带
        # 广播给 HTTP/SSE 客户端"——也就是说 daemon 自己的终端从来不需要
        # 额外订阅 SSE 才能看到内容，它本来就是"第一现场"。复用
        # run_connected_repl 相当于又订阅了一遍自己已经在打印的东西，
        # 表现出来就是同一条内容打印两遍（一遍无前缀、一遍
        # "[其他终端]" 前缀），且 interaction_req/permission_req 这类
        # "谁先响应算谁的"交互，daemon 自己的本地路径（见上面
        # set_headless_mode(False)）和这个多余的 observer 路径又会互相
        # 抢——不是"和 attach 的客户端显示一样"，而是画蛇添足。
        #
        # 正确做法：daemon 前台终端不需要 observer/渲染，只需要"能输入"。
        # 输入仍然通过本地 loopback HTTP 提交（/v1/chat，与其他客户端走
        # 同一条 AgentRunner 队列，避免多线程直接并发调用 agent.run_turn()
        # 带来的状态竞争），但提交后只静默等待这一轮结束（不注册任何
        # token/tool_call 等回调——那些内容已经由本地 hook 原样打印过了），
        # 结束后再读下一行输入。期间出现的权限审批/交互提问，走的是上面
        # 打开的"本地 CLI + HTTP 双路"，在同一个终端里直接问、直接答，
        # 不会被 observer 抢答，也不会重复打印。
        try:
            if attach_console:
                from mini_agent.cli.daemon import DaemonClient, _read_daemon_info
                from mini_agent.ui.terminal import get_terminal

                daemon_info = _read_daemon_info(project_root) or {
                    "pid": os.getpid(),
                    "http_port": http_port,
                    "project_root": str(project_root),
                }
                _client = DaemonClient(
                    daemon_info["http_port"], token=None,
                    project_root=daemon_info.get("project_root") or project_root,
                )
                _term = get_terminal()
                # [FIX] daemon 前台 attach-console 场景下，本地终端要同时
                # 承受：主线程自己的输入提示符渲染、AgentRunner 线程为
                # 其它 session/web 请求打印的内容、状态栏的周期性原地刷新
                # ——三路都在同一个物理终端上，且正常模式下 Terminal 靠
                # "记住已经画了几行状态栏/内容处于哪一行"来做相对光标
                # 移动+擦除（\x1b[nA \x1b[2K 之类）。这套记账只要遇到：
                # 长文本被终端自动折行（物理行数和逻辑行数对不上）、或者
                # 多个来源交替写屏幕，就非常容易算错，表现为内容被腰斩/
                # 前缀被冲掉/整体排版错位——也就是这几轮反馈里反复出现的
                # 问题。attach-console 这个场景的正确取舍是：宁可没有
                # 状态栏原地刷新的视觉效果，也要保证内容不丢、不错位。
                # simple_mode 下 Terminal 完全不做任何擦除/光标控制，
                # 所有输出按到达顺序原样打印（颜色仍然保留，只是不再有
                # "原地刷新"的状态栏），从根上避免这一整类竞态。
                _term.set_simple_mode(True)

                # [FIX] 输入循环之前跑在一条单独的后台线程
                # （daemon-attach-console）上——这是颜色丢失/状态栏把
                # 前缀擦掉/输入行排版跑飞、最终整个终端失控吐给底层 shell
                # 这一整串问题的根因：Terminal 依赖的 prompt_toolkit
                # Application（含它自己的 asyncio 事件循环、信号处理）
                # 设计上只能跑在主线程——放到子线程里初始化会静默退化/
                # 出错，表现出来就是画面完全不受 ptk 正常管理，跟
                # 状态栏刷新线程、渲染线程各自乱写屏幕，互相打架。
                # 真正的输入循环必须留在主线程里跑；改成用一条单独的
                # 后台线程只负责等待外部停止信号（SIGTERM/SIGINT 或
                # 输入循环自己退出时 set 的 stop_event），不做任何终端
                # 相关操作，因此放在子线程上是安全的。
                def _wait_for_stop() -> None:
                    while not stop_event.is_set():
                        stop_event.wait(timeout=5.0)

                _stop_wait_thread = _threading.Thread(
                    target=_wait_for_stop, name="daemon-stop-wait", daemon=True
                )
                _stop_wait_thread.start()

                try:
                    while not stop_event.is_set():
                        try:
                            line = _term.prompt_user()
                        except (KeyboardInterrupt, EOFError):
                            break
                        if line is None:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        # ptk 输入行 erase_when_done=True，提交后会自己把
                        # 那行擦掉；这里走统一渲染队列补打印一次带颜色的
                        # 回显，和 repl.py（非 daemon 本地 REPL）、
                        # daemon.py（connected 客户端）保持完全一致的
                        # 样式，而不是把提示符文字直接传给 prompt_user()
                        # ——那样既没有颜色，也不会被纳入 Terminal 统一的
                        # 记账体系。
                        from rich.markup import escape as _esc_echo_console
                        _term.print(
                            f"[bold green]You[/bold green][bold cyan] \u276f [/bold cyan]{_esc_echo_console(line)}"
                        )
                        if line in ("exit", "quit", ":q"):
                            break
                        # [FIX] 之前 send_message/stream_output 的异常和
                        # "用户主动退出输入循环"共用同一个外层
                        # except Exception -> finally: stop_event.set()，
                        # 导致单次请求（比如一次 HTTP 调用失败、或
                        # stream_output 内部报错）就会被静默吞掉
                        # （只写进 error.jsonl，终端上什么提示都没有），
                        # 然后连带把整个 daemon 关停——表现出来就是
                        # "daemon 进程在命令行输入之后就结束了"。
                        # 这里把单轮请求的异常收窄到本轮范围内处理：
                        # 打印出来、continue 到下一次输入，不再殃及
                        # 整个 daemon 进程。
                        try:
                            turn_id = _client.send_message(line, session_id=agent.session_id)
                            if not turn_id:
                                _term.print("[red][daemon] 消息提交失败[/red]")
                                continue
                            # 静默等待这一轮结束——内容已经由本地
                            # output hook 原样打印过，这里不传任何回调，
                            # 单纯阻塞到 turn_done 以便知道何时可以再次
                            # 提示输入。
                            _client.stream_output(turn_id)
                        except Exception as _mini_agent_exc:
                            from mini_agent.errors import log_exception
                            log_exception(_mini_agent_exc, where='mini_agent.cli.app.attach_console.turn')
                            _term.print(
                                f"[red][daemon] 本次请求出错，daemon 继续运行: {_mini_agent_exc}[/red]"
                            )
                            continue
                except Exception as _mini_agent_exc:
                    # 只有输入循环自身彻底失控（比如 _term 初始化/
                    # prompt_user 底层异常）才会走到这里——这种情况下
                    # 才有理由认为"这个终端"已经无法继续承担 daemon
                    # 前台角色，需要关停。
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.cli.app.attach_console.loop')
                finally:
                    # 用户主动退出了输入循环（exit/quit/EOF/Ctrl-C），
                    # 或者输入循环自身崩溃——这两种情况才等价于要求
                    # 关停整个 daemon（前台进程本来就是"这个终端就是
                    # daemon"的模型）。单轮请求异常已经在上面被吞掉、
                    # continue 掉，不会走到这里。
                    stop_event.set()
            else:
                # 持续等待，直到收到停止信号（SIGTERM/SIGINT）
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
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.cli.app')
                pass
        return

    # ── 交互 REPL ─────────────────────────────────────────────────────────────
    import anthropic  # noqa: F401  延迟导入，使 API key 校验错误有更清晰的信息
    from mini_agent.cli.repl import run_repl

    # [SYS-GOAL-MODE] 启动时检测是否有未完成的 goal 任务（比如上次进程被意外杀死），
    # 只做提示，不强制打断，用户可以选择 /goal resume 或忽略继续正常对话。
    try:
        if getattr(cfg.goal_mode, "auto_resume_prompt", True):
            from mini_agent.goal_mode.state import find_resumable_session, list_resumable_sessions
            _resumable_sid = find_resumable_session(project_root)
            if _resumable_sid and _resumable_sid != agent.session_id:
                # [FIX] 之前只提示"最近一个" session，如果有多个进程各自 /goal 了
                # 不同目标、都被意外杀死，这里只报一个会让用户误以为其他的丢了
                # （其实文件都还在，只是没暴露入口）。这里额外数一下总数，
                # 超过 1 个时提示用 /goal list 查看全部。
                _all_sessions = list_resumable_sessions(project_root)
                _total = len(_all_sessions)
                if _total > 1:
                    R.print_warning(
                        f"[Goal 模式] 检测到 {_total} 个未完成的目标任务，最近一个是 "
                        f"session: {_resumable_sid}。输入 [bold]/goal list[/bold] 查看全部，"
                        f"或直接 [bold]/goal resume {_resumable_sid}[/bold] 恢复最近这个，"
                        "也可忽略进入正常对话。"
                    )
                else:
                    R.print_warning(
                        f"[Goal 模式] 检测到未完成的目标任务（session: {_resumable_sid}），"
                        f"输入 [bold]/goal resume {_resumable_sid}[/bold] 可继续执行，"
                        "或直接忽略进入正常对话。"
                    )
    except Exception as e:
        # 不静默吞掉——检测逻辑本身出错也应该让用户/开发者看到，
        # 否则会呈现出"明明有未完成的 goal 却什么提示都没有"的假象，无法排查。
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.app._main_inner')
        if getattr(cfg, "verbose", False):
            R.print_warning(f"[Goal 模式] 启动时检测未完成任务失败：{e}")

    try:
        run_repl(agent, skill_loader)
    finally:
        if http_server:
            http_server.stop()