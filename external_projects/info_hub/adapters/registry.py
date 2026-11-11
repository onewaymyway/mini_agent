"""适配器自动发现：扫描 adapters/ 包下所有模块，注册继承 Adapter 且声明 kind_name 的类。

新增适配器只需在 adapters/ 下新建模块，无需修改本文件。
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Optional

from adapters.base import Adapter

_REGISTRY: dict[str, Adapter] = {}
_LOADED = False

_SKIP_MODULES = {"base", "registry", "__init__"}


def _discover() -> None:
    global _LOADED
    if _LOADED:
        return
    package = importlib.import_module("adapters")
    for mod_info in pkgutil.iter_modules(package.__path__):
        if mod_info.name in _SKIP_MODULES:
            continue
        module = importlib.import_module(f"adapters.{mod_info.name}")
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, Adapter) and cls is not Adapter and cls.kind_name:
                if cls.kind_name in _REGISTRY:
                    raise RuntimeError(f"适配器 kind_name 重复: {cls.kind_name}")
                _REGISTRY[cls.kind_name] = cls()
    _LOADED = True


def get_adapter(kind_name: str) -> Optional[Adapter]:
    _discover()
    return _REGISTRY.get(kind_name)


def list_adapters() -> list[str]:
    _discover()
    return sorted(_REGISTRY)
