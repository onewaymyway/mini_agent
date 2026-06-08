"""
perception/memory_factory.py — 记忆后端工厂

根据 MemoryConfig.backend 字符串创建对应的 MemoryBackend 实例。

注册新后端：
  from mini_agent.perception.memory_factory import register_memory_backend
  register_memory_backend("my_backend", lambda cfg: MyBackend(cfg))

内置后端：
  "local"  — MemoryStore（JSONL + TF-IDF，无外部依赖，默认）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from mini_agent.perception.memory_base import MemoryBackend

if TYPE_CHECKING:
    from mini_agent.config import AppConfig


# ── 后端注册表 ────────────────────────────────────────────────────────────────
# key:   MemoryConfig.backend 字段值（小写）
# value: Callable[[AppConfig], MemoryBackend]

def _load_local(cfg: "AppConfig") -> MemoryBackend:
    from pathlib import Path
    from mini_agent.perception.memory_store import MemoryStore
    store_path = cfg.memory.store_path or (cfg.project_root / ".agent" / "memory.jsonl")
    return MemoryStore(
        path=store_path,
        max_entries=cfg.memory.max_entries,
        decay_half_life_days=cfg.memory.decay_half_life_days,
    )


_REGISTRY: dict[str, Callable[["AppConfig"], MemoryBackend]] = {
    "local": _load_local,
    # 未来扩展点（外部依赖，不内置）：
    # "chroma": _load_chroma,
    # "redis":  _load_redis,
    # "sqlite": _load_sqlite,
}


# ── 公共 API ──────────────────────────────────────────────────────────────────

def create_memory_backend(cfg: "AppConfig") -> MemoryBackend:
    """
    根据 cfg.memory.backend 创建并返回对应的 MemoryBackend 实例。

    Args:
        cfg: AppConfig（含 MemoryConfig 子块）

    Returns:
        MemoryBackend 实例，已就绪可调用 add/search

    Raises:
        ValueError: backend 名称未注册

    Example:
        backend = create_memory_backend(cfg)
        backend.add(entry)
        results = backend.search("上次如何处理 JSON 解析错误", k=3)
    """
    backend_key = cfg.memory.backend.lower().strip()
    loader = _REGISTRY.get(backend_key)
    if loader is None:
        available = sorted(_REGISTRY)
        raise ValueError(
            f"Unknown memory backend: {backend_key!r}.\n"
            f"Available: {available}\n"
            f"Register a custom backend via register_memory_backend()."
        )
    return loader(cfg)


def register_memory_backend(
    name: str,
    loader: Callable[["AppConfig"], MemoryBackend],
) -> None:
    """
    动态注册自定义记忆后端。

    适合插件场景或测试中注入 mock 后端。

    Example:
        from mini_agent.perception.memory_factory import register_memory_backend

        class MyBackend(MemoryBackend):
            ...

        register_memory_backend("my_backend", lambda cfg: MyBackend(cfg))
    """
    _REGISTRY[name.lower()] = loader


def list_memory_backends() -> list[str]:
    """返回所有已注册的后端名称列表。"""
    return sorted(_REGISTRY)
