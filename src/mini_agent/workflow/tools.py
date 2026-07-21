"""
workflow/tools.py — 工作流工具注册

注册到主 Agent 工具注册表，让主 Agent 可以调用：
  generate_workflow   根据描述生成工作流定义
  save_workflow       保存工作流（生成后或用户手动编辑后调用）
  run_workflow        执行已保存的工作流（支持 background 后台执行）
  list_workflows      列举所有工作流
  show_workflow       查看工作流 YAML 定义
  delete_workflow     删除工作流

  [workflow机制改进计划.md P2-P4 新增，看护机制相关工具]
  resume_workflow_run     从断点续跑一次未完成/已暂停的执行
  list_workflow_runs      列举历史/当前的工作流执行记录
  get_workflow_run_status 查看某次执行的详细进度
  pause_workflow_run      暂停一次正在后台执行的工作流
  cancel_workflow_run     取消一次工作流执行
  approve_workflow_step   人工审批门放行
  reject_workflow_step    人工审批门拒绝

  [workflow机制改进计划.md P5 新增，Step 类型化配套工具]
  provide_workflow_step_input  向正在等待 human_input 类型 step 送入文本

  [workflow机制改进计划.md P6 新增，模板库]
  list_workflow_templates      列举内置工作流模板
  create_workflow_from_template 基于内置模板创建并保存一个新工作流

在 app.py 里调用 register_workflow_tools(cfg) 即可完成注册。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.config import AppConfig


def register_workflow_tools(cfg: "AppConfig") -> None:
    """向全局工具注册表注册所有工作流相关工具。"""
    from mini_agent.tools import tool

    store_path = cfg.project_root

    # ── 生成工作流 ──────────────────────────────────────────────────────────

    @tool(name="generate_workflow", group="workflow",
          description="根据自然语言描述生成工作流定义 YAML，生成后展示给用户确认，确认后调用 save_workflow 保存。")
    def generate_workflow(
        description: str,
        example_input: str = "",
    ) -> str:
        """
        description: 工作流的自然语言描述，如"做一个代码审查流程，包括分析、评估和报告"
        example_input: 可选，工作流运行时需要的输入参数示例，如 '{"code": "...", "lang": "python"}'
        """
        import mini_agent.ui.renderer as R
        R.print_info("[Workflow] 正在生成工作流定义...")

        from mini_agent.workflow.generator import WorkflowGenerator
        generator = WorkflowGenerator(cfg)

        yaml_str = generator.generate(description, example_input or None)

        try:
            wf = generator.parse_yaml(yaml_str)
            preview = generator.preview(wf)
            return (
                f"{preview}\n\n"
                f"---\n### 生成的 YAML 定义\n\n```yaml\n{yaml_str}\n```\n\n"
                f"如果满意，请调用 `save_workflow` 工具保存；"
                f"如需修改，请告诉我需要调整的地方。"
            )
        except ValueError as e:
            return f"生成的工作流定义有误：{e}\n\n原始 YAML：\n```yaml\n{yaml_str}\n```"

    # ── 保存工作流 ──────────────────────────────────────────────────────────

    @tool(name="save_workflow", group="workflow",
          description="保存工作流 YAML 定义到 .agent/workflows/ 目录。在 generate_workflow 之后或用户手动提供 YAML 时调用。")
    def save_workflow(yaml_content: str) -> str:
        """
        yaml_content: 完整的工作流 YAML 字符串
        """
        from mini_agent.workflow.generator import WorkflowGenerator
        from mini_agent.workflow.store import WorkflowStore

        generator = WorkflowGenerator(cfg)
        store = WorkflowStore(Path(store_path))

        try:
            wf = generator.parse_yaml(yaml_content)
        except ValueError as e:
            return f"❌ YAML 解析失败：{e}"

        try:
            path = store.save(wf, cfg=cfg)
            return (
                f"✅ 工作流 **{wf.name}** 已保存到 `{path}`\n"
                f"步骤：{' → '.join(s.id for s in wf.steps)}\n"
                f"运行方式：调用 `run_workflow` 工具，传入 name=\"{wf.name}\""
            )
        except ValueError as e:
            return f"❌ 保存失败：{e}"

    # ── 执行工作流 ──────────────────────────────────────────────────────────

    @tool(name="run_workflow", group="workflow",
          description="执行已保存的工作流。按步骤顺序运行，支持条件分支和角色 Agent。"
                      "background=True 时立即返回 workflow_session_id，在后台线程继续执行，"
                      "配合 get_workflow_run_status/pause_workflow_run/cancel_workflow_run 等工具监控与控制；"
                      "含 require_approval 步骤的工作流必须用 background=True 运行，否则审批会一直等到超时。")
    def run_workflow(
        name: str,
        inputs: str = "{}",
        background: Optional[bool] = None,
    ) -> str:
        """
        name: 工作流名称（与 YAML 文件名对应）
        inputs: JSON 字符串，工作流步骤 prompt 中需要的动态参数，如 '{"code": "def foo(): pass"}'
        background: 是否后台执行。不传时使用 agent_config.json 里
                    workflow.background_execution_default 的配置（默认 False，
                    即前台同步执行、直接返回完整结果摘要）。
        """
        from mini_agent.workflow.store import WorkflowStore
        from mini_agent.workflow.runner import WorkflowRunner

        store = WorkflowStore(Path(store_path))
        wf = store.load(name)
        if wf is None:
            available = [w["name"] for w in store.list_all()]
            return (
                f"❌ 找不到工作流 {name!r}\n"
                f"可用工作流：{available or '（无）'}"
            )

        try:
            parsed_inputs = json.loads(inputs) if inputs.strip() else {}
        except json.JSONDecodeError as e:
            return f"❌ inputs 参数不是合法 JSON：{e}"

        run_in_background = background
        if run_in_background is None:
            run_in_background = bool(getattr(getattr(cfg, "workflow", None), "background_execution_default", False))

        from mini_agent.workflow.runner import step_requires_approval
        has_approval_step = any(step_requires_approval(s, getattr(cfg, "workflow", None)) for s in wf.steps)
        if has_approval_step and not run_in_background:
            run_in_background = True  # 强制后台，否则审批门必然超时判拒绝

        runner = WorkflowRunner(cfg)

        if not run_in_background:
            result = runner.run(wf, parsed_inputs)
            return result.to_summary()

        import uuid
        wf_session_id = f"wfs_{uuid.uuid4().hex[:12]}"

        def _bg_run():
            try:
                runner.run(wf, parsed_inputs, workflow_session_id=wf_session_id)
            except Exception as e:
                from mini_agent.errors import log_exception
                log_exception(e, where="mini_agent.workflow.tools.run_workflow._bg_run")

        t = threading.Thread(target=_bg_run, daemon=True, name=f"wf-run-{wf_session_id}")
        t.start()

        return (
            f"🚀 工作流 **{wf.name}** 已在后台开始执行\n"
            f"workflow_session_id：`{wf_session_id}`\n"
            f"可用 `get_workflow_run_status(workflow_session_id=\"{wf_session_id}\")` 查看进度，"
            f"`pause_workflow_run` / `cancel_workflow_run` 控制执行"
            + ("，此工作流包含需要人工审批的步骤，跑到该步骤时会等待 `approve_workflow_step` / `reject_workflow_step`。" if has_approval_step else "")
        )

    # ── 列举工作流 ──────────────────────────────────────────────────────────

    @tool(name="list_workflows", group="workflow",
          description="列举所有已保存的工作流及其基本信息。")
    def list_workflows() -> str:
        from mini_agent.workflow.store import WorkflowStore
        store = WorkflowStore(Path(store_path))
        all_wf = store.list_all()
        if not all_wf:
            return "📭 当前没有已保存的工作流。\n使用 `generate_workflow` 工具创建一个。"

        lines = [f"📋 共 {len(all_wf)} 个工作流：\n"]
        for wf in all_wf:
            steps_str = " → ".join(wf["steps"])
            lines.append(f"**{wf['name']}** (v{wf['version']})")
            lines.append(f"  描述：{wf['description'] or '无'}")
            lines.append(f"  步骤：{steps_str}")
            lines.append("")
        return "\n".join(lines)

    # ── 查看工作流 YAML ──────────────────────────────────────────────────────

    @tool(name="show_workflow", group="workflow",
          description="查看指定工作流的完整 YAML 定义，用于检查或手动编辑前的确认。")
    def show_workflow(name: str) -> str:
        from mini_agent.workflow.store import WorkflowStore
        store = WorkflowStore(Path(store_path))
        yaml_str = store.export_yaml(name)
        if yaml_str is None:
            return f"❌ 找不到工作流 {name!r}"
        return f"```yaml\n{yaml_str}\n```"

    # ── 删除工作流 ──────────────────────────────────────────────────────────

    @tool(name="delete_workflow", group="workflow",
          description="删除指定的工作流定义文件。")
    def delete_workflow(name: str) -> str:
        from mini_agent.workflow.store import WorkflowStore
        store = WorkflowStore(Path(store_path))
        if store.delete(name):
            return f"✅ 工作流 {name!r} 已删除"
        return f"❌ 找不到工作流 {name!r}，无法删除"

    # ── 断点续跑（P2）────────────────────────────────────────────────────────

    @tool(name="resume_workflow_run", group="workflow",
          description="从断点续跑一次已暂停/未完整完成的工作流执行，跳过已完成的步骤只重跑剩余部分。")
    def resume_workflow_run(workflow_session_id: str, background: Optional[bool] = None) -> str:
        """
        workflow_session_id: 之前一次 run_workflow 返回的执行 ID
        background: 是否后台续跑，含义同 run_workflow 的 background 参数
        """
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.workflow.session import WorkflowSession
        from mini_agent.workflow.store import WorkflowStore
        from mini_agent.workflow.runner import WorkflowRunner
        from mini_agent.workflow.generator import WorkflowGenerator

        paths = AgentPaths(project_root=cfg.project_root)
        wf_session = WorkflowSession.load(paths, workflow_session_id)
        if wf_session is None:
            return f"❌ 找不到执行记录 {workflow_session_id!r}"

        snap_path = paths.workflow_session_def_snapshot(workflow_session_id)
        if not snap_path.exists():
            return f"❌ 执行 {workflow_session_id!r} 缺少工作流定义快照，无法续跑"

        generator = WorkflowGenerator(cfg)
        try:
            wf = generator.parse_yaml(snap_path.read_text(encoding="utf-8"))
        except ValueError as e:
            return f"❌ 定义快照解析失败：{e}"

        run_in_background = background
        if run_in_background is None:
            run_in_background = bool(getattr(getattr(cfg, "workflow", None), "background_execution_default", False))

        runner = WorkflowRunner(cfg)
        if not run_in_background:
            result = runner.run(wf, wf_session.inputs, workflow_session_id=workflow_session_id)
            return result.to_summary()

        def _bg_resume():
            try:
                runner.run(wf, wf_session.inputs, workflow_session_id=workflow_session_id)
            except Exception as e:
                from mini_agent.errors import log_exception
                log_exception(e, where="mini_agent.workflow.tools.resume_workflow_run._bg_resume")

        t = threading.Thread(target=_bg_resume, daemon=True, name=f"wf-resume-{workflow_session_id}")
        t.start()
        return f"🚀 已在后台续跑 workflow_session_id=`{workflow_session_id}`"

    # ── 执行记录查询（P2/P3）──────────────────────────────────────────────────

    @tool(name="list_workflow_runs", group="workflow",
          description="列举所有工作流执行记录（含历史已结束的与当前正在运行/暂停的）。")
    def list_workflow_runs(name: Optional[str] = None) -> str:
        """
        name: 可选，只看指定工作流名称的执行记录
        """
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.workflow.session import WorkflowSession

        paths = AgentPaths(project_root=cfg.project_root)
        ids = paths.list_workflow_session_ids()
        if not ids:
            return "📭 当前没有任何工作流执行记录。"

        lines = []
        for wf_session_id in sorted(ids):
            s = WorkflowSession.load(paths, wf_session_id)
            if s is None:
                continue
            if name and s.workflow_name != name:
                continue
            lines.append(f"- {s.summary_line()}")

        if not lines:
            return f"📭 没有找到工作流 {name!r} 的执行记录。" if name else "📭 当前没有任何工作流执行记录。"
        return f"📋 共 {len(lines)} 条执行记录：\n\n" + "\n".join(lines)

    @tool(name="get_workflow_run_status", group="workflow",
          description="查看某次工作流执行的详细进度（每个步骤的状态、是否有步骤在等待人工审批等）。")
    def get_workflow_run_status(workflow_session_id: str) -> str:
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.workflow.session import WorkflowSession

        paths = AgentPaths(project_root=cfg.project_root)
        s = WorkflowSession.load(paths, workflow_session_id)
        if s is None:
            return f"❌ 找不到执行记录 {workflow_session_id!r}"

        lines = [
            f"### 工作流执行 `{workflow_session_id}`",
            f"- 工作流：{s.workflow_name}",
            f"- 状态：{s.status.value}",
            f"- 当前批次：{s.current_batch_index}",
        ]
        if s.pending_approval_step:
            lines.append(f"- ⏳ 等待人工审批的步骤：`{s.pending_approval_step}`")
        if s.error:
            lines.append(f"- 错误：{s.error}")
        lines.append("\n**各步骤状态：**")
        for step_id, sr in s.step_results.items():
            score_str = f" 评分={sr.score}" if sr.score is not None else ""
            retry_str = f" 重试={sr.retries_used}" if sr.retries_used else ""
            lines.append(f"- {step_id}: {sr.status.value}（{sr.duration_seconds:.1f}s{score_str}{retry_str}）")
        return "\n".join(lines)

    # ── 执行控制：暂停/取消（P3）──────────────────────────────────────────────

    @tool(name="pause_workflow_run", group="workflow",
          description="暂停一次正在后台执行的工作流，会在当前批次跑完后停在下一批次前，可用 resume_workflow_run 续跑。")
    def pause_workflow_run(workflow_session_id: str) -> str:
        from mini_agent.workflow import registry as wf_registry
        control = wf_registry.get(workflow_session_id)
        if control is None:
            return (
                f"⚠️ 进程内没有找到执行 {workflow_session_id!r} 的活跃控制状态"
                "（可能已结束、或所在进程已重启）。若确实还在运行，请稍后用 "
                "get_workflow_run_status 确认其状态。"
            )
        control.request_pause()
        return f"⏸️ 已请求暂停 workflow_session_id=`{workflow_session_id}`，将在当前批次结束后生效"

    @tool(name="cancel_workflow_run", group="workflow",
          description="取消一次正在执行的工作流，正在跑的步骤会尽快中止，未开始的步骤标记为已取消。")
    def cancel_workflow_run(workflow_session_id: str) -> str:
        from mini_agent.workflow import registry as wf_registry
        control = wf_registry.get(workflow_session_id)
        if control is None:
            return f"⚠️ 进程内没有找到执行 {workflow_session_id!r} 的活跃控制状态，可能已经结束。"
        control.request_cancel()
        return f"🛑 已请求取消 workflow_session_id=`{workflow_session_id}`"

    # ── 人工审批门（P4）──────────────────────────────────────────────────────

    @tool(name="approve_workflow_step", group="workflow",
          description="批准一个正在等待人工审批的工作流步骤，使其继续执行。")
    def approve_workflow_step(workflow_session_id: str) -> str:
        from mini_agent.workflow import registry as wf_registry
        control = wf_registry.get(workflow_session_id)
        if control is None or not control.pending_approval_step:
            return f"⚠️ 执行 {workflow_session_id!r} 当前没有正在等待审批的步骤。"
        step_id = control.pending_approval_step
        control.request_approve(step_id)
        return f"✅ 已批准步骤 `{step_id}`（workflow_session_id=`{workflow_session_id}`）"

    @tool(name="reject_workflow_step", group="workflow",
          description="拒绝一个正在等待人工审批的工作流步骤，该步骤会被标记为 rejected 并跳过。")
    def reject_workflow_step(workflow_session_id: str, reason: str = "") -> str:
        from mini_agent.workflow import registry as wf_registry
        control = wf_registry.get(workflow_session_id)
        if control is None or not control.pending_approval_step:
            return f"⚠️ 执行 {workflow_session_id!r} 当前没有正在等待审批的步骤。"
        step_id = control.pending_approval_step
        control.request_reject(step_id, reason)
        return f"❌ 已拒绝步骤 `{step_id}`（workflow_session_id=`{workflow_session_id}`）{('，原因：' + reason) if reason else ''}"

    # ── human_input 步骤送入文本（P5）────────────────────────────────────────

    @tool(name="provide_workflow_step_input", group="workflow",
          description="向一个正在等待人工输入（human_input 类型 step）的工作流执行送入文本，使其继续执行。")
    def provide_workflow_step_input(workflow_session_id: str, input_text: str) -> str:
        """
        workflow_session_id: 正在执行的工作流的执行 ID
        input_text: 要送入的文本，将作为该 step 的 output，可被后续 step 用 {step_id.output} 引用
        """
        from mini_agent.workflow import registry as wf_registry
        control = wf_registry.get(workflow_session_id)
        if control is None or not control.pending_input_step:
            return f"⚠️ 执行 {workflow_session_id!r} 当前没有正在等待人工输入的步骤。"
        step_id = control.pending_input_step
        control.request_provide_input(step_id, input_text)
        return f"✅ 已向步骤 `{step_id}` 送入文本（workflow_session_id=`{workflow_session_id}`）"

    # ── 模板库（P6）──────────────────────────────────────────────────────────

    @tool(name="list_workflow_templates", group="workflow",
          description="列举内置工作流模板（code_review / research_report / multi_perspective_debate 等），供 create_workflow_from_template 使用。")
    def list_workflow_templates() -> str:
        from mini_agent.workflow.store import WorkflowStore
        store = WorkflowStore(Path(store_path))
        templates = store.list_templates()
        if not templates:
            return "📭 当前没有内置模板。"
        lines = [f"📋 共 {len(templates)} 个内置模板：\n"]
        for t in templates:
            lines.append(f"**{t['name']}**：{t['description'] or '无描述'}（{t['step_count']} 步）")
        return "\n".join(lines)

    @tool(name="create_workflow_from_template", group="workflow",
          description="基于内置模板创建一个新工作流并保存，比 generate_workflow 更稳定（模板经过验证，只需换个名字即可使用）。")
    def create_workflow_from_template(template_name: str, new_name: str) -> str:
        """
        template_name: 模板名称，参考 list_workflow_templates 的输出
        new_name: 新工作流的名称（保存后可直接用 run_workflow 执行）
        """
        from mini_agent.workflow.store import WorkflowStore
        store = WorkflowStore(Path(store_path))
        try:
            wf = store.instantiate_template(template_name, new_name)
        except ValueError as e:
            return f"❌ {e}"
        try:
            path = store.save(wf, cfg=cfg)
        except ValueError as e:
            return f"❌ 保存失败：{e}"
        return (
            f"✅ 已基于模板 **{template_name}** 创建工作流 **{wf.name}**，保存到 `{path}`\n"
            f"步骤：{' → '.join(s.id for s in wf.steps)}\n"
            f"运行方式：调用 `run_workflow` 工具，传入 name=\"{wf.name}\""
        )
