"""
tests/test_session.py

Session 持久化管理的完整测试套件，覆盖：
  - SessionMeta / Session 数据结构
  - SessionManager：新建、保存（JSON/JSONL）、加载、列举、删除
  - 历史序列化（处理 SDK 对象）
  - id 前缀匹配
  - 标题自动提取
  - Agent 集成：自动保存、手动加载、session 属性
  - 边界情况：空历史、无效 id、文件损坏
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from session import Session, SessionMeta, SessionManager, _serialize_history, _now_iso


# ── 共享工厂 ──────────────────────────────────────────────────────────────────

def make_mgr(tmp: Path, fmt: str = "json") -> SessionManager:
    return SessionManager(session_dir=tmp / "sessions", fmt=fmt)


SAMPLE_HISTORY = [
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": [{"type": "text", "text": "Hi!"}]},
    {"role": "user", "content": "Write a prime sieve"},
    {"role": "assistant", "content": [{"type": "text", "text": "Sure, here it is..."}]},
]

SAMPLE_STATS = {"turns": 2, "input_tokens": 100, "output_tokens": 200, "tool_calls": 1}


# ══════════════════════════════════════════════════════════════════════════════
# 数据结构测试
# ══════════════════════════════════════════════════════════════════════════════

class TestSessionMeta(unittest.TestCase):

    def _make_meta(self, updated_at=None) -> SessionMeta:
        return SessionMeta(
            id="abc12345",
            title="Test session",
            created_at="2025-01-01T10:00:00",
            updated_at=updated_at or "2025-01-01T10:05:00",
            provider="anthropic",
            model="claude-opus-4-5",
            turns=3,
            input_tokens=150,
            output_tokens=300,
            tool_calls=2,
            file_path="/tmp/abc12345.json",
            fmt="json",
        )

    def test_fields_accessible(self):
        m = self._make_meta()
        self.assertEqual(m.id, "abc12345")
        self.assertEqual(m.turns, 3)
        self.assertEqual(m.input_tokens, 150)

    def test_age_str_returns_string(self):
        m = self._make_meta()
        age = m.age_str
        self.assertIsInstance(age, str)
        self.assertTrue(len(age) > 0)

    def test_age_str_recent(self):
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        m = self._make_meta(updated_at=now_iso)
        age = m.age_str
        # Should be a non-empty string (recent = seconds or the timestamp itself)
        self.assertIsInstance(age, str)
        self.assertGreater(len(age), 0)


class TestSession(unittest.TestCase):

    def _make_session(self) -> Session:
        return Session(
            id="abc12345",
            title="Test",
            created_at="2025-01-01T10:00:00",
            updated_at="2025-01-01T10:05:00",
            provider="anthropic",
            model="claude-opus-4-5",
            stats=SAMPLE_STATS,
            history=SAMPLE_HISTORY,
            fmt="json",
            file_path="/tmp/test.json",
        )

    def test_to_dict_has_required_keys(self):
        s = self._make_session()
        d = s.to_dict()
        for key in ("id", "title", "created_at", "updated_at",
                    "provider", "model", "stats", "history"):
            self.assertIn(key, d)

    def test_meta_property(self):
        s = self._make_session()
        m = s.meta
        self.assertIsInstance(m, SessionMeta)
        self.assertEqual(m.id, s.id)
        self.assertEqual(m.turns, SAMPLE_STATS["turns"])

    def test_to_dict_history_preserved(self):
        s = self._make_session()
        d = s.to_dict()
        self.assertEqual(len(d["history"]), len(SAMPLE_HISTORY))


# ══════════════════════════════════════════════════════════════════════════════
# _serialize_history 测试
# ══════════════════════════════════════════════════════════════════════════════

class TestSerializeHistory(unittest.TestCase):

    def test_plain_dict_content_unchanged(self):
        history = [{"role": "user", "content": "hello"}]
        result = _serialize_history(history)
        self.assertEqual(result[0]["content"], "hello")

    def test_list_content_kept(self):
        history = [{"role": "assistant", "content": [{"type": "text", "text": "Hi"}]}]
        result = _serialize_history(history)
        self.assertEqual(result[0]["content"][0]["text"], "Hi")

    def test_sdk_object_serialized(self):
        """SDK ContentBlock 对象（非 dict）应被转换为 dict。"""
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "response text"
        del mock_block.id   # no id attribute
        del mock_block.name
        del mock_block.input

        history = [{"role": "assistant", "content": [mock_block]}]
        result = _serialize_history(history)
        block = result[0]["content"][0]
        self.assertIsInstance(block, dict)
        self.assertEqual(block["type"], "text")
        self.assertEqual(block["text"], "response text")

    def test_empty_history(self):
        self.assertEqual(_serialize_history([]), [])

    def test_mixed_content(self):
        """混合 dict 和 SDK 对象的 content 列表。"""
        mock_block = MagicMock()
        mock_block.type = "tool_use"
        mock_block.id   = "tc_1"
        mock_block.name = "bash"
        mock_block.input = {"command": "ls"}
        del mock_block.text

        history = [{"role": "assistant", "content": [
            {"type": "text", "text": "I'll run it"},
            mock_block,
        ]}]
        result = _serialize_history(history)
        content = result[0]["content"]
        self.assertEqual(content[0]["text"], "I'll run it")
        self.assertEqual(content[1]["type"], "tool_use")
        self.assertEqual(content[1]["name"], "bash")


# ══════════════════════════════════════════════════════════════════════════════
# SessionManager — JSON 格式测试
# ══════════════════════════════════════════════════════════════════════════════

class TestSessionManagerJSON(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mgr = make_mgr(self.tmp, fmt="json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_session_dir_created(self):
        self.assertTrue(self.mgr.session_dir.exists())

    def test_new_session_has_unique_id(self):
        s1 = self.mgr.new_session("anthropic", "claude-opus-4-5")
        s2 = self.mgr.new_session("anthropic", "claude-opus-4-5")
        self.assertNotEqual(s1.id, s2.id)

    def test_new_session_default_title(self):
        s = self.mgr.new_session("openai", "gpt-4o")
        self.assertEqual(s.title, "New session")

    def test_save_creates_file(self):
        s = self.mgr.new_session("anthropic", "claude-opus-4-5")
        path = self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        self.assertTrue(Path(path).exists())

    def test_save_file_is_valid_json(self):
        s = self.mgr.new_session("anthropic", "claude-opus-4-5")
        path = self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        data = json.loads(Path(path).read_text())
        self.assertIn("id", data)
        self.assertIn("history", data)

    def test_save_extracts_title_from_history(self):
        s = self.mgr.new_session("anthropic", "claude-opus-4-5")
        self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        self.assertNotEqual(s.title, "New session")
        self.assertIn("Hello", s.title)

    def test_save_long_title_truncated(self):
        s = self.mgr.new_session("anthropic", "claude-opus-4-5")
        long_msg = "A" * 100
        history = [{"role": "user", "content": long_msg}]
        self.mgr.save(s, history=history, stats=SAMPLE_STATS)
        self.assertLessEqual(len(s.title), 43)  # 40 + "…"

    def test_save_updates_updated_at(self):
        s = self.mgr.new_session("anthropic", "claude-opus-4-5")
        old_ts = s.created_at
        time.sleep(0.01)
        self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        # updated_at should be >= created_at
        self.assertGreaterEqual(s.updated_at, old_ts)

    def test_save_same_file_on_update(self):
        s = self.mgr.new_session("anthropic", "claude-opus-4-5")
        path1 = self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        path2 = self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        self.assertEqual(path1, path2)

    def test_load_returns_session(self):
        s = self.mgr.new_session("anthropic", "claude-opus-4-5")
        self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        loaded = self.mgr.load(s.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.id, s.id)

    def test_load_history_preserved(self):
        s = self.mgr.new_session("anthropic", "claude-opus-4-5")
        self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        loaded = self.mgr.load(s.id)
        self.assertEqual(len(loaded.history), len(SAMPLE_HISTORY))

    def test_load_stats_preserved(self):
        s = self.mgr.new_session("anthropic", "claude-opus-4-5")
        self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        loaded = self.mgr.load(s.id)
        self.assertEqual(loaded.stats["turns"], SAMPLE_STATS["turns"])
        self.assertEqual(loaded.stats["input_tokens"], SAMPLE_STATS["input_tokens"])

    def test_load_prefix_match(self):
        s = self.mgr.new_session("anthropic", "claude-opus-4-5")
        self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        # Use only first 4 chars of id
        loaded = self.mgr.load(s.id[:4])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.id, s.id)

    def test_load_nonexistent_returns_none(self):
        result = self.mgr.load("nonexistent-id-xyz")
        self.assertIsNone(result)

    def test_list_sessions_returns_metas(self):
        for i in range(3):
            s = self.mgr.new_session("anthropic", f"model-{i}")
            self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        metas = self.mgr.list_sessions()
        self.assertEqual(len(metas), 3)

    def test_list_sessions_sorted_by_mtime_desc(self):
        s1 = self.mgr.new_session("anthropic", "m1")
        self.mgr.save(s1, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        time.sleep(0.05)
        s2 = self.mgr.new_session("anthropic", "m2")
        self.mgr.save(s2, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        metas = self.mgr.list_sessions()
        # most recent first
        self.assertEqual(metas[0].id, s2.id)

    def test_list_sessions_respects_limit(self):
        for i in range(5):
            s = self.mgr.new_session("anthropic", f"model-{i}")
            self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        metas = self.mgr.list_sessions(limit=3)
        self.assertEqual(len(metas), 3)

    def test_list_sessions_empty_dir(self):
        metas = self.mgr.list_sessions()
        self.assertEqual(metas, [])

    def test_delete_removes_file(self):
        s = self.mgr.new_session("anthropic", "claude-opus-4-5")
        self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        ok = self.mgr.delete(s.id)
        self.assertTrue(ok)
        self.assertEqual(self.mgr.list_sessions(), [])

    def test_delete_nonexistent_returns_false(self):
        self.assertFalse(self.mgr.delete("nonexistent-id"))

    def test_meta_has_all_fields(self):
        s = self.mgr.new_session("anthropic", "claude-opus-4-5")
        self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        metas = self.mgr.list_sessions()
        m = metas[0]
        self.assertEqual(m.turns, SAMPLE_STATS["turns"])
        self.assertEqual(m.provider, "anthropic")
        self.assertEqual(m.model, "claude-opus-4-5")


# ══════════════════════════════════════════════════════════════════════════════
# SessionManager — JSONL 格式测试
# ══════════════════════════════════════════════════════════════════════════════

class TestSessionManagerJSONL(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mgr = make_mgr(self.tmp, fmt="jsonl")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_creates_jsonl_file(self):
        s = self.mgr.new_session("openai", "gpt-4o")
        path = self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        self.assertTrue(str(path).endswith(".jsonl"))
        self.assertTrue(Path(str(path)).exists())

    def test_jsonl_first_line_is_meta(self):
        s = self.mgr.new_session("openai", "gpt-4o")
        path = self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        lines = Path(path).read_text().strip().splitlines()
        meta = json.loads(lines[0])
        self.assertIn("id", meta)
        self.assertNotIn("history", meta)  # history not in first line

    def test_jsonl_subsequent_lines_are_history(self):
        s = self.mgr.new_session("openai", "gpt-4o")
        path = self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        lines = Path(path).read_text().strip().splitlines()
        # lines[0] = meta, lines[1:] = history entries
        self.assertEqual(len(lines) - 1, len(SAMPLE_HISTORY))

    def test_jsonl_load_roundtrip(self):
        s = self.mgr.new_session("openai", "gpt-4o")
        self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        loaded = self.mgr.load(s.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.id, s.id)
        self.assertEqual(len(loaded.history), len(SAMPLE_HISTORY))

    def test_jsonl_list_reads_first_line_only(self):
        """list_sessions 对 JSONL 应只读第一行（快速）。"""
        s = self.mgr.new_session("openai", "gpt-4o")
        self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        metas = self.mgr.list_sessions()
        self.assertEqual(len(metas), 1)
        self.assertEqual(metas[0].id, s.id)

    def test_jsonl_fmt_field(self):
        s = self.mgr.new_session("openai", "gpt-4o")
        self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        loaded = self.mgr.load(s.id)
        self.assertEqual(loaded.fmt, "jsonl")


# ══════════════════════════════════════════════════════════════════════════════
# 边界情况测试
# ══════════════════════════════════════════════════════════════════════════════

class TestSessionManagerEdgeCases(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mgr = make_mgr(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_empty_history(self):
        s = self.mgr.new_session("anthropic", "claude-opus-4-5")
        path = self.mgr.save(s, history=[], stats=SAMPLE_STATS)
        self.assertTrue(Path(path).exists())
        loaded = self.mgr.load(s.id)
        self.assertEqual(loaded.history, [])

    def test_save_title_not_updated_if_no_user_message(self):
        s = self.mgr.new_session("anthropic", "claude-opus-4-5")
        # Only assistant message
        history = [{"role": "assistant", "content": "Hello"}]
        self.mgr.save(s, history=history, stats=SAMPLE_STATS)
        self.assertEqual(s.title, "New session")

    def test_load_corrupted_file_returns_none(self):
        """损坏的 JSON 文件不应崩溃。"""
        corrupt = self.mgr.session_dir / "aaaaaaaa_20250101_000000.json"
        corrupt.write_text("{ not valid json }", encoding="utf-8")
        result = self.mgr.load("aaaaaaaa")
        self.assertIsNone(result)

    def test_list_skips_corrupted_files(self):
        """损坏的文件不影响其他 session 的列举。"""
        s = self.mgr.new_session("anthropic", "claude-opus-4-5")
        self.mgr.save(s, history=SAMPLE_HISTORY, stats=SAMPLE_STATS)
        corrupt = self.mgr.session_dir / "xxxxxxxx_20250101_000000.json"
        corrupt.write_text("bad json", encoding="utf-8")
        metas = self.mgr.list_sessions()
        # Should still return the valid session
        self.assertEqual(len(metas), 1)
        self.assertEqual(metas[0].id, s.id)

    def test_multiple_files_same_id_loads_latest(self):
        """同一 id 有多个文件时（异常情况），应加载最新的。"""
        s = self.mgr.new_session("anthropic", "claude-opus-4-5")
        # Create two files with same id prefix
        f1 = self.mgr.session_dir / f"{s.id}_20250101_100000.json"
        f2 = self.mgr.session_dir / f"{s.id}_20250101_120000.json"
        data = s.to_dict()
        data["history"] = [{"role": "user", "content": "old"}]
        f1.write_text(json.dumps(data), encoding="utf-8")
        data2 = s.to_dict()
        data2["history"] = [{"role": "user", "content": "new"}, {"role": "user", "content": "new2"}]
        f2.write_text(json.dumps(data2), encoding="utf-8")
        # Touch f2 to make it newer
        import os
        os.utime(f2, (time.time() + 10, time.time() + 10))
        loaded = self.mgr.load(s.id)
        self.assertIsNotNone(loaded)
        # Should load the file with "new" content
        self.assertEqual(len(loaded.history), 2)


# ══════════════════════════════════════════════════════════════════════════════
# Agent 集成测试
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentSessionIntegration(unittest.TestCase):

    def setUp(self):
        import tools.builtin  # noqa
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_agent(self, auto_save=True):
        from agent import Agent
        from config import load_config
        from permissions import PermissionGuard
        from llm.base import LLMResponse, LLMUsage

        cfg = load_config(
            project_root=self.tmp,
            session_dir=self.tmp / "sessions",
            session_fmt="json",
            auto_save_session=auto_save,
        )
        cfg.stream = False

        mock_llm = MagicMock()
        mock_llm.chat.return_value = LLMResponse(
            text="Hello!", tool_calls=[], usage=LLMUsage(5, 10, 15), stop_reason="end_turn"
        )
        guard = PermissionGuard(auto_approve=True)
        return Agent(cfg=cfg, guard=guard, llm_client=mock_llm)

    def test_session_id_assigned_on_init(self):
        agent = self._make_agent()
        self.assertIsNotNone(agent.session_id)
        self.assertEqual(len(agent.session_id), 8)

    def test_session_manager_created(self):
        agent = self._make_agent()
        self.assertIsNotNone(agent.session_manager)

    def test_auto_save_disabled(self):
        agent = self._make_agent(auto_save=False)
        self.assertIsNone(agent.session_manager)
        self.assertIsNone(agent.session_id)

    def test_run_turn_creates_session_file(self):
        agent = self._make_agent()
        agent.run_turn("Hello")
        sessions_dir = self.tmp / "sessions"
        files = list(sessions_dir.glob("*.json"))
        self.assertEqual(len(files), 1)

    def test_session_file_contains_history(self):
        agent = self._make_agent()
        agent.run_turn("Hello")
        files = list((self.tmp / "sessions").glob("*.json"))
        data = json.loads(files[0].read_text())
        self.assertTrue(len(data["history"]) >= 1)
        self.assertEqual(data["history"][0]["role"], "user")
        self.assertEqual(data["history"][0]["content"], "Hello")

    def test_session_file_contains_stats(self):
        agent = self._make_agent()
        agent.run_turn("Hello")
        files = list((self.tmp / "sessions").glob("*.json"))
        data = json.loads(files[0].read_text())
        self.assertIn("stats", data)
        self.assertEqual(data["stats"]["turns"], 1)

    def test_multiple_turns_update_same_file(self):
        agent = self._make_agent()
        agent.run_turn("Turn 1")
        agent.run_turn("Turn 2")
        files = list((self.tmp / "sessions").glob("*.json"))
        self.assertEqual(len(files), 1)  # same file updated
        data = json.loads(files[0].read_text())
        self.assertEqual(data["stats"]["turns"], 2)

    def test_save_session_manual(self):
        agent = self._make_agent()
        agent.run_turn("Hello")
        path = agent.save_session()
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).exists())

    def test_load_session_restores_history(self):
        # Save a session with one agent
        agent1 = self._make_agent()
        agent1.run_turn("Remember this")
        sid = agent1.session_id

        # Load into a fresh agent
        agent2 = self._make_agent()
        ok = agent2.load_session(sid)
        self.assertTrue(ok)
        self.assertEqual(agent2.session_id, sid)
        self.assertTrue(len(agent2.history) > 0)
        # First user message should be preserved
        user_msgs = [m for m in agent2.history if m["role"] == "user"]
        self.assertIn("Remember this", user_msgs[0]["content"])

    def test_load_session_restores_stats(self):
        agent1 = self._make_agent()
        agent1.run_turn("Hello")
        sid = agent1.session_id

        agent2 = self._make_agent()
        agent2.load_session(sid)
        self.assertEqual(agent2.stats.turns, 1)

    def test_load_nonexistent_session_returns_false(self):
        agent = self._make_agent()
        ok = agent.load_session("nonexistent-id-xyz")
        self.assertFalse(ok)

    def test_session_file_path_accessible(self):
        agent = self._make_agent()
        agent.run_turn("Hello")
        path = agent.session_file
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).exists())


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
