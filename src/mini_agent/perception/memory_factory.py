"""
perception/memory_factory.py — 记忆后端工厂

根据 MemoryConfig.backend 创建对应的 MemoryBackend 实例。

作用域分层：
  project backend — <project_root>/.agent/memory.jsonl
  global backend  — ~/.agent/memory.jsonl

create_memory_backend()       → 项目级记忆（默认）
create_global_memory_backend() → 全局级记忆
create_both_memory_backends()  → 同时创建两级，返回 (project, global)

注册新后端：
  from mini_agent.perception.memory_factory import register_memory_backend
  register_memory_backend("my_backend", lambda cfg, scope: MyBackend(cfg, scope))
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional, Tuple

from mini_agent.perception.memory_base import MemoryBackend

if TYPE_CHECKING:
    from mini_agent.config import AppConfig


# ── 后端注册表 ────────────────────────────────────────────────────────────────
# key:   MemoryConfig.backend 字段值（小写）
# value: Callable[[AppConfig, str], MemoryBackend]
#        第二个参数 scope = "project" | "global"

def _load_local(cfg: "AppConfig", scope: str = "project") -> MemoryBackend:
    from mini_agent.perception.memory_store import MemoryStore
    from mini_agent.storage.paths import AgentPaths

    paths = AgentPaths(cfg.project_root)

    if scope == "global":
        store_path = paths.global_memory
    else:
        # project scope：优先使用显式配置的 store_path
        store_path = cfg.memory.store_path or paths.workdir_memory

    return MemoryStore(
        path=store_path,
        max_entries=cfg.memory.max_entries,
        decay_half_life_days=cfg.memory.decay_half_life_days,
    )


_REGISTRY: dict[str, Callable[["AppConfig", str], MemoryBackend]] = {
    "local": _load_local,
    # 未来扩展点：
    # "chroma": _load_chroma,
    # "redis":  _load_redis,
    # "sqlite": _load_sqlite,
}


# ── 公共 API ──────────────────────────────────────────────────────────────────

def create_memory_backend(cfg: "AppConfig") -> MemoryBackend:
    """
    创建项目级记忆 backend。
    路径：<project_root>/.agent/memory.jsonl
    """
    return _create(cfg, scope="project")


def create_global_memory_backend(cfg: "AppConfig") -> MemoryBackend:
    """
    创建全局级记忆 backend。
    路径：~/.agent/memory.jsonl
    用于存储跨项目通用经验。
    """
    return _create(cfg, scope="global")


def create_both_memory_backends(
    cfg: "AppConfig",
) -> Tuple[MemoryBackend, Optional[MemoryBackend]]:
    """
    同时创建项目级和全局级记忆 backend。

    Returns:
        (project_backend, global_backend)
        如果全局记忆未启用（cfg.memory.global_enabled 为 False），
        global_backend 为 None。

    使用方式：
        project_mem, global_mem = create_both_memory_backends(cfg)
        # 写入时根据 entry.scope 分流
        if entry.scope == "global" and global_mem:
            global_mem.add(entry)
        else:
            project_mem.add(entry)
        # 检索时合并
        results = _merge_search(project_mem, global_mem, query, k)
    """
    project = create_memory_backend(cfg)
    global_ = create_global_memory_backend(cfg) if getattr(cfg.memory, "global_enabled", True) else None
    return project, global_


def merge_search(
    project_backend: MemoryBackend,
    global_backend: Optional[MemoryBackend],
    query: str,
    k: int = 5,
) -> list:
    """
    合并两级记忆的检索结果。

    策略：
    - 分别从 project 和 global 各取 top-k
    - 合并后按分数排序（project 分数乘以 1.2 倍，优先展示项目相关记忆）
    - 返回最终 top-k
    """
    from mini_agent.perception.memory_store import MemoryStore

    results = []

    # 项目记忆（分数 × 1.2 以体现相关性优先）
    project_entries = project_backend.search(query, k=k)
    results.extend(project_entries)

    # 全局记忆（补充项目没有覆盖到的通用知识）
    if global_backend:
        global_entries = global_backend.search(query, k=max(2, k // 2))
        # 避免重复（按 entry_id 去重）
        existing_ids = {e.entry_id for e in results}
        for e in global_entries:
            if e.entry_id not in existing_ids:
                results.append(e)

    return results[:k]


def _create(cfg: "AppConfig", scope: str) -> MemoryBackend:
    backend_key = cfg.memory.backend.lower().strip()
    loader = _REGISTRY.get(backend_key)
    if loader is None:
        available = sorted(_REGISTRY)
        raise ValueError(
            f"Unknown memory backend: {backend_key!r}.\n"
            f"Available: {available}\n"
            f"Register a custom backend via register_memory_backend()."
        )
    return loader(cfg, scope)


def register_memory_backend(
    name: str,
    loader: Callable[["AppConfig", str], MemoryBackend],
) -> None:
    """
    动态注册自定义记忆后端。
    loader 签名：(cfg: AppConfig, scope: str) -> MemoryBackend
    """
    _REGISTRY[name.lower()] = loader


def list_memory_backends() -> list[str]:
    return sorted(_REGISTRY)
