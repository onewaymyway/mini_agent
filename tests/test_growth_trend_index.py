"""tests/test_growth_trend_index.py — 趋势索引（方案 A）测试
（对应 next_doc/growth_tab_split_and_trend_index_plan.md §5 / §8.1）。

覆盖：
  1. load_topic_trend_index 与原 _topic_trend_series 在随机数据上逐 key 等价
     （含 limit 截断、乱序行、同 scanned_at）
  2. 畸形行被跳过，不影响其它 key
  3. N+1 回归：growth_topic_map / pending_followups / reports_needing_refresh /
     diagnostics_snapshot 对趋势文件的读取次数与话题数无关（核心断言用调用
     次数而不是耗时，避免时间类测试不稳定）
  4. 传入 trend_index 与不传时输出一致
  5. monthly_retrospective_summary(include_topic_map=False) 无 topic_map
"""

from __future__ import annotations

import random
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from mini_agent.config.models import GrowthAdvisorConfig
from mini_agent.evolution import growth_advisor as ga
from mini_agent.profile import UserProfile
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _trend_row(key, ts, count, conf=None):
    return {"dedupe_key": key, "topic": key, "scanned_at": ts, "evidence_count": count, "confidence": conf}


@contextmanager
def _count_trend_reads(paths):
    """统计 `_read_jsonl` 对趋势文件的调用次数（其它文件放行）。"""
    trend_path = paths.growth_topic_trend_path
    real = ga._read_jsonl
    counter = {"n": 0}

    def wrapper(path):
        if Path(path) == Path(trend_path):
            counter["n"] += 1
        return real(path)

    with mock.patch.object(ga, "_read_jsonl", wrapper):
        yield counter


def _seed_topics(paths, n_topics: int, *, points_per_topic: int = 6, seed: int = 7):
    """造 n 个"已采纳、已到回访窗口、有报告且证据显著增长"的候选 + 趋势快照，
    让 pending_followups / reports_needing_refresh / growth_topic_map 都真正
    走到趋势读取。"""
    rnd = random.Random(seed)
    backlog = ga.GrowthBacklog(paths)
    now = time.time()
    ids = []
    for i in range(n_topics):
        title = f"主题{i:03d}"
        c = backlog.add_or_merge(
            title, "理由", ["e1", "e2", "e3"],
            min_evidence_count=3, max_pending=10_000, dismissed_cooldown_days=30,
        )
        ga.generate_growth_report(paths, c)
        c = backlog.add_or_merge(
            title, "新理由", [f"e{j}" for j in range(1, 9)],
            min_evidence_count=3, max_pending=10_000, dismissed_cooldown_days=30,
        )
        backlog.set_status(c.candidate_id, ga.STATUS_ACCEPTED)
        ids.append((c.candidate_id, c.dedupe_key()))
    all_c = backlog.load_all()
    for c in all_c:
        c.accepted_at = now - 40 * 86400
    backlog.save_all(all_c)
    for _, key in ids:
        count = 2
        for k in range(points_per_topic):
            count += rnd.choice([0, 1, 2])
            ga._append_jsonl(
                paths.growth_topic_trend_path,
                _trend_row(key, now - (points_per_topic - k) * 86400 * 3, count, rnd.random()),
            )
    return ids


class TestIndexEquivalence(unittest.TestCase):
    def test_index_matches_original_series_per_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            rnd = random.Random(1)
            keys = [f"k{i}" for i in range(12)]
            rows = []
            for key in keys:
                for _ in range(rnd.randint(1, 40)):  # 部分 key 超过 limit=20
                    rows.append(_trend_row(key, 1_000_000 + rnd.randint(0, 50), rnd.randint(0, 30), rnd.random()))
            rnd.shuffle(rows)  # 乱序，且 randint 范围小，必有同 scanned_at
            for r in rows:
                ga._append_jsonl(paths.growth_topic_trend_path, r)

            index = ga.load_topic_trend_index(paths)
            for key in keys:
                for limit in (ga._DEFAULT_TREND_MAX_POINTS, 5, 1, 0, 1000):
                    self.assertEqual(
                        ga._topic_trend_series(paths, key, limit, index=index),
                        ga._topic_trend_series(paths, key, limit),
                        msg=f"key={key} limit={limit}",
                    )
            self.assertEqual(ga._topic_trend_series(paths, "不存在", index=index), [])

    def test_series_from_index_does_not_alias_index_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ga._append_jsonl(paths.growth_topic_trend_path, _trend_row("a", 1.0, 1))
            index = ga.load_topic_trend_index(paths)
            out = ga._topic_trend_series(paths, "a", index=index)
            out[0]["evidence_count"] = 999
            self.assertEqual(index["a"][0]["evidence_count"], 1)

    def test_malformed_rows_skipped_without_affecting_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            f = paths.growth_topic_trend_path
            f.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                '{"dedupe_key": "a", "scanned_at": 1.0, "evidence_count": 1}',
                "not json at all",
                '[1, 2, 3]',
                '{"scanned_at": 2.0, "evidence_count": 5}',                       # 缺 dedupe_key
                '{"dedupe_key": "b", "evidence_count": 5}',                       # 缺 scanned_at
                '{"dedupe_key": "b", "scanned_at": 3.0}',                         # 缺 evidence_count
                '{"dedupe_key": "b", "scanned_at": "oops", "evidence_count": 1}', # scanned_at 非数字
                '{"dedupe_key": "c", "scanned_at": 5.0, "evidence_count": 2, "confidence": 0.5}',
            ]
            f.write_text("\n".join(lines) + "\n", encoding="utf-8")
            index = ga.load_topic_trend_index(paths)
            self.assertEqual(set(index), {"a", "c"})
            self.assertEqual(index["c"], [{"scanned_at": 5.0, "evidence_count": 2, "confidence": 0.5}])

    def test_missing_file_gives_empty_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ga.load_topic_trend_index(_make_paths(tmp)), {})


