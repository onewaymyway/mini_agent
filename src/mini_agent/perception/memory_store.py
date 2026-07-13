"""
perception/memory_store.py — 跨 session 长期记忆。

不依赖外部向量数据库，使用 JSON 文件 + TF-IDF / 关键词检索。

修复（v2）：
  1. 中文分词粒度：改用双字/三字 n-gram 替代逐字切分，
     提升"数据库连接"等复合词的 TF-IDF 召回率。
  2. 时间衰减：搜索评分加入指数衰减因子 exp(-λ * days_ago)，
     防止旧记忆持续干扰当前上下文检索。
  3. 条目上限：超过 max_entries 时自动淘汰最旧条目，
     避免记忆文件无界增长导致检索质量下降。
  4. 持久化改写：淘汰后重写整个文件（而非只追加），保持磁盘与内存一致。

具身改进（v3 C2，时间加权记忆激活）：
  lesson 型条目的时间衰减不再使用全局统一半衰期，而是按 source +
  occurrence_count 计算专属半衰期（见 evolution/memory_aging.py），
  被人类亲自纠正、被反复印证的经验衰减更慢，一次性的回退记录衰减更快。
  非 lesson 条目（summary 等）行为不变，仍使用构造时传入的全局半衰期。
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field, asdict
from mini_agent.perception.memory_base import MemoryBackend
from pathlib import Path
from typing import Optional


# 时间衰减半衰期（天）。30天后分数衰减到原来的50%。
_DECAY_HALF_LIFE_DAYS = 30.0

# 默认最大记忆条目数
_DEFAULT_MAX_ENTRIES = 500


@dataclass
class MemoryEntry:
    session_id: str
    summary: str                        # session 摘要
    key_outcomes: list[str]             # 关键结论列表
    tags: list[str]                     # 自动提取的标签
    model: str
    created_at: float = field(default_factory=time.time)
    entry_id: str = ""
    scope: str = "project"              # "project" | "global"
                                        # project: 当前项目特有知识
                                        # global:  跨项目通用经验，写入 ~/.agent/memory.jsonl

    # ── Lesson Memory 扩展字段（Stage 1，对应设计文档第 3 节 / 6.2 节）─────────
    # 全部带默认值，保证现有 summary 型条目零迁移成本继续工作。
    entry_type: str = "summary"         # "summary" | "lesson" | "capability_map"
    trigger: str = ""                   # 触发场景描述（lesson 专属）
    outcome: str = ""                   # 实际发生了什么（lesson 专属）
    root_cause: str = ""                # 根因，如有（lesson 专属）
    suggested_action: str = ""          # 下次该怎么做（lesson 专属）
    confidence: float = 0.5             # 0-1，可信度（lesson 专属）
    occurrence_count: int = 1           # 同类 lesson 重复出现次数（lesson 专属）
    source: str = "self_reflection"     # "self_reflection" | "human_feedback" | "revert_record"

    # ── 图书馆式索引扩展字段（perception/library_index.py）─────────────────
    # 全部带默认值，保证现有条目零迁移成本继续工作；由 LibraryIndex.on_new_entry()
    # 在 add() 时填充，不需要调用方手动设置。
    category: str = ""                  # 分类树节点号，如 "000.003"（未归类为 "000"）
    entity_ids: list[str] = field(default_factory=list)  # 关联的实体 ID（entity_index.py）

    def __post_init__(self) -> None:
        if not self.entry_id:
            import uuid
            self.entry_id = uuid.uuid4().hex[:12]

    def to_search_text(self) -> str:
        """检索文本拼接：summary 型条目走旧逻辑，lesson 型条目额外纳入
        trigger/outcome/root_cause/suggested_action，否则这些信息无法被检索到。"""
        parts = [self.summary] + self.key_outcomes + self.tags
        if self.entry_type == "lesson":
            parts.extend([self.trigger, self.outcome, self.root_cause, self.suggested_action])
        return " ".join(p for p in parts if p)

    @property
    def age_days(self) -> float:
        return (time.time() - self.created_at) / 86400.0


class MemoryStore(MemoryBackend):
    """
    持久化长期记忆存储（MemoryBackend 的本地 JSONL 实现）。

    存储格式：JSONL，每行一条 MemoryEntry。
    检索：TF-IDF + 中文 n-gram 分词 + 时间衰减，返回 top-k 相关条目。

    用法：
        store = MemoryStore(path=Path(".agent/memory.jsonl"))
        store.add(entry)
        results = store.search("如何处理 JSON 解析错误", k=3)
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        decay_half_life_days: float = _DECAY_HALF_LIFE_DAYS,
        library_index=None,          # Optional["LibraryIndex"]，由 memory_factory 注入
        llm_classify_call=None,      # Optional[Callable[[str], str]]，规则未命中时的兜底分类调用
        consolidation_enabled: bool = True,   # [方案二] 淘汰前是否尝试归纳而非直接丢弃
        consolidation_min_group_size: int = 3,
        embed_call=None,             # Optional[Callable[[str], list[float]]]，供归纳时辅助聚类（可选）
    ) -> None:
        self._path = path or Path(".agent") / "memory.jsonl"  # 由 memory_factory 覆盖
        self._entries: list[MemoryEntry] = []
        self._loaded = False
        self._max_entries = max_entries
        # 衰减系数 λ = ln(2) / half_life
        self._decay_lambda = math.log(2) / max(decay_half_life_days, 0.1)
        # 图书馆式索引（分类树/实体目录/分类目录）：为 None 时 add()/search() 行为
        # 与改造前完全一致，保证该功能是纯增量、可关闭的。
        self._library = library_index
        self._llm_classify_call = llm_classify_call
        # ── [方案二] 记忆巩固 ──────────────────────────────────────────────
        self._consolidation_enabled = consolidation_enabled
        self._consolidation_min_group_size = consolidation_min_group_size
        self._embed_call = embed_call

    # ── 写入 ──────────────────────────────────────────────────────────────────

    def add(self, entry: MemoryEntry) -> None:
        self._ensure_loaded()
        if self._library is not None and not entry.category:
            try:
                self._library.on_new_entry(entry, llm_call=self._llm_classify_call)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.perception.memory_store.library')
        self._entries.append(entry)
        # 超出上限：淘汰最旧条目，并重写全文件
        if len(self._entries) > self._max_entries:
            # 按创建时间排序，保留最新的 max_entries 条
            self._entries.sort(key=lambda e: e.created_at)
            evict_count = len(self._entries) - self._max_entries
            candidates = self._entries[:evict_count]
            keep = self._entries[evict_count:]

            # [方案二/记忆巩固] 淘汰前尝试归纳而非直接丢弃，
            # 见 evolution/memory_consolidation.py。失败静默降级为原有行为。
            if self._consolidation_enabled:
                try:
                    from mini_agent.evolution.memory_consolidation import consolidate_before_eviction
                    consolidated, _truly_evicted = consolidate_before_eviction(
                        candidates,
                        embed_call=self._embed_call,
                        llm_call=self._llm_classify_call,
                        min_group_size=self._consolidation_min_group_size,
                    )
                    self._entries = consolidated + keep
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.perception.memory_store.consolidation')
                    self._entries = keep
            else:
                self._entries = keep

            self._rewrite_disk()
        else:
            self._append_to_disk(entry)

    def delete_by_session(self, session_id: str) -> None:
        self._ensure_loaded()
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.session_id != session_id]
        if len(self._entries) != before:
            self._rewrite_disk()

    def reload(self) -> None:
        """丢弃当前内存缓存，下次访问时重新从磁盘完整加载（见 MemoryBackend.reload 文档）。"""
        self._entries = []
        self._loaded = False

    # ── 检索 ──────────────────────────────────────────────────────────────────

    def search(self, query: str, k: int = 3) -> list[MemoryEntry]:
        """返回与 query 最相关的 top-k 条目（TF-IDF + 时间衰减）。"""
        self._ensure_loaded()
        if not self._entries:
            return []
        scores = self._score_all(query)
        ranked = sorted(scores, key=lambda x: -x[1])
        return [e for e, s in ranked[:k] if s > 0]

    def search_by_tag(self, tag: str) -> list[MemoryEntry]:
        self._ensure_loaded()
        tag = tag.lower()
        return [e for e in self._entries if tag in [t.lower() for t in e.tags]]

    def rank_subset(self, query: str, subset: list[MemoryEntry], k: int = 3) -> list[MemoryEntry]:
        """
        在给定的记忆子集（通常是 LibraryIndex.shelf_search 圈定的"书架"）内
        做与 search() 相同的 TF-IDF + 时间衰减打分排序，返回 top-k。
        与 search() 的区别只是候选集合从"全部条目"换成调用方传入的子集，
        IDF 统计也只在子集范围内计算（书架内的相关性排序，不受书架外文档的
        IDF 干扰，符合"先定位书架、再在架内精细检索"的语义）。
        """
        if not subset:
            return []
        query_tokens = _tokenize(query)
        if not query_tokens:
            return subset[:k]
        N = len(subset)
        doc_texts = [_tokenize(e.to_search_text()) for e in subset]
        results = []
        for entry, tokens in zip(subset, doc_texts):
            if not tokens:
                results.append((entry, 0.0))
                continue
            score = 0.0
            for qt in query_tokens:
                tf = tokens.count(qt) / len(tokens)
                df = sum(1 for t in doc_texts if qt in t)
                idf = math.log((N + 1) / (df + 1)) + 1
                score += tf * idf
            if getattr(entry, "entry_type", "summary") in ("lesson", "consolidated_lesson"):
                try:
                    from mini_agent.evolution.memory_aging import compute_decay_factor
                    decay = compute_decay_factor(entry)
                except Exception:
                    decay = math.exp(-self._decay_lambda * entry.age_days)
            else:
                decay = math.exp(-self._decay_lambda * entry.age_days)
            results.append((entry, score * decay))
        ranked = sorted(results, key=lambda x: -x[1])
        return [e for e, s in ranked[:k] if s > 0]

    def rewrite_categories(self, updates: dict[str, str]) -> None:
        """
        巩固循环 巩固时批量更新一批条目的 category 字段（分类树新增节点后，
        原本挂在 "000" 未分类下的记忆需要重新归位），随后整体重写磁盘文件。
        updates: {entry_id: new_category}
        """
        self._ensure_loaded()
        changed = False
        for entry in self._entries:
            new_category = updates.get(entry.entry_id)
            if new_category is not None and entry.category != new_category:
                entry.category = new_category
                changed = True
        if changed:
            self._rewrite_disk()

    @property
    def library(self):
        """暴露底层 LibraryIndex（可能为 None），供 巩固循环 巩固流程调用。"""
        return self._library

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            return
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    self._entries.append(MemoryEntry(**data))
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.perception.memory_store')
                    pass
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.memory_store')
            pass

    def _append_to_disk(self, entry: MemoryEntry) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.memory_store')
            pass

    def _rewrite_disk(self) -> None:
        """淘汰后重写整个文件，保持磁盘与内存一致。使用原子写入（tmp + rename）。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for entry in self._entries:
                    f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
            tmp.replace(self._path)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.memory_store')
            pass

    # ── 评分 ─────────────────────────────────────────────────────────────────

    def _score_all(self, query: str) -> list[tuple[MemoryEntry, float]]:
        query_tokens = _tokenize(query)
        if not query_tokens:
            return [(e, 0.0) for e in self._entries]

        N = len(self._entries)
        doc_texts = [_tokenize(e.to_search_text()) for e in self._entries]

        results = []
        for entry, tokens in zip(self._entries, doc_texts):
            if not tokens:
                results.append((entry, 0.0))
                continue
            # TF-IDF 分数
            score = 0.0
            for qt in query_tokens:
                tf = tokens.count(qt) / len(tokens)
                df = sum(1 for t in doc_texts if qt in t)
                idf = math.log((N + 1) / (df + 1)) + 1
                score += tf * idf
            # [具身改进 C2 / 时间加权记忆激活] lesson 型条目按 source +
            # occurrence_count 计算专属半衰期（human_feedback 衰减最慢、
            # revert_record 最快，被反复印证的经验衰减更慢）；非 lesson 条目
            # （summary 等）沿用构造时传入的全局半衰期配置，保持向后兼容。
            if getattr(entry, "entry_type", "summary") in ("lesson", "consolidated_lesson"):
                try:
                    from mini_agent.evolution.memory_aging import compute_decay_factor
                    decay = compute_decay_factor(entry)
                except Exception:
                    decay = math.exp(-self._decay_lambda * entry.age_days)
            else:
                decay = math.exp(-self._decay_lambda * entry.age_days)
            results.append((entry, score * decay))
        return results

    # ── 统计 ─────────────────────────────────────────────────────────────────

    @property
    def count(self) -> int:
        self._ensure_loaded()
        return len(self._entries)

    def all_entries(self) -> list[MemoryEntry]:
        self._ensure_loaded()
        return list(self._entries)


def _tokenize(text: str) -> list[str]:
    """
    分词：英文按单词切分，中文使用双字+三字 n-gram。

    改进原因：逐字切分对中文效果差，"数据库"切成"数""据""库"后
    IDF 权重极低（高频单字），TF-IDF 检索近乎失效。
    n-gram 保留了词语边界语义，"数据库"→["数据","据库","数据库"]，
    检索时能正确匹配复合词。
    """
    text = text.lower()

    _stopwords = {
        "the", "a", "an", "is", "in", "on", "at", "to", "for",
        "of", "and", "or", "with", "that", "this", "it", "was",
        "be", "by", "from", "as", "are", "were", "been",
    }

    tokens: list[str] = []

    # 英文单词（保留长度>1的非停用词）
    for word in re.findall(r"[a-z0-9]+", text):
        if word not in _stopwords and len(word) > 1:
            tokens.append(word)

    # 中文：提取连续汉字段，生成双字和三字 n-gram
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        # 双字 n-gram
        for i in range(len(seg) - 1):
            tokens.append(seg[i:i+2])
        # 三字 n-gram（长度≥3时）
        for i in range(len(seg) - 2):
            tokens.append(seg[i:i+3])
        # 单字兜底（仅对长度为1的孤立汉字段）
        if len(seg) == 1:
            tokens.append(seg)

    return tokens
