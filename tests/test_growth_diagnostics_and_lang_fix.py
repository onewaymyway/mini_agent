"""tests/test_growth_diagnostics_and_lang_fix.py — 覆盖
next_doc/growth_advisor_diagnostics_and_language_fix_plan.md 两个方向：

  1. `build_default_memory_store()` 能正确从 AgentPaths 构造出可读到
     真实记忆条目的 MemoryStore（回归此前 `MemoryStore(paths)` 误传参
     导致诊断面板"记忆总条数"永远是 0 的 bug）。
  2. `detect_primary_language()` 的语言检测启发式，以及
     `UserProfileManager.generate()` 把检测结果落盘为
     `derived["preferred_language"]`。
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.perception.memory_factory import build_default_memory_store
from mini_agent.perception.memory_store import MemoryEntry
from mini_agent.storage.paths import AgentPaths
from mini_agent.utils.lang_detect import DEFAULT_LANGUAGE, detect_primary_language


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class TestBuildDefaultMemoryStore(unittest.TestCase):
    def test_reads_entries_written_to_workdir_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            paths.workdir_memory.parent.mkdir(parents=True, exist_ok=True)
            now = time.time()
            entry = MemoryEntry(
                session_id="s1",
                summary="用户在调试 Python 打包问题",
                key_outcomes=[],
                tags=["python"],
                model="test-model",
                created_at=now,
                entry_id="e1",
            )
            writer_store = build_default_memory_store(paths)
            writer_store.add(entry)

            # 用一个新的、独立构造出来的 store 重新从磁盘读取，验证
            # build_default_memory_store 构造出来的路径是可跨实例读写的
            # 真实文件路径，而不是巧合地共用同一个内存态对象。
            reader_store = build_default_memory_store(paths)
            entries = reader_store.all_entries()

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].entry_id, "e1")

    def test_does_not_silently_wrap_agentpaths_as_path(self):
        # 回归测试：此前 `MemoryStore(paths)` 把整个 AgentPaths 实例当
        # 路径传入，`all_entries()` 会静默降级为空列表而不报错。这里
        # 断言修复后构造出来的 store 的内部路径确实是一个真实的文件路径
        # （workdir_memory），而不是 AgentPaths 实例本身。
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            store = build_default_memory_store(paths)
            self.assertEqual(store._path, paths.workdir_memory)
            self.assertIsInstance(store._path, Path)


class TestDetectPrimaryLanguage(unittest.TestCase):
    def test_empty_input_returns_default(self):
        self.assertEqual(detect_primary_language([]), DEFAULT_LANGUAGE)
        self.assertEqual(detect_primary_language(["", "   "]), DEFAULT_LANGUAGE)

    def test_detects_chinese(self):
        texts = ["用户正在调试一个 Python 打包相关的问题，习惯写单元测试"]
        self.assertEqual(detect_primary_language(texts), "zh")

    def test_detects_english(self):
        texts = ["The user is debugging a Python packaging issue and writes unit tests"]
        self.assertEqual(detect_primary_language(texts), "en")

    def test_detects_japanese_over_chinese(self):
        texts = ["ユーザーはPythonのパッケージングの問題をデバッグしています"]
        self.assertEqual(detect_primary_language(texts), "ja")

    def test_a_few_stray_cjk_chars_in_english_do_not_flip_result(self):
        texts = [
            "The user mentioned a Chinese term 中文 once but otherwise writes "
            "entirely in English across many long sentences about Python tooling."
        ]
        self.assertEqual(detect_primary_language(texts), "en")


class TestProfileGeneratePreferredLanguage(unittest.TestCase):
    def test_generate_writes_preferred_language_from_delta_entries(self):
        from mini_agent.profile import UserProfileManager

        class _FakeResp:
            text = json.dumps({
                "summary": "该用户是一名 Python 开发者",
                "tech_stack": ["Python"],
                "habits": ["写单元测试"],
            }, ensure_ascii=False)

        class _FakeLLMClient:
            def chat_with_retry(self, **kwargs):
                return _FakeResp()

        class _FakeEntry:
            def __init__(self, summary, tags, created_at):
                self.summary = summary
                self.tags = tags
                self.created_at = created_at

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            mgr = UserProfileManager(paths)
            now = time.time()
            entries = [
                _FakeEntry("用户在调试 Python 打包相关的问题", ["python"], now - 10),
                _FakeEntry("用户习惯先写单元测试再写实现", [], now - 5),
            ]

            profile = mgr.generate(_FakeLLMClient(), entries)

            self.assertEqual(profile.derived.get("preferred_language"), "zh")


if __name__ == "__main__":
    unittest.main()
