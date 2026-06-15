"""
workflow/tools.py — 工作流工具注册

注册到主 Agent 工具注册表，让主 Agent 可以调用：
  generate_workflow   根据描述生成工作流定义
  save_workflow       保存工作流（生成后或用户手动编辑后调用）
  run_workflow        执行已保存的工作流
  list_workflows      列举所有工作流
  show_workflow       查看工作流 YAML 定义
  delete_workflow     删除工作流

在 app.py 里调用 register_workflow_tools(cfg) 即可完成注册。
"""

from __future__ import annotations

import json
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
            path = store.save(wf)
            return (
                f"✅ 工作流 **{wf.name}** 已保存到 `{path}`\n"
                f"步骤：{' → '.join(s.id for s in wf.steps)}\n"
                f"运行方式：调用 `run_workflow` 工具，传入 name=\"{wf.name}\""
            )
        except ValueError as e:
            return f"❌ 保存失败：{e}"

    # ── 执行工作流 ──────────────────────────────────────────────────────────

    @tool(name="run_workflow", group="workflow",
          description="执行已保存的工作流。按步骤顺序运行，支持条件分支和角色 Agent。")
    def run_workflow(
        name: str,
        inputs: str = "{}",
    ) -> str:
        """
        name: 工作流名称（与 YAML 文件名对应）
        inputs: JSON 字符串，工作流步骤 prompt 中需要的动态参数，如 '{"code": "def foo(): pass"}'
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

        runner = WorkflowRunner(cfg)
        result = runner.run(wf, parsed_inputs)
        return result.to_summary()

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
