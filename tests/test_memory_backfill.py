"""tests/test_memory_backfill.py — evolution/memory_backfill.py 测试。

[next_doc/memory_backfill_and_profile_update_plan.md 方向一] 覆盖：

  - scan_sessions_for_backfill：只挑出 summary 为空、轮次达标的候选，
    按 updated_at 从旧到新排序，且不做时间窗口过滤（陈年 session 也
    应该出现在候选里，验证方案第 4 节风险项 1 的评审决策）。
  - backfill_sessions：dry-run 不写入任何东西；非 dry-run 会写入
    MemoryEntry 并回写 Session.summary。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_agent.session import SessionManager
from mini_agent.evolution.memory_backfill import (
    scan_sessions_for_backfill,
    backfill_sessions,
    generate_summary_from_text,
    backfill_cron_run,
)


def _user_msg(text: str) -> dict:
    return {"role": "user", "content": text, "_type": "user_input"}


def _assistant_msg(text: str) -> dict:
    return {"role": "assistant", "content": text, "_type": "assistant_output"}


class _FakeResp:
    def __init__(self, text: str):
        self.text = text


class _FakeLLMClient:
    def __init__(self, summary_text: str = "这是离线补生成的摘要"):
        self._summary_text = summary_text

    def chat_with_retry(self, **kwargs):
        return _FakeResp(self._summary_text)


class _FakeMemoryBackend:
    def __init__(self):
        self.entries = []

    def upsert(self, entry):
        self.entries.append(entry)

    def all_entries(self):
        return self.entries


def _make_session_with_history(mgr: SessionManager, *, turns: int, summary: str = "") -> str:
    session = mgr.new_session(provider="anthropic", model="claude-test")
    history = []
    for i in range(turns):
        history.append(_user_msg(f"第 {i} 轮请求"))
        history.append(_assistant_msg(f"第 {i} 轮回复"))
    stats = {"turns": turns, "input_tokens": 0, "output_tokens": 0, "tool_calls": 0}
    mgr.save(session, history=history, stats=stats)
    if summary:
        mgr.mark_summary_backfilled(session.id, summary, turns)
    return session.id


class TestScanSessionsForBackfill(unittest.TestCase):
    def test_only_empty_summary_and_turns_ok_are_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = SessionManager(session_dir=Path(tmp) / "sessions")
            ok_id = _make_session_with_history(mgr, turns=5)
            too_short_id = _make_session_with_history(mgr, turns=1)
            has_summary_id = _make_session_with_history(mgr, turns=5, summary="已经有摘要了")

            candidates = scan_sessions_for_backfill(mgr, min_turns_for_backfill=4)
            candidate_ids = {c.id for c in candidates}

            self.assertIn(ok_id, candidate_ids)
            self.assertNotIn(too_short_id, candidate_ids)
            self.assertNotIn(has_summary_id, candidate_ids)

    def test_no_time_window_old_sessions_still_candidates(self):
        """[方案第 4 节风险项 1：不加回溯窗口] 手动把 updated_at 改成很久
        以前，候选扫描依然应该把它列进来——没有任何"多旧就不回填"的
        过滤逻辑。"""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = SessionManager(session_dir=Path(tmp) / "sessions")
            old_id = _make_session_with_history(mgr, turns=5)

            meta_path = Path(tmp) / "sessions" / old_id / "meta.json"
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            data["updated_at"] = "2000-01-01T00:00:00+00:00"
            meta_path.write_text(json.dumps(data), encoding="utf-8")

            candidates = scan_sessions_for_backfill(mgr, min_turns_for_backfill=4)
            self.assertIn(old_id, {c.id for c in candidates})


class TestBackfillSessions(unittest.TestCase):
    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = SessionManager(session_dir=Path(tmp) / "sessions")
            sid = _make_session_with_history(mgr, turns=5)
            backend = _FakeMemoryBackend()

            report = backfill_sessions(
                mgr, memory_backend=backend, llm_client=_FakeLLMClient(),
                dry_run=True,
            )

            self.assertEqual(len(report.backfilled), 1)
            self.assertEqual(backend.entries, [])
            reloaded = mgr.load(sid)
            self.assertEqual(reloaded.summary, "")

    def test_real_run_writes_memory_and_session_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = SessionManager(session_dir=Path(tmp) / "sessions")
            sid = _make_session_with_history(mgr, turns=5)
            backend = _FakeMemoryBackend()

            report = backfill_sessions(
                mgr, memory_backend=backend, llm_client=_FakeLLMClient("补跑的摘要内容"),
                dry_run=False,
            )

            self.assertEqual(len(report.backfilled), 1)
            self.assertEqual(len(backend.entries), 1)
            self.assertEqual(backend.entries[0].session_id, sid)
            self.assertEqual(backend.entries[0].summary, "补跑的摘要内容")

            reloaded = mgr.load(sid)
            self.assertEqual(reloaded.summary, "补跑的摘要内容")

    def test_max_sessions_per_run_limits_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = SessionManager(session_dir=Path(tmp) / "sessions")
            for _ in range(5):
                _make_session_with_history(mgr, turns=5)
            backend = _FakeMemoryBackend()

            report = backfill_sessions(
                mgr, memory_backend=backend, llm_client=_FakeLLMClient(),
                max_sessions_per_run=2, dry_run=False,
            )

            self.assertEqual(report.total_candidates, 5)
            self.assertEqual(report.total_processed, 2)
            self.assertEqual(len(backend.entries), 2)


class TestGenerateSummaryFromText(unittest.TestCase):
    """[next_doc/growth_advisor_improvement_plan_v4.md 方向一 M3] 对一段
    文本（不是完整 history）离线摘要，供 cron 任务收尾场景复用。"""

    def test_empty_text_returns_empty_without_llm_call(self):
        class _ExplodingLLMClient:
            def chat_with_retry(self, **kwargs):
                raise AssertionError("空文本不应该触发 LLM 调用")

        self.assertEqual(generate_summary_from_text("   ", _ExplodingLLMClient()), "")

    def test_calls_llm_and_returns_stripped_text(self):
        summary = generate_summary_from_text(
            "已完成日报整理", _FakeLLMClient("  日报已整理完毕  "),
        )
        self.assertEqual(summary, "日报已整理完毕")

    def test_task_template_is_included_in_prompt_input(self):
        captured = {}

        class _CapturingLLMClient:
            def chat_with_retry(self, **kwargs):
                captured["messages"] = kwargs.get("messages")
                return _FakeResp("摘要内容")

        generate_summary_from_text(
            "任务产出正文", _CapturingLLMClient(), task_template="每小时检查待办",
        )
        prompt_text = captured["messages"][0]["content"]
        self.assertIn("每小时检查待办", prompt_text)
        self.assertIn("任务产出正文", prompt_text)


class TestBackfillCronRun(unittest.TestCase):
    """[next_doc/growth_advisor_improvement_plan_v4.md 方向一 M3]
    cron 任务收尾时直接产出记忆，`session_id` 形如 `cron:<job_id>:<run_id>`。"""

    def test_writes_memory_entry_with_cron_session_id(self):
        backend = _FakeMemoryBackend()
        entry = backfill_cron_run(
            "sys:daily_report", "run_abc123", "今天的日报已生成并发送。",
            memory_backend=backend, llm_client=_FakeLLMClient("日报任务今天已完成"),
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry.session_id, "cron:sys:daily_report:run_abc123")
        self.assertEqual(len(backend.entries), 1)
        self.assertEqual(backend.entries[0].summary, "日报任务今天已完成")

    def test_empty_summary_does_not_write(self):
        backend = _FakeMemoryBackend()
        entry = backfill_cron_run(
            "sys:daily_report", "run_1", "无实质内容",
            memory_backend=backend, llm_client=_FakeLLMClient(""),
        )
        self.assertIsNone(entry)
        self.assertEqual(len(backend.entries), 0)

    def test_highly_similar_consecutive_summaries_are_deduped(self):
        """同一个 job 连续触发、任务模板高度相似时，第二次不应该重复写入
        高度雷同的记忆（1.5 节去重）。"""
        backend = _FakeMemoryBackend()
        first = backfill_cron_run(
            "sys:hourly_check", "run_1", "检查了待办列表，没有发现新的待办事项。",
            memory_backend=backend, llm_client=_FakeLLMClient("本次检查未发现新待办"),
        )
        self.assertIsNotNone(first)
        second = backfill_cron_run(
            "sys:hourly_check", "run_2", "检查了待办列表，没有发现新的待办事项。",
            memory_backend=backend, llm_client=_FakeLLMClient("本次检查未发现新待办"),
        )
        self.assertIsNone(second)
        self.assertEqual(len(backend.entries), 1)  # 只有第一条被写入

    def test_dissimilar_consecutive_summaries_both_written(self):
        backend = _FakeMemoryBackend()
        backfill_cron_run(
            "sys:hourly_check", "run_1", "检查了待办列表，没有发现新的待办事项。",
            memory_backend=backend, llm_client=_FakeLLMClient("本次检查未发现新待办"),
        )
        second = backfill_cron_run(
            "sys:hourly_check", "run_2", "抓取了最新的行业资讯并整理成简报发送。",
            memory_backend=backend, llm_client=_FakeLLMClient("完成了一份全新的行业资讯简报"),
        )
        self.assertIsNotNone(second)
        self.assertEqual(len(backend.entries), 2)

    def test_dedup_only_compares_against_same_job(self):
        """去重只跟同一个 job 的历史摘要比较，不同 job 之间不应该互相
        影响（哪怕摘要文本碰巧很像）。"""
        backend = _FakeMemoryBackend()
        backfill_cron_run(
            "sys:job_a", "run_1", "完成了任务",
            memory_backend=backend, llm_client=_FakeLLMClient("任务已完成"),
        )
        other = backfill_cron_run(
            "sys:job_b", "run_1", "完成了任务",
            memory_backend=backend, llm_client=_FakeLLMClient("任务已完成"),
        )
        self.assertIsNotNone(other)
        self.assertEqual(len(backend.entries), 2)

    def test_memory_backend_query_failure_does_not_block_write(self):
        """去重查询失败（`all_entries()` 抛异常）时不应该阻止本次写入，
        去重是锦上添花，不能成为记忆生成路径上的新故障点。"""
        class _BrokenQueryBackend(_FakeMemoryBackend):
            def all_entries(self):
                raise RuntimeError("boom")

        backend = _BrokenQueryBackend()
        entry = backfill_cron_run(
            "sys:job_a", "run_1", "完成了任务",
            memory_backend=backend, llm_client=_FakeLLMClient("任务已完成"),
        )
        self.assertIsNotNone(entry)
        self.assertEqual(len(backend.entries), 1)


if __name__ == "__main__":
    unittest.main()
