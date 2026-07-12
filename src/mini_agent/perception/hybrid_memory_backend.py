"""
perception/hybrid_memory_backend.py — 混合检索记忆后端（方案一）

包装 MemoryStore（TF-IDF + n-gram，精确匹配兜底），新增 embedding 语义召回：
  - add()/search_by_tag()/delete_by_session()/reload() 等全部委托给内部 MemoryStore，
    行为完全不变。
  - search() 改为：TF-IDF 召回全量分数 + embedding 语义召回全量分数，
    按可配置权重（tfidf_weight / embedding_weight）合并去重排序。

embedding 来源：perception/local_embedding.py::get_shared_embedding_model()
返回的本地 ONNX 模型 embed() 方法，不依赖任何云端 provider。

失败降级：embedding 调用失败/模型未加载成功时，search() 自动退化为纯
TF-IDF（与 MemoryStore.search() 结果完全一致），不阻断记忆检索。

embedding 向量的持久化：不引入外部向量数据库依赖，改为在 MemoryEntry 旁
维护一个 <path>.embeddings.jsonl 影子文件（entry_id -> 向量），首次访问时
懒加载并为缺失向量的旧条目补算。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

from mini_agent.perception.memory_base import MemoryBackend

if TYPE_CHECKING:
    from mini_agent.perception.memory_store import MemoryEntry, MemoryStore


class HybridMemoryBackend(MemoryBackend):
    def __init__(
        self,
        inner: "MemoryStore",
        embed_call: Optional[Callable[[str], list]] = None,
        tfidf_weight: float = 0.5,
        embedding_weight: float = 0.5,
        embedding_top_n: int = 20,
        paths: Optional["object"] = None,
    ) -> None:
        self._inner = inner
        self._embed_call = embed_call
        self._tfidf_weight = tfidf_weight
        self._embedding_weight = embedding_weight
        self._embedding_top_n = embedding_top_n
        self._vectors_path = Path(str(inner._path)).with_suffix(".embeddings.jsonl")
        self._vectors: dict[str, list] = {}   # entry_id -> vector，懒加载
        self._vectors_loaded = False
        # [事件总线接入] paths 用于发布 "memory.sparse_region_detected" 事件。
        # 可选参数、默认 None——不传入时 search() 完全等价于改动前的行为，
        # 老的调用方（不知道事件总线存在）不会受影响。
        self._paths = paths
        self._last_sparse_event_ts: float = 0.0

    # ── 委托：与 MemoryStore 行为完全一致 ────────────────────────────────────

    def add(self, entry: "MemoryEntry") -> None:
        self._inner.add(entry)
        self._maybe_embed_async(entry)

    def upsert(self, entry: "MemoryEntry") -> None:
        self._inner.upsert(entry)
        self._maybe_embed_async(entry)

    def search_by_tag(self, tag: str) -> list:
        return self._inner.search_by_tag(tag)

    def delete_by_session(self, session_id: str) -> None:
        self._inner.delete_by_session(session_id)
        self._ensure_vectors_loaded()
        to_remove = [eid for eid, v in self._vectors.items()]
        # 简化：按当前 inner 条目集合重新过滤影子向量文件，去掉已不存在的 entry_id
        remaining_ids = {e.entry_id for e in self._inner.all_entries()}
        changed = False
        for eid in list(self._vectors.keys()):
            if eid not in remaining_ids:
                self._vectors.pop(eid, None)
                changed = True
        if changed:
            self._rewrite_vectors_disk()

    def reload(self) -> None:
        self._inner.reload()
        self._vectors = {}
        self._vectors_loaded = False

    @property
    def count(self) -> int:
        return self._inner.count

    def all_entries(self) -> list:
        return self._inner.all_entries()

    @property
    def backend_name(self) -> str:
        return "HybridMemoryBackend"

    @property
    def library(self):
        return getattr(self._inner, "library", None)

    # ── 核心改动：混合检索 ────────────────────────────────────────────────────

    def search(self, query: str, k: int = 3) -> list:
        self._inner._ensure_loaded()
        tfidf_ranked = self._inner._score_all(query)

        if self._embed_call is None:
            result = [e for e, s in sorted(tfidf_ranked, key=lambda x: -x[1])[:k] if s > 0]
            self._maybe_report_sparse_region(query, tfidf_hits=len(result), embed_hits=0)
            return result

        query_vec = self._safe_embed(query)
        if query_vec is None:
            result = [e for e, s in sorted(tfidf_ranked, key=lambda x: -x[1])[:k] if s > 0]
            self._maybe_report_sparse_region(query, tfidf_hits=len(result), embed_hits=0)
            return result

        embed_ranked = self._embedding_score_all(query_vec)
        merged = self._merge_scores(tfidf_ranked, embed_ranked)
        result = [e for e, s in sorted(merged, key=lambda x: -x[1])[:k] if s > 0]

        tfidf_hits = sum(1 for _, s in tfidf_ranked if s > 0)
        embed_hits = sum(1 for _, s in embed_ranked if s > 0)
        self._maybe_report_sparse_region(query, tfidf_hits=tfidf_hits, embed_hits=embed_hits)
        return result

    def _maybe_report_sparse_region(self, query: str, *, tfidf_hits: int, embed_hits: int) -> None:
        """
        [事件总线接入] TF-IDF 和 embedding 两路都几乎召回不到内容时，说明
        query 所在的语义领域记忆条目稀疏——这本身就是"这个领域值得主动去
        探索补充"的信号，比等 SoftGoalDeriver 靠 capability_map 的 total_calls
        统计间接推断"没试过"更直接：这里能捕捉到"用户/agent 实际问到了、
        但记忆里没有"的真实空白，而不只是"从没问过"。

        tier="tick"：不需要像 frustration 那样 0.5s 内响应，SoftGoalDeriver
        本来就是按自己的节拍（tick）跑 derive_candidates()，稀疏信号能在
        下一次 tick 被看到即可。

        限流而非严格边沿检测：与 frustration 有明确阈值不同，"query 是否
        稀疏"每次查询都可能不同，没有一个稳定的"边沿"概念可判断。这里退而
        求其次，用一个进程内最短发布间隔（默认 60s）防止高频检索把事件日志
        刷屏，代价是可能漏报一些密集出现在同一分钟内的稀疏 query——可接受，
        因为 SoftGoalDeriver 消费时本来就是取"最近一段时间内出现过的稀疏
        领域"做累积判断，不依赖每一次稀疏都被单独记录。
        """
        if self._paths is None:
            return  # 未传入 paths（老调用方/测试）：完全不发布，行为与改动前一致
        if tfidf_hits > 0 or embed_hits > 0:
            return  # 有召回结果，不算稀疏
        if not query or not query.strip():
            return

        import time as _time
        now = _time.time()
        _MIN_INTERVAL_SECONDS = 60.0
        if now - self._last_sparse_event_ts < _MIN_INTERVAL_SECONDS:
            return
        self._last_sparse_event_ts = now

        try:
            from mini_agent.perception.memory_store import _tokenize
            from mini_agent.perception import system_events as _se

            tokens = _tokenize(query)[:8]  # 只取前几个 token，事件 payload 不需要完整原文
            if not tokens:
                return
            _se.publish(
                self._paths,
                source="hybrid_memory_backend",
                event_type="memory.sparse_region_detected",
                tier="tick",
                payload={"query_tokens": tokens},
            )
        except Exception:
            pass  # 事件发布是旁路增强，不能影响记忆检索本身

    def _safe_embed(self, text: str) -> Optional[list]:
        try:
            return self._embed_call(text)
        except Exception:
            return None

    def _embedding_score_all(self, query_vec: list) -> list:
        from mini_agent.perception.local_embedding import cosine_similarity

        self._ensure_vectors_loaded()
        results = []
        for entry in self._inner.all_entries():
            vec = self._vectors.get(entry.entry_id)
            if vec is None:
                vec = self._safe_embed(entry.to_search_text())
                if vec is not None:
                    self._vectors[entry.entry_id] = vec
                    self._append_vector_to_disk(entry.entry_id, vec)
            score = cosine_similarity(query_vec, vec) if vec is not None else 0.0
            results.append((entry, score))
        return results

    def _merge_scores(self, tfidf_ranked: list, embed_ranked: list) -> list:
        """两路分数各自做 min-max 归一化后按权重相加。"""
        def _normalize(pairs: list) -> dict:
            if not pairs:
                return {}
            scores = [s for _, s in pairs]
            lo, hi = min(scores), max(scores)
            if hi - lo <= 1e-12:
                # 所有分数相同：非零则视为满分，全零则保持为 0（避免除以0导致误判为"无关"）。
                filler = 1.0 if hi > 0 else 0.0
                return {e.entry_id: filler for e, _ in pairs}
            span = hi - lo
            return {e.entry_id: (s - lo) / span for e, s in pairs}

        tfidf_norm = _normalize(tfidf_ranked)
        embed_norm = _normalize(embed_ranked)
        by_id = {e.entry_id: e for e, _ in tfidf_ranked}
        by_id.update({e.entry_id: e for e, _ in embed_ranked})

        merged = []
        for eid, entry in by_id.items():
            score = (
                self._tfidf_weight * tfidf_norm.get(eid, 0.0)
                + self._embedding_weight * embed_norm.get(eid, 0.0)
            )
            merged.append((entry, score))
        return merged

    # ── 影子向量文件持久化 ────────────────────────────────────────────────────

    def _ensure_vectors_loaded(self) -> None:
        if self._vectors_loaded:
            return
        self._vectors_loaded = True
        if not self._vectors_path.exists():
            return
        try:
            for line in self._vectors_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                self._vectors[data["entry_id"]] = data["vector"]
        except Exception:
            pass

    def _append_vector_to_disk(self, entry_id: str, vector: list) -> None:
        try:
            self._vectors_path.parent.mkdir(parents=True, exist_ok=True)
            with self._vectors_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"entry_id": entry_id, "vector": vector}) + "\n")
        except Exception:
            pass

    def _rewrite_vectors_disk(self) -> None:
        try:
            self._vectors_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._vectors_path.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for eid, vec in self._vectors.items():
                    f.write(json.dumps({"entry_id": eid, "vector": vec}) + "\n")
            tmp.replace(self._vectors_path)
        except Exception:
            pass

    def _maybe_embed_async(self, entry: "MemoryEntry") -> None:
        """向量计算失败不影响 add() 成功，同步执行但异常被吞掉
        （"异步"指不阻断写入路径的正确性，不是真正的后台线程——保持实现简单，
        向量计算本身是几十毫秒级的本地推理，不值得引入线程池复杂度）。"""
        if self._embed_call is None:
            return
        try:
            vec = self._embed_call(entry.to_search_text())
            if vec is not None:
                self._ensure_vectors_loaded()
                self._vectors[entry.entry_id] = vec
                self._append_vector_to_disk(entry.entry_id, vec)
        except Exception:
            pass


__all__ = ["HybridMemoryBackend"]
