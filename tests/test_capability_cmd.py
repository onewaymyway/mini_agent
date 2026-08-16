"""tests/test_capability_cmd.py — 覆盖 `cli/commands/capability_cmd.py`：
设计文档 §4 里发现的"cron 需要一个 slash command 中间层"缺口的落地，
`/capability cycle` 是 `sys:capability_learning_cycle` 未来接线时会引用
的命令，本测试确认命令处理器本身能跑通、不抛异常、且 P1 阶段确实不产生
真实检索/未经审查的 wiki 写入副作用。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from mini_agent.cli.commands import capability_cmd
from mini_agent.evolution.capability_learning import (
    CapabilityQuestionStore,
    CapabilityTrackStore,
)
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class _FakeAgent:
    def __init__(self, paths, cfg=None):
        self._paths = paths
        self.cfg = cfg or object()


class TestNoAgent(unittest.TestCase):
    def test_missing_agent_reports_error_not_exception(self):
        # 不应该抛异常
        capability_cmd.handle_capability_cmd(["list"], agent=None)


class TestListAndCreate(unittest.TestCase):
    def test_list_with_no_tracks(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent = _FakeAgent(_make_paths(tmp))
            capability_cmd.handle_capability_cmd([], agent=agent)  # 不抛异常

    def test_create_with_pipe_separated_title_and_desc(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            agent = _FakeAgent(paths)
            capability_cmd.handle_capability_cmd(
                ["create", "股票分析能力", "|", "希望你具备强大的股票分析能力"], agent=agent,
            )
            tracks = CapabilityTrackStore(paths).list_tracks()
            self.assertEqual(len(tracks), 1)
            self.assertIn("股票分析能力", tracks[0].title)

    def test_create_without_args_reports_error_not_exception(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent = _FakeAgent(_make_paths(tmp))
            capability_cmd.handle_capability_cmd(["create"], agent=agent)


class TestCycle(unittest.TestCase):
    def test_cycle_with_no_tracks_is_noop(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent = _FakeAgent(_make_paths(tmp))
            capability_cmd.handle_capability_cmd(["cycle"], agent=agent)

    def test_cycle_on_knowledge_track_skips_without_real_retriever(self):
        """P1 阶段没有真实 retriever，需要检索的子主题应该被跳过、不产生
        网络请求/未审查的 wiki 写入，覆盖率状态不应从 uncovered 变成 covered。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            agent = _FakeAgent(paths)
            store = CapabilityTrackStore(paths)
            track = store.create(
                title="股票分析能力", persona_desc="希望你具备强大的股票分析能力",
            )
            capability_cmd.handle_capability_cmd(["cycle"], agent=agent)

            refreshed = store.get(track.track_id)
            self.assertTrue(
                all(t.coverage_state != "covered" for t in refreshed.outline)
            )


class TestQuestionsAndAnswer(unittest.TestCase):
    def test_questions_with_none_pending(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent = _FakeAgent(_make_paths(tmp))
            capability_cmd.handle_capability_cmd(["questions"], agent=agent)

    def test_answer_unknown_question_reports_error_not_exception(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent = _FakeAgent(_make_paths(tmp))
            capability_cmd.handle_capability_cmd(
                ["answer", "capq_does_not_exist", "some", "answer"], agent=agent,
            )

    def test_answer_pending_question_marks_answered(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            agent = _FakeAgent(paths)
            q_store = CapabilityQuestionStore(paths)
            q = q_store.raise_question(
                track_id="trk1", topic_id="topic1", question="你更关注短线还是长线？",
            )
            capability_cmd.handle_capability_cmd(
                ["answer", q.question_id, "长线为主"], agent=agent,
            )
            refreshed = [
                item for item in q_store.list_questions(track_id="trk1")
                if item.question_id == q.question_id
            ][0]
            self.assertEqual(refreshed.status, "answered")
            self.assertEqual(refreshed.answer, "长线为主")


class TestUnknownSubcommand(unittest.TestCase):
    def test_unknown_subcommand_reports_error_not_exception(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent = _FakeAgent(_make_paths(tmp))
            capability_cmd.handle_capability_cmd(["bogus"], agent=agent)


class TestPersonaSubcommands(unittest.TestCase):
    def test_persona_create_with_flag_sets_target_type(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            agent = _FakeAgent(paths)
            capability_cmd.handle_capability_cmd(
                ["create", "老李投顾", "|", "经验老道的资深投资顾问人设", "--persona"],
                agent=agent,
            )
            tracks = CapabilityTrackStore(paths).list_tracks()
            self.assertEqual(len(tracks), 1)
            self.assertEqual(tracks[0].target_type, "persona")

    def test_persona_subcommand_requires_track_id(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent = _FakeAgent(_make_paths(tmp))
            capability_cmd.handle_capability_cmd(["persona", "draft"], agent=agent)  # 不抛异常

    def test_persona_subcommand_unknown_track_reports_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent = _FakeAgent(_make_paths(tmp))
            capability_cmd.handle_capability_cmd(
                ["persona", "draft", "no_such_track"], agent=agent,
            )  # 不抛异常

    def test_persona_subcommand_rejects_knowledge_type_track(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            agent = _FakeAgent(paths)
            track = CapabilityTrackStore(paths).create(
                title="股票分析", persona_desc="x", outline_names=["技术分析基础"],
            )
            capability_cmd.handle_capability_cmd(
                ["persona", "draft", track.track_id], agent=agent,
            )  # knowledge 型，应该报错但不抛异常

    def test_persona_draft_then_show_then_publish_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            agent = _FakeAgent(paths)
            track = CapabilityTrackStore(paths).create(
                title="老李投顾", persona_desc="经验老道的资深投资顾问人设",
                outline_names=["身份背景", "说话习惯"], target_type="persona",
            )
            capability_cmd.handle_capability_cmd(
                ["persona", "draft", track.track_id], agent=agent,
            )
            from mini_agent.evolution.capability_learning import load_persona_draft
            draft_text = load_persona_draft(paths, track.track_id)
            self.assertIsNotNone(draft_text)

            capability_cmd.handle_capability_cmd(
                ["persona", "show", track.track_id], agent=agent,
            )

            capability_cmd.handle_capability_cmd(
                ["persona", "publish", track.track_id], agent=agent,
            )
            published = paths.project_personas_dir / "老李投顾.md"
            self.assertTrue(published.exists())

    def test_persona_publish_without_draft_reports_error_not_exception(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            agent = _FakeAgent(paths)
            track = CapabilityTrackStore(paths).create(
                title="老李投顾", persona_desc="x", target_type="persona",
            )
            capability_cmd.handle_capability_cmd(
                ["persona", "publish", track.track_id], agent=agent,
            )  # 没有草稿，应该报错但不抛异常


if __name__ == "__main__":
    unittest.main()
