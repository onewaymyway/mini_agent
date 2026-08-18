"""tests/test_growth_diagnostics_backfill_count_cache.py

覆盖 next_doc/growth_diagnostics_backfill_count_cache_plan.md：
诊断快照里 backfill_candidates_count 加了一层进程内 TTL 缓存，避免
每次 diagnostics_snapshot() 都触发一次全量 session 扫描（曾在 session
数量多时导致 growth_diagnostics_snapshot 的 45s blocking_guard 超时）；
force_refresh_backfill_count=True 时绕过缓存拿真实最新值。
"""
from __future__ import annotations

import tempfile
import time
import unittest

from mini_agent.evolution import growth_advisor as ga
from mini_agent.config.models import GrowthAdvisorConfig
from mini_agent.profile import UserProfile
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=tmp)


def _save_backfillable_session(paths):
    from mini_agent.session import SessionManager

    sm = SessionManager(project_root=paths.project_root)
    session = sm.new_session(provider="test-provider", model="test-model")
    history = [
        {"role": "user", "content": "帮我实现一个功能"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "再补充一点细节"},
        {"role": "assistant", "content": "收到"},
    ]
    stats = {"turns": 4, "input_tokens": 0, "output_tokens": 0, "tool_calls": 0}
    sm.save(session, history=history, stats=stats)  # summary 留空 -> 应被回填扫描命中


class TestBackfillCountCache(unittest.TestCase):
    def setUp(self):
        # 每个用例用独立 tmp project_root，cache key 天然隔离，
        # 不需要手动清理模块级缓存字典。
        self._tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = self._tmp_ctx.name
        self.addCleanup(self._tmp_ctx.cleanup)
        self.paths = _make_paths(self.tmp)
        self.cfg = GrowthAdvisorConfig()
        self.profile = UserProfile()

    def test_second_call_within_ttl_reuses_cached_count(self):
        snap1 = ga.diagnostics_snapshot(self.paths, self.cfg, self.profile, None)
        self.assertEqual(snap1["memory"]["backfill_candidates_count"], 0)

        # 缓存写入之后才新增待回填 session——不强制刷新时应该仍然读到
        # 缓存里的旧值（0），而不是重新扫描出的新值。
        _save_backfillable_session(self.paths)
        snap2 = ga.diagnostics_snapshot(self.paths, self.cfg, self.profile, None)
        self.assertEqual(snap2["memory"]["backfill_candidates_count"], 0)

    def test_force_refresh_bypasses_cache(self):
        ga.diagnostics_snapshot(self.paths, self.cfg, self.profile, None)
        _save_backfillable_session(self.paths)

        snap = ga.diagnostics_snapshot(
            self.paths, self.cfg, self.profile, None,
            force_refresh_backfill_count=True,
        )
        self.assertGreaterEqual(snap["memory"]["backfill_candidates_count"], 1)

    def test_computed_at_timestamp_present_and_recent(self):
        before = time.time()
        snap = ga.diagnostics_snapshot(self.paths, self.cfg, self.profile, None)
        after = time.time()
        computed_at = snap["memory"]["backfill_candidates_count_computed_at"]
        self.assertIsNotNone(computed_at)
        self.assertGreaterEqual(computed_at, before)
        self.assertLessEqual(computed_at, after)

    def test_force_refresh_updates_cache_for_subsequent_normal_calls(self):
        """强制刷新之后，紧接着的普通（非强制）调用应该复用刚刚刷新出的
        新值——对应看板"点刷新按钮 → rerun → 普通拉取"这条真实交互路径。"""
        ga.diagnostics_snapshot(self.paths, self.cfg, self.profile, None)
        _save_backfillable_session(self.paths)

        ga.diagnostics_snapshot(
            self.paths, self.cfg, self.profile, None,
            force_refresh_backfill_count=True,
        )
        snap_after = ga.diagnostics_snapshot(self.paths, self.cfg, self.profile, None)
        self.assertGreaterEqual(snap_after["memory"]["backfill_candidates_count"], 1)

    def test_different_project_roots_have_independent_cache_entries(self):
        with tempfile.TemporaryDirectory() as tmp2:
            paths2 = _make_paths(tmp2)
            _save_backfillable_session(paths2)

            snap1 = ga.diagnostics_snapshot(self.paths, self.cfg, self.profile, None)
            snap2 = ga.diagnostics_snapshot(paths2, self.cfg, self.profile, None)
            self.assertEqual(snap1["memory"]["backfill_candidates_count"], 0)
            self.assertGreaterEqual(snap2["memory"]["backfill_candidates_count"], 1)


if __name__ == "__main__":
    unittest.main()
