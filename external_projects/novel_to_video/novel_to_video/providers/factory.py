"""providers/factory.py — 按名称构造 provider。

选择顺序：函数参数 > 项目设置（project.json）> 环境变量 NOVEL_TO_VIDEO_PROVIDERS=stub。
stub 模式下图像与文本都走离线实现，不消耗任何 API 额度。
"""

from __future__ import annotations

import os
from typing import Optional

from .image import ImageProvider, StubImageProvider
from .video import StubVideoProvider, VideoProvider
from .tts import EdgeTTS, SilenceTTS, TTSProvider
from .text import MiniAgentTextLLM, RuleTextLLM, TextLLM
from . import skill_bridge
from .skill_providers import SkillImageProvider, SkillVideoProvider


class ProviderError(RuntimeError):
    pass


def _forced_stub() -> bool:
    return os.environ.get("NOVEL_TO_VIDEO_PROVIDERS", "").strip().lower() == "stub"


def no_key_message(kind: str) -> str:
    """没有可用 key 时的说明：列出实际检查过的位置，便于用户定位。"""
    try:
        cfg = skill_bridge.key_config()
        pj = skill_bridge.providers_json_path()
        where = f"providers.json（已找到：{pj}）" if pj else "providers.json（未找到，应位于 mini_agent 项目根目录）"
    except skill_bridge.SkillUnavailable as exc:
        return f"无法使用 Agnes skill：{exc}"
    return (f"未找到可用的 Agnes API key（{kind}）。skill 按以下顺序查找：环境变量 AGNES_API_KEYS、"
            f"{where} 中 providers.agnes.api_keys 或 llm_fallback_chain 里 provider=agnes 的 api_keys、"
            f"环境变量 AGNES_API_KEY。当前解析结果：{cfg['count']} 把 key（来源：{cfg['source']}）。")


def _skill_or_raise(factory, kind: str):
    try:
        return factory()
    except RuntimeError as exc:
        if str(exc) == "no-key":
            raise ProviderError(no_key_message(kind)) from exc
        raise ProviderError(str(exc)) from exc
    except skill_bridge.SkillUnavailable as exc:
        raise ProviderError(f"无法使用 Agnes skill：{exc}") from exc


def make_image_provider(name: Optional[str] = None) -> ImageProvider:
    """图像 provider。agnes 通过 mini_agent 的 gen_image_with_text skill 调用，key 由 skill 解析。"""
    name = "stub" if _forced_stub() else (name or "agnes")
    if name == "stub":
        return StubImageProvider()
    if name == "agnes":
        return _skill_or_raise(lambda: SkillImageProvider(), "图像")
    raise ProviderError(f"未知图像 provider：{name}")


def make_text_llm(name: Optional[str] = None) -> TextLLM:
    name = "rules" if _forced_stub() else (name or "miniagent")
    if name == "rules":
        return RuleTextLLM()
    if name == "miniagent":
        return MiniAgentTextLLM()
    raise ProviderError(f"未知文本 provider：{name}")


def make_video_provider(name: Optional[str] = None) -> VideoProvider:
    """视频 provider。agnes 通过 mini_agent 的 gen_video_with_text skill 调用，key 由 skill 解析。"""
    name = "stub" if _forced_stub() else (name or "agnes")
    if name == "stub":
        return StubVideoProvider()
    if name == "agnes":
        return _skill_or_raise(lambda: SkillVideoProvider(), "视频")
    raise ProviderError(f"未知视频 provider：{name}")


def make_tts_provider(name: Optional[str] = None) -> TTSProvider:
    name = "stub" if _forced_stub() else (name or "edge-tts")
    if name == "stub":
        return SilenceTTS()
    if name == "edge-tts":
        try:
            return EdgeTTS()
        except RuntimeError as exc:
            raise ProviderError(str(exc)) from exc
    raise ProviderError(f"未知 TTS provider：{name}")
