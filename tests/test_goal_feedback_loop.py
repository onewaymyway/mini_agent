"""
tests/test_goal_feedback_loop.py — 覆盖 next_doc/goal_tree_visibility_
wiki_and_report_plan.md Stage 4（能力 C 反馈闭环）

  1. add_user_feedback 默认写入 status="pending"，about 为空
  2. add_user_feedback(about=...) 正确落盘 about 字段
  3. accept_candidate 处理关联候选后，对应反馈自动标记 addressed；
     未关联的笼统反馈不受影响
  4. reject_candidate 同样触发 addressed
  5. 不关联该候选的反馈（about 指向别的 candidate_id）不会被误标记
  6. cycle_tuning.confirm_tuning_proposal(goal_backlog=...) 标记 addressed；
     不传 goal_backlog 时行为与改动前一致（不报错，也不标记）
  7. apply_tuning_proposal 标记 addressed
  8. reject_tuning_proposal 标记 addressed
  9. mark_feedback_addressed 对不存在的节点返回 0，不抛异常
  10. goal_tree_report.build_goal_tree_report 的 pending_feedback 只收集
      仍是 pending 的反馈，addressed 的不出现
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.perception import cycle_tuning as ct
from mini_agent.perception import goal_tree_report as gtr
from mini_agent.perception.goal_backlog import load_goal_backlog
from mini_agent.storage.paths import AgentPaths


class TestFeedbackStatusField(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.gb = load_goal_backlog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_default_status_pending_no_about(self):
        node = self.gb.add_goal("Goal", source="user")
        self.gb.add_user_feedback(node.id, "please speed up")
        fb = self.gb.get(node.id).user_feedback
        self.assertEqual(len(fb), 1)
        self.assertEqual(fb[0]["status"], "pending")
        self.assertNotIn("about", fb[0])

    def test_about_field_persisted(self):
        node = self.gb.add_goal("Goal", source="user")
        self.gb.add_user_feedback(node.id, "reject this one", about="candidate:cand_1")
        fb = self.gb.get(node.id).user_feedback
        self.assertEqual(fb[0]["about"], "candidate:cand_1")
        self.assertEqual(fb[0]["status"], "pending")


class TestCandidateFeedbackClosure(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.gb = load_goal_backlog(self.paths)
        self.node = self.gb.add_goal("Goal", source="user")
        self.gb.append_decompose_candidates(self.node.id, [
            {"id": "cand_1", "title": "候选一"},
            {"id": "cand_2", "title": "候选二"},
        ])

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_accept_marks_associated_feedback_addressed(self):
        self.gb.add_user_feedback(self.node.id, "改一下候选一", about="candidate:cand_1")
        self.gb.add_user_feedback(self.node.id, "笼统意见")

        self.gb.accept_candidate(self.node.id, "cand_1")

        fb = self.gb.get(self.node.id).user_feedback
        associated = next(f for f in fb if f.get("about") == "candidate:cand_1")
        general = next(f for f in fb if not f.get("about"))
        self.assertEqual(associated["status"], "addressed")
        self.assertEqual(general["status"], "pending")

    def test_reject_marks_associated_feedback_addressed(self):
        self.gb.add_user_feedback(self.node.id, "不要这个候选", about="candidate:cand_2")

        self.gb.reject_candidate(self.node.id, "cand_2")

        fb = self.gb.get(self.node.id).user_feedback
        self.assertEqual(fb[0]["status"], "addressed")

    def test_unrelated_candidate_feedback_not_marked(self):
        self.gb.add_user_feedback(self.node.id, "针对候选二的意见", about="candidate:cand_2")

        self.gb.accept_candidate(self.node.id, "cand_1")

        fb = self.gb.get(self.node.id).user_feedback
        self.assertEqual(fb[0]["status"], "pending")

    def test_mark_feedback_addressed_missing_node_returns_zero(self):
        self.assertEqual(self.gb.mark_feedback_addressed("nope", "candidate:cand_1"), 0)


class TestTuningProposalFeedbackClosure(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.gb = load_goal_backlog(self.paths)
        self.node = self.gb.add_goal("Tuning goal", source="user")

    def tearDown(self):
        self._tmpdir.cleanup()

    def _new_proposal(self, changes):
        p = ct.build_tuning_proposal(self.node.id, changes)
        ct.save_proposal(self.paths, p)
        return p

    def test_confirm_without_goal_backlog_does_not_raise_or_mark(self):
        p = self._new_proposal([{"param": "priority", "to": 3}])
        self.gb.add_user_feedback(self.node.id, "bump priority", about=f"proposal:{p.id}")
        ct.confirm_tuning_proposal(self.paths, self.node.id, p.id)  # no goal_backlog
        fb = self.gb.get(self.node.id).user_feedback
        self.assertEqual(fb[0]["status"], "pending")

    def test_confirm_with_goal_backlog_marks_addressed(self):
        p = self._new_proposal([{"param": "priority", "to": 3}])
        self.gb.add_user_feedback(self.node.id, "bump priority", about=f"proposal:{p.id}")
        ct.confirm_tuning_proposal(self.paths, self.node.id, p.id, goal_backlog=self.gb)
        fb = self.gb.get(self.node.id).user_feedback
        self.assertEqual(fb[0]["status"], "addressed")

    def test_apply_marks_addressed(self):
        p = self._new_proposal([{"param": "priority", "to": 3}])
        self.gb.add_user_feedback(self.node.id, "bump priority", about=f"proposal:{p.id}")
        ct.confirm_tuning_proposal(self.paths, self.node.id, p.id, goal_backlog=self.gb)
        from mini_agent.evolution.cron_scheduler import CronScheduler
        cs = CronScheduler(self.paths)
        cs.load()
        ct.apply_tuning_proposal(self.paths, self.gb, cs, self.node.id, p.id)
        fb = self.gb.get(self.node.id).user_feedback
        self.assertEqual(fb[0]["status"], "addressed")

    def test_reject_marks_addressed(self):
        p = self._new_proposal([{"param": "priority", "to": 3}])
        self.gb.add_user_feedback(self.node.id, "bump priority", about=f"proposal:{p.id}")
        ct.reject_tuning_proposal(self.paths, self.gb, self.node.id, p.id, reason="no")
        fb = self.gb.get(self.node.id).user_feedback
        self.assertEqual(fb[0]["status"], "addressed")


class TestPendingFeedbackInTreeReport(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.gb = load_goal_backlog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_pending_feedback_excludes_addressed(self):
        node = self.gb.add_goal("Goal", source="user")
        self.gb.append_decompose_candidates(node.id, [{"id": "cand_1", "title": "候选"}])
        self.gb.add_user_feedback(node.id, "针对候选的意见", about="candidate:cand_1")
        self.gb.add_user_feedback(node.id, "笼统意见")

        self.gb.accept_candidate(node.id, "cand_1")

        report = gtr.build_goal_tree_report(self.paths, self.gb)
        texts = [f["text"] for f in report.pending_feedback]
        self.assertIn("笼统意见", texts)
        self.assertNotIn("针对候选的意见", texts)


if __name__ == "__main__":
    unittest.main()
