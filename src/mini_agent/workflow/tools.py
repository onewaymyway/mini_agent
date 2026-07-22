"""
workflow/tools.py — 工作流工具注册

注册到主 Agent 工具注册表，让主 Agent 可以调用：
  generate_workflow   根据描述生成工作流定义（[P9-1b] 生成结果自动附带一次
                      dry-run 预览：并发分批/condition 求值，保存前就能发现
                      "以为会并发其实会串行"这类问题）
  save_workflow       保存工作流（生成后或用户手动编辑后调用）
  run_workflow        执行已保存的工作流（支持 background 后台执行）
  list_workflows      列举所有工作流
  show_workflow       查看工作流 YAML 定义
  delete_workflow     删除工作流

  [workflow机制改进计划.md P2-P4 新增，看护机制相关工具]
  resume_workflow_run     从断点续跑一次未完成/已暂停的执行
  list_workflow_runs      列举历史/当前的工作流执行记录
  get_workflow_run_status 查看某次执行的详细进度

  [workflow_system_next_directions.md P9-1a 新增]
  get_workflow_stats      汇总某个工作流的历史执行统计（成功率/各步骤平均
                          耗时评分重试率/condition 命中率），纯粹对已落盘
                          的 WorkflowSession 数据做聚合，不改动执行逻辑
  pause_workflow_run      暂停一次正在后台执行的工作流
  cancel_workflow_run     取消一次工作流执行
  approve_workflow_step   人工审批门放行
  reject_workflow_step    人工审批门拒绝

  [workflow机制改进计划.md P5 新增，Step 类型化配套工具]
  provide_workflow_step_input  向正在等待 human_input 类型 step 送入文本

  [workflow机制改进计划.md P6 新增，模板库]
  list_workflow_templates      列举内置工作流模板
  create_workflow_from_template 基于内置模板创建并保存一个新工作流

  [workflow机制改进计划（P7）新增]
  preview_workflow      dry-run 预览执行计划（并发分批/prompt占位符/condition求值），不实际执行

  [session_to_workflow_design.md（P8）新增，把已完成的 session 沉淀成 workflow]
  list_recent_sessions          列出最近的历史 session，帮用户定位 session_id
  summarize_session_for_workflow 第①阶段：总结指定 session 成 TaskSummary，供用户确认
  build_workflow_from_summary    第②阶段：TaskSummary（+用户调整意见）→ workflow YAML 预览

  以上工具"真正做事"的核心逻辑已抽取到 workflow/api_helpers.py 的纯函数中，
  本模块只负责把返回的结构化结果包装成给 LLM 看的 Markdown 字符串；
  api/routes.py 的 REST 端点调用同一批纯函数，包装成 JSON 给看板前端用，
  两边共享同一套状态机逻辑，不重复维护。

在 app.py 里调用 register_workflow_tools(cfg) 即可完成注册。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from mini_agent.workflow.session_summarizer import TaskSummary


# [session_to_workflow_design.md 5.1] ①总结阶段产出的 TaskSummary 按
# session_id 缓存在内存里，供②构建阶段（build_workflow_from_summary）直接
# 复用而不用重新总结一次——两个工具调用之间用户会有一轮确认/调整对话，
# 中间不应该丢失总结结果。进程重启会丢失缓存，属于可接受的降级（用户
# 重新调用 summarize_session_for_workflow 即可，不是数据丢失，只是多一次
# LLM 调用）。
_task_summary_cache: dict[str, "TaskSummary"] = {}


def register_workflow_tools(cfg: "AppConfig") -> None:
    """向全局工具注册表注册所有工作流相关工具。"""
    from mini_agent.tools import tool

    store_path = cfg.project_root

    # ── 生成结果附带一次 dry-run 预览（P9-1b）───────────────────────────────

    def _format_dry_run_preview(wf, inputs: dict) -> str:
        """
        [P9-1b workflow_system_next_directions.md §1.2b] 生成完 YAML 后，
        自动跑一次 preview_workflow_def（P7 preview_workflow 的能力，走并发
        分批/占位符替换/condition 求值但不真正执行 Agent/工具），让用户在
        保存前就能看到"这个 workflow 大概会怎么分批执行、condition 大概会
        怎么判"，而不是只看步骤列表脑内模拟。dry-run 本身失败（比如
        依赖图有环）不应该阻塞展示 YAML，调用方需要包一层 try/except。
        """
        from mini_agent.workflow import api_helpers

        preview = api_helpers.preview_workflow_def(cfg, wf, inputs)
        lines = ["### Dry-run 预览（不实际执行）", ""]
        lines.append("**并发分批**：")
        for i, batch in enumerate(preview["batches"], start=1):
            names = "、".join(f"{s['id']}（{s['type']}）" for s in batch)
            tag = "并发" if len(batch) > 1 else "单步"
            lines.append(f"  批次 {i}（{tag}）：{names}")
        if preview["conditions"]:
            lines.append("")
            lines.append("**condition 求值**：")
            for step_id, desc in preview["conditions"].items():
                lines.append(f"  - {step_id}: {desc}")
        return "\n".join(lines)

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

            dry_run_section = ""
            try:
                parsed_inputs = json.loads(example_input) if example_input else {}
                if not isinstance(parsed_inputs, dict):
                    parsed_inputs = {}
                dry_run_section = "\n\n" + _format_dry_run_preview(wf, parsed_inputs)
            except Exception as e:
                # dry-run 只是锦上添花，失败不影响主流程（YAML 已经生成）
                from mini_agent.errors import log_exception
                log_exception(e, where="mini_agent.workflow.tools.generate_workflow.dry_run")

            return (
                f"{preview}{dry_run_section}\n\n"
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
            from mini_agent.workflow.git_integration import save_hint
            hint = save_hint(Path(cfg.project_root), path)
            return (
                f"✅ 工作流 **{wf.name}** 已保存到 `{path}`\n"
                f"步骤：{' → '.join(s.id for s in wf.steps)}\n"
                f"运行方式：调用 `run_workflow` 工具，传入 name=\"{wf.name}\""
                + (f"\n\n{hint}" if hint else "")
            )
        except ValueError as e:
            return f"❌ 保存失败：{e}"

    # ── session → workflow 转换（session_to_workflow_design.md）──────────────

    @tool(name="list_recent_sessions", group="workflow",
          description="列出最近的历史 session（id、起止时间、首条用户输入摘要），"
                      "用户记不清具体 session_id 时用这个工具帮用户定位，"
                      "定位到之后再调用 summarize_session_for_workflow。")
    def list_recent_sessions(limit: int = 10) -> str:
        """
        limit: 列出最近 N 个 session，默认 10
        """
        from mini_agent.session import SessionManager

        mgr = SessionManager(project_root=cfg.project_root)
        metas = mgr.list_sessions(limit=limit)
        if not metas:
            return "📭 没有找到任何历史 session。"

        lines = [f"📋 最近 {len(metas)} 个 session："]
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
            lines.append(
                f"- `{meta.id}`（{meta.age_str}，{meta.turns} 轮）"
                + (f"：{first_input}" if first_input else "")
            )
        return "\n".join(lines)

    @tool(name="summarize_session_for_workflow", group="workflow",
          description="session→workflow 转换第①阶段：读取指定 session 的历史，"
                      "起一个临时 Agent 总结出这次任务的目标/阶段/参数候选，"
                      "返回给用户确认。session_id 不确定时先调用 "
                      "list_recent_sessions；对当前正在运行的这个 session 自己"
                      "生成 workflow 时，直接传当前 session 的 id 即可。用户"
                      "确认总结无误（或提出调整意见）后，调用 "
                      "build_workflow_from_summary 进入第②阶段。")
    def summarize_session_for_workflow(session_id: str) -> str:
        """
        session_id: 要总结的 session 的完整 id 或前缀
        """
        from mini_agent.session import SessionManager
        from mini_agent.workflow.session_summarizer import summarize_session_for_workflow as _summarize

        mgr = SessionManager(project_root=cfg.project_root)
        sess = mgr.load(session_id)
        if sess is None:
            return f"❌ 找不到 session `{session_id}`。可先调用 list_recent_sessions 确认 id。"

        try:
            summary = _summarize(sess.history, cfg)
        except ValueError as e:
            return f"❌ 总结失败：{e}"

        _task_summary_cache[sess.id] = summary
        return (
            f"{summary.to_markdown()}\n\n"
            f"---\n（内部记录：session_id=`{sess.id}`，确认后请调用 "
            f"`build_workflow_from_summary(session_id=\"{sess.id}\")`）"
        )

    @tool(name="build_workflow_from_summary", group="workflow",
          description="session→workflow 转换第②阶段：读回上一步"
                      "summarize_session_for_workflow 生成的 TaskSummary，"
                      "结合用户的调整意见生成 workflow YAML 预览。"
                      "必须先调用 summarize_session_for_workflow 并得到用户"
                      "确认后才能调用这个工具。生成结果满意后，像 "
                      "generate_workflow 一样调用 save_workflow 保存。")
    def build_workflow_from_summary(session_id: str, adjustments: str = "") -> str:
        """
        session_id: 与 summarize_session_for_workflow 调用时相同的 session_id
        adjustments: 用户对①阶段总结提出的调整意见，如"修复阶段不要做成质检门"
        """
        from mini_agent.session import SessionManager
        from mini_agent.workflow.generator import WorkflowGenerator

        # session_id 可能是前缀，先解析成完整 id 再查缓存
        mgr = SessionManager(project_root=cfg.project_root)
        sess = mgr.load(session_id)
        full_id = sess.id if sess is not None else session_id

        summary = _task_summary_cache.get(full_id) or _task_summary_cache.get(session_id)
        if summary is None:
            return (
                f"❌ 没有找到 session `{session_id}` 的总结结果，"
                f"请先调用 summarize_session_for_workflow(session_id=\"{session_id}\")。"
            )

        generator = WorkflowGenerator(cfg)
        yaml_str = generator.generate_from_summary(summary, adjustments)

        try:
            wf = generator.parse_yaml(yaml_str)
            preview = generator.preview(wf)
            extra_note = ""
            if summary.repeated_pattern:
                extra_note = (
                    "\n\n💡 原 session 里有阶段组合重复出现，"
                    "如果满意可以考虑把重复的那部分存成可复用 step 片段"
                    "（save_snippet）。"
                )

            dry_run_section = ""
            try:
                parsed_inputs = {
                    p.name: p.example_value
                    for p in (summary.candidate_parameters or [])
                    if p.name
                }
                dry_run_section = "\n\n" + _format_dry_run_preview(wf, parsed_inputs)
            except Exception as e:
                from mini_agent.errors import log_exception
                log_exception(e, where="mini_agent.workflow.tools.build_workflow_from_summary.dry_run")

            return (
                f"{preview}{dry_run_section}\n\n"
                f"---\n### 生成的 YAML 定义\n\n```yaml\n{yaml_str}\n```"
                f"{extra_note}\n\n"
                f"如果满意，请调用 `save_workflow` 工具保存；"
                f"如需修改，请告诉我需要调整的地方。"
            )
        except ValueError as e:
            return f"生成的工作流定义有误：{e}\n\n原始 YAML：\n```yaml\n{yaml_str}\n```"

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
        from mini_agent.workflow import api_helpers

        try:
            parsed_inputs = json.loads(inputs) if inputs.strip() else {}
        except json.JSONDecodeError as e:
            return f"❌ inputs 参数不是合法 JSON：{e}"

        try:
            outcome = api_helpers.start_workflow_run(cfg, name, parsed_inputs, background)
        except api_helpers.WorkflowApiError as e:
            return f"❌ {e.message}"

        if outcome["mode"] == "sync":
            return outcome["result"].to_summary()

        wf_session_id = outcome["workflow_session_id"]
        wf_output_dir = outcome["output_dir"]
        has_approval_step = outcome["has_approval_step"]
        return (
            f"🚀 工作流 **{name}** 已在后台开始执行\n"
            f"workflow_session_id：`{wf_session_id}`\n"
            f"可用 `get_workflow_run_status(workflow_session_id=\"{wf_session_id}\")` 查看进度，"
            f"`pause_workflow_run` / `cancel_workflow_run` 控制执行"
            + ("，此工作流包含需要人工审批的步骤，跑到该步骤时会等待 `approve_workflow_step` / `reject_workflow_step`。" if has_approval_step else "")
            + f"\n📁 本次工作流的默认输出目录：`{wf_output_dir}`（用户未指定路径时，产出文件请写入这里，不要写入你自己的 session output 目录）"
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
        from mini_agent.workflow import api_helpers

        try:
            outcome = api_helpers.resume_workflow_run(cfg, workflow_session_id, background)
        except api_helpers.WorkflowApiError as e:
            return f"❌ {e.message}"

        if outcome["mode"] == "sync":
            return outcome["result"].to_summary()
        return f"🚀 已在后台续跑 workflow_session_id=`{workflow_session_id}`"

    # ── 执行记录查询（P2/P3）──────────────────────────────────────────────────

    @tool(name="list_workflow_runs", group="workflow",
          description="列举所有工作流执行记录（含历史已结束的与当前正在运行/暂停的）。")
    def list_workflow_runs(name: Optional[str] = None) -> str:
        """
        name: 可选，只看指定工作流名称的执行记录
        """
        from mini_agent.workflow import api_helpers

        runs = api_helpers.list_workflow_runs(cfg, name)
        if not runs:
            return f"📭 没有找到工作流 {name!r} 的执行记录。" if name else "📭 当前没有任何工作流执行记录。"
        lines = [f"- {r['summary_line']}" for r in runs]
        return f"📋 共 {len(lines)} 条执行记录：\n\n" + "\n".join(lines)

    @tool(name="get_workflow_stats", group="workflow",
          description="汇总某个工作流的历史执行统计（成功率、各步骤平均耗时/评分/重试率、"
                       "condition 命中率），用于判断这个工作流长期跑下来靠不靠谱、哪个步骤该调了。")
    def get_workflow_stats(name: str) -> str:
        """
        [P9-1a workflow_system_next_directions.md §1.2a]
        name: 工作流名称
        """
        from mini_agent.workflow import api_helpers

        stats = api_helpers.get_workflow_stats(cfg, name)
        if stats["total_runs"] == 0:
            return f"📭 工作流 {name!r} 还没有任何执行记录，暂无统计数据。"

        lines = [
            f"📊 工作流 {name!r} 统计（共 {stats['total_runs']} 次执行，"
            f"成功率 {stats['success_rate']:.0%}）",
            "",
            "步骤统计：",
        ]
        for step_id, s in stats["step_stats"].items():
            score_part = f"，平均分 {s['avg_score']}" if s["avg_score"] is not None else ""
            retry_part = f"，平均重试 {s['avg_retries_used']}" if s["avg_retries_used"] else ""
            lines.append(
                f"  - {step_id}: 出现 {s['total']} 次，成功 {s['done']} 次"
                f"（失败率 {s['fail_rate']:.0%}），平均耗时 {s['avg_duration']}s"
                f"{score_part}{retry_part}"
            )

        if stats["condition_stats"]:
            lines.append("")
            lines.append("condition 命中率（该步骤未被跳过的比例）：")
            for step_id, c in stats["condition_stats"].items():
                lines.append(f"  - {step_id}: {c['true_rate']:.0%}（共 {c['total']} 次）")

        return "\n".join(lines)

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
        lines.append(f"- 📁 默认输出目录：`{paths.workflow_session_output_dir(workflow_session_id)}`")
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
        from mini_agent.workflow import api_helpers
        try:
            api_helpers.pause_workflow_run(cfg, workflow_session_id)
        except api_helpers.WorkflowApiError as e:
            return f"⚠️ {e.message}"
        return f"⏸️ 已请求暂停 workflow_session_id=`{workflow_session_id}`，将在当前批次结束后生效"

    @tool(name="cancel_workflow_run", group="workflow",
          description="取消一次正在执行的工作流，正在跑的步骤会尽快中止，未开始的步骤标记为已取消。")
    def cancel_workflow_run(workflow_session_id: str) -> str:
        from mini_agent.workflow import api_helpers
        try:
            api_helpers.cancel_workflow_run(cfg, workflow_session_id)
        except api_helpers.WorkflowApiError as e:
            return f"⚠️ {e.message}"
        return f"🛑 已请求取消 workflow_session_id=`{workflow_session_id}`"

    # ── 人工审批门（P4）──────────────────────────────────────────────────────

    @tool(name="approve_workflow_step", group="workflow",
          description="批准一个正在等待人工审批的工作流步骤，使其继续执行。")
    def approve_workflow_step(workflow_session_id: str) -> str:
        from mini_agent.workflow import api_helpers
        try:
            step_id = api_helpers.approve_workflow_step(cfg, workflow_session_id)
        except api_helpers.WorkflowApiError as e:
            return f"⚠️ {e.message}"
        return f"✅ 已批准步骤 `{step_id}`（workflow_session_id=`{workflow_session_id}`）"

    @tool(name="reject_workflow_step", group="workflow",
          description="拒绝一个正在等待人工审批的工作流步骤，该步骤会被标记为 rejected 并跳过。")
    def reject_workflow_step(workflow_session_id: str, reason: str = "") -> str:
        from mini_agent.workflow import api_helpers
        try:
            step_id = api_helpers.reject_workflow_step(cfg, workflow_session_id, reason)
        except api_helpers.WorkflowApiError as e:
            return f"⚠️ {e.message}"
        return f"❌ 已拒绝步骤 `{step_id}`（workflow_session_id=`{workflow_session_id}`）{('，原因：' + reason) if reason else ''}"

    # ── human_input 步骤送入文本（P5）────────────────────────────────────────

    @tool(name="provide_workflow_step_input", group="workflow",
          description="向一个正在等待人工输入（human_input 类型 step）的工作流执行送入文本，使其继续执行。")
    def provide_workflow_step_input(workflow_session_id: str, input_text: str) -> str:
        """
        workflow_session_id: 正在执行的工作流的执行 ID
        input_text: 要送入的文本，将作为该 step 的 output，可被后续 step 用 {step_id.output} 引用
        """
        from mini_agent.workflow import api_helpers
        try:
            step_id = api_helpers.provide_workflow_step_input(cfg, workflow_session_id, input_text)
        except api_helpers.WorkflowApiError as e:
            return f"⚠️ {e.message}"
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

    # ── Dry-run 预览（workflow机制改进计划（P7）二、3.2）───────────────────────

    @tool(name="preview_workflow", group="workflow",
          description="预览工作流执行计划（不实际运行）：展示并发分批、prompt 占位符替换结果、"
                      "condition 表达式的静态求值情况，用于运行前确认。")
    def preview_workflow(name: str, inputs: str = "{}") -> str:
        """
        name: 工作流名称
        inputs: JSON 字符串，与 run_workflow 的 inputs 含义一致
        """
        from mini_agent.workflow import api_helpers
        try:
            parsed_inputs = json.loads(inputs) if inputs.strip() else {}
        except json.JSONDecodeError as e:
            return f"❌ inputs 参数不是合法 JSON：{e}"

        try:
            preview = api_helpers.preview_workflow(cfg, name, parsed_inputs)
        except api_helpers.WorkflowApiError as e:
            return f"❌ {e.message}"

        lines = [f"### 工作流预览：{preview['workflow_name']}", ""]
        lines.append("**并发批次：**")
        for i, batch in enumerate(preview["batches"], start=1):
            step_desc = "、".join(f"`{s['id']}`({s['type']})" for s in batch)
            lines.append(f"{i}. {step_desc}")
        lines.append("")
        lines.append("**Prompt 预览（`{step_id.output}` 等运行时占位符原样保留）：**")
        for step_id, prompt in preview["resolved_prompts"].items():
            preview_text = prompt[:200].replace("\n", " ")
            if len(prompt) > 200:
                preview_text += "..."
            lines.append(f"- `{step_id}`：{preview_text}")
        if preview["conditions"]:
            lines.append("")
            lines.append("**Condition 表达式：**")
            for step_id, cond in preview["conditions"].items():
                lines.append(f"- `{step_id}`：{cond}")
        return "\n".join(lines)
