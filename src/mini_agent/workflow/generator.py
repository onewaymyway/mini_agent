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
            debug_llm=False,
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
