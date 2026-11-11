"""检查器自动发现：扫描 checkers/ 下所有模块，注册继承 Checker 且声明 name 的类。"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Optional

from checkers.base import Checker

_REGISTRY: dict[str, Checker] = {}
_LOADED = False
_SKIP = {"base", "registry", "__init__"}


def _discover() -> None:
    global _LOADED
    if _LOADED:
        return
    package = importlib.import_module("checkers")
    for info in pkgutil.iter_modules(package.__path__):
        if info.name in _SKIP:
            continue
        module = importlib.import_module(f"checkers.{info.name}")
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, Checker) and cls is not Checker and cls.name and cls.__module__ == module.__name__:
                if cls.name in _REGISTRY:
                    raise RuntimeError(f"检查器 name 重复: {cls.name}")
                _REGISTRY[cls.name] = cls()
    _LOADED = True


def get_checker(name: str) -> Optional[Checker]:
    _discover()
    return _REGISTRY.get(name)


def list_checkers() -> list[str]:
    _discover()
    return sorted(_REGISTRY)
