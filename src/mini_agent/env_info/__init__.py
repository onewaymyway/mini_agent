"""
env_info — 环境信息采集与注入模块

公开 API：
    EnvInfoProvider   — 自定义 Provider 的抽象基类
    EnvInfoRegistry   — 注册、采集、格式化的管理器
    build_env_block() — 便捷函数，直接返回格式化后的 Markdown 段落

快速使用：
    from mini_agent.env_info import build_env_block
    block = build_env_block()   # 使用默认三个内置 Provider

自定义 Provider：
    from mini_agent.env_info import EnvInfoProvider

    class MyProvider(EnvInfoProvider):
        name = "my.provider"
        def collect(self):
            return {"My key": "my value"}
"""

from mini_agent.env_info.base import EnvInfoProvider
from mini_agent.env_info.registry import EnvInfoRegistry

__all__ = ["EnvInfoProvider", "EnvInfoRegistry", "build_env_block"]


def build_env_block(
    providers: list[str] | None = None,
    provider_kwargs: dict | None = None,
) -> str:
    """
    便捷函数：构建环境信息 Markdown 块。

    Args:
        providers:       Provider 标识列表，None 表示使用默认三个内置 Provider
        provider_kwargs: 各 Provider 的初始化参数

    Returns:
        格式化好的 Markdown 字符串，可直接注入 system prompt。
        若无任何信息采集到，返回空字符串。
    """
    registry = EnvInfoRegistry.from_config(providers, provider_kwargs)
    return registry.build_block()
