"""tests/test_wiki_utility_audit.py — P2 wiki 利用率审计测试。

覆盖：
  1. `wiki_shelf_search()` 命中结果会追加一条记录到 usage_log.jsonl
     （规则/图扩展阶段，无 llm_call 也要记录）
  2. 无命中（rule_hits 为空）时不追加记录
  3. `run_wiki_utility_audit_once()`：窗口内命中正确聚合 hit_count/
     grounded_count/last_used_at
  4. 窗口外的记录不计入统计，但仍保留在（未超过日志保留期的）日志文件里
  5. 超过日志保留期的记录被修剪掉
  6. 日志文件不存在时返回空摘要，不报错
  7. `load_wiki_usage_stats()` 正确读取审计产出
  8. `ensure_wiki_utility_audit_job()` 注册与本地回调触发
"""

from __future__ import annotations

import json
import time
import unittest

from mini_agent.evolution.wiki_utility_audit import (
    JOB_ID,
    AUDIT_WINDOW_SECONDS,
    LOG_RETENTION_SECONDS,
    run_wiki_utility_audit_once,
    load_wiki_usage_stats,
    ensure_wiki_utility_audit_job,
)
from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.search import wiki_shelf_search
from mini_agent.wiki.writer import write_page

import tempfile
from pathlib import Path


def _make_paths(tmp: str) -> AgentPaths:
    p = AgentPaths(Path(tmp))
    p.ensure_wiki_dirs()
    return p


def _write_log(paths: AgentPaths, records: list[dict]) -> None:
    paths.wiki_usage_log_path.parent.mkdir(parents=True, exist_ok=True)
    paths.wiki_usage_log_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


class TestSearchInstrumentation(unittest.TestCase):
    def test_hit_appends_usage_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            write_page(
                paths, page_id="client-pool", page_type="entity",
                body="ClientPool 负责多 LLM provider 的 API key 轮换。",
                tags=["module", "llm"],
            )
            result = wiki_shelf_search(paths, "ClientPool API key", tag_top_n=5, rerank_top_n=5)
            self.assertTrue(result.pages)
            self.assertTrue(paths.wiki_usage_log_path.exists())
            lines = paths.wiki_usage_log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            rec = json.loads(lines[0])
            self.assertIn("client-pool", rec["page_ids"])
            self.assertEqual(rec["stage_reached"], result.stage_reached)

    def test_no_hit_no_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            result = wiki_shelf_search(paths, "完全不存在的主题词零命中", tag_top_n=5, rerank_top_n=5)
            self.assertEqual(result.pages, [])
            self.assertFalse(paths.wiki_usage_log_path.exists())


class TestWikiUtilityAudit(unittest.TestCase):
    def test_missing_log_returns_empty_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            summary = run_wiki_utility_audit_once(paths)
            self.assertEqual(summary.log_lines_scanned, 0)
            self.assertTrue(summary.ok)

    def test_aggregation_within_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            _write_log(paths, [
                {"ts": now - 10, "page_ids": ["a", "b"], "grounded_page_ids": ["a"]},
                {"ts": now - 5, "page_ids": ["a"], "grounded_page_ids": []},
            ])
            summary = run_wiki_utility_audit_once(paths)
            self.assertEqual(summary.pages_with_usage, 2)
            stats = load_wiki_usage_stats(paths)
            self.assertEqual(stats["a"]["hit_count"], 2)
            self.assertEqual(stats["a"]["grounded_count"], 1)
            self.assertEqual(stats["b"]["hit_count"], 1)
            self.assertEqual(stats["b"]["grounded_count"], 0)

    def test_outside_audit_window_not_counted_but_log_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            old_but_kept = now - AUDIT_WINDOW_SECONDS - 3600
            _write_log(paths, [
                {"ts": old_but_kept, "page_ids": ["old-page"], "grounded_page_ids": []},
            ])
            summary = run_wiki_utility_audit_once(paths)
            self.assertEqual(summary.pages_with_usage, 0)
            self.assertEqual(summary.log_lines_kept, 1)
            stats = load_wiki_usage_stats(paths)
            self.assertNotIn("old-page", stats)

    def test_log_trimmed_beyond_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            too_old = now - LOG_RETENTION_SECONDS - 3600
            fresh = now - 10
            _write_log(paths, [
                {"ts": too_old, "page_ids": ["ancient"], "grounded_page_ids": []},
                {"ts": fresh, "page_ids": ["recent"], "grounded_page_ids": []},
            ])
            summary = run_wiki_utility_audit_once(paths)
            self.assertEqual(summary.log_lines_scanned, 2)
            self.assertEqual(summary.log_lines_kept, 1)
            remaining = paths.wiki_usage_log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(remaining), 1)
            self.assertIn("recent", remaining[0])

    def test_ensure_job_registers_and_handler_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_log(paths, [
                {"ts": time.time(), "page_ids": ["x"], "grounded_page_ids": []},
            ])
            scheduler = CronScheduler(paths)
            newly_added = ensure_wiki_utility_audit_job(paths, scheduler)
            self.assertTrue(newly_added)
            job = next(j for j in scheduler.list_jobs() if j.id == JOB_ID)
            self.assertTrue(job.enabled)
            self.assertEqual(job.schedule, "interval:604800")

            ok = scheduler.run_now(JOB_ID)
            self.assertTrue(ok)
            stats = load_wiki_usage_stats(paths)
            self.assertIn("x", stats)

            newly_added_again = ensure_wiki_utility_audit_job(paths, scheduler)
            self.assertFalse(newly_added_again)


if __name__ == "__main__":
    unittest.main()
