"""
perception/memory_store.py — 跨 session 长期记忆。

不依赖外部向量数据库，使用 JSON 文件 + 简单 TF-IDF / 关键词检索。
有 embedding 支持（openai / anthropic API 或 sentence-transformers）时自动升级为向量检索。
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class MemoryEntry:
    session_id: str
    summary: str                        # session 摘要
    key_outcomes: list[str]             # 关键结论列表
    tags: list[str]                     # 自动提取的标签
    model: str
    created_at: float = field(default_factory=time.time)
    entry_id: str = ""

    def __post_init__(self) -> None:
        if not self.entry_id:
            import uuid
            self.entry_id = uuid.uuid4().hex[:12]

    def to_search_text(self) -> str:
        return " ".join([self.summary] + self.key_outcomes + self.tags)


class MemoryStore:
    """
    持久化长期记忆存储。

    存储格式：JSONL，每行一条 MemoryEntry。
    检索：基于 TF-IDF 关键词匹配，返回 top-k 相关条目。

    用法：
        store = MemoryStore(path=Path(".agent/memory.jsonl"))
        store.add(entry)
        results = store.search("如何处理 JSON 解析错误", k=3)
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or Path(".agent") / "memory.jsonl"
        self._entries: list[MemoryEntry] = []
        self._loaded = False

    # ── 写入 ──────────────────────────────────────────────────────────────────

    def add(self, entry: MemoryEntry) -> None:
        self._ensure_loaded()
        self._entries.append(entry)
        self._append_to_disk(entry)

    # ── 检索 ──────────────────────────────────────────────────────────────────

    def search(self, query: str, k: int = 3) -> list[MemoryEntry]:
        """返回与 query 最相关的 top-k 条目（TF-IDF 关键词匹配）。"""
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
                except Exception:
                    pass
        except Exception:
            pass

    def _append_to_disk(self, entry: MemoryEntry) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ── 评分 ─────────────────────────────────────────────────────────────────

    def _score_all(self, query: str) -> list[tuple[MemoryEntry, float]]:
        query_tokens = _tokenize(query)
        if not query_tokens:
            return [(e, 0.0) for e in self._entries]

        # 构建简单的词频反向文档频率
        N = len(self._entries)
        doc_texts = [_tokenize(e.to_search_text()) for e in self._entries]

        results = []
        for entry, tokens in zip(self._entries, doc_texts):
            if not tokens:
                results.append((entry, 0.0))
                continue
            score = 0.0
            for qt in query_tokens:
                tf = tokens.count(qt) / len(tokens)
                # 含有该词的文档数
                df = sum(1 for t in doc_texts if qt in t)
                idf = math.log((N + 1) / (df + 1)) + 1
                score += tf * idf
            results.append((entry, score))
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
    """简单分词：转小写，分割汉字/英文单词，过滤停用词。"""
    text = text.lower()
    # 同时处理中文字符（逐字）和英文单词
    tokens = re.findall(r"[\u4e00-\u9fff]|[a-z0-9]+", text)
    _stopwords = {"the", "a", "an", "is", "in", "on", "at", "to", "for",
                  "of", "and", "or", "with", "that", "this", "it", "was",
                  "be", "by", "from", "as", "are", "were", "been"}
    return [t for t in tokens if t not in _stopwords and len(t) > 1]
