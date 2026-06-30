"""
tests/test_lesson_to_reminder.py — [具身改进 B2] Lesson → Reminder 自动闭环测试

覆盖：
  1. human_feedback 来源分组：1 次即激活，写入 reminder_dir 顶层，enabled: true
  2. self_reflection 来源分组：未达 T1 门槛时不生成；达到后写入 drafts/，enabled: false
  3. 工具名提取：trigger 文本中反引号包裹的标识符被正确识别为 condition.tool_name
  4. 幂等性：重复扫描不会重复生成同名文件
  5. promote_draft()：把草稿从 drafts/ 提升到正式目录并翻转 enabled
  6. run_lesson_to_reminder_scan()：MemoryStore 便捷入口
  7. 生成的文件能被真实的 ReminderLoader 正确解析（端到端格式校验）
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.perception.memory_store import MemoryEntry
from mini_agent.evolution.lesson_to_reminder import (
    LessonToReminderBridge,
    promote_draft,
    run_lesson_to_reminder_scan,
)


def _lesson(
    session_id="s1",
    trigger="工具 `bash` 调用时未确认路径就执行了 rm -rf",
    suggested_action="执行 rm -rf 前先用 ls 确认目标路径",
    source="self_reflection",
    occurrence_count=1,
    entry_id=None,
):
    kwargs = dict(
        session_id=session_id,
        summary="",
        key_outcomes=[],
        tags=["lesson"],
        model="test-model",
        entry_type="lesson",
        trigger=trigger,
        outcome="误删了不该删的文件",
        root_cause="",
        suggested_action=suggested_action,
        confidence=0.6,
        occurrence_count=occurrence_count,
        source=source,
    )
    if entry_id:
        kwargs["entry_id"] = entry_id
    return MemoryEntry(**kwargs)


class TestHumanFeedbackActivation(unittest.TestCase):
    def test_single_human_feedback_entry_activates_immediately(self):
        entries = [_lesson(source="human_feedback", occurrence_count=1)]
        with tempfile.TemporaryDirectory() as d:
            bridge = LessonToReminderBridge(Path(d))
            generated = bridge.scan(entries)
            self.assertEqual(len(generated), 1)
            self.assertTrue(generated[0].activated)
            self.assertIn("enabled: true", generated[0].markdown)

    def test_activated_reminder_written_to_top_level_dir(self):
        entries = [_lesson(source="human_feedback", occurrence_count=1)]
        with tempfile.TemporaryDirectory() as d:
            reminder_dir = Path(d)
            bridge = LessonToReminderBridge(reminder_dir)
            written = bridge.scan_and_write(entries)
            self.assertEqual(len(written), 1)
            self.assertEqual(written[0].parent, reminder_dir)
            self.assertTrue(written[0].exists())


class TestSelfReflectionThreshold(unittest.TestCase):
    def test_single_self_reflection_entry_not_generated(self):
        """单条 self_reflection lesson 达不到 T1 聚合门槛，不应生成任何文件。"""
        entries = [_lesson(source="self_reflection", occurrence_count=1)]
        with tempfile.TemporaryDirectory() as d:
            bridge = LessonToReminderBridge(Path(d))
            generated = bridge.scan(entries)
            self.assertEqual(generated, [])

    def test_reaching_t1_threshold_generates_draft(self):
        """同类 lesson 出现在 ≥2 个 session、occurrence 总和 ≥3 时达到 T1 门槛。"""
        entries = [
            _lesson(session_id="s1", source="self_reflection", occurrence_count=2,
                    entry_id="aaa111111111"),
            _lesson(session_id="s2", source="self_reflection", occurrence_count=1,
                    entry_id="bbb222222222"),
        ]
        with tempfile.TemporaryDirectory() as d:
            reminder_dir = Path(d)
            bridge = LessonToReminderBridge(reminder_dir)
            generated = bridge.scan(entries)
            self.assertEqual(len(generated), 1)
            self.assertFalse(generated[0].activated)
            self.assertIn("enabled: false", generated[0].markdown)

            written = bridge.scan_and_write(entries)
            self.assertEqual(written[0].parent, reminder_dir / "drafts")
            self.assertTrue(written[0].exists())


class TestToolNameExtraction(unittest.TestCase):
    def test_tool_name_extracted_into_condition(self):
        entries = [_lesson(
            source="human_feedback",
            trigger="工具 `write_file` 调用前没有确认是否会覆盖已有内容",
        )]
        with tempfile.TemporaryDirectory() as d:
            bridge = LessonToReminderBridge(Path(d))
            generated = bridge.scan(entries)
            self.assertEqual(len(generated), 1)
            self.assertIn('tool_name: "write_file"', generated[0].markdown)

    def test_no_tool_name_omits_condition_tool_name(self):
        entries = [_lesson(
            source="human_feedback",
            trigger="用户多次纠正了同一类格式错误，没有明确指向某个工具",
        )]
        with tempfile.TemporaryDirectory() as d:
            bridge = LessonToReminderBridge(Path(d))
            generated = bridge.scan(entries)
            self.assertEqual(len(generated), 1)
            self.assertNotIn("tool_name:", generated[0].markdown)


class TestIdempotency(unittest.TestCase):
    def test_repeated_scan_does_not_duplicate(self):
        entries = [_lesson(source="human_feedback", occurrence_count=1)]
        with tempfile.TemporaryDirectory() as d:
            bridge = LessonToReminderBridge(Path(d))
            first = bridge.scan_and_write(entries)
            self.assertEqual(len(first), 1)
            second = bridge.scan(entries)  # 再次扫描同样的 entries
            self.assertEqual(second, [])


class TestPromoteDraft(unittest.TestCase):
    def test_promote_moves_file_and_flips_enabled(self):
        entries = [
            _lesson(session_id="s1", source="self_reflection", occurrence_count=2,
                    entry_id="ccc333333333"),
            _lesson(session_id="s2", source="self_reflection", occurrence_count=1,
                    entry_id="ddd444444444"),
        ]
        with tempfile.TemporaryDirectory() as d:
            reminder_dir = Path(d)
            bridge = LessonToReminderBridge(reminder_dir)
            written = bridge.scan_and_write(entries)
            draft_path = written[0]
            filename = draft_path.name

            promoted = promote_draft(reminder_dir, filename)
            self.assertIsNotNone(promoted)
            self.assertEqual(promoted.parent, reminder_dir)
            self.assertFalse(draft_path.exists())
            self.assertIn("enabled: true", promoted.read_text(encoding="utf-8"))

    def test_promote_nonexistent_draft_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            result = promote_draft(Path(d), "auto_nonexistent.md")
            self.assertIsNone(result)


class TestRunLessonToReminderScan(unittest.TestCase):
    def test_scans_memory_store_entries(self):
        from mini_agent.perception.memory_store import MemoryStore

        with tempfile.TemporaryDirectory() as d:
            store_path = Path(d) / "memory.jsonl"
            store = MemoryStore(path=store_path)
            store.add(_lesson(source="human_feedback", occurrence_count=1))

            reminder_dir = Path(d) / "reminders"
            written = run_lesson_to_reminder_scan(store, reminder_dir)
            self.assertEqual(len(written), 1)
            self.assertTrue(written[0].exists())


class TestGeneratedFileParsesWithRealLoader(unittest.TestCase):
    """端到端校验：生成的 .md 文件必须能被真实的 ReminderLoader 正确解析。"""

    def test_activated_reminder_loads_via_real_loader(self):
        from mini_agent.reminders.loader import ReminderLoader, TRIGGER_PRE_TOOL

        entries = [_lesson(
            source="human_feedback",
            trigger="工具 `bash` 调用时未确认路径就执行了 rm -rf",
        )]
        with tempfile.TemporaryDirectory() as d:
            reminder_dir = Path(d)
            bridge = LessonToReminderBridge(reminder_dir)
            bridge.scan_and_write(entries)

            loader = ReminderLoader(system_dir=reminder_dir, custom_dir=None)
            loaded = loader.load()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].trigger_event, TRIGGER_PRE_TOOL)
            self.assertEqual(loaded[0].condition.tool_name, "bash")
            self.assertTrue(loaded[0].enabled)

    def test_draft_not_loaded_by_real_loader(self):
        """drafts/ 子目录下的文件不应被 ReminderLoader 扫描到（不递归子目录）。"""
        from mini_agent.reminders.loader import ReminderLoader

        entries = [
            _lesson(session_id="s1", source="self_reflection", occurrence_count=2,
                    entry_id="eee555555555"),
            _lesson(session_id="s2", source="self_reflection", occurrence_count=1,
                    entry_id="fff666666666"),
        ]
        with tempfile.TemporaryDirectory() as d:
            reminder_dir = Path(d)
            bridge = LessonToReminderBridge(reminder_dir)
            written = bridge.scan_and_write(entries)
            self.assertEqual(written[0].parent, reminder_dir / "drafts")

            loader = ReminderLoader(system_dir=reminder_dir, custom_dir=None)
            loaded = loader.load()
            self.assertEqual(loaded, [])


if __name__ == "__main__":
    unittest.main()
