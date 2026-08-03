"""tests/test_correction_writer.py — 用户纠正事件回灌通道（F4）专属单测。

补齐 next_doc/system_connectivity_gaps_and_missing_capabilities_plan.md
P0 建议里指出的技术债：此前该模块只跑通了周边集成测试
（test_correction_detector.py / test_context_builder_*），自己没有专属
单测。

覆盖：
  1. page_id 为空/None → 直接返回 routed=False, reason="no_page_id"，
     不落盘任何记录（调用方应回退到 lesson memory 路径）
  2. 命中真实存在的页面 → mark_page_state 成功、routed=True，正文追加
     纠正记录，correction_events.jsonl 有对应记录
  3. page_id 指向不存在的页面 → mark_page_state 内部失败 → routed=False,
     reason="mark_failed"，但仍然记录一条 marked_stale=False 的事件
     （"如实记录"，不是静默丢弃）
  4. correction_text 超长 → 落盘时按 MAX_CORRECTION_TEXT_CHARS 截断
  5. recent_correction_events：文件不存在 → 返回空列表，不报错
  6. recent_correction_events：日志内容损坏（非 JSON 行）不崩溃，跳过
  7. recent_correction_events：limit 参数生效，只返回最近 N 条
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.correction_writer import (
    MAX_CORRECTION_TEXT_CHARS,
    recent_correction_events,
    route_correction,
)
from mini_agent.wiki.writer import write_page


def _make_paths(tmp: str) -> AgentPaths:
    p = AgentPaths(project_root=Path(tmp))
    p.ensure_wiki_dirs()
    return p


class TestRouteCorrection(unittest.TestCase):
    def test_no_page_id_returns_false_and_no_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            result = route_correction(paths, None, "这个决策理由不对")
            self.assertFalse(result.routed)
            self.assertEqual(result.reason, "no_page_id")
            log_path = paths.wiki_dir / "correction_events.jsonl"
            self.assertFalse(log_path.exists())

    def test_empty_string_page_id_also_treated_as_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            result = route_correction(paths, "", "纠正内容")
            self.assertFalse(result.routed)
            self.assertEqual(result.reason, "no_page_id")

    def test_hits_existing_page_marks_stale_and_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            write_page(
                paths,
                page_id="decision_use_a",
                page_type="decision",
                body="我们选择方案 A，因为成本更低。",
                status="active",
            )
            result = route_correction(
                paths, "decision_use_a", "其实上次选 A 是因为兼容性，不是成本",
            )
            self.assertTrue(result.routed)
            self.assertEqual(result.page_id, "decision_use_a")

            events = recent_correction_events(paths)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["page_id"], "decision_use_a")
            self.assertTrue(events[0]["marked_stale"])

    def test_missing_page_marks_failed_but_still_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            result = route_correction(paths, "page_does_not_exist", "纠正内容")
            self.assertFalse(result.routed)
            self.assertEqual(result.reason, "mark_failed")

            events = recent_correction_events(paths)
            self.assertEqual(len(events), 1)
            self.assertFalse(events[0]["marked_stale"])

    def test_correction_text_is_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            write_page(
                paths, page_id="decision_x", page_type="decision", body="内容",
            )
            long_text = "纠" * (MAX_CORRECTION_TEXT_CHARS + 100)
            route_correction(paths, "decision_x", long_text)

            events = recent_correction_events(paths)
            self.assertEqual(len(events[0]["correction_text"]), MAX_CORRECTION_TEXT_CHARS)


class TestRecentCorrectionEvents(unittest.TestCase):
    def test_missing_file_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(recent_correction_events(paths), [])

    def test_corrupted_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            log_path = paths.wiki_dir / "correction_events.jsonl"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps({"page_id": "p1"}, ensure_ascii=False) + "\n")
                f.write("{this is not json\n")
                f.write(json.dumps({"page_id": "p2"}, ensure_ascii=False) + "\n")

            events = recent_correction_events(paths)
            self.assertEqual(len(events), 2)
            self.assertEqual([e["page_id"] for e in events], ["p1", "p2"])

    def test_limit_returns_only_most_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            write_page(paths, page_id="decision_y", page_type="decision", body="内容")
            for i in range(5):
                route_correction(paths, "decision_y", f"纠正 {i}")

            events = recent_correction_events(paths, limit=2)
            self.assertEqual(len(events), 2)
            self.assertIn("纠正 3", events[0]["correction_text"])
            self.assertIn("纠正 4", events[1]["correction_text"])


if __name__ == "__main__":
    unittest.main()
