"""tests/test_capability_wiki_freshness.py

覆盖 next_doc/capability_wiki_freshness_improvement_plan.md 两个阶段：
  - 阶段 1：内容完整性三态判定（empty/thin/sufficient），thin 不再被
    错误标成 covered；wiki_writer 拿到 completeness 并落盘；旧式三参数
    wiki_writer 签名仍然兼容（TypeError 兜底）。
  - 阶段 2：OutlineTopic.volatility 默认值改为 "periodic"；
    CapabilityTrackStore.migrate_stable_volatility_to_periodic() 批量迁移。
"""
from __future__ import annotations

import pytest

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.capability_learning import (
    CONTENT_SUFFICIENT_MIN_CHARS,
    CapabilityLedgerStore,
    CapabilityTrack,
    CapabilityTrackStore,
    OutlineTopic,
    run_capability_learning_cycle,
)


@pytest.fixture
def paths(tmp_path):
    return AgentPaths(project_root=tmp_path)


def _make_track(paths, *, volatility="periodic", coverage_state="uncovered"):
    store = CapabilityTrackStore(paths)
    track = store.create(title="测试方向", persona_desc="测试")
    track.outline.append(
        OutlineTopic(
            topic_id="topic_1", name="子主题A",
            coverage_state=coverage_state, volatility=volatility,
        )
    )
    store.update(track.track_id, outline=track.outline)
    return store.get(track.track_id)


def _thin_retriever(topic, track):
    return [{"summary": "太短了"}]  # 远小于 CONTENT_SUFFICIENT_MIN_CHARS


def _sufficient_retriever(topic, track):
    return [{"summary": "x" * CONTENT_SUFFICIENT_MIN_CHARS}]


def _empty_retriever(topic, track):
    return []


class _RecordingWriter:
    """记录被调用时收到的 completeness，同时兼容"没传 completeness"的调用。"""

    def __init__(self):
        self.calls = []

    def __call__(self, topic, track, results, *, completeness=None):
        self.calls.append(completeness)
        return [f"cap_{topic.topic_id}"]


def _legacy_three_arg_writer(topic, track, results):
    """模拟改造前的旧签名 wiki_writer，不接受 completeness 关键字参数。"""
    return [f"legacy_{topic.topic_id}"]


class TestContentCompletenessThreeState:
    def test_thin_content_not_marked_covered(self, paths):
        track = _make_track(paths)
        writer = _RecordingWriter()
        result = run_capability_learning_cycle(
            paths, retriever=_thin_retriever, wiki_writer=writer,
        )
        assert result["topics_researched"] == 1
        assert result["topics_research_thin"] == 1
        assert result["topics_research_empty"] == 0
        assert writer.calls == ["thin"]

        updated = CapabilityTrackStore(paths).get(track.track_id)
        assert updated.outline[0].coverage_state == "partial"

    def test_sufficient_content_marked_covered(self, paths):
        track = _make_track(paths)
        writer = _RecordingWriter()
        result = run_capability_learning_cycle(
            paths, retriever=_sufficient_retriever, wiki_writer=writer,
        )
        assert result["topics_researched"] == 1
        assert result["topics_research_thin"] == 0
        assert result["topics_research_empty"] == 0
        assert writer.calls == ["sufficient"]

        updated = CapabilityTrackStore(paths).get(track.track_id)
        assert updated.outline[0].coverage_state == "covered"

    def test_empty_content_still_partial_and_counted(self, paths):
        track = _make_track(paths)
        writer = _RecordingWriter()
        result = run_capability_learning_cycle(
            paths, retriever=_empty_retriever, wiki_writer=writer,
        )
        assert result["topics_research_empty"] == 1
        assert result["topics_research_thin"] == 0
        assert writer.calls == ["empty"]
        updated = CapabilityTrackStore(paths).get(track.track_id)
        assert updated.outline[0].coverage_state == "partial"

    def test_thin_topic_is_retried_next_cycle(self, paths):
        """thin 内容不需要等 volatility 周期，下一轮立刻会被重新选中。"""
        _make_track(paths)
        writer = _RecordingWriter()
        run_capability_learning_cycle(paths, retriever=_thin_retriever, wiki_writer=writer)

        writer2 = _RecordingWriter()
        result2 = run_capability_learning_cycle(
            paths, retriever=_sufficient_retriever, wiki_writer=writer2,
        )
        assert result2["topics_researched"] == 1
        assert writer2.calls == ["sufficient"]

    def test_legacy_three_arg_wiki_writer_still_works(self, paths):
        """旧式 wiki_writer 签名（不接受 completeness 关键字参数）应该
        通过 TypeError 兜底继续正常工作，不因为签名升级而报错。"""
        _make_track(paths)
        result = run_capability_learning_cycle(
            paths, retriever=_sufficient_retriever, wiki_writer=_legacy_three_arg_writer,
        )
        assert result["topics_researched"] == 1
        assert result["topics_research_empty"] == 0


