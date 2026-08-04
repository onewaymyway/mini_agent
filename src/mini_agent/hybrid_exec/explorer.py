"""
hybrid_exec/explorer.py — Explorer 体系：从 0 生成脚本

对应 next_doc/hybrid_exec_design_plan.md §3.5。

LLMExplorer：单轮 LLM 直接生成脚本草稿，成本低。
AgentExplorer：多轮 Agent + 工具探索（P2 实现，见 _agent.py），成本高但
泛化强，读写文件系统权限受 TaskSpec.agent_fs_write_enabled 控制。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from .runner import RunnerAppConfig
from .spec import TaskSpec

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _strip_code_fence(text: str) -> str:
    """防御性处理：即使 prompt 已经要求不要用代码块包裹，模型偶尔还是会加，
    这里做个宽松的兜底剥离，避免直接把 ``` 当脚本内容存进仓库。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]  # 去掉开头的 ```python 或 ```
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines)
    return cleaned.strip() + "\n"


class Explorer(ABC):
    @abstractmethod
    def explore(self, task: TaskSpec) -> str:
        """返回符合 run(ctx) 协议的脚本源码。"""


class LLMExplorer(Explorer):
    """单轮 LLM 直接生成脚本草稿，成本低，适合规则清晰、输入结构稳定的任务。"""

    def __init__(self, app_cfg: RunnerAppConfig) -> None:
        self.app_cfg = app_cfg

    def explore(self, task: TaskSpec) -> str:
        from ._llm import build_llm_helper

        template = (_PROMPTS_DIR / "explore_script.md").read_text(encoding="utf-8")
        prompt = template.format(
            description=task.description,
            input_sample=json.dumps(task.input_data, ensure_ascii=False, indent=2),
        )
        helper = build_llm_helper(self.app_cfg)
        text = helper.ask(
            prompt,
            system="你是一名严谨的 Python 工程师，只输出代码，不输出多余解释。",
        )
        return _strip_code_fence(text)


class AgentExplorer(Explorer):
    """多轮 Agent + 工具探索，成本高但泛化强，适合任务描述模糊、需要先探查
    环境/数据形状的场景。是否允许 Agent 写文件系统由
    TaskSpec.agent_fs_write_enabled 控制（见 _agent.py 头部说明）。"""

    def __init__(self, app_cfg: RunnerAppConfig, *, max_turns: int = 8) -> None:
        self.app_cfg = app_cfg
        self.max_turns = max_turns

    def explore(self, task: TaskSpec) -> str:
        from ._agent import run_agent_prompt

        template = (_PROMPTS_DIR / "explore_script_agent.md").read_text(encoding="utf-8")
        prompt = template.format(
            description=task.description,
            input_sample=json.dumps(task.input_data, ensure_ascii=False, indent=2),
        )
        text = run_agent_prompt(
            self.app_cfg, task, prompt, max_turns=self.max_turns, session_label="explore"
        )
        return _strip_code_fence(text)
