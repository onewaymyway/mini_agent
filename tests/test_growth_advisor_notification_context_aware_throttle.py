"""tests/test_growth_advisor_notification_context_aware_throttle.py

覆盖 next_doc/growth_advisor_ideal_advisor_gap_and_roadmap_plan.md
感知维度分析追加的候选方向：推送的情境感知（软性节流）。

  _recent_conversation_density_ratio() —— 最近一周 vs 更早几周周均值
    的密度比值，数据不足时返回 None。
  _effective_notification_min_confidence() —— 默认原样返回配置值；
    开启且命中"更安静"信号时软性抬高门槛。
  _maybe_dispatch_notification() 接入验证。
"""

from __future__ import annotations

import tempfile
import time
import unittest

from mini_agent.config.models import GrowthAdvisorConfig
from mini_agent.evolution import growth_advisor as ga
from mini_agent.storage.paths import AgentPaths
from pathlib import Path


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class _FakeEntry:
    def __init__(self, entry_id, created_at):
        self.entry_id = entry_id
        self.created_at = created_at


class _FakeMemoryStore:
    def __init__(self, entries):
        self._entries = entries

    def all_entries(self):
        return self._entries


class TestRecentConversationDensityRatio(unittest.TestCase):
    def test_none_when_no_memory_store(self):
        self.assertIsNone(ga._recent_conversation_density_ratio(None))

    def test_none_when_no_baseline_data(self):
        now = time.time()
        # 只有最近一周的数据，没有基线窗口数据
        entries = [_FakeEntry(f"e{i}", now - i * 3600) for i in range(5)]
        store = _FakeMemoryStore(entries)
        self.assertIsNone(ga._recent_conversation_density_ratio(store, now=now))

    def test_ratio_below_one_when_recent_quieter(self):
        now = time.time()
        recent = [_FakeEntry("r1", now - 3600)]  # 最近一周只有 1 条
        # 基线 4 周内，每周 5 条，共 20 条（放在 8~35 天前）
        baseline = [
            _FakeEntry(f"b{i}", now - (8 + i) * 86400) for i in range(20)
        ]
        store = _FakeMemoryStore(recent + baseline)
        ratio = ga._recent_conversation_density_ratio(store, now=now)
        self.assertIsNotNone(ratio)
        self.assertLess(ratio, 1.0)

    def test_ratio_around_one_when_activity_stable(self):
        now = time.time()
        recent = [_FakeEntry(f"r{i}", now - i * 3600) for i in range(5)]
        baseline = [_FakeEntry(f"b{i}", now - (8 + i) * 86400) for i in range(20)]
        store = _FakeMemoryStore(recent + baseline)
        ratio = ga._recent_conversation_density_ratio(store, now=now)
        self.assertIsNotNone(ratio)
        self.assertAlmostEqual(ratio, 1.0, delta=0.01)


class TestEffectiveNotificationMinConfidence(unittest.TestCase):
    def test_disabled_returns_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig(
                notification_min_confidence=0.6,
                notification_context_aware_throttle_enabled=False,
            )
            now = time.time()
            store = _FakeMemoryStore([_FakeEntry("r1", now - 3600)])
            result = ga._effective_notification_min_confidence(paths, cfg, store)
            self.assertEqual(result, 0.6)

    def test_enabled_but_no_signal_returns_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig(
                notification_min_confidence=0.6,
                notification_context_aware_throttle_enabled=True,
            )
            result = ga._effective_notification_min_confidence(paths, cfg, None)
            self.assertEqual(result, 0.6)

    def test_enabled_and_quiet_boosts_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig(
                notification_min_confidence=0.6,
                notification_context_aware_throttle_enabled=True,
                notification_low_activity_ratio_threshold=0.3,
                notification_low_activity_confidence_boost=0.15,
            )
            now = time.time()
            recent = [_FakeEntry("r1", now - 3600)]  # 明显更安静
            baseline = [_FakeEntry(f"b{i}", now - (8 + i) * 86400) for i in range(20)]
            store = _FakeMemoryStore(recent + baseline)
            result = ga._effective_notification_min_confidence(paths, cfg, store)
            self.assertAlmostEqual(result, 0.75, delta=0.001)

    def test_enabled_and_stable_activity_no_boost(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig(
                notification_min_confidence=0.6,
                notification_context_aware_throttle_enabled=True,
            )
            now = time.time()
            recent = [_FakeEntry(f"r{i}", now - i * 3600) for i in range(5)]
            baseline = [_FakeEntry(f"b{i}", now - (8 + i) * 86400) for i in range(20)]
            store = _FakeMemoryStore(recent + baseline)
            result = ga._effective_notification_min_confidence(paths, cfg, store)
            self.assertEqual(result, 0.6)

    def test_boost_capped_at_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig(
                notification_min_confidence=0.95,
                notification_context_aware_throttle_enabled=True,
                notification_low_activity_confidence_boost=0.5,
            )
            now = time.time()
            recent = [_FakeEntry("r1", now - 3600)]
            baseline = [_FakeEntry(f"b{i}", now - (8 + i) * 86400) for i in range(20)]
            store = _FakeMemoryStore(recent + baseline)
            result = ga._effective_notification_min_confidence(paths, cfg, store)
            self.assertLessEqual(result, 1.0)

    def test_zero_threshold_treated_as_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig(
                notification_min_confidence=0.6,
                notification_context_aware_throttle_enabled=True,
                notification_low_activity_ratio_threshold=0.0,
            )
            now = time.time()
            recent = [_FakeEntry("r1", now - 3600)]
            baseline = [_FakeEntry(f"b{i}", now - (8 + i) * 86400) for i in range(20)]
            store = _FakeMemoryStore(recent + baseline)
            result = ga._effective_notification_min_confidence(paths, cfg, store)
            self.assertEqual(result, 0.6)


class TestMaybeDispatchNotificationIntegration(unittest.TestCase):
    def test_default_unaffected_without_memory_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                "数据分析", "理由", [f"e{i}" for i in range(8)],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            report = ga.generate_growth_report(paths, cand)
            cfg = GrowthAdvisorConfig(notification_min_confidence=0.5)
            result = ga._maybe_dispatch_notification(paths, cfg, {cand.candidate_id: cand}, [report])
            self.assertIsNotNone(result)

    def test_quiet_period_can_suppress_borderline_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            # 证据数刚好卡在中等置信度（不是满分）
            cand = backlog.add_or_merge(
                "数据分析", "理由", [f"e{i}" for i in range(4)],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            report = ga.generate_growth_report(paths, cand)
            cfg = GrowthAdvisorConfig(
                notification_min_confidence=cand.confidence,  # 刚好等于置信度，未开启情境感知时应通过
                notification_context_aware_throttle_enabled=True,
                notification_low_activity_ratio_threshold=0.3,
                notification_low_activity_confidence_boost=0.5,
            )
            now = time.time()
            recent = [_FakeEntry("r1", now - 3600)]
            baseline = [_FakeEntry(f"b{i}", now - (8 + i) * 86400) for i in range(20)]
            store = _FakeMemoryStore(recent + baseline)
            result = ga._maybe_dispatch_notification(
                paths, cfg, {cand.candidate_id: cand}, [report], memory_store=store,
            )
            self.assertIsNone(result)  # 门槛被软性抬高后，这条中等置信度的报告被过滤


if __name__ == "__main__":
    unittest.main()
