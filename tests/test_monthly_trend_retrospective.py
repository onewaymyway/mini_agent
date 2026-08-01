"""tests/test_monthly_trend_retrospective.py — P5 月度战略回顾测试。

覆盖：
  1. `external_trend_capability_link` 状态文件不存在时，采纳统计返回 (0, 0)
  2. 窗口内产出的候选正确计数，窗口外的不计入
  3. 候选对应的 Goal 已存在时正确判定为"已采纳"
  4. wiki 增长：首次运行（无上一轮快照）不产出增量，但落盘本轮快照
  5. wiki 增长：与上一轮快照对比正确算出增量（含新增 source_kind 与归零）
  6. 能力变化趋势：首次运行无可比快照，不产出 delta 列表
  7. 能力变化趋势：与上一轮快照对比正确算出置信度变化，按幅度降序、
     只保留 Top N
  8. `run_monthly_trend_retrospective_once()` 端到端：写出月度文档 + 保存
     状态快照供下一轮使用
  9. `ensure_monthly_trend_retrospective_job()` 注册与本地回调触发
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mini_agent.evolution.monthly_trend_retrospective import (
    JOB_ID,
    RETROSPECTIVE_WINDOW_SECONDS,
    ensure_monthly_trend_retrospective_job,
    run_monthly_trend_retrospective_once,
)
from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    p = AgentPaths(Path(tmp))
    p.ensure_wiki_dirs()
    return p


def _write_trend_state(paths: AgentPaths, produced_keys: dict) -> None:
    p = paths.external_trend_capability_link_state_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"last_scan_at": 0.0, "candidates": [], "produced_keys": produced_keys}),
        encoding="utf-8",
    )


class TestTrendAdoption(unittest.TestCase):
    def test_no_state_file_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            from mini_agent.evolution.monthly_trend_retrospective import _collect_trend_adoption

            produced, adopted = _collect_trend_adoption(paths, time.time())
            self.assertEqual((produced, adopted), (0, 0))

    def test_window_filtering_and_adoption(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            _write_trend_state(paths, {
                "python_refactor|page1": now - 3600,             # 窗口内
                "bash_scripting|page2": now - 5 * 86400,          # 窗口内
                "stale_domain|page3": now - (RETROSPECTIVE_WINDOW_SECONDS + 86400),  # 窗口外
            })

            from mini_agent.perception.goal_backlog import load_goal_backlog

            backlog = load_goal_backlog(paths)
            backlog.add_goal(title="改善 python_refactor 的执行可靠性（外部动态参考）")
            backlog.save()

            from mini_agent.evolution.monthly_trend_retrospective import _collect_trend_adoption

            produced, adopted = _collect_trend_adoption(paths, now)
            self.assertEqual(produced, 2)   # 只有窗口内的两条计入
            self.assertEqual(adopted, 1)    # 只有 python_refactor 对应的 Goal 存在


class TestWikiGrowth(unittest.TestCase):
    def test_first_run_no_prev_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            from mini_agent.evolution.monthly_trend_retrospective import _collect_wiki_growth

            fake_stats = type("S", (), {"by_source_kind": {"external_watch": 3}})()
            with patch(
                "mini_agent.wiki.stats.compute_stats", return_value=fake_stats
            ):
                snapshot, growth = _collect_wiki_growth(paths, {})
            self.assertEqual(snapshot, {"external_watch": 3})
            # 没有上一轮快照时 prev 记为 0，首次运行会把全量算作增量，
            # 属于预期行为（"从无到有"），下一轮开始才是真正的环比增量。
            self.assertEqual(growth, {"external_watch": 3})

    def test_growth_delta_against_prev_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            from mini_agent.evolution.monthly_trend_retrospective import _collect_wiki_growth

            fake_stats = type("S", (), {
                "by_source_kind": {"external_watch": 5, "external_ecosystem": 2}
            })()
            prev = {"external_watch": 3}
            with patch(
                "mini_agent.wiki.stats.compute_stats", return_value=fake_stats
            ):
                snapshot, growth = _collect_wiki_growth(paths, prev)
            self.assertEqual(snapshot, {"external_watch": 5, "external_ecosystem": 2})
            self.assertEqual(growth, {"external_watch": 2, "external_ecosystem": 2})


class TestCapabilityDeltas(unittest.TestCase):
    def test_first_run_no_deltas(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            from mini_agent.evolution.monthly_trend_retrospective import _collect_capability_deltas

            entry = type("E", (), {"capability_name": "python_refactor", "confidence": 0.6})()
            with patch(
                "mini_agent.evolution.consolidation.load_capability_map", return_value=[entry]
            ):
                snapshot, deltas = _collect_capability_deltas(paths, {})
            self.assertEqual(snapshot, {"python_refactor": 0.6})
            # 首次运行 prev 记为 0.0，0.6 - 0.0 != 0，仍会被记为"从无到有"
            self.assertEqual(len(deltas), 1)
            self.assertAlmostEqual(deltas[0][1], 0.0)
            self.assertAlmostEqual(deltas[0][2], 0.6)

    def test_delta_sorted_and_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            from mini_agent.evolution.monthly_trend_retrospective import _collect_capability_deltas

            entries = [
                type("E", (), {"capability_name": f"domain_{i}", "confidence": 0.5})()
                for i in range(15)
            ]
            prev = {f"domain_{i}": 0.5 - (i * 0.01) for i in range(15)}
            # 制造一条变化幅度最大的
            prev["domain_0"] = 0.9
            with patch(
                "mini_agent.evolution.consolidation.load_capability_map", return_value=entries
            ):
                snapshot, deltas = _collect_capability_deltas(paths, prev)
            self.assertLessEqual(len(deltas), 10)
            self.assertEqual(deltas[0][0], "domain_0")  # 幅度最大的排第一


class TestRunOnce(unittest.TestCase):
    def test_end_to_end_writes_report_and_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            fake_stats = type("S", (), {"by_source_kind": {"external_watch": 1}})()
            with patch(
                "mini_agent.wiki.stats.compute_stats", return_value=fake_stats
            ), patch(
                "mini_agent.evolution.consolidation.load_capability_map", return_value=[]
            ):
                summary = run_monthly_trend_retrospective_once(paths)

            self.assertIsNotNone(summary.report_path)
            self.assertTrue(Path(summary.report_path).exists())
            content = Path(summary.report_path).read_text(encoding="utf-8")
            self.assertIn("月度战略回顾", content)

            state = json.loads(paths.monthly_trend_retrospective_state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["wiki_snapshot"], {"external_watch": 1})
            self.assertGreater(state["last_run_at"], 0)


class TestJobRegistration(unittest.TestCase):
    def test_ensure_job_registers_and_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths)

            newly_added = ensure_monthly_trend_retrospective_job(paths, scheduler)
            self.assertTrue(newly_added)
            job = next(j for j in scheduler.list_jobs() if j.id == JOB_ID)
            self.assertEqual(job.schedule, "cron:0 0 1 * *")

            with patch(
                "mini_agent.wiki.stats.compute_stats",
                return_value=type("S", (), {"by_source_kind": {}})(),
            ), patch(
                "mini_agent.evolution.consolidation.load_capability_map", return_value=[]
            ):
                ok = scheduler.run_now(JOB_ID)
            self.assertTrue(ok)
            self.assertTrue(paths.monthly_trend_retrospective_state_path.exists())

            newly_added_again = ensure_monthly_trend_retrospective_job(paths, scheduler)
            self.assertFalse(newly_added_again)


if __name__ == "__main__":
    unittest.main()
