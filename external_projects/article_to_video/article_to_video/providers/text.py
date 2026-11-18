"""providers/text.py — 文本 LLM provider（P1 分节摘要与大纲）。

TextLLM.ask() 返回原始文本；解析与校验由调用方（planning）完成。
  * MiniAgentTextLLM：复用 mini_agent 的 LLMHelper（与 novel_to_video 同一套 provider 配置）。
规则版（离线）不经过 TextLLM，由 planning 直接生成，见 planning/rules.py。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Tuple

from .. import cancel
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
        # 模型请求可能持续数十秒：经 cancel.call，用户停止时立即返回
        return cancel.call(lambda: self._get_helper().ask(prompt, system=system))


_STATUS: dict = {"at": 0.0, "value": (False, "尚未检查")}
STATUS_TTL_SEC = 60.0


def text_model_status(force: bool = False) -> Tuple[bool, str]:
    """文本模型是否已就绪：只检查能否读取 mini_agent 的模型配置并构造客户端，不发出任何模型请求。

    结果缓存 STATUS_TTL_SEC 秒，避免界面每次刷新都读取配置。返回 (是否就绪, 说明)。
    """
    now = time.time()
    if not force and now - _STATUS["at"] < STATUS_TTL_SEC:
        return _STATUS["value"]
    try:
        MiniAgentTextLLM()._get_helper()
        value: Tuple[bool, str] = (True, "已读取模型配置")
    except Exception as exc:  # noqa: BLE001 — 原因展示给用户
        value = (False, f"{type(exc).__name__}: {str(exc)[:200]}")
    _STATUS.update(at=now, value=value)
    return value


def make_text_llm(name: str) -> TextLLM:
    if name == "miniagent":
        return MiniAgentTextLLM()
    raise ValueError(f"未知的文本模型 provider：{name}")
