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

def _load_local(cfg: "AppConfig", scope: str = "project", user_id: Optional[str] = None) -> MemoryBackend:
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
            library_index = _build_library_index(paths, scope, cfg=cfg, user_id=user_id)
        except Exception:
            library_index = None  # 索引组件失败不应阻断记忆系统本身可用

    return MemoryStore(
        path=store_path,
        max_entries=cfg.memory.max_entries,
        decay_half_life_days=cfg.memory.decay_half_life_days,
        library_index=library_index,
        consolidation_enabled=getattr(cfg.memory, "consolidation_enabled", True),
        consolidation_min_group_size=getattr(cfg.memory, "consolidation_min_group_size", 3),
    )


def _load_hybrid(cfg: "AppConfig", scope: str = "project", user_id: Optional[str] = None) -> MemoryBackend:
    """
    方案一：混合 TF-IDF + 本地离线 embedding 检索后端。

    复用 _load_local() 构造内部 MemoryStore，行为完全不变；
    embedding_enabled=False 时直接返回内部 MemoryStore（等价于 backend="local"）。
    embedding 相关 import 全部延迟到这里，未开启开关时不会引入
    onnxruntime/tokenizers 依赖。
    """
    store = _load_local(cfg, scope=scope, user_id=user_id)

    if not getattr(cfg.memory, "embedding_enabled", False):
        return store   # [默认路径] 未开启 embedding，直接返回原有 MemoryStore，零改动

    try:
        from mini_agent.perception.hybrid_memory_backend import HybridMemoryBackend
        from mini_agent.perception.local_embedding import get_shared_embedding_model
        from mini_agent.storage.paths import AgentPaths

        embed_model = get_shared_embedding_model(
            cfg.memory.embedding_model, cfg.memory.embedding_model_cache_dir
        )
        # 让底层 MemoryStore 的 [方案二] 归纳逻辑也能用上语义相似度
        # （consolidate_before_eviction 内部会用 store._embed_call）。
        if hasattr(store, "_embed_call"):
            store._embed_call = embed_model.embed
        return HybridMemoryBackend(
            inner=store,
            embed_call=embed_model.embed,
            tfidf_weight=cfg.memory.embedding_tfidf_weight,
            embedding_weight=cfg.memory.embedding_weight,
            embedding_top_n=cfg.memory.embedding_top_n,
            paths=AgentPaths(cfg.project_root),
        )
    except Exception:
        # 模型下载失败/onnxruntime 未安装（用户开了开关但没装 extras）/加载出错：
        # 静默降级为纯 MemoryStore，不阻断 agent 启动，只在 debug 日志里记录原因
        import logging
        logging.getLogger(__name__).warning(
            "[embedding] 加载本地 embedding 模型失败，已降级为纯 TF-IDF 检索。"
            "如果你已开启 embedding_enabled，请确认已安装 `pip install mini-agent[embedding]`。"
        )
        return store


def _build_library_index(paths, scope: str, cfg: "AppConfig" = None, user_id: Optional[str] = None):
    """
    构建图书馆式索引（分类树/实体目录/分类目录/知识编年目录）。
    global scope 复用 global_dir 下的同名文件，与 project scope 完全隔离
    （跨项目的分类体系不应该混在一起——global 记忆本身也是"跨项目通用经验"，
    有自己独立的一套书架）。

    改进7（多用户软隔离）：当 cfg.memory.library_index_user_scoped 打开且
    传入了 user_id 时，各用户拥有独立的一套书架文件（按 user_id 加后缀），
    避免"同一项目下不同用户的使用习惯差异很大"时互相稀释关键词权重。
    默认关闭——大多数场景下同项目内不同人的经验值得共享归并，只有明确需要
    按用户区分书架时才开启。
    """
    from mini_agent.perception.library_index import LibraryIndex

    base = paths.global_dir if scope == "global" else paths.workdir_dir

    user_scoped = bool(cfg and getattr(cfg.memory, "library_index_user_scoped", False) and user_id)
    suffix = f".{user_id}" if user_scoped else ""

    return LibraryIndex(
        classification_tree_path=base / f"classification_tree{suffix}.json",
        unclassified_candidates_path=base / f"unclassified_candidates{suffix}.jsonl",
        entity_index_path=base / f"entities{suffix}.json",
        category_catalog_path=base / f"category_catalog{suffix}.json",
        knowledge_timeline_path=base / f"knowledge_timeline{suffix}.jsonl",
        knowledge_timeline_index_path=base / f"knowledge_timeline_index{suffix}.json",
    )


_REGISTRY: dict[str, Callable[["AppConfig", str], MemoryBackend]] = {
    "local": _load_local,
    "hybrid": _load_hybrid,   # 方案一：混合 TF-IDF + 本地离线 embedding
    # 未来扩展点：
    # "chroma": _load_chroma,
    # "redis":  _load_redis,
    # "sqlite": _load_sqlite,
}


# ── 公共 API ──────────────────────────────────────────────────────────────────

def create_memory_backend(cfg: "AppConfig", user_id: Optional[str] = None) -> MemoryBackend:
    """
    创建项目级记忆 backend。
    路径：<project_root>/.agent/memory.jsonl
    user_id: 改进7，多用户软隔离场景下传入，需配合
             cfg.memory.library_index_user_scoped=True 才会真正按用户分书架。
    """
    return _create(cfg, scope="project", user_id=user_id)


def create_global_memory_backend(cfg: "AppConfig", user_id: Optional[str] = None) -> MemoryBackend:
    """
    创建全局级记忆 backend。
    路径：~/.agent/memory.jsonl
    用于存储跨项目通用经验。
    """
    return _create(cfg, scope="global", user_id=user_id)


def create_both_memory_backends(
    cfg: "AppConfig", user_id: Optional[str] = None,
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
    project = create_memory_backend(cfg, user_id=user_id)
    global_ = create_global_memory_backend(cfg, user_id=user_id) if getattr(cfg.memory, "global_enabled", True) else None
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


def _create(cfg: "AppConfig", scope: str, user_id: Optional[str] = None) -> MemoryBackend:
    backend_key = cfg.memory.backend.lower().strip()
    loader = _REGISTRY.get(backend_key)
    if loader is None:
        available = sorted(_REGISTRY)
        raise ValueError(
            f"Unknown memory backend: {backend_key!r}.\n"
            f"Available: {available}\n"
            f"Register a custom backend via register_memory_backend()."
        )
    # 改进7：user_id 是新增的可选参数，第三方通过 register_memory_backend()
    # 注册的自定义 loader 签名仍是 (cfg, scope)，用 inspect 探测一下避免报错。
    import inspect as _inspect
    try:
        params = _inspect.signature(loader).parameters
    except (TypeError, ValueError):
        params = {}
    if "user_id" in params:
        return loader(cfg, scope, user_id=user_id)
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
