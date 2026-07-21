"""
cli/commands/workflow_cmd.py — /workflow slash 命令处理

子命令（与 workflow/tools.py 里暴露给 Agent 的工具一一对应，供用户直接在
CLI 里操作，不需要绕一圈让主 Agent 去调用工具）：
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
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import mini_agent.ui.renderer as R


def handle_workflow_cmd(args: list[str], agent) -> None:
    if not args:
        R.print_error(
            "用法：\n"
            "  /workflow list                         列举所有已保存的工作流\n"
            "  /workflow show <name>                  查看工作流 YAML 定义\n"
            "  /workflow run <name> [inputs_json] [--background]\n"
            "                                          执行工作流\n"
            "  /workflow runs [name]                  列举执行记录\n"
            "  /workflow status <workflow_session_id>  查看某次执行的详细进度\n"
            "  /workflow resume <workflow_session_id> [--background]\n"
            "                                          从断点续跑\n"
            "  /workflow pause <workflow_session_id>   暂停一次后台执行\n"
            "  /workflow cancel <workflow_session_id>  取消一次执行\n"
            "  /workflow approve <workflow_session_id> 批准当前等待审批的步骤\n"
            "  /workflow reject <workflow_session_id> [reason]\n"
            "                                          拒绝当前等待审批的步骤\n"
            "  /workflow input <workflow_session_id> <text>\n"
            "                                          向等待人工输入的步骤送入文本\n"
            "  /workflow templates                     列举内置工作流模板\n"
            "  /workflow from-template <template_name> <new_name>\n"
            "                                          基于内置模板创建工作流\n"
            "  /workflow delete <name>                 删除工作流定义\n"
            "  /workflow to-dir <name>                 升级为文件夹模式（agents/skills/prompts）"
        )
        return

    sub = args[0].lower()
    rest = args[1:]
    cfg = agent.cfg

    if sub == "list":
        _handle_list(cfg)
    elif sub == "show":
        _handle_show(cfg, rest)
    elif sub == "run":
        _handle_run(cfg, rest)
    elif sub == "runs":
        _handle_runs(cfg, rest)
    elif sub == "status":
        _handle_status(cfg, rest)
    elif sub == "resume":
        _handle_resume(cfg, rest)
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
    else:
        R.print_error(f"未知子命令：/workflow {sub}（输入 /workflow 查看用法）")


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


def _handle_run(cfg, rest: list[str]) -> None:
    if not rest:
        R.print_error("用法：/workflow run <name> [inputs_json] [--background]")
        return
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
    if has_approval_step and not background:
        R.print_warning("该工作流包含需要人工审批的步骤，已自动切换为 --background 执行")
        background = True

    runner = WorkflowRunner(cfg)
    if not background:
        R.print_info(f"[Workflow] 开始执行 {name} ...")
        result = runner.run(wf, parsed_inputs)
        R.print_info(result.to_summary())
        return

    import uuid
    wf_session_id = f"wfs_{uuid.uuid4().hex[:12]}"

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


def _handle_resume(cfg, rest: list[str]) -> None:
    if not rest:
        R.print_error("用法：/workflow resume <workflow_session_id> [--background]")
        return
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
    if not background:
        result = runner.run(wf, wf_session.inputs, workflow_session_id=wf_session_id)
        R.print_info(result.to_summary())
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
