"""
hybrid_exec/fallback.py — FallbackExecutor：脚本彻底不可用时的兜底

对应 next_doc/hybrid_exec_design_plan.md §3.7。

这一层不产出脚本、不写回仓库——是"这次先把事办了"的应急通道。
llm_direct：单轮 LLM 直接给答案。
agent_direct：多轮 Agent 直接给答案（P2 实现，见 _agent.py），是最高能力
层级，没有再降级的空间。
"""

from __future__ import annotations

import json

from .runner import RunnerAppConfig
from .spec import TaskSpec


class FallbackExecutor:
    def __init__(self, app_cfg: RunnerAppConfig, *, agent_max_turns: int = 10) -> None:
        self.app_cfg = app_cfg
        self.agent_max_turns = agent_max_turns

    def llm_direct(self, task: TaskSpec) -> str:
        """单轮直接向 LLM 要答案，不生成脚本。"""
        from ._llm import build_llm_helper

        prompt = (
            f"任务描述：{task.description}\n\n"
            f"输入数据（JSON）：\n{json.dumps(task.input_data, ensure_ascii=False, indent=2)}\n\n"
            "请直接给出这个任务的最终结果。"
        )
        helper = build_llm_helper(self.app_cfg)
        return helper.ask(prompt, system="你是一个可靠的助手，直接给出任务结果，不要输出多余的过程说明。")

    def agent_direct(self, task: TaskSpec) -> str:
        """多轮 Agent 直接完成任务本身，不产出脚本。"""
        from pathlib import Path

        from ._agent import run_agent_prompt

        template_path = Path(__file__).parent / "prompts" / "fallback_agent.md"
        template = template_path.read_text(encoding="utf-8")
        prompt = template.format(
            description=task.description,
            input_sample=json.dumps(task.input_data, ensure_ascii=False, indent=2),
        )
        return run_agent_prompt(
            self.app_cfg, task, prompt, max_turns=self.agent_max_turns, session_label="fallback"
        )
