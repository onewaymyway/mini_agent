"""tests/test_external_input_novelty_judge.py — §2 NoveltyJudge 测试

覆盖：
  1. Stage①：候选生成、exclude_channels 排除、重复候选跳过
  2. Stage②：无 llm_helper 时不产生调用；importance=high 才写入候选队列；
     medium/low 直接丢弃
  3. confirm：创建新 Goal，标记 confirmed
  4. dismiss：标记 dismissed，不创建 Goal
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.external_input.gateway import publish_event
from mini_agent.external_input.novelty_judge import (
    confirm_novelty_candidate,
    count_pending_novelty_candidates,
    dismiss_novelty_candidate,
    list_pending_novelty_candidates,
    run_novelty_candidate_once,
    run_novelty_importance_judge_once,
)
from mini_agent.external_input.source import ExternalInputEvent
from mini_agent.storage.paths import AgentPaths


class _FakeLLMHelper:
    def __init__(self, response: str):
        self._response = response

    def ask(self, prompt: str) -> str:
        return self._response


class TestNoveltyJudgeStage1(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def _publish(self, id_, title, channel="hn"):
        evt = ExternalInputEvent(
            id=id_, source_id="hn_frontpage", source_type="watch",
            signal="new_item", title=title, detail="detail", channel=channel,
        )
        publish_event(self.paths, evt)

    def test_candidate_generation_basic(self):
        self._publish("basic-gen-1", "AI breakthrough")
        summary = run_novelty_candidate_once(self.paths)
        self.assertEqual(summary.scanned_events, 1)
        self.assertEqual(summary.candidates_written, 1)

    def test_exclude_channels_filters_noise(self):
        cfg_path = self.paths.workdir_dir / "notification" / "novelty_judge.yaml"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text("exclude_channels:\n  - weather\n", encoding="utf-8")

        self._publish("exclude-noise-1", "sunny today", channel="weather")
        self._publish("exclude-noise-2", "AI news", channel="hn")
        summary = run_novelty_candidate_once(self.paths)
        self.assertEqual(summary.candidates_excluded_by_channel, 1)
        self.assertEqual(summary.candidates_written, 1)

    def test_duplicate_candidate_skipped_on_replay(self):
        # 直接往原始候选队列里手工构造一条已存在记录，模拟"候选去重按
        # candidate_id 判断"这件事，不依赖网关的兜底去重缓存（那是另一层
        # 独立机制，见 gateway.py::_RecentIdCache，全局单例会跨测试用例
        # 保留状态，不适合在这里用真实发布两次同一事件来触发）。
        import json
        p = self.paths.external_input_novelty_candidates_raw
        p.parent.mkdir(parents=True, exist_ok=True)
        existing = {
            "candidate_id": "novelty:hn_frontpage:e1", "event_id": "e1",
            "source_id": "hn_frontpage", "title": "AI breakthrough",
            "detail": "detail", "url": None, "judged": False, "created_at": 0.0,
        }
        p.write_text(json.dumps(existing, ensure_ascii=False) + "\n", encoding="utf-8")

        self._publish("e1", "AI breakthrough")
        summary = run_novelty_candidate_once(self.paths, consumer_name="c1")
        self.assertEqual(summary.candidates_skipped_existing, 1)
        self.assertEqual(summary.candidates_written, 0)


class TestNoveltyJudgeStage2(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def _seed_candidate(self, cand_id="novelty:src:e1", title="Something big"):
        p = self.paths.external_input_novelty_candidates_raw
        p.parent.mkdir(parents=True, exist_ok=True)
        import json
        rec = {
            "candidate_id": cand_id, "event_id": "e1", "source_id": "src",
            "title": title, "detail": "detail", "url": None,
            "judged": False, "created_at": 0.0,
        }
        p.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")

    def test_no_llm_helper_no_op(self):
        self._seed_candidate()
        summary = run_novelty_importance_judge_once(self.paths, llm_helper=None)
        self.assertEqual(summary.llm_batches, 0)
        self.assertEqual(count_pending_novelty_candidates(self.paths), 0)

    def test_importance_high_creates_pending_candidate(self):
        self._seed_candidate()
        helper = _FakeLLMHelper(
            '{"index": 1, "importance": "high", "suggested_title": "New Goal", "reason": "big deal"}'
        )
        summary = run_novelty_importance_judge_once(self.paths, llm_helper=helper)
        self.assertEqual(summary.high_count, 1)
        pending = list_pending_novelty_candidates(self.paths)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["suggested_title"], "New Goal")

    def test_importance_medium_low_discarded(self):
        self._seed_candidate()
        helper = _FakeLLMHelper('{"index": 1, "importance": "medium", "reason": "meh"}')
        summary = run_novelty_importance_judge_once(self.paths, llm_helper=helper)
        self.assertEqual(summary.discarded_count, 1)
        self.assertEqual(count_pending_novelty_candidates(self.paths), 0)


class TestNoveltyJudgeConfirmDismiss(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def _seed_pending(self, cand_id="novelty:src:e1"):
        p = self.paths.notification_novelty_candidates
        p.parent.mkdir(parents=True, exist_ok=True)
        import json, time
        rec = {
            "candidate_id": cand_id, "source_id": "src", "title": "Original title",
            "detail": "detail", "url": "https://example.com",
            "suggested_title": "New direction", "reason": "important",
            "importance": "high", "judged_at": time.time(), "status": "pending",
        }
        p.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")

    def test_confirm_creates_goal_and_marks_confirmed(self):
        self._seed_pending()
        from mini_agent.perception.goal_backlog import load_goal_backlog
        backlog = load_goal_backlog(self.paths)

        node = confirm_novelty_candidate(self.paths, "novelty:src:e1", goal_backlog=backlog)
        self.assertIsNotNone(node)
        self.assertEqual(node.title, "New direction")
        self.assertEqual(count_pending_novelty_candidates(self.paths), 0)

    def test_dismiss_marks_dismissed_without_creating_goal(self):
        self._seed_pending()
        ok = dismiss_novelty_candidate(self.paths, "novelty:src:e1")
        self.assertTrue(ok)
        self.assertEqual(count_pending_novelty_candidates(self.paths), 0)

    def test_confirm_nonexistent_returns_none(self):
        result = confirm_novelty_candidate(self.paths, "does-not-exist")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
