"""
hybrid_exec/repairer.py — Repairer 体系：脚本报错后的修复

对应 next_doc/hybrid_exec_design_plan.md §3.6。

LLMRepairer：单轮 LLM 定位并修复局部问题，适合语法错误、边界条件遗漏等。
AgentRepairer：多轮 Agent 修复（P2 实现，见 _agent.py），允许读文件/反复
试跑，适合需要理解外部环境的报错，读写权限同样受
TaskSpec.agent_fs_write_enabled 控制。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from .explorer import _strip_code_fence
from .runner import RunnerAppConfig
from .spec import ScriptOutcome, TaskSpec

_PROMPTS_DIR = Path(__file__).parent / "prompts"


class Repairer(ABC):
    @abstractmethod
    def repair(self, task: TaskSpec, broken_code: str, outcome: ScriptOutcome) -> str:
        """返回修复后的脚本源码。"""


class LLMRepairer(Repairer):
    """单轮 LLM 定位并修复局部问题，适合语法错误、边界条件遗漏等。

    llm 参数含义同 `explorer.py::LLMExplorer`：嵌入 workflow 时可传入
    workflow 已经解析好的 llm 对象直接复用；独立调用不传则自动按
    `app_cfg.project_root` 加载 `providers.json`。
    """

    def __init__(self, app_cfg: RunnerAppConfig, *, llm: object = None) -> None:
        self.app_cfg = app_cfg
        self._llm = llm

    def repair(self, task: TaskSpec, broken_code: str, outcome: ScriptOutcome) -> str:
        from ._llm import build_llm_helper

        template = (_PROMPTS_DIR / "repair_script.md").read_text(encoding="utf-8")
        prompt = template.format(
            description=task.description,
            input_sample=json.dumps(task.input_data, ensure_ascii=False, indent=2),
            broken_code=broken_code,
            error_type=outcome.error_type or "",
            error_message=outcome.error or "",
            traceback=outcome.traceback or "(无 traceback)",
        )
        helper = self._llm if self._llm is not None else build_llm_helper(self.app_cfg)
        text = helper.ask(
            prompt,
            system="你是一名严谨的 Python 工程师，只输出修复后的代码，不输出多余解释。",
        )
        return _strip_code_fence(text)


class AgentRepairer(Repairer):
    """多轮 Agent 修复，允许读文件/反复试跑，适合需要理解外部环境的报错。"""

    def __init__(self, app_cfg: RunnerAppConfig, *, max_turns: int = 8) -> None:
        self.app_cfg = app_cfg
        self.max_turns = max_turns

    def repair(self, task: TaskSpec, broken_code: str, outcome: ScriptOutcome) -> str:
        from ._agent import run_agent_prompt

        template = (_PROMPTS_DIR / "repair_script_agent.md").read_text(encoding="utf-8")
        prompt = template.format(
            description=task.description,
            input_sample=json.dumps(task.input_data, ensure_ascii=False, indent=2),
            broken_code=broken_code,
            error_type=outcome.error_type or "",
            error_message=outcome.error or "",
            traceback=outcome.traceback or "(无 traceback)",
        )
        text = run_agent_prompt(
            self.app_cfg, task, prompt, max_turns=self.max_turns, session_label="repair"
        )
        return _strip_code_fence(text)