class TestMakeWikiWriterCompletenessFrontmatter:
    def test_frontmatter_records_completeness(self, paths, monkeypatch):
        from mini_agent.evolution.capability_learning import make_wiki_writer
        from mini_agent.wiki import writer as wiki_writer_mod

        captured = {}
        original_write_page = wiki_writer_mod.write_page

        def _spy_write_page(**kwargs):
            captured.update(kwargs)
            return original_write_page(**kwargs)

        monkeypatch.setattr(wiki_writer_mod, "write_page", _spy_write_page)

        track = CapabilityTrack(track_id="t1", title="T", persona_desc="d", wiki_tag="capability:t")
        topic = OutlineTopic(topic_id="topic_1", name="子主题A")
        writer = make_wiki_writer(paths)
        writer(topic, track, [{"summary": "x" * CONTENT_SUFFICIENT_MIN_CHARS}], completeness="sufficient")

        assert captured["extra_frontmatter"]["content_completeness"] == "sufficient"


class TestVolatilityDefault:
    def test_new_outline_topic_defaults_to_periodic(self):
        topic = OutlineTopic(topic_id="topic_1", name="子主题A")
        assert topic.volatility == "periodic"

    def test_created_track_topics_default_to_periodic(self, paths):
        store = CapabilityTrackStore(paths)
        track = store.create(
            title="T", persona_desc="d", outline_names=["子主题A", "子主题B"],
        )
        assert all(t.volatility == "periodic" for t in track.outline)

    def test_from_dict_missing_field_defaults_to_periodic(self):
        topic = OutlineTopic.from_dict({"topic_id": "topic_1", "name": "A"})
        assert topic.volatility == "periodic"


