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

    library_index = None
    if getattr(cfg.memory, "library_index_enabled", True):
        try:
            library_index = _build_library_index(paths, scope)
        except Exception:
            library_index = None  # 索引组件失败不应阻断记忆系统本身可用

    return MemoryStore(
        path=store_path,
        max_entries=cfg.memory.max_entries,
        decay_half_life_days=cfg.memory.decay_half_life_days,
        library_index=library_index,
    )


def _build_library_index(paths, scope: str):
    """
    构建图书馆式索引（分类树/实体目录/分类目录/知识编年目录）。
    global scope 复用 global_dir 下的同名文件，与 project scope 完全隔离
    （跨项目的分类体系不应该混在一起——global 记忆本身也是"跨项目通用经验"，
    有自己独立的一套书架）。
    """
    from mini_agent.perception.library_index import LibraryIndex

    if scope == "global":
        base = paths.global_dir
    else:
        base = paths.workdir_dir

    return LibraryIndex(
        classification_tree_path=base / "classification_tree.json",
        unclassified_candidates_path=base / "unclassified_candidates.jsonl",
        entity_index_path=base / "entities.json",
        category_catalog_path=base / "category_catalog.json",
        knowledge_timeline_path=base / "knowledge_timeline.jsonl",
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


def set_llm_classify_call(backend: MemoryBackend, llm_call: Optional[Callable[[str], str]]) -> None:
    """
    把一个轻量分类调用（prompt: str -> str）接到已创建好的 backend 上，
    用于分类规则未命中时的 LLM 兜底、以及 Phase G 巩固时的实体摘要重写。

    设计成事后 attach 而不是构造参数，是为了不改变 _REGISTRY 里
    loader 的 (cfg, scope) -> MemoryBackend 签名（自定义 backend 也不用改）。
    只对本地 MemoryStore 生效；其它 backend 类型静默忽略。
    """
    if hasattr(backend, "_llm_classify_call"):
        backend._llm_classify_call = llm_call


def build_llm_call(client) -> Callable[[str], str]:
    """
    把 Agent 当前正在用的 LLMClient 包装成 (prompt: str) -> str 的轻量调用，
    供 ClassificationTree.classify_by_llm 的兜底分类、EntityStore.rewrite_summary
    的摘要重写复用——不需要单独接一个新的 LLM provider 或新开一次会话，
    直接复用 Agent 已经建立好的 client_pool.current_client。

    这类调用是单轮、无工具、无历史的最简 chat()：
        client.chat(messages=[{"role": "user", "content": prompt}], system="", tools=[])
    如果调用失败（网络错误/超限/provider 异常），返回空字符串而不是抛异常——
    调用方（classification.py / entity_index.py）本身已经把"LLM 不可用"当作
    一等公民处理（规则兜底 / 朴素拼接摘要），失败了直接退化，不影响主流程。
    """

    def _call(prompt: str) -> str:
        try:
            response = client.chat(
                messages=[{"role": "user", "content": prompt}],
                system="",
                tools=[],
            )
            return (response.text or "").strip()
        except Exception:
            return ""

    return _call


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
