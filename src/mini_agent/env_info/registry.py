"""
env_info/registry.py — EnvInfoRegistry

职责：
  1. 注册 EnvInfoProvider 实例
  2. collect()：遍历所有 Provider，合并输出
  3. build_block()：将采集结果格式化为 System Prompt 段落
  4. from_config()：从配置字符串列表动态构建 Registry

Provider 加载优先级（from_config）：
  - "builtin.system"   → SystemInfoProvider
  - "builtin.runtime"  → RuntimeInfoProvider
  - "builtin.locale"   → LocaleInfoProvider
  - "pkg.module.Class" → importlib 动态加载，实例化时无参数
    （如需参数，用 agent_config.json 的子配置块传入，或在 Provider.__init__ 读环境变量）
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

from mini_agent.env_info.base import EnvInfoProvider

logger = logging.getLogger(__name__)

# ── 内置别名映射 ───────────────────────────────────────────────────────────────

_BUILTIN_ALIASES: dict[str, str] = {
    "builtin.system":  "mini_agent.env_info.providers.system.SystemInfoProvider",
    "builtin.runtime": "mini_agent.env_info.providers.runtime.RuntimeInfoProvider",
    "builtin.locale":  "mini_agent.env_info.providers.locale.LocaleInfoProvider",
}

_DEFAULT_PROVIDERS = list(_BUILTIN_ALIASES.keys())


class EnvInfoRegistry:
    """
    管理所有 EnvInfoProvider 的注册与采集。

    用法（直接构建）：
        registry = EnvInfoRegistry()
        registry.register(SystemInfoProvider())
        block = registry.build_block()

    用法（从配置构建）：
        registry = EnvInfoRegistry.from_config(
            providers=["builtin.system", "builtin.runtime"],
            provider_kwargs={"builtin.system": {"include_hostname": True}},
        )
    """

    def __init__(self) -> None:
        self._providers: list[EnvInfoProvider] = []

    # ── 注册 API ───────────────────────────────────────────────────────────────

    def register(self, provider: EnvInfoProvider) -> None:
        """注册一个 Provider 实例。"""
        self._providers.append(provider)

    def register_all(self, providers: list[EnvInfoProvider]) -> None:
        """批量注册。"""
        for p in providers:
            self.register(p)

    @property
    def providers(self) -> list[EnvInfoProvider]:
        return list(self._providers)

    # ── 采集 API ───────────────────────────────────────────────────────────────

    def collect(self) -> dict[str, str]:
        """
        遍历所有 Provider，合并采集结果。
        后注册的 Provider 若 key 重复，会覆盖先前的值（允许用户 Provider 覆盖内置值）。
        """
        merged: dict[str, str] = {}
        for p in self._providers:
            try:
                data = p.safe_collect()
                merged.update(data)
            except Exception as e:
                logger.debug("[env_info] Provider %s failed: %s", p.name, e)
        return merged

    def build_block(self) -> str:
        """
        将采集结果格式化为 System Prompt Markdown 段落。

        输出示例：
            ## Environment
            - OS: macOS 14.5 (arm64)
            - Python: 3.12.3 (venv: .venv)
            - Timezone: Asia/Shanghai (UTC+8)
        """
        data = self.collect()
        if not data:
            return ""

        lines = ["## Environment"]
        for key, value in data.items():
            lines.append(f"- {key}: {value}")
        return "\n".join(lines)

    # ── 工厂方法 ───────────────────────────────────────────────────────────────

    @classmethod
    def from_config(
        cls,
        providers: list[str] | None = None,
        provider_kwargs: dict[str, dict[str, Any]] | None = None,
    ) -> "EnvInfoRegistry":
        """
        从配置字符串列表动态构建 Registry。

        Args:
            providers: Provider 标识列表。支持：
                       - 内置别名："builtin.system", "builtin.runtime", "builtin.locale"
                       - 完整类路径："mypkg.myplugin.MyProvider"
            provider_kwargs: 各 Provider 的初始化参数，key 为 Provider 标识。
                             示例：{"builtin.system": {"include_hostname": True}}

        Returns:
            已注册所有成功加载的 Provider 的 Registry 实例。
        """
        if providers is None:
            providers = _DEFAULT_PROVIDERS

        kwargs_map = provider_kwargs or {}
        registry = cls()

        for spec in providers:
            try:
                provider = _load_provider(spec, kwargs_map.get(spec, {}))
                registry.register(provider)
                logger.debug("[env_info] Loaded provider: %s", spec)
            except Exception as e:
                logger.warning("[env_info] Failed to load provider %r: %s", spec, e)

        return registry

    def __repr__(self) -> str:
        names = [p.name for p in self._providers]
        return f"EnvInfoRegistry(providers={names})"


# ── 内部辅助 ───────────────────────────────────────────────────────────────────

def _load_provider(spec: str, kwargs: dict[str, Any]) -> EnvInfoProvider:
    """
    根据字符串 spec 加载并实例化一个 Provider。

    spec 可以是：
    - 内置别名（如 "builtin.system"）
    - 完整 Python 类路径（如 "mypkg.module.MyProvider"）
    """
    # 解析别名
    resolved = _BUILTIN_ALIASES.get(spec, spec)

    # 拆分 module 和 class
    if "." not in resolved:
        raise ValueError(f"Invalid provider spec {spec!r}: expected 'module.ClassName' format")

    module_path, class_name = resolved.rsplit(".", 1)

    # 动态 import
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ImportError(f"Cannot import module {module_path!r} for provider {spec!r}: {e}") from e

    # 获取类
    cls = getattr(module, class_name, None)
    if cls is None:
        raise AttributeError(f"Module {module_path!r} has no class {class_name!r}")

    if not (isinstance(cls, type) and issubclass(cls, EnvInfoProvider)):
        raise TypeError(f"{spec!r} ({cls}) is not a subclass of EnvInfoProvider")

    # 实例化
    return cls(**kwargs)