class TestForceRefreshAllTopics:
    def test_resets_covered_topics_to_partial(self, paths):
        store = CapabilityTrackStore(paths)
        track = store.create(title="T", persona_desc="d")
        track.outline = [
            OutlineTopic(topic_id="topic_1", name="A", coverage_state="covered",
                         wiki_page_ids=["cap_topic_1"]),
            OutlineTopic(topic_id="topic_2", name="B", coverage_state="uncovered"),
            OutlineTopic(topic_id="topic_3", name="C", coverage_state="partial"),
        ]
        store.update(track.track_id, outline=track.outline)

        result = store.force_refresh_all_topics()
        assert result == {"tracks_affected": 1, "topics_reset": 1}

        updated = store.get(track.track_id)
        by_id = {t.topic_id: t.coverage_state for t in updated.outline}
        assert by_id["topic_1"] == "partial"
        assert by_id["topic_2"] == "uncovered"  # 不受影响
        assert by_id["topic_3"] == "partial"    # 本来就是 partial，不受影响

    def test_keeps_existing_wiki_page_ids(self, paths):
        store = CapabilityTrackStore(paths)
        track = store.create(title="T", persona_desc="d")
        track.outline = [
            OutlineTopic(topic_id="topic_1", name="A", coverage_state="covered",
                         wiki_page_ids=["cap_topic_1"]),
        ]
        store.update(track.track_id, outline=track.outline)

        store.force_refresh_all_topics()
        updated = store.get(track.track_id)
        assert updated.outline[0].wiki_page_ids == ["cap_topic_1"]

    def test_scoped_to_single_track(self, paths):
        store = CapabilityTrackStore(paths)
        t1 = store.create(title="T1", persona_desc="d")
        t1.outline = [OutlineTopic(topic_id="a", name="A", coverage_state="covered")]
        store.update(t1.track_id, outline=t1.outline)
        t2 = store.create(title="T2", persona_desc="d")
        t2.outline = [OutlineTopic(topic_id="b", name="B", coverage_state="covered")]
        store.update(t2.track_id, outline=t2.outline)

        result = store.force_refresh_all_topics(track_id=t1.track_id)
        assert result == {"tracks_affected": 1, "topics_reset": 1}
        assert store.get(t1.track_id).outline[0].coverage_state == "partial"
        assert store.get(t2.track_id).outline[0].coverage_state == "covered"  # 不受影响

    def test_no_covered_topics_is_noop(self, paths):
        store = CapabilityTrackStore(paths)
        store.create(title="T", persona_desc="d", outline_names=["A"])
        result = store.force_refresh_all_topics()
        assert result == {"tracks_affected": 0, "topics_reset": 0}

    def test_unknown_track_id_is_noop(self, paths):
        store = CapabilityTrackStore(paths)
        track = store.create(title="T", persona_desc="d")
        track.outline = [OutlineTopic(topic_id="a", name="A", coverage_state="covered")]
        store.update(track.track_id, outline=track.outline)

        result = store.force_refresh_all_topics(track_id="does_not_exist")
        assert result == {"tracks_affected": 0, "topics_reset": 0}
        assert store.get(track.track_id).outline[0].coverage_state == "covered"

    def test_refreshed_topic_reenters_candidate_pool_next_cycle(self, paths):
        """刷新后不用等 volatility 周期，下一轮 cycle 就会被重新选中检索。"""
        store = CapabilityTrackStore(paths)
        track = store.create(title="T", persona_desc="d")
        track.outline = [
            OutlineTopic(topic_id="topic_1", name="A", coverage_state="covered",
                         volatility="periodic"),
        ]
        store.update(track.track_id, outline=track.outline)

        store.force_refresh_all_topics()

        writer = _RecordingWriter()
        result = run_capability_learning_cycle(
            paths, retriever=_sufficient_retriever, wiki_writer=writer,
        )
        assert result["topics_researched"] == 1
        assert writer.calls == ["sufficient"]
    def test_migrates_stable_topics_to_periodic(self, paths):
        store = CapabilityTrackStore(paths)
        track = store.create(title="T", persona_desc="d")
        track.outline = [
            OutlineTopic(topic_id="topic_1", name="A", volatility="stable"),
            OutlineTopic(topic_id="topic_2", name="B", volatility="volatile"),
            OutlineTopic(topic_id="topic_3", name="C", volatility="periodic"),
        ]
        store.update(track.track_id, outline=track.outline)

        result = store.migrate_stable_volatility_to_periodic()
        assert result == {"tracks_affected": 1, "topics_migrated": 1}

        updated = store.get(track.track_id)
        by_id = {t.topic_id: t.volatility for t in updated.outline}
        assert by_id["topic_1"] == "periodic"
        assert by_id["topic_2"] == "volatile"  # 不受影响
        assert by_id["topic_3"] == "periodic"

    def test_migration_is_idempotent(self, paths):
        store = CapabilityTrackStore(paths)
        track = store.create(title="T", persona_desc="d")
        track.outline = [OutlineTopic(topic_id="topic_1", name="A", volatility="stable")]
        store.update(track.track_id, outline=track.outline)

        first = store.migrate_stable_volatility_to_periodic()
        second = store.migrate_stable_volatility_to_periodic()
        assert first == {"tracks_affected": 1, "topics_migrated": 1}
        assert second == {"tracks_affected": 0, "topics_migrated": 0}

    def test_no_stable_topics_is_noop(self, paths):
        store = CapabilityTrackStore(paths)
        store.create(title="T", persona_desc="d", outline_names=["A"])
        result = store.migrate_stable_volatility_to_periodic()
        assert result == {"tracks_affected": 0, "topics_migrated": 0}
