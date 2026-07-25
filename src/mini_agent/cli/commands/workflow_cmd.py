"""
cli/commands/workflow_cmd.py — workflow 子命令处理

两种触发方式，共用同一套子命令分发（见 handle_workflow_cmd / run_workflow_cli
/ _dispatch）：
  1. 交互 REPL 内的 `/workflow ...` 斜杠命令（cli/repl.py 调用
     handle_workflow_cmd(args, agent)，从已构造好的 agent 取 cfg）。
  2. 独立命令行 `mini-agent workflow ...`（cli/app.py 的 main() 最前面按
     sys.argv[1] == "workflow" 短路，调用 run_workflow_cli(argv,
     project_root)，只 load_config() 不构造 Agent，不需要先进入交互模式，
     适合 cron/systemd/CI 里直接触发一次已保存的 workflow）。两种方式下
     `run`/`resume --background` 的实现不同：REPL 里进程本来就会长期存活，
     用 daemon 线程即可；独立 CLI 一次性命令跑完就退出，daemon 线程会被
     一起杀掉，因此改成 spawn 一个真正独立的 OS 子进程（见
     _spawn_detached_run），父进程退出后子进程仍会跑完。

子命令列表（与 workflow/tools.py 里暴露给 Agent 的工具一一对应，供用户直接
操作，不需要绕一圈让主 Agent 去调用工具；下面用 `/workflow` 前缀举例，
独立 CLI 场景把前缀换成 `mini-agent workflow` 即可，子命令名和参数不变）：
  /workflow list                          — 列举所有已保存的工作流
  /workflow show <name>                   — 查看工作流 YAML 定义
  /workflow run <name> [inputs_json] [--background]
                                           — 执行工作流
  /workflow runs [name]                   — 列举执行记录（可按工作流名过滤）
  /workflow status <workflow_session_id>  — 查看某次执行的详细进度
  /workflow resume <workflow_session_id> [--background]
                                           — 从断点续跑
  /workflow pause <workflow_session_id>   — 暂停一次后台执行
  /workflow cancel <workflow_session_id>  — 取消一次执行
  /workflow approve <workflow_session_id> — 批准当前等待审批的步骤
  /workflow reject <workflow_session_id> [reason]
                                           — 拒绝当前等待审批的步骤
  /workflow input <workflow_session_id> <text>
                                           — [P5] 向正在等待人工输入（human_input
                                             类型 step）的执行送入文本
  /workflow templates                     — [P6] 列举内置工作流模板
  /workflow from-template <template_name> <new_name>
                                           — [P6] 基于内置模板创建并保存一个新工作流
  /workflow delete <name>                 — 删除工作流定义
  /workflow to-dir <name>                 — 将单文件工作流升级为文件夹模式
                                             （生成 agents/skills/prompts 子目录）
  /workflow sessions                      — [session_to_workflow_design.md P8]
                                             列出最近的历史 session，帮助定位 session_id
  /workflow from-session <session_id>     — [P8] 从指定 session 生成 workflow：
                                             总结→展示确认→构建→展示确认→保存，
                                             全程以连续的确认提示呈现（会读取
                                             stdin 交互确认，独立 CLI 场景下需要
                                             在能交互输入的终端里跑，不适合完全
                                             无人值守的 cron/systemd 场景）
  /workflow stats <name>                  — [P9-1a] 汇总历史执行统计（成功率/
                                             各步骤平均耗时评分重试率/condition命中率）
  /workflow history <name>                — [P9-2] 查看该 workflow 定义文件的
                                             git 提交历史（直接复用 git log，
                                             不重新发明版本历史机制）
  /workflow diff <name>                   — [P9-2] 查看该 workflow 定义相对
                                             上次 commit 的改动（结构化 step
                                             级别摘要 + 原始 git diff）

独立 CLI 独有参数：`--project`/`-p <path>` 指定项目根目录（默认当前目录），
解析方式与 daemon/user/self 子命令一致，见 cli/app.py::_extract_project_root。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import mini_agent.ui.renderer as R


def handle_workflow_cmd(args: list[str], agent) -> None:
    """REPL 内 `/workflow ...` 斜杠命令入口，从已经构造好的 agent 取 cfg。"""
    _dispatch(args, agent.cfg)


def run_workflow_cli(argv: list[str], project_root: Path) -> int:
    """
    `mini-agent workflow ...` 独立命令行入口（不进入交互 REPL、不构造
    Agent）。与 `/workflow ...` 共用下面同一套 `_dispatch`/`_handle_*`
    实现——这些函数本来就只依赖 `cfg`，从未真正用到 `agent` 的其它能力
    （工具调用、对话历史等），所以这里只需要单独 `load_config()` 一次，
    不需要经过 `daemon`/`user`/`self` 那种"转发给已运行进程"的 HTTP 方式，
    也不需要像交互模式一样装配 SkillLoader/PermissionGuard/Agent。

    典型用途：cron/systemd timer、CI 流水线、shell 脚本里直接跑一个已保存
    的 workflow，不需要为此启动一整个交互式 Agent 会话。例如：

        mini-agent workflow run nightly_release_check \\
            '{"release_tag": "v1.4.0"}' --background --project /path/to/repo

    与 cli/app.py 里 `daemon`/`user`/`self`/`eval` 子命令的短路方式一致：
    在 main() 最前面按 `sys.argv[1] == "workflow"` 整体拦截，不进入
    build_parser() 的主 argparse 流程（该流程的位置参数 `prompt` 与子命令
    模式互斥，见 eval_cmd.py 顶部注释里的同一条说明）。

    返回进程退出码：找不到子命令/参数错误时返回 1，正常执行（包括
    `run`/`resume` 在前台同步跑完但工作流本身状态是 failed/partial）返回 0
    ——"命令本身有没有跑起来"和"工作流执行结果好不好"是两回事，后者请用
    `mini-agent workflow status <id>` 或退出后检查落盘的 session 记录，不
    通过进程退出码表达，与 `/workflow run` 在 REPL 里的语义保持一致。
    """
    from mini_agent.config import load_config

    if not argv:
        _print_usage()
        _flush_terminal()
        return 1

    cfg = load_config(project_root=project_root)
    try:
        _dispatch(argv, cfg, standalone=True)
    except SystemExit:
        raise
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where="mini_agent.cli.commands.workflow_cmd.run_workflow_cli")
        R.print_error(f"执行失败：{e}")
        return 1
    finally:
        _flush_terminal()
    return 0


def _flush_terminal() -> None:
    """[独立 CLI 一次性命令] R.print_info/print_error 底层是 terminal.term，
    状态栏刷新/补打印跑在后台线程、按低频定时器/队列异步落盘到 stdout（见
    ui/terminal.py Terminal.stop() 的注释）。REPL 场景下进程本来就长期存活，
    这层异步不是问题；独立 CLI 命令跑完立刻返回、解释器随即退出，不显式
    stop() 的话，最后一批消息可能还没来得及被后台线程画到 stdout 上，输出
    就"丢了"（不是真丢，只是没赶上打印）。与 repl.py/daemon.py 退出前调用
    _term.stop() 是同一个原因，见那两处。"""
    from mini_agent.ui.terminal import term as _term
    try:
        _term.stop()
    except Exception:
        pass


def _print_usage() -> None:
    R.print_error(
        "用法（REPL 内用 /workflow ...；命令行直接用 mini-agent workflow ...）：\n"
        "  workflow list                         列举所有已保存的工作流\n"
        "  workflow show <name>                  查看工作流 YAML 定义\n"
        "  workflow run <name> [inputs_json] [--background]\n"
        "                                         执行工作流\n"
        "  workflow runs [name]                  列举执行记录\n"
        "  workflow status <workflow_session_id>  查看某次执行的详细进度\n"
        "  workflow resume <workflow_session_id> [--background]\n"
        "                                         从断点续跑\n"
        "  workflow pause <workflow_session_id>   暂停一次后台执行\n"
        "  workflow cancel <workflow_session_id>  取消一次执行\n"
        "  workflow approve <workflow_session_id> 批准当前等待审批的步骤\n"
        "  workflow reject <workflow_session_id> [reason]\n"
        "                                         拒绝当前等待审批的步骤\n"
        "  workflow input <workflow_session_id> <text>\n"
        "                                         向等待人工输入的步骤送入文本\n"
        "  workflow templates                     列举内置工作流模板\n"
        "  workflow from-template <template_name> <new_name>\n"
        "                                         基于内置模板创建工作流\n"
        "  workflow delete <name>                 删除工作流定义\n"
        "  workflow to-dir <name>                 升级为文件夹模式（agents/skills/prompts）\n"
        "  workflow sessions                      列出最近的历史 session\n"
        "  workflow from-session <session_id>     从指定 session 生成 workflow（总结→确认→构建→确认→保存）\n"
        "  workflow stats <name>                  汇总历史执行统计（成功率/步骤耗时评分重试率/condition命中率）\n"
        "  workflow history <name>                查看该 workflow 定义文件的 git 提交历史\n"
        "  workflow diff <name>                   查看该 workflow 定义相对上次 commit 的改动\n"
        "命令行独有参数：--project/-p <path> 指定项目根目录（默认当前目录）"
    )


def _dispatch(args: list[str], cfg, standalone: bool = False) -> None:
    """真正的子命令分发，供 REPL（`/workflow ...`）和独立 CLI
    （`mini-agent workflow ...`）共用；两者的差异只在 cfg 的来源，以及
    `run`/`resume` 的 `--background` 到底是"进程内 daemon 线程"还是
    "spawn 一个独立 OS 子进程"（见 _handle_run/_handle_resume 里的
    standalone 分支和 _spawn_detached_run）。"""
    if not args:
        _print_usage()
        return

    sub = args[0].lower()
    rest = args[1:]

    if sub == "list":
        _handle_list(cfg)
    elif sub == "show":
        _handle_show(cfg, rest)
    elif sub == "run":
        _handle_run(cfg, rest, standalone=standalone)
    elif sub == "runs":
        _handle_runs(cfg, rest)
    elif sub == "status":
        _handle_status(cfg, rest)
    elif sub == "resume":
        _handle_resume(cfg, rest, standalone=standalone)
    elif sub == "pause":
        _handle_pause(rest)
    elif sub == "cancel":
        _handle_cancel(rest)
    elif sub == "approve":
        _handle_approve(rest)
    elif sub == "reject":
        _handle_reject(rest)
    elif sub == "input":
        _handle_input(rest)
    elif sub == "templates":
        _handle_templates(cfg)
    elif sub == "from-template":
        _handle_from_template(cfg, rest)
    elif sub == "delete":
        _handle_delete(cfg, rest)
    elif sub == "to-dir":
        _handle_to_dir(cfg, rest)
    elif sub == "sessions":
        _handle_sessions(cfg, rest)
    elif sub == "from-session":
        _handle_from_session(cfg, rest)
    elif sub == "stats":
        _handle_stats(cfg, rest)
    elif sub == "history":
        _handle_history(cfg, rest)
    elif sub == "diff":
        _handle_diff(cfg, rest)
    else:
        R.print_error(f"未知子命令：{sub}（输入 workflow 查看用法）")


def _handle_list(cfg) -> None:
    from mini_agent.workflow.store import WorkflowStore
    store = WorkflowStore(Path(cfg.project_root))
    all_wf = store.list_all()
    if not all_wf:
        R.print_info("📭 当前没有已保存的工作流。可让 Agent 调用 generate_workflow 创建一个。")
        return
    R.print_info(f"📋 共 {len(all_wf)} 个工作流：")
    for wf in all_wf:
        R.print_info(f"  - {wf['name']} (v{wf['version']})：{' → '.join(wf['steps'])}")


def _handle_show(cfg, rest: list[str]) -> None:
    if not rest:
        R.print_error("用法：/workflow show <name>")
        return
    from mini_agent.workflow.store import WorkflowStore
    store = WorkflowStore(Path(cfg.project_root))
    yaml_str = store.export_yaml(rest[0])
    if yaml_str is None:
        R.print_error(f"找不到工作流 {rest[0]!r}")
        return
    R.print_info(yaml_str)


def _spawn_detached_run(cfg, base_argv: list[str], wf_session_id: str) -> Path:
    """
    [独立 CLI 后台执行] 以 `python -m mini_agent` 重新拉起自己一个子进程，
    带上隐藏参数 `--__detached-session-id <id>` 让子进程直接前台同步执行
    （见 _handle_run 里对应分支），子进程的标准输出/错误重定向到落盘的
    日志文件，且不继承父进程的进程组（`start_new_session=True`，POSIX 下
    等价于 `setsid`）——父进程（乃至触发它的 shell/cron/systemd）退出后，
    子进程仍会独立跑完，这是它与 REPL 内 daemon 线程方案的本质区别。

    base_argv: 不含 'workflow' 本身的子命令 argv，如
               ["run", "<name>", "<inputs_json>"] 或
               ["resume", "<workflow_session_id>"]。
    返回子进程 stdout/stderr 重定向到的日志文件路径，供提示用户。
    """
    import subprocess
    import sys as _sys
    from mini_agent.storage.paths import AgentPaths

    paths = AgentPaths(project_root=cfg.project_root)
    session_dir = paths.workflow_session_dir(wf_session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    log_path = session_dir / "cli_detached.log"

    argv = [
        _sys.executable, "-m", "mini_agent",
        "workflow", *base_argv,
        "--__detached-session-id", wf_session_id,
        "--project", str(cfg.project_root),
    ]
    with open(log_path, "ab") as log_f:
        kwargs = {}
        if hasattr(__import__("os"), "setsid"):
            kwargs["start_new_session"] = True  # POSIX：脱离父进程组，父进程退出不影响子进程
        subprocess.Popen(
            argv,
            stdout=log_f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            cwd=str(cfg.project_root),
            **kwargs,
        )
    return log_path


def _handle_run(cfg, rest: list[str], standalone: bool = False) -> None:
    if not rest:
        R.print_error("用法：workflow run <name> [inputs_json] [--background]")
        return

    # [独立 CLI 后台执行] 隐藏内部参数：detached 子进程重新以 `mini-agent
    # workflow run ...` 调用自己时，用这个参数带回父进程预先分配好的
    # workflow_session_id，并强制走"前台同步执行"这条分支——detach 只需要
    # 发生一次，子进程不应该再继续 fork 一层。见 _spawn_detached_run()。
    detached_session_id = None
    if "--__detached-session-id" in rest:
        idx = rest.index("--__detached-session-id")
        if idx + 1 < len(rest):
            detached_session_id = rest[idx + 1]
        rest = rest[:idx] + rest[idx + 2:]

    name = rest[0]
    background = "--background" in rest
    positional = [a for a in rest[1:] if a != "--background"]
    inputs_json = positional[0] if positional else "{}"

    from mini_agent.workflow.store import WorkflowStore
    from mini_agent.workflow.runner import WorkflowRunner

    store = WorkflowStore(Path(cfg.project_root))
    wf = store.load(name)
    if wf is None:
        R.print_error(f"找不到工作流 {name!r}")
        return
    try:
        parsed_inputs = json.loads(inputs_json) if inputs_json.strip() else {}
    except json.JSONDecodeError as e:
        R.print_error(f"inputs 不是合法 JSON：{e}")
        return

    from mini_agent.workflow.runner import step_requires_approval
    has_approval_step = any(step_requires_approval(s, getattr(cfg, "workflow", None)) for s in wf.steps)
    if has_approval_step and not background and detached_session_id is None:
        R.print_warning("该工作流包含需要人工审批的步骤，已自动切换为 --background 执行")
        background = True

    runner = WorkflowRunner(cfg)

    # detached 子进程：直接前台同步跑，用父进程分配好的 session id，
    # 不再 fork、不再关心 background 标志本身。
    if detached_session_id is not None:
        runner.run(wf, parsed_inputs, workflow_session_id=detached_session_id)
        return

    if not background:
        R.print_info(f"[Workflow] 开始执行 {name} ...")
        result = runner.run(wf, parsed_inputs)
        R.print_info(result.to_summary())
        return

    import uuid
    wf_session_id = f"wfs_{uuid.uuid4().hex[:12]}"

    if standalone:
        # [独立 CLI] 这里不能用 daemon 线程：`mini-agent workflow run ...`
        # 一次性命令打印完提示就准备退出进程，daemon 线程会随进程一起被杀
        # 掉，工作流根本不会真的跑完。改成 spawn 一个完全独立的 OS 子进程
        # （不继承父进程的生命周期），父进程立即返回，子进程即使父进程/
        # 触发它的 shell 已经退出（比如 cron/systemd 场景）也会继续跑到底。
        log_path = _spawn_detached_run(cfg, ["run", name, inputs_json], wf_session_id)
        R.print_info(
            f"🚀 工作流 **{name}** 已在独立后台进程中开始执行\n"
            f"workflow_session_id：{wf_session_id}\n"
            f"用 `mini-agent workflow status {wf_session_id} --project {cfg.project_root}` 查看进度\n"
            f"该进程的标准输出/错误重定向到：{log_path}"
        )
        return

    def _bg_run():
        try:
            runner.run(wf, parsed_inputs, workflow_session_id=wf_session_id)
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.cli.commands.workflow_cmd._handle_run._bg_run")

    threading.Thread(target=_bg_run, daemon=True, name=f"wf-run-{wf_session_id}").start()
    R.print_info(
        f"🚀 已在后台开始执行，workflow_session_id={wf_session_id}\n"
        f"用 /workflow status {wf_session_id} 查看进度"
    )


def _handle_runs(cfg, rest: list[str]) -> None:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.workflow.session import WorkflowSession

    name_filter = rest[0] if rest else None
    paths = AgentPaths(project_root=cfg.project_root)
    ids = paths.list_workflow_session_ids()
    if not ids:
        R.print_info("📭 当前没有任何工作流执行记录。")
        return
    shown = 0
    for wf_session_id in sorted(ids):
        s = WorkflowSession.load(paths, wf_session_id)
        if s is None:
            continue
        if name_filter and s.workflow_name != name_filter:
            continue
        R.print_info(f"  - {s.summary_line()}")
        shown += 1
    if not shown:
        R.print_info(f"📭 没有找到工作流 {name_filter!r} 的执行记录。" if name_filter else "📭 当前没有任何工作流执行记录。")


def _handle_status(cfg, rest: list[str]) -> None:
    if not rest:
        R.print_error("用法：/workflow status <workflow_session_id>")
        return
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.workflow.session import WorkflowSession

    paths = AgentPaths(project_root=cfg.project_root)
    s = WorkflowSession.load(paths, rest[0])
    if s is None:
        R.print_error(f"找不到执行记录 {rest[0]!r}")
        return
    R.print_info(f"工作流：{s.workflow_name}  状态：{s.status.value}  批次：{s.current_batch_index}")
    if s.pending_approval_step:
        R.print_info(f"  ⏳ 等待人工审批的步骤：{s.pending_approval_step}")
    if s.error:
        R.print_info(f"  错误：{s.error}")
    for step_id, sr in s.step_results.items():
        R.print_info(f"  - {step_id}: {sr.status.value} ({sr.duration_seconds:.1f}s)")


def _handle_resume(cfg, rest: list[str], standalone: bool = False) -> None:
    if not rest:
        R.print_error("用法：workflow resume <workflow_session_id> [--background]")
        return

    # 见 _handle_run 里同名参数的说明：detached 子进程用它强制走前台同步分支。
    detached = "--__detached-session-id" in rest
    if detached:
        idx = rest.index("--__detached-session-id")
        rest = rest[:idx] + rest[idx + 2:]

    wf_session_id = rest[0]
    background = "--background" in rest

    from mini_agent.storage.paths import AgentPaths
    from mini_agent.workflow.session import WorkflowSession
    from mini_agent.workflow.runner import WorkflowRunner
    from mini_agent.workflow.generator import WorkflowGenerator

    paths = AgentPaths(project_root=cfg.project_root)
    wf_session = WorkflowSession.load(paths, wf_session_id)
    if wf_session is None:
        R.print_error(f"找不到执行记录 {wf_session_id!r}")
        return
    snap_path = paths.workflow_session_def_snapshot(wf_session_id)
    if not snap_path.exists():
        R.print_error(f"执行 {wf_session_id!r} 缺少工作流定义快照，无法续跑")
        return
    generator = WorkflowGenerator(cfg)
    try:
        wf = generator.parse_yaml(snap_path.read_text(encoding="utf-8"))
    except ValueError as e:
        R.print_error(f"定义快照解析失败：{e}")
        return

    runner = WorkflowRunner(cfg)

    if detached:
        runner.run(wf, wf_session.inputs, workflow_session_id=wf_session_id)
        return

    if not background:
        result = runner.run(wf, wf_session.inputs, workflow_session_id=wf_session_id)
        R.print_info(result.to_summary())
        return

    if standalone:
        log_path = _spawn_detached_run(cfg, ["resume", wf_session_id], wf_session_id)
        R.print_info(
            f"🚀 已在独立后台进程中续跑 workflow_session_id={wf_session_id}\n"
            f"用 `mini-agent workflow status {wf_session_id} --project {cfg.project_root}` 查看进度\n"
            f"该进程的标准输出/错误重定向到：{log_path}"
        )
        return

    def _bg_resume():
        try:
            runner.run(wf, wf_session.inputs, workflow_session_id=wf_session_id)
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.cli.commands.workflow_cmd._handle_resume._bg_resume")

    threading.Thread(target=_bg_resume, daemon=True, name=f"wf-resume-{wf_session_id}").start()
    R.print_info(f"🚀 已在后台续跑 workflow_session_id={wf_session_id}")


def _handle_pause(rest: list[str]) -> None:
    if not rest:
        R.print_error("用法：/workflow pause <workflow_session_id>")
        return
    from mini_agent.workflow import registry as wf_registry
    control = wf_registry.get(rest[0])
    if control is None:
        R.print_warning(f"进程内没有找到执行 {rest[0]!r} 的活跃控制状态（可能已结束或进程已重启）")
        return
    control.request_pause()
    R.print_info(f"⏸️ 已请求暂停 {rest[0]}，将在当前批次结束后生效")


def _handle_cancel(rest: list[str]) -> None:
    if not rest:
        R.print_error("用法：/workflow cancel <workflow_session_id>")
        return
    from mini_agent.workflow import registry as wf_registry
    control = wf_registry.get(rest[0])
    if control is None:
        R.print_warning(f"进程内没有找到执行 {rest[0]!r} 的活跃控制状态，可能已经结束")
        return
    control.request_cancel()
    R.print_info(f"🛑 已请求取消 {rest[0]}")


def _handle_approve(rest: list[str]) -> None:
    if not rest:
        R.print_error("用法：/workflow approve <workflow_session_id>")
        return
    from mini_agent.workflow import registry as wf_registry
    control = wf_registry.get(rest[0])
    if control is None or not control.pending_approval_step:
        R.print_warning(f"执行 {rest[0]!r} 当前没有正在等待审批的步骤")
        return
    step_id = control.pending_approval_step
    control.request_approve(step_id)
    R.print_info(f"✅ 已批准步骤 {step_id}")


def _handle_reject(rest: list[str]) -> None:
    if not rest:
        R.print_error("用法：/workflow reject <workflow_session_id> [reason]")
        return
    from mini_agent.workflow import registry as wf_registry
    control = wf_registry.get(rest[0])
    if control is None or not control.pending_approval_step:
        R.print_warning(f"执行 {rest[0]!r} 当前没有正在等待审批的步骤")
        return
    step_id = control.pending_approval_step
    reason = " ".join(rest[1:])
    control.request_reject(step_id, reason)
    R.print_info(f"❌ 已拒绝步骤 {step_id}" + (f"，原因：{reason}" if reason else ""))


def _handle_input(rest: list[str]) -> None:
    """[workflow机制改进计划.md P5] 向正在等待 human_input 类型 step 的执行送入文本。"""
    if len(rest) < 2:
        R.print_error("用法：/workflow input <workflow_session_id> <text>")
        return
    wf_session_id = rest[0]
    text = " ".join(rest[1:])
    from mini_agent.workflow import registry as wf_registry
    control = wf_registry.get(wf_session_id)
    if control is None or not control.pending_input_step:
        R.print_warning(f"执行 {wf_session_id!r} 当前没有正在等待人工输入的步骤")
        return
    step_id = control.pending_input_step
    control.request_provide_input(step_id, text)
    R.print_info(f"✅ 已向步骤 {step_id} 送入文本")


def _handle_templates(cfg) -> None:
    """[workflow机制改进计划.md P6] 列举内置工作流模板。"""
    from mini_agent.workflow.store import WorkflowStore
    store = WorkflowStore(Path(cfg.project_root))
    templates = store.list_templates()
    if not templates:
        R.print_info("📭 当前没有内置模板。")
        return
    R.print_info(f"📋 共 {len(templates)} 个内置模板：")
    for t in templates:
        R.print_info(f"  - {t['name']}：{t['description'] or '无描述'}（{' → '.join(t['steps'])}）")


def _handle_from_template(cfg, rest: list[str]) -> None:
    """[workflow机制改进计划.md P6] 基于内置模板创建并保存一个新工作流。"""
    if len(rest) < 2:
        R.print_error("用法：/workflow from-template <template_name> <new_name>")
        return
    template_name, new_name = rest[0], rest[1]
    from mini_agent.workflow.store import WorkflowStore
    store = WorkflowStore(Path(cfg.project_root))
    try:
        wf = store.instantiate_template(template_name, new_name)
    except ValueError as e:
        R.print_error(str(e))
        return
    try:
        path = store.save(wf, cfg=cfg)
    except ValueError as e:
        R.print_error(f"保存失败：{e}")
        return
    R.print_info(
        f"✅ 已基于模板 {template_name} 创建工作流 {wf.name}，保存到 {path}\n"
        f"步骤：{' → '.join(s.id for s in wf.steps)}\n"
        f"运行方式：/workflow run {wf.name}"
    )


def _handle_delete(cfg, rest: list[str]) -> None:
    if not rest:
        R.print_error("用法：/workflow delete <name>")
        return
    from mini_agent.workflow.store import WorkflowStore
    store = WorkflowStore(Path(cfg.project_root))
    if store.delete(rest[0]):
        R.print_info(f"✅ 工作流 {rest[0]!r} 已删除")
    else:
        R.print_error(f"找不到工作流 {rest[0]!r}，无法删除")


def _handle_to_dir(cfg, rest: list[str]) -> None:
    """[workflow_directory_mode_design.md 阶段5] 把已有单文件工作流升级为文件夹模式，
    生成 agents/skills/prompts 子目录，方便后续放置工作流私有的 agent/skill/prompt 文件。"""
    if not rest:
        R.print_error("用法：/workflow to-dir <name>")
        return
    from mini_agent.workflow.store import WorkflowStore
    store = WorkflowStore(Path(cfg.project_root))
    name = rest[0]
    if not store.exists(name):
        R.print_error(f"找不到工作流 {name!r}")
        return
    try:
        path = store.to_dir(name)
    except Exception as e:
        R.print_error(f"转换失败：{e}")
        return
    R.print_info(
        f"✅ 工作流 {name!r} 已转换为文件夹模式：{path}\n"
        f"   可在同目录下的 agents/、skills/、prompts/ 中放置私有资源，\n"
        f"   step 里用 prompt_file（相对路径）引用 prompts/ 下的模板文件，\n"
        f"   role/skill_name 会优先匹配本地 agents/、skills/ 目录。"
    )


# ── session → workflow 转换（session_to_workflow_design.md P8）──────────────

def _handle_sessions(cfg, rest: list[str]) -> None:
    """/workflow sessions — 等价于 workflow/tools.py 的 list_recent_sessions 工具。"""
    if not getattr(cfg.workflow, "session_to_workflow_enabled", True):
        R.print_error("session→workflow 功能已在配置中关闭（agent_config.json → workflow.session_to_workflow_enabled）。")
        return
    from mini_agent.session import SessionManager

    limit = 10
    if rest:
        try:
            limit = int(rest[0])
        except ValueError:
            R.print_error("用法：/workflow sessions [limit]（limit 需为整数）")
            return

    mgr = SessionManager(project_root=cfg.project_root)
    metas = mgr.list_sessions(limit=limit)
    if not metas:
        R.print_info("📭 没有找到任何历史 session。")
        return

    R.print_info(f"📋 最近 {len(metas)} 个 session：")
    for meta in metas:
        first_input = ""
        try:
            sess = mgr.load(meta.id)
            if sess is not None:
                for m in sess.history:
                    if m.get("_type") == "user_input" and isinstance(m.get("content"), str):
                        first_input = m["content"][:60]
                        break
        except Exception:
            pass
        suffix = f"：{first_input}" if first_input else ""
        R.print_info(f"  - {meta.id}（{meta.age_str}，{meta.turns} 轮）{suffix}")


def _handle_from_session(cfg, rest: list[str]) -> None:
    """
    /workflow from-session <session_id>

    CLI 路径下没有"主 Agent 编排多个工具调用"这一层，直接顺序调用
    "总结→展示确认（y/n/修改意见）→构建→展示确认→保存"，用同步阻塞的方式
    做完整个流程（session_to_workflow_design.md 5.2 节）。复用
    workflow/session_summarizer.py 和 workflow/generator.py 里的纯函数实现，
    不经过 workflow/tools.py 的 @tool 缓存机制（同一次命令内一路传递
    TaskSummary，不需要跨调用缓存）。
    """
    if not rest:
        R.print_error("用法：/workflow from-session <session_id>")
        return
    if not getattr(cfg.workflow, "session_to_workflow_enabled", True):
        R.print_error("session→workflow 功能已在配置中关闭（agent_config.json → workflow.session_to_workflow_enabled）。")
        return
    session_id = rest[0]

    from mini_agent.session import SessionManager
    from mini_agent.workflow.session_summarizer import summarize_session_for_workflow
    from mini_agent.workflow.generator import WorkflowGenerator

    mgr = SessionManager(project_root=cfg.project_root)
    sess = mgr.load(session_id)
    if sess is None:
        R.print_error(f"找不到 session {session_id!r}，可先用 /workflow sessions 确认 id")
        return

    R.print_info(f"[Workflow] 正在总结 session {sess.id} ...")
    try:
        summary = summarize_session_for_workflow(sess.history, cfg)
    except ValueError as e:
        R.print_error(f"总结失败：{e}")
        return

    R.print_info(summary.to_markdown())
    try:
        confirm = input("\n以上理解正确吗？直接回车确认，或输入需要调整的意见（输入 q 取消）：").strip()
    except (EOFError, KeyboardInterrupt):
        R.print_warning("已取消")
        return
    if confirm.lower() == "q":
        R.print_warning("已取消")
        return
    adjustments = confirm  # 空字符串 = 无调整意见，原样传给②阶段

    R.print_info("[Workflow] 正在构建 workflow YAML ...")
    generator = WorkflowGenerator(cfg)
    yaml_str = generator.generate_from_summary(summary, adjustments)
    try:
        wf = generator.parse_yaml(yaml_str)
    except ValueError as e:
        R.print_error(f"生成的工作流定义有误：{e}\n\n原始 YAML：\n{yaml_str}")
        return

    R.print_info(generator.preview(wf))
    R.print_info(f"\n### 生成的 YAML 定义\n\n{yaml_str}")
    if summary.repeated_pattern:
        R.print_info(
            "\n💡 原 session 里有阶段组合重复出现，"
            "如果满意可以考虑把重复的那部分存成可复用 step 片段（save_snippet）。"
        )

    try:
        save_confirm = input("\n保存这个工作流吗？(y/N)：").strip().lower()
    except (EOFError, KeyboardInterrupt):
        R.print_warning("已取消保存")
        return
    if save_confirm != "y":
        R.print_warning("未保存")
        return

    from mini_agent.workflow.store import WorkflowStore
    store = WorkflowStore(Path(cfg.project_root))
    try:
        path = store.save(wf, cfg=cfg)
    except ValueError as e:
        R.print_error(f"保存失败：{e}")
        return
    R.print_info(
        f"✅ 工作流 **{wf.name}** 已保存到 `{path}`\n"
        f"步骤：{' → '.join(s.id for s in wf.steps)}\n"
        f"运行方式：/workflow run {wf.name}"
    )


# ── 历史执行统计 / git 集成（P9-1a / P9-2）──────────────────────────────────

def _handle_stats(cfg, rest: list[str]) -> None:
    """/workflow stats <name> — 等价于 get_workflow_stats 工具。"""
    if not rest:
        R.print_error("用法：/workflow stats <name>")
        return
    from mini_agent.workflow import api_helpers

    stats = api_helpers.get_workflow_stats(cfg, rest[0])
    if stats["total_runs"] == 0:
        R.print_info(f"📭 工作流 {rest[0]!r} 还没有任何执行记录，暂无统计数据。")
        return

    R.print_info(
        f"📊 工作流 {rest[0]!r} 统计（共 {stats['total_runs']} 次执行，"
        f"成功率 {stats['success_rate']:.0%}）"
    )
    for step_id, s in stats["step_stats"].items():
        score_part = f"，平均分 {s['avg_score']}" if s["avg_score"] is not None else ""
        retry_part = f"，平均重试 {s['avg_retries_used']}" if s["avg_retries_used"] else ""
        R.print_info(
            f"  - {step_id}: 出现 {s['total']} 次，成功 {s['done']} 次"
            f"（失败率 {s['fail_rate']:.0%}），平均耗时 {s['avg_duration']}s"
            f"{score_part}{retry_part}"
        )
    if stats["condition_stats"]:
        R.print_info("condition 命中率（该步骤未被跳过的比例）：")
        for step_id, c in stats["condition_stats"].items():
            R.print_info(f"  - {step_id}: {c['true_rate']:.0%}（共 {c['total']} 次）")


def _handle_history(cfg, rest: list[str]) -> None:
    """
    /workflow history <name> — [P9-2] 查看该 workflow 定义文件的 git 提交
    历史。直接复用 `git log --oneline`，不重新发明版本历史机制（见
    next_doc/workflow_system_next_directions.md §2）。
    """
    if not rest:
        R.print_error("用法：/workflow history <name>")
        return
    from mini_agent.workflow.git_integration import git_log_for_workflow
    R.print_info(git_log_for_workflow(Path(cfg.project_root), rest[0]))


def _handle_diff(cfg, rest: list[str]) -> None:
    """
    /workflow diff <name> — [P9-2] 查看该 workflow 定义相对上次 commit 的
    改动：先给一段"step 级别"结构化摘要（哪个字段从什么改成了什么），
    再附上原始 `git diff` 全文。
    """
    if not rest:
        R.print_error("用法：/workflow diff <name>")
        return
    from mini_agent.workflow.git_integration import git_diff_for_workflow
    R.print_info(git_diff_for_workflow(Path(cfg.project_root), rest[0]))
