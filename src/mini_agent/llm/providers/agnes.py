"""
llm/providers/agnes.py — Agnes AI provider

Agnes AI（agnes-ai.com）是一个 AI 网关，提供 Agnes-1.5-Flash / Agnes-2.0-Flash
等模型，完全兼容 OpenAI Chat Completions API。

API 特性：
  - 完全兼容 OpenAI SDK（直接复用 OpenAIProvider）
  - Endpoint: https://apihub.agnes-ai.com/v1/chat/completions
    （即 base_url = https://apihub.agnes-ai.com/v1）
  - Authentication: Bearer Token（Authorization: Bearer YOUR_API_KEY）
  - 模型 ID 示例: "agnes-2.0-flash"、"agnes-1.5-flash"
  - 支持原生 tools / tool_choice、流式输出、图片 URL 输入（image_url）
  - 支持 Thinking 模式：
      OpenAI 兼容请求 — chat_template_kwargs.enable_thinking: true
      Anthropic 兼容请求 — thinking: {"type": "enabled", "budget_tokens": N}
    本 Provider 走 OpenAI 兼容协议，如需开启 Thinking，可通过
    config.extra["chat_template_kwargs"] = {"enable_thinking": True} 传入，
    会被 OpenAIProvider._build_kwargs() 透传进请求体。

配置示例（providers.json / LLMConfig）：
  {
    "provider": "agnes",
    "model": "agnes-2.0-flash",
    "api_key": "YOUR_API_KEY"
  }

环境变量：
  AGNES_API_KEY   — 未在 config 中显式提供 api_key 时回退读取
  AGNES_BASE_URL  — 自定义网关地址（默认 https://apihub.agnes-ai.com/v1）
"""

from __future__ import annotations

import os

from .openai import OpenAIProvider
from ..base import LLMConfig

_DEFAULT_BASE_URL = "https://apihub.agnes-ai.com/v1"


class AgnesProvider(OpenAIProvider):
    """
    Agnes AI provider，继承 OpenAIProvider。

    覆盖 __init__() 以：
      1. 注入默认 base_url（apihub.agnes-ai.com/v1）
      2. 从环境变量 AGNES_API_KEY 读取 key（若 config 未提供）
    其余请求构建、响应解析、流式处理、错误包装均复用 OpenAIProvider，
    因为 Agnes 的 Chat Completions API 与 OpenAI 完全兼容。
    """

    def __init__(self, config: LLMConfig) -> None:
        # 从环境变量补全 api_key
        if not config.api_key:
            config.api_key = os.environ.get("AGNES_API_KEY", "")

        # 注入默认 base_url（允许通过 config.base_url 或环境变量覆盖）
        if not config.base_url:
            config.base_url = os.environ.get("AGNES_BASE_URL", _DEFAULT_BASE_URL)

        super().__init__(config)

    @property
    def provider_name(self) -> str:
        return "Agnes"
