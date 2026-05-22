"""
llm — 统一 LLM 抽象层

对外只暴露这些符号：

    from llm import LLMClient, LLMConfig, LLMResponse, create_client

快速上手：
    from llm import create_client, LLMConfig

    client = create_client(LLMConfig(
        provider="anthropic",
        model="claude-opus-4-5",
        api_key="sk-ant-..."
    ))
    response = client.chat(messages, system, tools)
    print(response.text, response.tool_calls, response.usage)

切换 provider：
    client = create_client(LLMConfig(provider="ollama", model="llama3.1"))
    client = create_client(LLMConfig(provider="openai", model="gpt-4o", api_key="..."))
"""

from .base import (
    LLMClient,
    LLMConfig,
    LLMResponse,
    LLMUsage,
    ToolCall,
    ToolSchema,
    StreamCallback,
    ReasoningCallback,
    LLMError,
    LLMConfigError,
    LLMProviderError,
    LLMTimeoutError,
    LLMRateLimitError,
)
from .factory import create_client, register_provider, list_providers

__all__ = [
    "LLMClient", "LLMConfig", "LLMResponse", "LLMUsage",
    "ToolCall", "ToolSchema", "StreamCallback", "ReasoningCallback",
    "create_client", "register_provider", "list_providers",
    "LLMError", "LLMConfigError", "LLMProviderError",
    "LLMTimeoutError", "LLMRateLimitError",
]
