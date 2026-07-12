"""
tests/test_outcome_tracker.py — 自我进化效果回填闭环测试

重点覆盖本次新增行为：verdict == "worsened" 时自动回写 source="eval_failure"
的 lesson，并发布 evolution.outcome_negative 事件——闭合
lesson → skill_propose → outcome_tracker → lesson 的环。
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.evolution import outcome_tracker
from mini_agent.perception import system_events as se
from mini_agent.perception.memory_store import MemoryEntry
from mini_agent.storage.paths import AgentPaths


class _FakeMemoryBackend:
    """最小可用的 memory_backend stub：只实现 outcome_tracker 用到的
    all_entries()/add()，不依赖真实 MemoryStore 的持久化/索引逻辑。"""

    def __init__(self):
        self._entries: list[MemoryEntry] = []

    def all_entries(self) -> list:
        return list(self._entries)

    def add(self, entry: MemoryEntry) -> None:
        self._entries.append(entry)


def _make_lesson(trigger: str, occurrence_count: int = 1) -> MemoryEntry:
    return MemoryEntry(
        session_id="s1",
        summary=f"lesson about: {trigger}",
        key_outcomes=[],
        tags=["lesson"],
        model="test",
        entry_type="lesson",
        trigger=trigger,
        outcome="something happened",
        source="self_reflection",
        occurrence_count=occurrence_count,
    )


class TestOutcomeTrackerBasics(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backend = _FakeMemoryBackend()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _group_key_for(self, trigger: str) -> str:
        """借助 group_lessons() 反推真实分组 key，不猜测内部实现。"""
        from mini_agent.perception.lesson_review import group_lessons

        groups = group_lessons([e for e in self.backend.all_entries() if e.entry_type == "lesson"])
        for g in groups:
            if any(trigger in e.trigger for e in g.entries):
                return g.key
        raise AssertionError(f"未找到 trigger={trigger!r} 对应的分组")

    def test_record_commit_baseline_and_tick_no_change(self):
        for _ in range(3):
            self.backend.add(_make_lesson("bash script permission error"))
        group_key = self._group_key_for("bash script permission error")

        outcome_tracker.record_commit_baseline(
            self.paths, self.backend,
            commit_id="c1", lesson_group_id=group_key, commit_summary="fix permission handling",
        )
        records = outcome_tracker.get_all(self.paths)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].baseline_trigger_count, 3)

        # 手动把 deadline 拨到过去，模拟观察期已结束
        records[0].observation_deadline = time.time() - 1
        outcome_tracker._save_all(self.paths, records)

        resolved = outcome_tracker.tick(self.paths, self.backend)
        self.assertEqual(len(resolved), 1)
        # lesson 数量没变化（还是3条同类 lesson）→ 触发次数不变 → no_change
        self.assertEqual(resolved[0].verdict, "no_change")

    def test_worsened_verdict_writes_eval_failure_lesson_and_publishes_event(self):
        for _ in range(3):
            self.backend.add(_make_lesson("bash script permission error"))
        group_key = self._group_key_for("bash script permission error")

        outcome_tracker.record_commit_baseline(
            self.paths, self.backend,
            commit_id="c2", lesson_group_id=group_key, commit_summary="attempted fix that made it worse",
        )
        # 观察期内又新增了更多同类 lesson（问题变多了，说明修复没生效甚至更糟）
        for _ in range(3):
            self.backend.add(_make_lesson("bash script permission error"))

        records = outcome_tracker.get_all(self.paths)
        records[0].observation_deadline = time.time() - 1
        outcome_tracker._save_all(self.paths, records)

        lessons_before = len(self.backend.all_entries())
        resolved = outcome_tracker.tick(self.paths, self.backend)

        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].verdict, "worsened")

        # 1) 应该多了一条 source="eval_failure" 的新 lesson
        lessons_after = self.backend.all_entries()
        self.assertEqual(len(lessons_after), lessons_before + 1)
        new_lesson = lessons_after[-1]
        self.assertEqual(new_lesson.entry_type, "lesson")
        self.assertEqual(new_lesson.source, "eval_failure")
        self.assertIn("c2", new_lesson.outcome)
        self.assertIn("attempted fix that made it worse", new_lesson.outcome)

        # 2) 应该发布了 evolution.outcome_negative 事件（tier=tick）
        events = se.poll_since(self.paths, consumer_name="test_consumer", tiers=["tick"])
        matched = [e for e in events if e.event_type == "evolution.outcome_negative"]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].payload["commit_id"], "c2")
        self.assertEqual(matched[0].payload["baseline_trigger_count"], 3)
        self.assertEqual(matched[0].payload["post_trigger_count"], 6)

    def test_improved_verdict_does_not_write_lesson(self):
        for _ in range(4):
            self.backend.add(_make_lesson("database connection timeout"))
        group_key = self._group_key_for("database connection timeout")

        outcome_tracker.record_commit_baseline(
            self.paths, self.backend,
            commit_id="c3", lesson_group_id=group_key, commit_summary="fixed connection pool",
        )
        # 观察期内没有新增同类 lesson，且旧的 lesson 条目在测试里保持不变——
        # 为了模拟"问题不再触发"，这里直接清空 backend 里的旧 lesson。
        self.backend._entries = []

        records = outcome_tracker.get_all(self.paths)
        records[0].observation_deadline = time.time() - 1
        outcome_tracker._save_all(self.paths, records)

        lessons_before = len(self.backend.all_entries())
        resolved = outcome_tracker.tick(self.paths, self.backend)

        self.assertEqual(resolved[0].verdict, "improved")
        self.assertEqual(len(self.backend.all_entries()), lessons_before)  # 没有新增 lesson

        events = se.poll_since(self.paths, consumer_name="test_consumer_2", tiers=["tick"])
        matched = [e for e in events if e.event_type == "evolution.outcome_negative"]
        self.assertEqual(len(matched), 0)

    def test_mark_reverted_still_works(self):
        for _ in range(3):
            self.backend.add(_make_lesson("some trigger"))
        group_key = self._group_key_for("some trigger")
        outcome_tracker.record_commit_baseline(
            self.paths, self.backend, commit_id="c4", lesson_group_id=group_key,
        )
        outcome_tracker.mark_reverted(self.paths, "c4")
        records = outcome_tracker.get_all(self.paths)
        self.assertEqual(records[0].verdict, "reverted_by_user")
        self.assertEqual(records[0].status, "resolved")


class TestMemoryAgingEvalFailureSource(unittest.TestCase):
    def test_eval_failure_has_dedicated_half_life(self):
        from mini_agent.evolution.memory_aging import compute_half_life_days

        entry = _make_lesson("x")
        entry.source = "eval_failure"
        half_life = compute_half_life_days(entry)
        # 应该命中专属半衰期（21天），而不是回退到默认 30 天
        self.assertEqual(half_life, 21.0)


if __name__ == "__main__":
    unittest.main()
