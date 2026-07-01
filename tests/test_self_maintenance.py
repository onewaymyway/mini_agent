"""
tests/test_self_maintenance.py — [具身改进 C4] 自维护模块测试

覆盖：
  1. _check_tool_health：扫描 traces.jsonl，正确识别高失败率工具，
     样本量不足时不下结论
  2. _check_skill_freshness：复用 skill_loader.tracker，识别长期未用 skill
  3. _check_memory_conflicts：lesson 聚类中同时出现正/负信号时标记矛盾
  4. generate_repair_suggestions：文本包含关键信息
  5. 时间门控：should_run_self_maintenance / record_self_maintenance_run
  6. run_self_maintenance 端到端：写入 activity_digest.jsonl
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.evolution.self_maintenance import (
    ConflictingLessonFinding,
    HealthReport,
    SelfMaintenanceModule,
    StaleSkillFinding,
    StaleToolFinding,
    append_digest_record,
    record_self_maintenance_run,
    run_self_maintenance,
    should_run_self_maintenance,
)
from mini_agent.perception.memory_store import MemoryEntry
from mini_agent.storage.paths import AgentPaths


def _write_traces(session_dir: Path, events: list[dict]) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    with open(session_dir / "traces.jsonl", "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


class _FakeTracker:
    def __init__(self, records: dict):
        self._records = records

    def get_record(self, name):
        return self._records.get(name)


class _FakeRecord:
    def __init__(self, last_used_at: float):
        self.last_used_at = last_used_at


class _FakeSkillLoader:
    def __init__(self, active: list, tracker):
        self.active = active
        self.tracker = tracker


class TestCheckToolHealth(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.paths = AgentPaths(self.tmpdir)

    def test_high_failure_rate_tool_flagged(self):
        events = [{"phase": "tool_call", "tool_name": "flaky", "is_error": i < 4} for i in range(5)]
        _write_traces(self.paths.sessions_dir / "s1", events)

        findings = SelfMaintenanceModule._check_tool_health(self.paths)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].tool_name, "flaky")
        self.assertEqual(findings[0].error_count, 4)

    def test_healthy_tool_not_flagged(self):
        events = [{"phase": "tool_call", "tool_name": "reliable", "is_error": False} for _ in range(5)]
        _write_traces(self.paths.sessions_dir / "s1", events)

        findings = SelfMaintenanceModule._check_tool_health(self.paths)
        self.assertEqual(findings, [])

    def test_insufficient_samples_not_flagged(self):
        # 只有 2 次调用，低于 _TOOL_MIN_SAMPLES=3，即使全部失败也不下结论
        events = [{"phase": "tool_call", "tool_name": "rare", "is_error": True} for _ in range(2)]
        _write_traces(self.paths.sessions_dir / "s1", events)

        findings = SelfMaintenanceModule._check_tool_health(self.paths)
        self.assertEqual(findings, [])

    def test_no_sessions_dir_returns_empty(self):
        findings = SelfMaintenanceModule._check_tool_health(self.paths)
        self.assertEqual(findings, [])


class TestCheckSkillFreshness(unittest.TestCase):
    def test_stale_skill_flagged(self):
        old_ts = time.time() - 40 * 86400  # 40 天前
        tracker = _FakeTracker({"old_skill": _FakeRecord(old_ts)})
        loader = _FakeSkillLoader(active=["old_skill"], tracker=tracker)

        findings = SelfMaintenanceModule._check_skill_freshness(loader)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].skill_name, "old_skill")
        self.assertGreaterEqual(findings[0].last_used_days_ago, 30.0)

    def test_recently_used_skill_not_flagged(self):
        recent_ts = time.time() - 1 * 86400
        tracker = _FakeTracker({"fresh_skill": _FakeRecord(recent_ts)})
        loader = _FakeSkillLoader(active=["fresh_skill"], tracker=tracker)

        findings = SelfMaintenanceModule._check_skill_freshness(loader)
        self.assertEqual(findings, [])

    def test_no_skill_loader_returns_empty(self):
        self.assertEqual(SelfMaintenanceModule._check_skill_freshness(None), [])

    def test_no_tracker_returns_empty(self):
        loader = _FakeSkillLoader(active=["x"], tracker=None)
        self.assertEqual(SelfMaintenanceModule._check_skill_freshness(loader), [])

    def test_never_used_skill_not_flagged(self):
        # last_used_at=0 表示从未记录，不应被标记为"过时"（可能是刚激活）
        tracker = _FakeTracker({"new_skill": _FakeRecord(0.0)})
        loader = _FakeSkillLoader(active=["new_skill"], tracker=tracker)
        self.assertEqual(SelfMaintenanceModule._check_skill_freshness(loader), [])


class _FakeMemoryBackend:
    def __init__(self, entries):
        self._entries = entries

    def all_entries(self):
        return self._entries


class TestCheckMemoryConflicts(unittest.TestCase):
    def test_conflicting_lessons_in_same_group_detected(self):
        positive = MemoryEntry(
            session_id="s1", summary="", key_outcomes=[], tags=[], model="m",
            entry_type="lesson", trigger="读取大文件处理方式",
            outcome="先用 wc -l 确认行数，效果很好，应该这样做",
            suggested_action="先确认行数再读取",
            occurrence_count=2,
        )
        negative = MemoryEntry(
            session_id="s2", summary="", key_outcomes=[], tags=[], model="m",
            entry_type="lesson", trigger="读取大文件处理方式",
            outcome="先确认行数反而导致失败，不应该这样做",
            suggested_action="直接读取即可，避免额外步骤",
            occurrence_count=2,
        )
        backend = _FakeMemoryBackend([positive, negative])

        findings = SelfMaintenanceModule._check_memory_conflicts(backend)
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0].positive_sample)
        self.assertTrue(findings[0].negative_sample)

    def test_no_conflict_when_only_positive(self):
        e1 = MemoryEntry(
            session_id="s1", summary="", key_outcomes=[], tags=[], model="m",
            entry_type="lesson", trigger="代码格式化",
            outcome="格式化成功，效果很好",
            occurrence_count=2,
        )
        e2 = MemoryEntry(
            session_id="s2", summary="", key_outcomes=[], tags=[], model="m",
            entry_type="lesson", trigger="代码格式化",
            outcome="格式化也成功，建议保留",
            occurrence_count=2,
        )
        backend = _FakeMemoryBackend([e1, e2])
        findings = SelfMaintenanceModule._check_memory_conflicts(backend)
        self.assertEqual(findings, [])

    def test_no_backend_returns_empty(self):
        self.assertEqual(SelfMaintenanceModule._check_memory_conflicts(None), [])

    def test_too_few_entries_returns_empty(self):
        e1 = MemoryEntry(
            session_id="s1", summary="", key_outcomes=[], tags=[], model="m", entry_type="lesson",
        )
        backend = _FakeMemoryBackend([e1])
        self.assertEqual(SelfMaintenanceModule._check_memory_conflicts(backend), [])


class TestGenerateRepairSuggestions(unittest.TestCase):
    def test_suggestions_mention_each_finding_type(self):
        report = HealthReport(
            stale_tools=[StaleToolFinding("bad_tool", 5, 4, 0.8)],
            stale_skills=[StaleSkillFinding("old_skill", 40.0)],
            conflicting_lessons=[ConflictingLessonFinding("k", "正面", "负面")],
        )
        suggestions = SelfMaintenanceModule().generate_repair_suggestions(report)
        self.assertEqual(len(suggestions), 3)
        self.assertTrue(any("bad_tool" in s for s in suggestions))
        self.assertTrue(any("old_skill" in s for s in suggestions))
        self.assertTrue(any("正面" in s and "负面" in s for s in suggestions))

    def test_empty_report_yields_no_suggestions(self):
        self.assertEqual(SelfMaintenanceModule().generate_repair_suggestions(HealthReport()), [])


class TestTimeGating(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.paths = AgentPaths(self.tmpdir)

    def test_should_run_true_when_never_run(self):
        self.assertTrue(should_run_self_maintenance(self.paths))

    def test_should_run_false_immediately_after_record(self):
        record_self_maintenance_run(self.paths)
        self.assertFalse(should_run_self_maintenance(self.paths, interval_hours=24.0))

    def test_should_run_true_after_interval_elapsed(self):
        record_self_maintenance_run(self.paths)
        self.assertTrue(should_run_self_maintenance(self.paths, interval_hours=0.0))


class TestRunSelfMaintenanceEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.paths = AgentPaths(self.tmpdir)

    def test_writes_digest_when_findings_exist(self):
        events = [{"phase": "tool_call", "tool_name": "flaky", "is_error": True} for _ in range(5)]
        _write_traces(self.paths.sessions_dir / "s1", events)

        report = run_self_maintenance(self.paths)
        self.assertTrue(report.has_findings)

        digest_path = self.paths.workdir_dir / "activity_digest.jsonl"
        self.assertTrue(digest_path.exists())
        lines = digest_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["type"], "health_report")
        self.assertIn("suggestions", record)

    def test_no_digest_written_when_no_findings(self):
        report = run_self_maintenance(self.paths)
        self.assertFalse(report.has_findings)
        digest_path = self.paths.workdir_dir / "activity_digest.jsonl"
        self.assertFalse(digest_path.exists())

    def test_marks_run_timestamp_even_without_findings(self):
        self.assertTrue(should_run_self_maintenance(self.paths))
        run_self_maintenance(self.paths)
        self.assertFalse(should_run_self_maintenance(self.paths))

    def test_append_digest_record_directly(self):
        append_digest_record(self.paths, {"type": "custom_event", "summary": "test"})
        digest_path = self.paths.workdir_dir / "activity_digest.jsonl"
        self.assertTrue(digest_path.exists())


if __name__ == "__main__":
    unittest.main()
