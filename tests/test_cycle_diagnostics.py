"""
tests/test_cycle_diagnostics.py — 覆盖
next_doc/goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md Stage 1

  1. build_cycle_diagnostics: Goal 不存在时返回 found=False，不抛异常
  2. 基本字段聚合：cycle_count/recurring/status/progress_notes_tail
  3. mechanism_notes 静态文案随 recurring/phase/spec 状态变化
  4. execution_phase 健康告警透传（check_phase_health 命中时报告里能看到）
  5. _tail_jsonl_records：只读文件尾部，数量正确，边界情况（文件不存在/
     无结尾换行/want 超过总行数）不抛异常
  6. recent_cycle_summaries 热数据 + 冷数据拼接
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.perception import cycle_diagnostics as cd
from mini_agent.perception import execution_phase as ep
from mini_agent.perception.goal_backlog import load_goal_backlog
from mini_agent.storage.paths import AgentPaths


class TestBuildCycleDiagnostics(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.gb = load_goal_backlog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_goal_not_found(self):
        report = cd.build_cycle_diagnostics(self.paths, self.gb, "nope")
        self.assertFalse(report.found)
        self.assertIsNotNone(report.error)

    def test_basic_fields(self):
        node = self.gb.add_goal("My recurring goal", source="user")
        self.gb.update_fields(node.id, recurring=True, cycle_count=5)
        self.gb.append_progress_note(node.id, "cycle 5 done")

        report = cd.build_cycle_diagnostics(self.paths, self.gb, node.id)
        self.assertTrue(report.found)
        self.assertEqual(report.goal_id, node.id)
        self.assertEqual(report.goal_title, "My recurring goal")
        self.assertTrue(report.recurring)
        self.assertEqual(report.cycle_count, 5)
        self.assertIn("cycle 5 done", report.progress_notes_tail)
        # mechanism_notes 是静态文本，不为空，且没有异常导致报告整体失败
        self.assertTrue(report.mechanism_notes)

    def test_mechanism_notes_reflect_recurring_flag(self):
        recurring_node = self.gb.add_goal("Recurring", source="user")
        self.gb.update_fields(recurring_node.id, recurring=True)
        report = cd.build_cycle_diagnostics(self.paths, self.gb, recurring_node.id)
        joined = "\n".join(report.mechanism_notes)
        self.assertIn("cycle_%04d", joined)

        oneoff_node = self.gb.add_goal("One-off", source="user")
        report2 = cd.build_cycle_diagnostics(self.paths, self.gb, oneoff_node.id)
        joined2 = "\n".join(report2.mechanism_notes)
        self.assertIn("run_%04d", joined2)

    def test_execution_phase_locked_mode_surfaces_in_notes_and_fields(self):
        node = self.gb.add_goal("Phase test", source="user")
        ep.set_mode(self.paths, node.id, "running", reason="test")

        report = cd.build_cycle_diagnostics(self.paths, self.gb, node.id)
        self.assertEqual(report.execution_phase_mode, "running")
        self.assertTrue(report.execution_phase_locked)
        joined = "\n".join(report.mechanism_notes)
        self.assertIn("running", joined)

    def test_health_alert_surfaced_when_stuck_in_explore(self):
        node = self.gb.add_goal("Stuck goal", source="user")
        state = ep.load_phase(self.paths, node.id)
        state.mode = "auto"
        state.cycles_in_mode = 10  # >= DEFAULT_STUCK_EXPLORE_CYCLES
        ep.save_phase(self.paths, state)

        report = cd.build_cycle_diagnostics(self.paths, self.gb, node.id)
        self.assertTrue(any("explore" in a["message"] for a in report.recent_health_alerts))

    def test_output_dir_field_is_populated(self):
        node = self.gb.add_goal("Output dir test", source="user")
        report = cd.build_cycle_diagnostics(self.paths, self.gb, node.id)
        self.assertIn(node.id, report.output_dir)


class TestTailJsonlRecords(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write_jsonl(self, name: str, n: int, *, trailing_newline: bool = True) -> Path:
        p = self.dir / name
        lines = [json.dumps({"id": f"rec_{i}"}) for i in range(n)]
        text = "\n".join(lines)
        if trailing_newline:
            text += "\n"
        p.write_text(text, encoding="utf-8")
        return p

    def test_missing_file_returns_empty(self):
        self.assertEqual(cd._tail_jsonl_records(self.dir / "missing.jsonl", 5), [])

    def test_want_zero_returns_empty(self):
        p = self._write_jsonl("a.jsonl", 10)
        self.assertEqual(cd._tail_jsonl_records(p, 0), [])

    def test_reads_last_n_records_in_order(self):
        p = self._write_jsonl("b.jsonl", 50)
        recs = cd._tail_jsonl_records(p, 5)
        self.assertEqual([r["id"] for r in recs], [f"rec_{i}" for i in range(45, 50)])

    def test_want_exceeds_total_returns_all(self):
        p = self._write_jsonl("c.jsonl", 10)
        recs = cd._tail_jsonl_records(p, 100)
        self.assertEqual(len(recs), 10)
        self.assertEqual(recs[0]["id"], "rec_0")
        self.assertEqual(recs[-1]["id"], "rec_9")

    def test_no_trailing_newline(self):
        p = self._write_jsonl("d.jsonl", 10, trailing_newline=False)
        recs = cd._tail_jsonl_records(p, 3)
        self.assertEqual([r["id"] for r in recs], ["rec_7", "rec_8", "rec_9"])

    def test_corrupted_line_is_skipped(self):
        p = self.dir / "e.jsonl"
        p.write_text('{"id": "ok1"}\nnot json\n{"id": "ok2"}\n', encoding="utf-8")
        recs = cd._tail_jsonl_records(p, 10)
        self.assertEqual([r["id"] for r in recs], ["ok1", "ok2"])


class TestRecentCycleSummariesHotAndCold(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.gb = load_goal_backlog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_falls_back_to_archive_when_hot_data_insufficient(self):
        node = self.gb.add_goal("Archived goal", source="user")
        self.gb.update_fields(node.id, recurring=True)

        archive_path = self.paths.workdir_dir / "goal_cycle_archive.jsonl"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with open(archive_path, "a", encoding="utf-8") as f:
            for i in range(3):
                f.write(json.dumps({
                    "id": f"archived_child_{i}",
                    "status": "completed",
                    "progress_notes": f"archived cycle {i}",
                    "last_touched_at": time.time(),
                }) + "\n")

        report = cd.build_cycle_diagnostics(self.paths, self.gb, node.id, recent_n=5)
        archived_entries = [s for s in report.recent_cycle_summaries if s.get("archived")]
        self.assertEqual(len(archived_entries), 3)


class TestSummarizeReportWithLLM(unittest.TestCase):
    """Stage 3（可选）：summarize_report_with_llm 的失败回退与正常路径。"""

    def _report(self, found=True):
        return cd.CycleDiagnosticsReport(
            goal_id="g1", goal_title="Test Goal", found=found,
            recurring=True, schedule="interval:3600", cycle_count=3,
            execution_phase_mode="running",
        )

    def test_llm_ask_none_returns_none(self):
        self.assertIsNone(cd.summarize_report_with_llm(self._report(), None))

    def test_goal_not_found_returns_none_even_with_llm(self):
        called = []
        def fake_ask(prompt):
            called.append(prompt)
            return "should not be reached"
        result = cd.summarize_report_with_llm(self._report(found=False), fake_ask)
        self.assertIsNone(result)
        self.assertEqual(called, [])

    def test_llm_exception_falls_back_to_none(self):
        def broken_ask(prompt):
            raise RuntimeError("llm unavailable")
        self.assertIsNone(cd.summarize_report_with_llm(self._report(), broken_ask))

    def test_llm_empty_text_falls_back_to_none(self):
        self.assertIsNone(cd.summarize_report_with_llm(self._report(), lambda p: "   "))

    def test_llm_success_returns_stripped_text(self):
        def fake_ask(prompt):
            self.assertIn("Test Goal", prompt)
            self.assertIn("running", prompt)
            return "  这个 Goal 目前进展平稳。  "
        result = cd.summarize_report_with_llm(self._report(), fake_ask)
        self.assertEqual(result, "这个 Goal 目前进展平稳。")


if __name__ == "__main__":
    unittest.main()
