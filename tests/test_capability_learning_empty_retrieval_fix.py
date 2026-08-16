"""tests/test_capability_learning_empty_retrieval_fix.py

覆盖 bug 修复：`run_capability_learning_cycle()` 此前在检索没有任何结果
（比如 web_search provider 报错/被限流后 `make_web_search_retriever()`
兜底返回 `[]`）时，`wiki_writer` 仍然会无条件写出一页只有"（暂无检索
结果）"的占位内容，但 `topic.coverage_state` 却被错误标成 `covered`，
导致这个子主题从此再也不会被 `scan_outline_gaps()` 选回候选池重试
——看起来"已覆盖"，实际上永远是一页空内容。学习台账里的
"检索并写入 1 个 wiki 页面"也没有反映出这一页其实是空的。

修复后：`results` 为空（或所有摘要都是空字符串）时——
  - `topic.coverage_state` 保持/回退为 `partial`，不是 `covered`
  - 台账 action 记为 `research_empty`，summary 文案说明"未获得有效
    结果、下轮会重试、不计入已覆盖"
  - `summary["topics_research_empty"]` 计数该子主题
  - `maybe_dispatch_capability_notification()` 的"新沉淀页面数"用
    `topics_researched - topics_research_empty`，不把空占位页算作
    新沉淀内容
"""
from __future__ import annotations

import pytest

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.capability_learning import (
    CapabilityLedgerStore,
    CapabilityTrackStore,
    OutlineTopic,
    maybe_dispatch_capability_notification,
    run_capability_learning_cycle,
)


@pytest.fixture
def paths(tmp_path):
    return AgentPaths(project_root=tmp_path)


def _empty_retriever(topic, track):
    return []


def _placeholder_writer(topic, track, results):
    """模拟 make_wiki_writer 的真实行为：即使 results 为空，也无条件
    写出一页占位内容，返回非空 page_ids。"""
    return [f"cap_placeholder_{topic.topic_id}"]


def _real_retriever(topic, track):
    return [{"url": "https://example.com/a", "summary": "真实检索到的内容"}]


def _real_writer(topic, track, results):
    return [f"wiki_page_{topic.topic_id}"]


class TestEmptyRetrievalCoverageState:
    def test_empty_results_keeps_topic_not_covered(self, paths):
        store = CapabilityTrackStore(paths)
        track = store.create(title="股票分析能力", persona_desc="x", outline_names=["技术分析基础"])

        summary = run_capability_learning_cycle(
            paths, retriever=_empty_retriever, wiki_writer=_placeholder_writer,
        )

        refreshed = store.get(track.track_id)
        topic = refreshed.outline[0]
        # 核心修复点：检索没有任何结果时，即使 wiki_writer 无条件写出了
        # 一页占位内容（page_ids 非空），也不应该被标成 covered
        assert topic.coverage_state == "partial"
        assert topic.wiki_page_ids == [f"cap_placeholder_{topic.topic_id}"]

        assert summary["topics_research_empty"] == 1
        assert summary["topics_researched"] == 1

    def test_empty_results_ledger_uses_research_empty_action(self, paths):
        store = CapabilityTrackStore(paths)
        track = store.create(title="股票分析能力", persona_desc="x", outline_names=["技术分析基础"])

        run_capability_learning_cycle(paths, retriever=_empty_retriever, wiki_writer=_placeholder_writer)

        ledger = CapabilityLedgerStore(paths).list_for_track(track.track_id)
        entry = next(e for e in ledger if e.topic_id == track.outline[0].topic_id)
        # from ledger for the topic (need topic_id from refreshed track before mutation—use original id)
        assert entry.action == "research_empty"
        assert "未获得有效结果" in entry.summary
        assert "不计入已覆盖" in entry.summary

    def test_topic_retried_next_cycle_after_empty_result(self, paths):
        """empty 结果标成 partial 后，下一轮 scan_outline_gaps 应该还能
        再次选中它（不会因为写了占位页就被跳过）。"""
        store = CapabilityTrackStore(paths)
        track = store.create(title="股票分析能力", persona_desc="x", outline_names=["技术分析基础"])

        run_capability_learning_cycle(paths, retriever=_empty_retriever, wiki_writer=_placeholder_writer)

        calls = []

        def _counting_retriever(topic, track):
            calls.append(topic.topic_id)
            return [{"url": "https://example.com/b", "summary": "这次真的查到了"}]

        run_capability_learning_cycle(paths, retriever=_counting_retriever, wiki_writer=_real_writer)

        assert len(calls) == 1
        refreshed = store.get(track.track_id)
        assert refreshed.outline[0].coverage_state == "covered"

    def test_real_results_still_mark_covered_as_before(self, paths):
        """回归：真正查到内容时行为不变，仍然标记为 covered。"""
        store = CapabilityTrackStore(paths)
        track = store.create(title="股票分析能力", persona_desc="x", outline_names=["技术分析基础"])

        summary = run_capability_learning_cycle(paths, retriever=_real_retriever, wiki_writer=_real_writer)

        refreshed = store.get(track.track_id)
        assert refreshed.outline[0].coverage_state == "covered"
        assert summary["topics_research_empty"] == 0
        assert summary["topics_researched"] == 1

        ledger = CapabilityLedgerStore(paths).list_for_track(track.track_id)
        entry = next(e for e in ledger if e.topic_id == track.outline[0].topic_id)
        assert entry.action == "researched"

    def test_results_with_only_blank_summaries_treated_as_empty(self, paths):
        """summary/text 全是空字符串（不是真的 None/[]，但内容为空）也应该
        被视为"没有有效结果"，而不是因为 results 列表本身非空就当作有内容。"""
        store = CapabilityTrackStore(paths)
        track = store.create(title="股票分析能力", persona_desc="x", outline_names=["技术分析基础"])

        def _blank_retriever(topic, track):
            return [{"url": "https://example.com/a", "summary": "   "}]

        summary = run_capability_learning_cycle(
            paths, retriever=_blank_retriever, wiki_writer=_placeholder_writer,
        )
        assert summary["topics_research_empty"] == 1
        refreshed = store.get(track.track_id)
        assert refreshed.outline[0].coverage_state == "partial"


class TestNotificationExcludesEmptyPlaceholders:
    def test_empty_only_cycle_does_not_notify(self, paths):
        cycle_summary = {"questions_raised": 0, "topics_researched": 1, "topics_research_empty": 1}
        result = maybe_dispatch_capability_notification(
            paths, None, cycle_summary, pending_questions_count=0,
        )
        # topics_researched - topics_research_empty == 0，且没有新问题 -> 空轮，不发送
        assert result is None

    def test_mixed_cycle_notifies_with_real_page_count(self, paths):
        cycle_summary = {"questions_raised": 0, "topics_researched": 3, "topics_research_empty": 1}
        result = maybe_dispatch_capability_notification(
            paths, None, cycle_summary, pending_questions_count=0,
        )
        assert result is not None
        assert result["sent"] is True
