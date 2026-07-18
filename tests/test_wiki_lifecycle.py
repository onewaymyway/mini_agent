"""
tests/test_wiki_lifecycle.py — wiki 提取层与组织层改进计划 O4 单元测试

覆盖：
  - wiki/lifecycle.py::mark_page_state 对 entity/decision/experience 三类
    页面的分发正确性
  - fact 锚点粒度的状态标记读写（world_writer.py 生成的锚点 + lifecycle.py
    的 anchor 更新）
  - touch_validated：stale -> fresh 回升、superseded 不因隐式验证回升
  - stale_candidate_scan：超期/未超期/非 fresh 状态三种场景的判定
  - wiki/search.py::_rule_score 的 lifecycle_discount_enabled 折扣（默认关闭
    时行为不变，开启后 stale 减半 / superseded 归零）
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, "src")

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.lifecycle import mark_page_state, stale_candidate_scan, touch_validated
from mini_agent.wiki.parser import parse_page
from mini_agent.wiki.search import _rule_score, _tokenize
from mini_agent.wiki.writer import update_lifecycle_fields, write_page


@pytest.fixture
def paths(tmp_path):
    p = AgentPaths(tmp_path)
    p.ensure_wiki_dirs()
    return p


def _seed(paths):
    write_page(
        paths, page_id="client-pool", page_type="entity",
        body="## 概述\n\nClientPool 负责多 LLM provider 的 key 轮换。\n",
        tags=["module"],
    )
    write_page(
        paths, page_id="key-rotation-decision", page_type="decision",
        body="决定用轮询策略做 key 轮换。",
        tags=["decision"],
    )
    write_page(
        paths, page_id="onboarding-experience", page_type="experience",
        body="首次接入新 provider 时要先跑一遍集成测试。",
        tags=["experience"],
    )


# ── mark_page_state 分发正确性 ──────────────────────────────────────────

def test_mark_page_state_dispatches_across_page_types(paths):
    _seed(paths)
    for page_id in ("client-pool", "key-rotation-decision", "onboarding-experience"):
        ok = mark_page_state(
            paths, page_id, confidence="superseded",
            reason="人类纠正：这个说法已经不对了", validated_by="correction_check",
        )
        assert ok is True

        page = parse_page(paths.wiki_dir.rglob(f"{page_id}.md").__next__())
        assert page.raw_frontmatter.get("knowledge_state") == "superseded"
        assert "correction_check" in (page.raw_frontmatter.get("validated_by") or [])
        assert page.raw_frontmatter.get("last_validated_at")
        assert "历史沿革" in page.body


def test_mark_page_state_invalid_confidence_returns_false(paths):
    _seed(paths)
    assert mark_page_state(paths, "client-pool", confidence="bogus") is False


def test_mark_page_state_missing_page_returns_false(paths):
    _seed(paths)
    assert mark_page_state(paths, "does-not-exist", confidence="stale") is False


# ── fact 锚点粒度标记 ────────────────────────────────────────────────────

def test_fact_anchor_mark_and_read(paths):
    from mini_agent.wiki.world_writer import FactCandidate, queue_facts, consolidate_pending

    _seed(paths)
    queue_facts(
        paths,
        [FactCandidate(statement="ClientPool 默认并发数是 4", related_entities=["client-pool"], confidence=0.7)],
        source_entries=["entry_1"],
    )
    consolidate_pending(paths, llm_call=None)

    page_path = paths.wiki_entities_dir / "client-pool.md"
    page = parse_page(page_path)
    assert "fact_id: client-pool#fact-1" in page.body
    assert "knowledge_state: fresh" in page.body

    ok = mark_page_state(paths, "client-pool", confidence="stale", anchor="client-pool#fact-1")
    assert ok is True

    page = parse_page(page_path)
    assert "fact_id: client-pool#fact-1; knowledge_state: stale" in page.body
    # 页面级 frontmatter 不应该被锚点级标记影响
    assert page.raw_frontmatter.get("knowledge_state") is None


def test_fact_anchor_unknown_anchor_returns_false(paths):
    from mini_agent.wiki.world_writer import FactCandidate, queue_facts, consolidate_pending

    _seed(paths)
    queue_facts(
        paths,
        [FactCandidate(statement="ClientPool 默认并发数是 4", related_entities=["client-pool"], confidence=0.7)],
        source_entries=["entry_1"],
    )
    consolidate_pending(paths, llm_call=None)

    assert mark_page_state(
        paths, "client-pool", confidence="stale", anchor="client-pool#fact-99",
    ) is False


def test_second_fact_gets_incrementing_anchor(paths):
    from mini_agent.wiki.world_writer import FactCandidate, queue_facts, consolidate_pending

    _seed(paths)
    queue_facts(
        paths,
        [FactCandidate(statement="事实一", related_entities=["client-pool"], confidence=0.6)],
        source_entries=["entry_1"],
    )
    consolidate_pending(paths, llm_call=None)
    queue_facts(
        paths,
        [FactCandidate(statement="事实二", related_entities=["client-pool"], confidence=0.6)],
        source_entries=["entry_2"],
    )
    consolidate_pending(paths, llm_call=None)

    page = parse_page(paths.wiki_entities_dir / "client-pool.md")
    assert "fact_id: client-pool#fact-1" in page.body
    assert "fact_id: client-pool#fact-2" in page.body


# ── touch_validated ──────────────────────────────────────────────────────

def test_touch_validated_recovers_stale_to_fresh(paths):
    _seed(paths)
    page = parse_page(paths.wiki_entities_dir / "client-pool.md")
    update_lifecycle_fields(paths, page, knowledge_state="stale")

    assert touch_validated(paths, "client-pool", validated_by="grounded_hit") is True
    page = parse_page(paths.wiki_entities_dir / "client-pool.md")
    assert page.raw_frontmatter.get("knowledge_state") == "fresh"
    assert "grounded_hit" in (page.raw_frontmatter.get("validated_by") or [])


def test_touch_validated_does_not_recover_superseded(paths):
    _seed(paths)
    page = parse_page(paths.wiki_entities_dir / "client-pool.md")
    update_lifecycle_fields(paths, page, knowledge_state="superseded")

    assert touch_validated(paths, "client-pool", validated_by="grounded_hit") is True
    page = parse_page(paths.wiki_entities_dir / "client-pool.md")
    assert page.raw_frontmatter.get("knowledge_state") == "superseded"


# ── stale_candidate_scan ─────────────────────────────────────────────────

def test_stale_candidate_scan_marks_overdue_fresh_pages(paths):
    _seed(paths)
    old_ts = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    page = parse_page(paths.wiki_entities_dir / "client-pool.md")
    update_lifecycle_fields(paths, page, knowledge_state="fresh")
    # 手动回填一个过期的 last_validated_at（模拟"很久没被验证过"）
    import mini_agent.wiki.writer as writer_mod
    page = parse_page(paths.wiki_entities_dir / "client-pool.md")
    extra = {k: v for k, v in page.raw_frontmatter.items()
             if k not in {"id", "type", "tags", "status", "confidence", "created", "updated", "links", "source_entries"}}
    extra["last_validated_at"] = old_ts
    text = writer_mod.render_page(
        page_id=page.id, page_type=page.type, body=page.body, tags=page.tags,
        status=page.status, confidence=page.confidence, created=page.created,
        updated=page.updated, links=page.strong_links(), source_entries=page.source_entries,
        extra_frontmatter=extra,
    )
    writer_mod._atomic_write_text(page.path, text)

    result = stale_candidate_scan(paths, threshold_days=90)
    assert result["marked_stale"] == 1

    page = parse_page(paths.wiki_entities_dir / "client-pool.md")
    assert page.raw_frontmatter.get("knowledge_state") == "stale"
    assert "stale_scan" in (page.raw_frontmatter.get("validated_by") or [])


def test_stale_candidate_scan_skips_recently_validated(paths):
    _seed(paths)
    page = parse_page(paths.wiki_entities_dir / "client-pool.md")
    update_lifecycle_fields(paths, page, knowledge_state="fresh")  # last_validated_at = 现在

    result = stale_candidate_scan(paths, threshold_days=90)
    assert result["marked_stale"] == 0


def test_stale_candidate_scan_skips_non_fresh_pages(paths):
    _seed(paths)
    page = parse_page(paths.wiki_entities_dir / "client-pool.md")
    update_lifecycle_fields(paths, page, knowledge_state="superseded")

    result = stale_candidate_scan(paths, threshold_days=0)
    page_after = parse_page(paths.wiki_entities_dir / "client-pool.md")
    # superseded 不应被巡检"降级"为 stale
    assert page_after.raw_frontmatter.get("knowledge_state") == "superseded"


# ── search.py 折扣集成（默认关闭 / 显式开启两种场景）────────────────────

def test_rule_score_lifecycle_discount_disabled_by_default(paths):
    _seed(paths)
    page = parse_page(paths.wiki_entities_dir / "client-pool.md")
    update_lifecycle_fields(paths, page, knowledge_state="superseded")
    page = parse_page(paths.wiki_entities_dir / "client-pool.md")

    tokens = set(_tokenize("ClientPool key 轮换"))
    score_default = _rule_score(tokens, set(), page)
    score_explicit_off = _rule_score(tokens, set(), page, lifecycle_discount_enabled=False)
    assert score_default == score_explicit_off
    assert score_default > 0.0


def test_rule_score_lifecycle_discount_enabled(paths):
    _seed(paths)
    page = parse_page(paths.wiki_entities_dir / "client-pool.md")
    update_lifecycle_fields(paths, page, knowledge_state="stale")
    stale_page = parse_page(paths.wiki_entities_dir / "client-pool.md")

    page2 = parse_page(paths.wiki_entities_dir / "client-pool.md")
    update_lifecycle_fields(paths, page2, knowledge_state="superseded")
    superseded_page = parse_page(paths.wiki_entities_dir / "client-pool.md")

    tokens = set(_tokenize("ClientPool key 轮换"))
    baseline = _rule_score(tokens, set(), stale_page, lifecycle_discount_enabled=False)
    discounted_stale = _rule_score(tokens, set(), stale_page, lifecycle_discount_enabled=True)
    discounted_superseded = _rule_score(tokens, set(), superseded_page, lifecycle_discount_enabled=True)

    assert discounted_stale == pytest.approx(baseline * 0.5)
    assert discounted_superseded == 0.0
