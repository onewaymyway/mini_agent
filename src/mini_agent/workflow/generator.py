"""
workflow/generator.py — LLM 自动生成工作流定义

用法：
  generator = WorkflowGenerator(cfg)
  yaml_str = generator.generate("做一个代码审查流程，包括分析、评估和报告")
  wf = generator.parse_yaml(yaml_str)   # → WorkflowDef
  store.save(wf)
"""

from __future__ import annotations

import re
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from .session_summarizer import TaskSummary

from .schema import WorkflowDef


GENERATOR_SYSTEM = """你是一个工作流设计专家。
用户描述一个任务，你需要设计一个结构化的 AI 工作流并输出 YAML 定义。

工作流规则：
1. 每个步骤必须有唯一的 id（英文小写下划线）
2. steps 按执行顺序排列，通过 depends_on 声明依赖
3. prompt 是该步骤的完整指令，可用 {prev_step_id.output} 引用前步骤输出
4. role 字段：null（主 Agent 执行）或 "evaluator"（质量评估）
5. condition 字段：可选，如 "analyze.score >= 6"（引用步骤评分，0-100 整数）
6. 动态输入参数用 {param_name} 占位，如 {code}、{topic}

输出要求：
- 只输出 YAML，不要加任何解释文字
- 不要加 ```yaml 代码块标记
- name 字段用英文小写，description 用中文
- 步骤数量适中（3-6步），不要过度拆分

YAML 模板：
name: workflow_name
description: 工作流描述
version: "1.0"
steps:
  - id: step_one
    name: 第一步名称
    prompt: |
      这里写步骤的详细指令。
      如需引用输入参数：{param_name}
    role: null

  - id: step_two
    name: 第二步名称
    prompt: |
      基于第一步结果：{step_one.output}
      继续处理...
    depends_on: [step_one]
    role: null

  - id: evaluate
    name: 质量评估
    prompt: |
      请对以下内容进行质量评估（输出格式中必须包含 SCORE: x/10）：
      {step_two.output}
    depends_on: [step_two]
    role: evaluator

  - id: final
    name: 最终输出
    prompt: 综合以上内容生成最终报告。内容：{step_two.output} 评分：{evaluate.score}
    depends_on: [evaluate]
    condition: "evaluate.score >= 60"
    role: null"""


