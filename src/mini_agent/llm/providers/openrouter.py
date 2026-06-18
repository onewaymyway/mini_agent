"""
llm/providers/openrouter.py — OpenRouter provider

OpenRouter 是统一的 LLM API 网关，聚合 100+ 模型（Claude、GPT-4o、Gemini、
Llama、Mistral 等），提供统一计费和限流管理。

API 特性：
  - 完全兼容 OpenAI SDK（直接复用 OpenAIProvider）
  - Base URL: https://openrouter.ai/api/v1
  - 模型 ID 格式: "anthropic/claude-opus-4-7"、"openai/gpt-4o" 等
  - 推荐额外请求头: HTTP-Referer、X-Title（用于 OpenRouter 统计和排名）

工具调用：
  OpenRouter 对 native tool calling 的转发能力因底层模型而异，稳定性参差不齐。
  本实现统一走 system-prompt 工具协议（ProviderMixin._prepare_tools），
  完全绕过 OpenRouter 的 tool 转发，确保所有模型行为一致。

配置示例（providers.json）：
  {
    "provider": "openrouter",
    "model": "anthropic/claude-opus-4-7",
    "api_key": "sk-or-..."
  }

自定义头（可选，通过 extra 传入）：
  {
    "provider": "openrouter",
    "model": "openai/gpt-4o",
    "api_key": "sk-or-...",
    "extra": {
      "http_referer": "https://your-site.com",
      "x_title": "YourAppName"
    }
  }
"""

from __future__ import annotations

import os

from .openai import OpenAIProvider
from ..base import LLMConfig, LLMProviderError

_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_REFERER = "https://github.com/mini-agent/mini-agent"
_DEFAULT_TITLE = "mini_agent"


class OpenRouterProvider(OpenAIProvider):
    """
    OpenRouter provider，继承 OpenAIProvider。

    覆盖 _build_client() 以：
      1. 注入默认 base_url（openrouter.ai）
      2. 注入 HTTP-Referer 和 X-Title 请求头
      3. 从环境变量 OPENROUTER_API_KEY 读取 key（若 config 未提供）
    """

    def __init__(self, config: LLMConfig) -> None:
        # 从环境变量补全 api_key
        if not config.api_key:
            config.api_key = os.environ.get("OPENROUTER_API_KEY", "")

        # 注入默认 base_url
        if not config.base_url:
            config.base_url = os.environ.get("OPENROUTER_BASE_URL", _DEFAULT_BASE_URL)

        # 注入默认请求头（允许用户通过 extra 覆盖）
        headers = {
            "HTTP-Referer": config.extra.pop("http_referer", _DEFAULT_REFERER),
            "X-Title": config.extra.pop("x_title", _DEFAULT_TITLE),
        }
        # 合并到 extra["default_headers"]，不覆盖用户已设置的值
        existing = config.extra.get("default_headers", {})
        config.extra["default_headers"] = {**headers, **existing}

        super().__init__(config)

    @property
    def provider_name(self) -> str:
        return "OpenRouter"
