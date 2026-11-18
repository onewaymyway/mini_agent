"""providers/text.py — 文本 LLM provider（P1 分节摘要与大纲）。

TextLLM.ask() 返回原始文本；解析与校验由调用方（planning）完成。
  * MiniAgentTextLLM：复用 mini_agent 的 LLMHelper（与 novel_to_video 同一套 provider 配置）。
规则版（离线）不经过 TextLLM，由 planning 直接生成，见 planning/rules.py。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..paths import mini_agent_root


class TextLLM:
    name = "base"

    def ask(self, prompt: str, system: str = "") -> str:  # pragma: no cover - interface
        raise NotImplementedError


class MiniAgentTextLLM(TextLLM):
    name = "miniagent"

    def __init__(self, project_root: Optional[Path] = None):
        self._root = project_root or mini_agent_root()
        self._helper = None

    def _get_helper(self):
        if self._helper is None:
            from mini_agent.config import load_config
            from mini_agent.llm.service import LLMHelper

            cfg = load_config(project_root=self._root, verbose=False, sandbox=True, auto_approve=True)
            self._helper = LLMHelper.from_config(cfg)
        return self._helper

    def ask(self, prompt: str, system: str = "") -> str:
        return self._get_helper().ask(prompt, system=system)


def make_text_llm(name: str) -> TextLLM:
    if name == "miniagent":
        return MiniAgentTextLLM()
    raise ValueError(f"未知的文本模型 provider：{name}")
