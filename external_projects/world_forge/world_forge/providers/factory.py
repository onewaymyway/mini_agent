"""providers/factory.py — 按环境选择 provider。

WORLD_FORGE_PROVIDERS=stub  → 全部离线（文本规则生成 + 占位图）
否则：文本用 mini_agent LLMHelper；图像优先 Agnes skill，失败则降级占位图并标注。
"""

from __future__ import annotations

import os
from typing import Tuple

from .image import AgnesSkillImageProvider, ImageProvider, SkillUnavailable, StubImageProvider
from .text import MiniAgentTextLLM, RuleTextLLM, TextLLM


def forced_stub() -> bool:
    return os.environ.get("WORLD_FORGE_PROVIDERS", "").strip().lower() == "stub"


def make_text_llm() -> Tuple[TextLLM, str]:
    if forced_stub():
        return RuleTextLLM(), "离线规则生成（WORLD_FORGE_PROVIDERS=stub）"
    try:
        from mini_agent.llm.service import LLMHelper  # noqa: F401  - 仅检测可用性
    except ImportError:
        return RuleTextLLM(), "未检测到 mini_agent，已降级为离线规则生成"
    return MiniAgentTextLLM(), "mini_agent LLMHelper"


def make_image_provider() -> Tuple[ImageProvider, str]:
    if forced_stub():
        return StubImageProvider(), "占位图（WORLD_FORGE_PROVIDERS=stub）"
    try:
        return AgnesSkillImageProvider(), "Agnes 图像模型（gen_image_with_text skill）"
    except SkillUnavailable as exc:
        return StubImageProvider(), f"已降级为占位图：{exc}"
