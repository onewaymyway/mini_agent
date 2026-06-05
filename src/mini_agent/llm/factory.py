"""
llm/factory.py — Provider 工厂

根据 LLMConfig.provider 字符串实例化对应的 LLMClient。

注册新 provider 只需两步：
  1. 在 llm/providers/ 下新建文件，继承 LLMClient 并实现 chat() / stream()
  2. 在下面的 _REGISTRY 字典中添加一行

工厂会延迟导入 provider 模块（懒加载），避免在未安装对应 SDK 时报错。
"""

from __future__ import annotations

from typing import Callable, Optional, TYPE_CHECKING

from .base import LLMClient, LLMConfig, LLMConfigError

if TYPE_CHECKING:
    pass


# ── Provider 注册表 ───────────────────────────────────────────────────────────
# key:   LLMConfig.provider 字段值（小写）
# value: 无参 callable，返回 LLMClient 子类（延迟导入，避免无关 SDK 的 ImportError）

def _load_anthropic():
    from .providers.anthropic import AnthropicProvider
    return AnthropicProvider

def _load_openai():
    from .providers.openai import OpenAIProvider
    return OpenAIProvider

def _load_ollama():
    from .providers.ollama import OllamaProvider
    return OllamaProvider

def _load_nvidia():
    from .providers.nvidia import NvidiaProvider
    return NvidiaProvider


_REGISTRY: dict[str, Callable[[], type[LLMClient]]] = {
    # ── 已内置的 provider ─────────────────────────────────────────
    "anthropic":  _load_anthropic,
    "claude":     _load_anthropic,   # 别名
    "openai":     _load_openai,
    "azure":      _load_openai,      # Azure OpenAI 兼容 OpenAI 格式
    # OpenAI 兼容的第三方服务（只需设置 base_url 和 api_key）
    "deepseek":   _load_openai,
    "moonshot":   _load_openai,
    "qwen":       _load_openai,
    "groq":       _load_openai,
    "together":   _load_openai,
    "fireworks":  _load_openai,
    # ── 本地模型 ──────────────────────────────────────────────────
    "ollama":     _load_ollama,
    "local":      _load_ollama,      # 别名
    # ── NVIDIA NIM ───────────────────────────────────────────────
    "nvidia":     _load_nvidia,
    "nim":        _load_nvidia,      # 别名
}


# ── 公共工厂函数 ──────────────────────────────────────────────────────────────

def create_client(config: LLMConfig) -> LLMClient:
    """
    根据 config.provider 创建并返回对应的 LLMClient 实例。

    Args:
        config: 包含 provider、model、api_key 等信息的 LLMConfig

    Returns:
        已实例化的 LLMClient 子类

    Raises:
        LLMConfigError: provider 名称未注册，或配置缺失必要字段

    Example:
        client = create_client(LLMConfig(provider="anthropic", model="claude-opus-4-5", api_key="..."))
        client = create_client(LLMConfig(provider="ollama", model="llama3.1"))
        client = create_client(LLMConfig(provider="openai", model="gpt-4o", api_key="...",
                                         base_url="https://api.deepseek.com"))
    """
    provider_key = config.provider.lower().strip()

    loader = _REGISTRY.get(provider_key)
    if loader is None:
        available = sorted(_REGISTRY)
        raise LLMConfigError(
            f"Unknown LLM provider: {config.provider!r}.\n"
            f"Available providers: {available}\n"
            f"To add a new provider, register it in llm/factory.py._REGISTRY."
        )

    try:
        provider_cls = loader()
    except ImportError as e:
        raise LLMConfigError(
            f"Provider {config.provider!r} requires an extra package. {e}"
        )

    client = provider_cls(config)
    client.validate_config()
    return client


def register_provider(name: str, loader: Callable[[], type[LLMClient]]) -> None:
    """
    动态注册自定义 provider（运行时调用）。

    适合插件场景或测试中注入 mock provider。

    Example:
        from mini_agent.llm.factory import register_provider
        from my_package import MyCustomProvider
        register_provider("my-provider", lambda: MyCustomProvider)
    """
    _REGISTRY[name.lower()] = loader


# 每个 loader 函数的"规范名"（别名不在此列表中）
_CANONICAL_NAMES: dict[str, str] = {
    "_load_anthropic": "anthropic",
    "_load_openai":    "openai",
    "_load_ollama":    "ollama",
    "_load_nvidia":    "nvidia",
}


def list_providers() -> list[str]:
    """
    返回所有已注册的 provider 规范名称（去掉别名、排序）。

    规范名来自 _CANONICAL_NAMES；对于动态注册的 provider，
    使用各组名称中字母序最小的那个作为规范名。
    """
    loader_to_names: dict[Callable, list[str]] = {}
    for name, loader in _REGISTRY.items():
        loader_to_names.setdefault(loader, []).append(name)

    result: list[str] = []
    for loader, names in loader_to_names.items():
        canonical = _CANONICAL_NAMES.get(loader.__name__, min(names))
        result.append(canonical)
    return sorted(set(result))
