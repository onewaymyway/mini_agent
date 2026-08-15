"""
tests/test_context_builder_persona_wiki_scopes.py

persona_capability_learning_design.md §11：验证当前激活 persona 声明的
`wiki_scopes` 会被透传给 `library.wiki_search(tags=...)`，让检索优先/
但不强制限定在该角色声明的知识范围内（软优先，见 §11.3）。

覆盖：
- 未激活 persona / persona 未声明 wiki_scopes → tags 传 None，行为与改动前一致
- 激活 persona 且声明了 wiki_scopes → tags 传入该角色的 wiki_scopes 列表
- persona_getter / loader 异常 → 静默退化为不限制，不影响检索主流程
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mini_agent.config import AppConfig
from mini_agent.context_builder import ContextBuilder
from mini_agent.orchestrator.persona_profiles import PersonaProfile


class _FakeWikiSearchResult:
    def __init__(self, pages=None, answer="", grounded_page_ids=None, stage_reached="none"):
        self.pages = pages or []
        self.answer = answer
        self.grounded_page_ids = grounded_page_ids or []
        self.stage_reached = stage_reached


class _FakeLibrary:
    def __init__(self, wiki_result=None):
        self._wiki_result = wiki_result or _FakeWikiSearchResult()
        self.last_tags = "UNSET"

    def wiki_search(self, query, llm_call=None, tags=None, k=5, confidence_weight=None, use_index=True):
        self.last_tags = tags
        return self._wiki_result

    def shelf_search(self, store, query, k=3, llm_call=None):
        return []


class _FakeMemoryStore:
    def __init__(self, library=None):
        self.library = library

    def search(self, query, k=3):
        return []


def _make_cfg(project_root: Path) -> AppConfig:
    return AppConfig(project_root=project_root)


class _FakeLoader:
    def __init__(self, personas: dict[str, PersonaProfile]):
        self._personas = personas

    def get(self, name):
        return self._personas.get(name)


def test_no_active_persona_passes_no_tags(tmp_path):
    library = _FakeLibrary()
    memory = _FakeMemoryStore(library=library)
    cfg = _make_cfg(tmp_path)

    builder = ContextBuilder(cfg=cfg, memory=memory, persona_getter=lambda: None)
    builder.refresh_turn_context("query")

    assert library.last_tags is None


def test_persona_without_wiki_scopes_passes_no_tags(tmp_path, monkeypatch):
    persona = PersonaProfile(name="jarvis", wiki_scopes=[])
    monkeypatch.setattr(
        "mini_agent.orchestrator.persona_profiles.get_persona_loader",
        lambda: _FakeLoader({"jarvis": persona}),
    )
    library = _FakeLibrary()
    memory = _FakeMemoryStore(library=library)
    cfg = _make_cfg(tmp_path)

    builder = ContextBuilder(cfg=cfg, memory=memory, persona_getter=lambda: "jarvis")
    builder.refresh_turn_context("query")

    assert library.last_tags is None


def test_persona_with_wiki_scopes_passes_them_as_tags(tmp_path, monkeypatch):
    persona = PersonaProfile(
        name="stock-advisor", wiki_scopes=["capability:stock_analysis", "capability:macro_economics"],
    )
    monkeypatch.setattr(
        "mini_agent.orchestrator.persona_profiles.get_persona_loader",
        lambda: _FakeLoader({"stock-advisor": persona}),
    )
    library = _FakeLibrary()
    memory = _FakeMemoryStore(library=library)
    cfg = _make_cfg(tmp_path)

    builder = ContextBuilder(cfg=cfg, memory=memory, persona_getter=lambda: "stock-advisor")
    builder.refresh_turn_context("利率对股价的影响")

    assert library.last_tags == ["capability:stock_analysis", "capability:macro_economics"]


def test_persona_lookup_failure_degrades_to_no_tags(tmp_path, monkeypatch):
    def _boom():
        raise RuntimeError("loader unavailable")

    monkeypatch.setattr("mini_agent.orchestrator.persona_profiles.get_persona_loader", _boom)
    library = _FakeLibrary()
    memory = _FakeMemoryStore(library=library)
    cfg = _make_cfg(tmp_path)

    builder = ContextBuilder(cfg=cfg, memory=memory, persona_getter=lambda: "whatever")
    # 不应抛异常，静默退化为不限制
    builder.refresh_turn_context("query")

    assert library.last_tags is None


def test_persona_field_parses_from_frontmatter(tmp_path):
    from mini_agent.orchestrator.persona_profiles import _parse_persona

    md = tmp_path / "stock-advisor.md"
    md.write_text(
        "---\n"
        "name: stock-advisor\n"
        "display_name: 老李投顾\n"
        "wiki_scopes:\n"
        "  - capability:stock_analysis\n"
        "  - capability:macro_economics\n"
        "---\n"
        "正文内容\n",
        encoding="utf-8",
    )
    persona = _parse_persona(md)
    assert persona is not None
    assert persona.wiki_scopes == ["capability:stock_analysis", "capability:macro_economics"]


def test_persona_field_defaults_to_empty_when_absent(tmp_path):
    from mini_agent.orchestrator.persona_profiles import _parse_persona

    md = tmp_path / "jarvis.md"
    md.write_text("---\nname: jarvis\n---\n正文\n", encoding="utf-8")
    persona = _parse_persona(md)
    assert persona is not None
    assert persona.wiki_scopes == []


# ── §14.1-a 使用驱动学习：wiki_search 未命中时的 miss 记录接线 ──────────────


def test_miss_recorded_when_persona_scope_matches_active_track(tmp_path, monkeypatch):
    """persona 绑定的 wiki_scopes 命中某个 active knowledge 型 Track 的
    wiki_tag，且 wiki_search 未命中时，应该记一条 miss_observed 台账。"""
    from mini_agent.evolution.capability_learning import CapabilityLedgerStore, CapabilityTrackStore

    track_store = CapabilityTrackStore(_agent_paths(tmp_path))
    track = track_store.create(
        title="股票分析能力", persona_desc="希望你具备强大的股票分析能力",
    )
    persona = PersonaProfile(name="stock-advisor", wiki_scopes=[track.wiki_tag])
    monkeypatch.setattr(
        "mini_agent.orchestrator.persona_profiles.get_persona_loader",
        lambda: _FakeLoader({"stock-advisor": persona}),
    )

    library = _FakeLibrary()  # 空结果 = 未命中
    memory = _FakeMemoryStore(library=library)
    cfg = _make_cfg(tmp_path)

    builder = ContextBuilder(cfg=cfg, memory=memory, persona_getter=lambda: "stock-advisor")
    builder.refresh_turn_context("利率对股价的影响")

    ledger = CapabilityLedgerStore(_agent_paths(tmp_path)).list_for_track(track.track_id)
    miss_entries = [e for e in ledger if e.action == "miss_observed"]
    assert len(miss_entries) == 1
    assert "利率对股价的影响" in miss_entries[0].summary


def test_no_miss_recorded_when_scope_does_not_match_any_track(tmp_path, monkeypatch):
    from mini_agent.evolution.capability_learning import CapabilityLedgerStore, CapabilityTrackStore

    persona = PersonaProfile(name="stock-advisor", wiki_scopes=["capability:unrelated_topic"])
    monkeypatch.setattr(
        "mini_agent.orchestrator.persona_profiles.get_persona_loader",
        lambda: _FakeLoader({"stock-advisor": persona}),
    )

    track_store = CapabilityTrackStore(_agent_paths(tmp_path))
    track = track_store.create(title="股票分析能力", persona_desc="希望你具备强大的股票分析能力")

    library = _FakeLibrary()
    memory = _FakeMemoryStore(library=library)
    cfg = _make_cfg(tmp_path)

    builder = ContextBuilder(cfg=cfg, memory=memory, persona_getter=lambda: "stock-advisor")
    builder.refresh_turn_context("query")

    ledger = CapabilityLedgerStore(_agent_paths(tmp_path)).list_for_track(track.track_id)
    assert [e for e in ledger if e.action == "miss_observed"] == []


def test_no_miss_recorded_without_active_persona(tmp_path):
    from mini_agent.evolution.capability_learning import CapabilityTrackStore

    track_store = CapabilityTrackStore(_agent_paths(tmp_path))
    track = track_store.create(title="股票分析能力", persona_desc="希望你具备强大的股票分析能力")

    library = _FakeLibrary()
    memory = _FakeMemoryStore(library=library)
    cfg = _make_cfg(tmp_path)

    builder = ContextBuilder(cfg=cfg, memory=memory, persona_getter=lambda: None)
    builder.refresh_turn_context("query")

    from mini_agent.evolution.capability_learning import CapabilityLedgerStore
    ledger = CapabilityLedgerStore(_agent_paths(tmp_path)).list_for_track(track.track_id)
    assert ledger == []


def test_miss_tracking_disabled_by_config_skips_recording(tmp_path, monkeypatch):
    from mini_agent.config import MemoryConfig
    from mini_agent.evolution.capability_learning import CapabilityLedgerStore, CapabilityTrackStore

    track_store = CapabilityTrackStore(_agent_paths(tmp_path))
    track = track_store.create(title="股票分析能力", persona_desc="希望你具备强大的股票分析能力")

    persona = PersonaProfile(name="stock-advisor", wiki_scopes=[track.wiki_tag])
    monkeypatch.setattr(
        "mini_agent.orchestrator.persona_profiles.get_persona_loader",
        lambda: _FakeLoader({"stock-advisor": persona}),
    )

    library = _FakeLibrary()
    memory = _FakeMemoryStore(library=library)
    cfg = AppConfig(
        project_root=tmp_path,
        memory=MemoryConfig(capability_wiki_miss_tracking_enabled=False),
    )

    builder = ContextBuilder(cfg=cfg, memory=memory, persona_getter=lambda: "stock-advisor")
    builder.refresh_turn_context("query")

    ledger = CapabilityLedgerStore(_agent_paths(tmp_path)).list_for_track(track.track_id)
    assert ledger == []


def test_miss_recording_failure_does_not_break_turn_refresh(tmp_path, monkeypatch):
    """记录环节本身抛异常也不能影响检索主流程（静默降级）。"""
    persona = PersonaProfile(name="stock-advisor", wiki_scopes=["capability:stock_analysis"])
    monkeypatch.setattr(
        "mini_agent.orchestrator.persona_profiles.get_persona_loader",
        lambda: _FakeLoader({"stock-advisor": persona}),
    )
    monkeypatch.setattr(
        "mini_agent.evolution.capability_learning.CapabilityTrackStore.list_tracks",
        lambda self, status=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    library = _FakeLibrary()
    memory = _FakeMemoryStore(library=library)
    cfg = _make_cfg(tmp_path)

    builder = ContextBuilder(cfg=cfg, memory=memory, persona_getter=lambda: "stock-advisor")
    # 不应抛异常
    builder.refresh_turn_context("query")


def _agent_paths(project_root: Path):
    from mini_agent.storage.paths import AgentPaths

    return AgentPaths(project_root=project_root)
