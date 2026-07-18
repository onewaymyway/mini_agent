"""
tests/test_context_builder_wiki_search_primary.py — wiki 式知识库改进计划 P4

覆盖"实际切换"本身：ContextBuilder.refresh_turn_context() 默认优先尝试
wiki_search，命中 grounded 结果时采用其输出并跳过 shelf_search；未命中/
未配置 llm_call/wiki_search 异常/配置关闭时，行为与切换前完全一致地退回
shelf_search → merge_search → 全库 search 链路。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from mini_agent.config import AppConfig
from mini_agent.context_builder import ContextBuilder


class _FakeWikiPage:
    def __init__(self, id, body, source_entries=None):
        self.id = id
        self.body = body
        self.source_entries = source_entries or []


class _FakeWikiSearchResult:
    def __init__(self, pages=None, answer="", grounded_page_ids=None, stage_reached="none"):
        self.pages = pages or []
        self.answer = answer
        self.grounded_page_ids = grounded_page_ids or []
        self.stage_reached = stage_reached


@dataclass
class _FakeMemoryEntry:
    entry_id: str
    session_id: str = "sess01"
    summary: str = "some summary"


class _FakeLibrary:
    """伪造 LibraryIndex，只实现测试需要的 wiki_search / shelf_search。"""

    def __init__(self, wiki_result=None, wiki_exc=None, shelf_results=None, wiki_paths=None):
        self._wiki_result = wiki_result
        self._wiki_exc = wiki_exc
        self._shelf_results = shelf_results if shelf_results is not None else []
        self._wiki_paths = wiki_paths
        self.wiki_search_calls = []
        self.shelf_search_calls = 0

    def wiki_search(self, query, llm_call=None, tags=None, k=5, confidence_weight=None, use_index=True):
        self.wiki_search_calls.append(llm_call)
        if self._wiki_exc is not None:
            raise self._wiki_exc
        return self._wiki_result or _FakeWikiSearchResult()

    def shelf_search(self, store, query, k=3, llm_call=None):
        self.shelf_search_calls += 1
        return self._shelf_results


class _FakeMemoryStore:
    def __init__(self, library=None):
        self.library = library

    def search(self, query, k=3):
        return []


def _make_cfg(project_root: Path, **memory_kwargs) -> AppConfig:
    from mini_agent.config import MemoryConfig

    return AppConfig(project_root=project_root, memory=MemoryConfig(**memory_kwargs))


# ── wiki_search 命中：采用其结果，不触碰 shelf_search ────────────────────


def test_wiki_search_hit_uses_wiki_snippet_and_skips_shelf(tmp_path):
    page = _FakeWikiPage("p1", "body text about judge system", source_entries=["e1", "e2"])
    result = _FakeWikiSearchResult(
        pages=[page], answer="综合回答：judge 系统整合了 xxx", grounded_page_ids=["p1"],
        stage_reached="llm",
    )
    library = _FakeLibrary(wiki_result=result)
    memory = _FakeMemoryStore(library=library)

    cfg = _make_cfg(tmp_path)
    builder = ContextBuilder(cfg=cfg, memory=memory, llm_call_getter=lambda: (lambda p: "ok"))
    builder.refresh_turn_context("judge system")

    assert "judge 系统整合了 xxx" in builder._cached_memory_snippet
    assert builder.last_injected_memory_ids == ["e1", "e2"]
    assert library.shelf_search_calls == 0  # wiki 命中后不应该再跑 shelf_search


def test_wiki_search_hit_without_answer_falls_back_to_page_body(tmp_path):
    page = _FakeWikiPage("p1", "raw page body content", source_entries=["e9"])
    result = _FakeWikiSearchResult(pages=[page], answer="", grounded_page_ids=["p1"])
    library = _FakeLibrary(wiki_result=result)
    memory = _FakeMemoryStore(library=library)

    cfg = _make_cfg(tmp_path)
    builder = ContextBuilder(cfg=cfg, memory=memory, llm_call_getter=lambda: (lambda p: "ok"))
    builder.refresh_turn_context("query")

    assert "raw page body content" in builder._cached_memory_snippet
    assert builder.last_injected_memory_ids == ["e9"]


# ── wiki_search 未命中/异常/关闭：退回既有链路，行为与切换前一致 ─────────


def test_wiki_search_no_grounded_falls_back_to_shelf_search(tmp_path):
    result = _FakeWikiSearchResult(pages=[], grounded_page_ids=[], stage_reached="none")
    entry = _FakeMemoryEntry(entry_id="e1")
    library = _FakeLibrary(wiki_result=result, shelf_results=[entry])
    memory = _FakeMemoryStore(library=library)

    cfg = _make_cfg(tmp_path)
    builder = ContextBuilder(cfg=cfg, memory=memory)
    builder.refresh_turn_context("query")

    assert library.shelf_search_calls == 1
    assert "Relevant past experience" in builder._cached_memory_snippet
    assert builder.last_injected_memory_ids == ["e1"]


def test_wiki_search_exception_falls_back_to_shelf_search(tmp_path):
    library = _FakeLibrary(wiki_exc=RuntimeError("boom"), shelf_results=[_FakeMemoryEntry("e2")])
    memory = _FakeMemoryStore(library=library)

    cfg = _make_cfg(tmp_path)
    builder = ContextBuilder(cfg=cfg, memory=memory)
    builder.refresh_turn_context("query")

    assert library.shelf_search_calls == 1
    assert builder.last_injected_memory_ids == ["e2"]


def test_wiki_search_primary_disabled_skips_wiki_entirely(tmp_path):
    result = _FakeWikiSearchResult(
        pages=[_FakeWikiPage("p1", "x", ["e1"])], answer="不应该被用到", grounded_page_ids=["p1"],
    )
    library = _FakeLibrary(wiki_result=result, shelf_results=[_FakeMemoryEntry("e2")])
    memory = _FakeMemoryStore(library=library)

    cfg = _make_cfg(tmp_path, library_wiki_search_primary=False)
    builder = ContextBuilder(cfg=cfg, memory=memory)
    builder.refresh_turn_context("query")

    assert library.wiki_search_calls == []  # 完全没调用 wiki_search
    assert library.shelf_search_calls == 1
    assert builder.last_injected_memory_ids == ["e2"]


def test_no_llm_call_getter_still_attempts_wiki_search_with_none(tmp_path):
    """没有配置 llm_call_getter 时，wiki_search 应该被以 llm_call=None 调用

    （对应三段式检索里"没有 llm_call 就跳过 LLM 精排"的既有行为），而不是
    直接跳过整个 wiki_search 环节。"""
    result = _FakeWikiSearchResult(pages=[], grounded_page_ids=[], stage_reached="graph")
    library = _FakeLibrary(wiki_result=result, shelf_results=[])
    memory = _FakeMemoryStore(library=library)

    cfg = _make_cfg(tmp_path)
    builder = ContextBuilder(cfg=cfg, memory=memory)
    builder.refresh_turn_context("query")

    assert library.wiki_search_calls == [None]