class TestNoNPlusOne(unittest.TestCase):
    N = 25

    def test_topic_map_reads_trend_file_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _seed_topics(paths, self.N)
            with _count_trend_reads(paths) as cnt:
                rows = ga.growth_topic_map(paths)
            self.assertEqual(len(rows), self.N)
            self.assertEqual(cnt["n"], 1)

    def test_topic_map_with_injected_index_reads_zero_times(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _seed_topics(paths, 5)
            index = ga.load_topic_trend_index(paths)
            with _count_trend_reads(paths) as cnt:
                ga.growth_topic_map(paths, trend_index=index)
            self.assertEqual(cnt["n"], 0)

    def test_pending_followups_reads_trend_file_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _seed_topics(paths, self.N)
            with _count_trend_reads(paths) as cnt:
                ga.pending_followups(paths, GrowthAdvisorConfig(followup_review_days=30))
            self.assertEqual(cnt["n"], 1)

    def test_pending_followups_without_trend_need_reads_zero(self):
        """没有任何到期候选 -> 不需要趋势数据 -> 不读趋势文件。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            with _count_trend_reads(paths) as cnt:
                self.assertEqual(ga.pending_followups(paths, GrowthAdvisorConfig()), [])
            self.assertEqual(cnt["n"], 0)

    def test_reports_needing_refresh_reads_trend_file_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _seed_topics(paths, self.N)
            with _count_trend_reads(paths) as cnt:
                rows = ga.reports_needing_refresh(paths, GrowthAdvisorConfig())
            self.assertEqual(len(rows), self.N)
            self.assertEqual(cnt["n"], 1)

    def test_diagnostics_snapshot_reads_trend_file_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _seed_topics(paths, self.N)
            with _count_trend_reads(paths) as cnt:
                snap = ga.diagnostics_snapshot(paths, GrowthAdvisorConfig(), UserProfile(), None)
            self.assertEqual(cnt["n"], 1)
            self.assertEqual(snap["reports_needing_refresh_count"], self.N)

    def test_read_count_independent_of_topic_count(self):
        counts = []
        for n in (3, 30):
            with tempfile.TemporaryDirectory() as tmp:
                paths = _make_paths(tmp)
                _seed_topics(paths, n)
                with _count_trend_reads(paths) as cnt:
                    ga.growth_topic_map(paths)
                    ga.pending_followups(paths, GrowthAdvisorConfig())
                    ga.reports_needing_refresh(paths, GrowthAdvisorConfig())
                counts.append(cnt["n"])
        self.assertEqual(counts[0], counts[1])  # 都是 3（每个函数各 1 次），与话题数无关


class TestIndexedOutputEquality(unittest.TestCase):
    def test_outputs_same_with_and_without_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _seed_topics(paths, 15)
            cfg = GrowthAdvisorConfig(followup_review_days=30)
            index = ga.load_topic_trend_index(paths)

            self.assertEqual(ga.growth_topic_map(paths), ga.growth_topic_map(paths, trend_index=index))
            self.assertEqual(
                [c.candidate_id for c in ga.pending_followups(paths, cfg)],
                [c.candidate_id for c in ga.pending_followups(paths, cfg, trend_index=index)],
            )
            self.assertEqual(
                ga.reports_needing_refresh(paths, cfg), ga.reports_needing_refresh(paths, cfg, trend_index=index)
            )
            cands = ga.GrowthBacklog(paths).load_all()
            for c in cands[:5]:
                self.assertEqual(
                    ga.followup_question_hint(paths, c, cfg=cfg),
                    ga.followup_question_hint(paths, c, cfg=cfg, trend_index=index),
                )

    def test_rising_and_delta_helpers_equal_with_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            for off, cnt in [(20 * 86400, 2), (10 * 86400, 5), (0, 9)]:
                ga._append_jsonl(paths.growth_topic_trend_path, _trend_row("x", now - off, cnt))
            index = ga.load_topic_trend_index(paths)
            self.assertIs(ga._topic_trend_rising(paths, "x", window_days=30, index=index), True)
            self.assertEqual(
                ga._recent_evidence_delta(paths, "x", window_days=14),
                ga._recent_evidence_delta(paths, "x", window_days=14, index=index),
            )
            self.assertIsNone(ga._topic_trend_rising(paths, "none", window_days=30, index=index))


class TestRetrospectiveIncludeTopicMap(unittest.TestCase):
    def test_include_topic_map_false_drops_only_topic_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _seed_topics(paths, 4)
            full = ga.monthly_retrospective_summary(paths)
            slim = ga.monthly_retrospective_summary(paths, include_topic_map=False)
            self.assertIn("topic_map", full)
            self.assertNotIn("topic_map", slim)
            full_wo = {k: v for k, v in full.items() if k != "topic_map"}
            self.assertEqual(full_wo, slim)

    def test_slim_summary_does_not_read_trend_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _seed_topics(paths, 4)
            with _count_trend_reads(paths) as cnt:
                ga.monthly_retrospective_summary(paths, include_topic_map=False)
            self.assertEqual(cnt["n"], 0)

    def test_trend_index_passthrough_to_topic_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _seed_topics(paths, 4)
            index = ga.load_topic_trend_index(paths)
            with _count_trend_reads(paths) as cnt:
                res = ga.monthly_retrospective_summary(paths, trend_index=index)
            self.assertEqual(cnt["n"], 0)
            self.assertEqual(len(res["topic_map"]), 4)


if __name__ == "__main__":
    unittest.main()
