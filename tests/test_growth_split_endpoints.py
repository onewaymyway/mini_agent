"""tests/test_growth_split_endpoints.py — 成长顾问概览拆分端点测试
（对应 next_doc/growth_tab_split_and_trend_index_plan.md §6.1/6.2 与 §8.2）。

覆盖：
  1. overview + diagnostics + topic_map 拼起来与 /growth/summary 逐字段一致
  2. overview 不含 topic_map 与 diagnostics
  3. diagnostics 超时降级为占位，不影响其它端点（where 互相独立）
  4. refresh_diagnostics 透传
  5. /growth/summary 整个请求趋势文件只读 1 次
  6. /growth/followups 仍可用且提示语共用趋势索引
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.config.loader import load_config
from mini_agent.evolution import growth_advisor as ga
from mini_agent.storage.paths import AgentPaths

# 复用趋势索引测试里的造数据工具
try:
    from tests.test_growth_trend_index import _seed_topics  # noqa: E402
except ImportError:  # 以 unittest / 直接运行 tests 目录时
    from test_growth_trend_index import _seed_topics  # noqa: E402


def _strip_volatile(d):
    """去掉依赖当前时间/缓存的字段，便于两次独立请求之间比较。"""
    if isinstance(d, dict):
        return {k: _strip_volatile(v) for k, v in d.items() if k != "backfill_candidates_count_computed_at"}
    if isinstance(d, list):
        return [_strip_volatile(x) for x in d]
    return d


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "agent_config.json").write_text("{}", encoding="utf-8")
        self.cfg = load_config(config_file=self.root / "agent_config.json", project_root=self.root)
        self.paths = AgentPaths(self.root)
        app = FastAPI()
        app.include_router(router)
        bridge = SimpleNamespace(agent=SimpleNamespace(cfg=self.cfg, llm_helper=None))
        app.state.http_server = SimpleNamespace(bridge=bridge, autonomous_loop=None)
        self.client = TestClient(app)

    def tearDown(self):
        self._tmp.cleanup()


class TestSplitMatchesSummary(_Base):
    def test_parts_equal_summary(self):
        _seed_topics(self.paths, 6)
        summary = self.client.get("/v1/growth/summary").json()
        overview = self.client.get("/v1/growth/overview").json()
        diagnostics = self.client.get("/v1/growth/diagnostics").json()
        topic_map = self.client.get("/v1/growth/topic_map").json()

        self.assertEqual(set(summary), {"candidates", "reports", "retrospective", "first_touch_notice_shown", "diagnostics"})
        self.assertEqual(summary["candidates"], overview["candidates"])
        self.assertEqual(summary["reports"], overview["reports"])
        self.assertEqual(summary["first_touch_notice_shown"], overview["first_touch_notice_shown"])
        self.assertEqual(_strip_volatile(summary["diagnostics"]), _strip_volatile(diagnostics))
        self.assertEqual(summary["retrospective"].get("topic_map"), topic_map["topic_map"])
        retro_wo = {k: v for k, v in summary["retrospective"].items() if k != "topic_map"}
        self.assertEqual(retro_wo, overview["retrospective"])
        self.assertEqual(len(topic_map["topic_map"]), 6)

    def test_summary_matches_pre_split_functions(self):
        """与直接调用 growth_advisor（拆分前的口径）一致。"""
        _seed_topics(self.paths, 4)
        summary = self.client.get("/v1/growth/summary").json()
        expect = ga.monthly_retrospective_summary(self.paths)
        self.assertEqual(summary["retrospective"]["topic_map"], expect["topic_map"])
        self.assertEqual(summary["retrospective"]["total_candidates"], expect["total_candidates"])

    def test_empty_data(self):
        summary = self.client.get("/v1/growth/summary").json()
        self.assertEqual(summary["candidates"], [])
        self.assertEqual(summary["retrospective"]["topic_map"], [])


class TestOverviewShape(_Base):
    def test_overview_has_no_topic_map_or_diagnostics(self):
        _seed_topics(self.paths, 3)
        body = self.client.get("/v1/growth/overview").json()
        self.assertEqual(set(body), {"candidates", "reports", "retrospective", "first_touch_notice_shown"})
        self.assertNotIn("topic_map", body["retrospective"])
        self.assertNotIn("diagnostics", body)

    def test_overview_does_not_read_trend_file(self):
        _seed_topics(self.paths, 3)
        real = ga._read_jsonl
        n = {"c": 0}

        def wrapper(path):
            if Path(path) == Path(self.paths.growth_topic_trend_path):
                n["c"] += 1
            return real(path)

        with mock.patch.object(ga, "_read_jsonl", wrapper):
            self.assertEqual(self.client.get("/v1/growth/overview").status_code, 200)
        self.assertEqual(n["c"], 0)


class TestDegradationAndPassthrough(_Base):
    def test_diagnostics_timeout_gives_placeholder_others_unaffected(self):
        _seed_topics(self.paths, 3)
        self.cfg.http.blocking_call_timeout_seconds = 0.3

        def slow(*a, **k):
            time.sleep(1.0)
            return {}

        with mock.patch.object(ga, "diagnostics_snapshot", slow):
            diag = self.client.get("/v1/growth/diagnostics")
            self.assertEqual(diag.status_code, 200)
            self.assertIn("_note", diag.json())
            self.assertIn("cron_jobs", diag.json())
            # 其它板块不受影响（where 独立，熔断不串）
            self.assertEqual(self.client.get("/v1/growth/overview").status_code, 200)
            tm = self.client.get("/v1/growth/topic_map").json()
            self.assertEqual(len(tm["topic_map"]), 3)
            self.assertNotIn("_note", tm)
            # summary 同样降级而不是 500
            summ = self.client.get("/v1/growth/summary")
            self.assertEqual(summ.status_code, 200)
            self.assertIn("_note", summ.json()["diagnostics"])
            self.assertEqual(len(summ.json()["candidates"]), 3)

    def test_topic_map_timeout_gives_empty_with_note(self):
        _seed_topics(self.paths, 2)
        self.cfg.http.blocking_call_timeout_seconds = 0.3

        def slow(*a, **k):
            time.sleep(1.0)
            return []

        with mock.patch.object(ga, "growth_topic_map", slow):
            tm = self.client.get("/v1/growth/topic_map").json()
            self.assertEqual(tm["topic_map"], [])
            self.assertIn("_note", tm)
            self.assertEqual(self.client.get("/v1/growth/overview").status_code, 200)

    def test_refresh_diagnostics_passthrough(self):
        seen = []

        def spy(*a, **k):
            seen.append(k.get("force_refresh_backfill_count"))
            return {}

        with mock.patch.object(ga, "diagnostics_snapshot", spy):
            self.client.get("/v1/growth/diagnostics")
            self.client.get("/v1/growth/diagnostics?refresh_diagnostics=true")
            self.client.get("/v1/growth/summary?refresh_diagnostics=true")
        self.assertEqual(seen, [False, True, True])


class TestSummaryReadsTrendOnce(_Base):
    def test_summary_reads_trend_file_once(self):
        _seed_topics(self.paths, 12)
        real = ga._read_jsonl
        n = {"c": 0}

        def wrapper(path):
            if Path(path) == Path(self.paths.growth_topic_trend_path):
                n["c"] += 1
            return real(path)

        with mock.patch.object(ga, "_read_jsonl", wrapper):
            resp = self.client.get("/v1/growth/summary")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(n["c"], 1)


class TestFollowupsRoute(_Base):
    def test_followups_still_works_and_reads_trend_at_most_twice(self):
        ids = _seed_topics(self.paths, 8)
        # 改成走平的趋势（证据数不涨），保证候选都到期、真正出现在回访列表里
        self.paths.growth_topic_trend_path.write_text("", encoding="utf-8")
        now = time.time()
        for _, key in ids:
            for k in range(3):
                ga._append_jsonl(
                    self.paths.growth_topic_trend_path,
                    {"dedupe_key": key, "topic": key, "scanned_at": now - (3 - k) * 86400,
                     "evidence_count": 5, "confidence": None},
                )
        real = ga._read_jsonl
        n = {"c": 0}

        def wrapper(path):
            if Path(path) == Path(self.paths.growth_topic_trend_path):
                n["c"] += 1
            return real(path)

        with mock.patch.object(ga, "_read_jsonl", wrapper):
            resp = self.client.get("/v1/growth/followups")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["followups"]
        self.assertGreater(len(items), 0)  # 造数据保证有到期候选，否则本用例无意义
        self.assertTrue(all("question_hint" in x for x in items))
        # pending_followups 一次 + 提示语共用索引一次，与候选数量无关
        self.assertLessEqual(n["c"], 2)


if __name__ == "__main__":
    unittest.main()
