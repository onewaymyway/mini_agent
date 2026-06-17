"""
env_info/base.py — EnvInfoProvider 抽象基类

所有环境信息提供者都实现这个接口：
  - name: str            唯一标识，用于日志和 config 引用
  - enabled: bool        可在 config 中禁用某个 provider
  - collect() -> dict    返回 {display_label: value} 字典
                         失败时返回 {} 而不是抛出异常

设计原则：
  - collect() 内部必须捕获所有异常，返回空 dict 表示"跳过"
  - 返回值 dict 的 key 是展示用的标签（如 "OS"、"Python"），
    value 是字符串，复杂数据应格式化为可读字符串
  - Provider 不应有副作用，collect() 应该是幂等的
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EnvInfoProvider(ABC):
    """
    环境信息提供者抽象基类。

    子类只需实现 name 属性和 collect() 方法。
    collect() 内部异常必须静默处理，返回 {} 而不是传播异常。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 唯一标识，用于日志和 config 引用。"""
        ...

    @property
    def enabled(self) -> bool:
        """是否启用，子类可覆盖以支持动态开关。"""
        return True

    @abstractmethod
    def collect(self) -> dict[str, str]:
        """
        采集环境信息。

        Returns:
            {display_label: value} 字典。
            失败时返回 {}，不抛出异常。
        """
        ...

    def safe_collect(self) -> dict[str, str]:
        """
        带保护的 collect()，捕获所有异常。
        Registry 统一调用此方法而非 collect()。
        """
        if not self.enabled:
            return {}
        try:
            return self.collect() or {}
        except Exception:
            return {}