class WorkflowGenerator:
    """使用 LLM 生成工作流定义。"""

    def __init__(self, cfg: "AppConfig") -> None:
        self._cfg = cfg

    def generate(self, description: str, example_input: Optional[str] = None) -> str:
        """
        根据自然语言描述生成工作流 YAML。
        返回原始 YAML 字符串（用户可以再编辑）。
        """
        from mini_agent.config import load_config
        from mini_agent.agent import Agent
        from mini_agent.permissions import PermissionGuard
        from mini_agent.tools import get_default_registry

        gen_cfg = load_config(
            project_root=self._cfg.project_root,
            verbose=False,
            sandbox=self._cfg.sandbox,
            auto_approve=True,
            model=self._cfg.model,
            llm_provider=self._cfg.llm_provider,
            llm_base_url=self._cfg.llm_base_url,
            # [BUGFIX] 同 evaluator.py：继承 self._cfg 的 --debug-llm，而不是硬编码 False。
            debug_llm=getattr(self._cfg, "debug_llm", False),
            debug_llm_console=getattr(self._cfg, "debug_llm_console", False),
        )
        gen_cfg.api_key = self._cfg.api_key
        gen_cfg.max_turns = 3
        gen_cfg.stream = False
        gen_cfg.system_extra = GENERATOR_SYSTEM

        guard = PermissionGuard(
            auto_approve=True,
            sandbox=self._cfg.sandbox,
            project_root=self._cfg.project_root,
        )
        empty_registry = get_default_registry().filtered(names=[], groups=[])
        agent = Agent(cfg=gen_cfg, guard=guard, registry=empty_registry)

        prompt_parts = [f"请为以下需求设计一个工作流：\n\n{description}"]
        if example_input:
            prompt_parts.append(f"\n示例输入参数参考：\n{example_input}")
        prompt_parts.append("\n直接输出 YAML，不需要任何额外说明。")

        raw = agent.run_turn("\n".join(prompt_parts))
        return self._clean_yaml(raw)

    def generate_from_summary(self, task_summary: "TaskSummary", adjustments: str = "") -> str:
        """
        session_to_workflow 第②阶段：把已确认的 TaskSummary 转换成 workflow YAML。
        对应 next_doc/session_to_workflow_design.md 第 4 节。

        与 generate() 平行存在，同样是"起临时 Agent + system_extra 覆盖 +
        一次 run_turn()"，只是喂给 LLM 的 prompt 从"自然语言 description"
        换成"结构化的 TaskSummary 序列化文本 + 明确的字段映射指引"。

        adjustments: 用户对①阶段总结提出的调整意见（如"修复阶段不要做成
        质检门"），原样拼进 prompt，供②阶段生成时纳入考虑。
        """
        from mini_agent.config import load_config
        from mini_agent.agent import Agent
        from mini_agent.permissions import PermissionGuard
        from mini_agent.tools import get_default_registry

        gen_cfg = load_config(
            project_root=self._cfg.project_root,
            verbose=False,
            sandbox=self._cfg.sandbox,
            auto_approve=True,
            model=self._cfg.model,
            llm_provider=self._cfg.llm_provider,
            llm_base_url=self._cfg.llm_base_url,
            debug_llm=getattr(self._cfg, "debug_llm", False),
            debug_llm_console=getattr(self._cfg, "debug_llm_console", False),
        )
        gen_cfg.api_key = self._cfg.api_key
        gen_cfg.max_turns = 3
        gen_cfg.stream = False
        gen_cfg.system_extra = GENERATOR_SYSTEM

        guard = PermissionGuard(
            auto_approve=True,
            sandbox=self._cfg.sandbox,
            project_root=self._cfg.project_root,
        )
        registered_tool_names = set(get_default_registry().names)
        empty_registry = get_default_registry().filtered(names=[], groups=[])
        agent = Agent(cfg=gen_cfg, guard=guard, registry=empty_registry)

        import json as _json
        summary_json = _json.dumps(task_summary.to_dict(), ensure_ascii=False, indent=2)

        prompt_parts = [
            "请把以下已确认的任务总结，转换成一个 workflow YAML 定义：",
            "- stages[] 里每个阶段映射成一个 step，id 直接用 stage.id",
            "- depends_on 用 stage.depends_on_stage_ids",
            "- gate_candidate=true 的阶段，用 role: evaluator + condition + "
            "retry_on_gate_fail 表达质检门语义，不要把重试展开成多个 step",
            "- candidate_parameters[] 里的值在 prompt 里换成 {参数名} 占位符，"
            "并据此生成 example_input",
            "- repeated_pattern 非空时，在生成结果最后额外提示\"这段可以存成"
            "可复用 step 片段\"，但不要求这一步就自动调用 save_snippet",
            "- 除非 stage.approach 明确到\"调用某个确定性工具就够了\"这种"
            f"程度，否则每个 step 用 type: agent（可用的确定性工具名限于："
            f"{', '.join(sorted(registered_tool_names)) or '（无）'}；生成"
            "tool_call/script 类型时 tool_name 必须在这个列表里，否则改用 "
            "type: agent）",
            "",
            "任务总结：",
            summary_json,
        ]
        if adjustments.strip():
            prompt_parts.append("")
            prompt_parts.append(f"用户对总结提出的调整意见（生成时请一并采纳）：\n{adjustments.strip()}")
        prompt_parts.append("\n直接输出 YAML，不需要任何额外说明。")

        raw = agent.run_turn("\n".join(prompt_parts))
        yaml_str = self._clean_yaml(raw)

        # 工具名幻觉防护：生成后再校验一遍 tool_call/script 类型的 tool_name，
        # 查不到就降级成 agent 类型（prompt 约束是第一道防线，这里是兜底，
        # 避免 LLM 未遵守约束时产出无法执行的 workflow）。
        return self._downgrade_unknown_tool_types(yaml_str, registered_tool_names)

    def _downgrade_unknown_tool_types(self, yaml_str: str, registered_tool_names: set) -> str:
        """
        生成后兜底：把 type: tool_call/script 但 tool_name 不在已注册工具表
        里的 step 降级为 type: agent（去掉 tool_name/tool_args，保留 prompt），
        避免因为解析失败而拦截整个生成结果——降级后仍能被 parse_yaml() 正常
        校验通过。解析失败（YAML 本身有语法错误）时原样返回，交给调用方的
        parse_yaml() 报错。
        """
        try:
            import yaml  # type: ignore
            data = yaml.safe_load(yaml_str)
        except Exception:
            return yaml_str
        if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
            return yaml_str

        changed = False
        for step in data["steps"]:
            if not isinstance(step, dict):
                continue
            if step.get("type") in ("tool_call", "script") and step.get("tool_name") not in registered_tool_names:
                step["type"] = "agent"
                step.pop("tool_name", None)
                step.pop("tool_args", None)
                changed = True

        if not changed:
            return yaml_str
        try:
            import yaml  # type: ignore
            return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        except Exception:
            return yaml_str

    def _clean_yaml(self, raw: str) -> str:
        """清理 LLM 输出中多余的 markdown 标记。"""
        # 去掉 ```yaml ... ``` 包裹
        raw = re.sub(r'^```(?:yaml)?\s*\n', '', raw.strip(), flags=re.MULTILINE)
        raw = re.sub(r'\n```\s*$', '', raw.strip(), flags=re.MULTILINE)
        return raw.strip()

    def parse_yaml(self, yaml_str: str) -> WorkflowDef:
        """把 YAML 字符串解析为 WorkflowDef，同时做校验。"""
        try:
            import yaml  # type: ignore
            data = yaml.safe_load(yaml_str)
        except Exception as e:
            raise ValueError(f"YAML 解析失败：{e}")

        if not isinstance(data, dict):
            raise ValueError("YAML 顶层必须是字典结构")

        wf = WorkflowDef.from_dict(data)
        errors = wf.validate()
        if errors:
            raise ValueError("工作流定义校验失败：\n" + "\n".join(f"  - {e}" for e in errors))
        return wf

    def preview(self, wf: WorkflowDef) -> str:
        """生成人类可读的工作流预览（用于用户确认）。"""
        lines = [
            f"## 工作流预览：{wf.name}",
            f"描述：{wf.description or '无'}",
            f"版本：{wf.version}  步骤数：{len(wf.steps)}",
            "",
            "### 步骤列表",
        ]
        for i, s in enumerate(wf.steps, 1):
            role_label = f" [角色:{s.role}]" if s.role else ""
            dep_label = f" ← 依赖：{s.depends_on}" if s.depends_on else ""
            cond_label = f" [条件:{s.condition}]" if s.condition else ""
            lines.append(f"{i}. **{s.id}** — {s.name}{role_label}{dep_label}{cond_label}")
            # prompt 预览前 80 字
            preview = s.prompt.replace("\n", " ")[:80]
            if len(s.prompt) > 80:
                preview += "..."
            lines.append(f"   *{preview}*")
        return "\n".join(lines)
