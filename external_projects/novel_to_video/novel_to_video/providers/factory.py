"""providers/factory.py — 按名称构造 provider。

选择顺序：函数参数 > 项目设置（project.json）> 环境变量 NOVEL_TO_VIDEO_PROVIDERS=stub。
stub 模式下图像与文本都走离线实现，不消耗任何 API 额度。
"""

from __future__ import annotations

import os
from typing import Optional

from .image import AgnesImageProvider, ImageProvider, StubImageProvider
from .video import AgnesVideoProvider, StubVideoProvider, VideoProvider
from .tts import EdgeTTS, SilenceTTS, TTSProvider
from .keypool import pool_from_env
from .text import MiniAgentTextLLM, RuleTextLLM, TextLLM


class ProviderError(RuntimeError):
    pass


def _forced_stub() -> bool:
    return os.environ.get("NOVEL_TO_VIDEO_PROVIDERS", "").strip().lower() == "stub"


def make_image_provider(name: Optional[str] = None) -> ImageProvider:
    name = "stub" if _forced_stub() else (name or "agnes")
    if name == "stub":
        return StubImageProvider()
    if name == "agnes":
        pool = pool_from_env()
        if pool is None:
            raise ProviderError("未配置图像 API key：请设置环境变量 AGNES_API_KEYS（逗号分隔）或 AGNES_API_KEY")
        return AgnesImageProvider(pool)
    raise ProviderError(f"未知图像 provider：{name}")


def make_text_llm(name: Optional[str] = None) -> TextLLM:
    name = "rules" if _forced_stub() else (name or "miniagent")
    if name == "rules":
        return RuleTextLLM()
    if name == "miniagent":
        return MiniAgentTextLLM()
    raise ProviderError(f"未知文本 provider：{name}")


def make_video_provider(name: Optional[str] = None) -> VideoProvider:
    name = "stub" if _forced_stub() else (name or "agnes")
    if name == "stub":
        return StubVideoProvider()
    if name == "agnes":
        pool = pool_from_env()
        if pool is None:
            raise ProviderError("未配置视频 API key：请设置 AGNES_API_KEYS 或 AGNES_API_KEY")
        return AgnesVideoProvider(pool)
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
