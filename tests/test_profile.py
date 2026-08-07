"""tests/test_profile.py — UserProfileManager 测试。

覆盖 P4-0（next_doc/growth_advisor_improvement_plan_v2.md 第 2 节）：
generate() 从整体覆盖 profile.derived 改成合并式更新——生成前手动写入的
非 LLM 字段（如 growth_advisor 写入的 growth_focus_areas）在 generate()
之后必须原样保留，同时 LLM 输出对应的固定字段（summary/tech_stack/habits/
source_entry_count/updated_at）确实被正确更新。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_agent.profile import PROFILE_GENERATED_KEYS, UserProfileManager
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class _FakeResp:
    def __init__(self, text: str):
        self.text = text


class _FakeLLMClient:
    def __init__(self, payload: dict):
        self._payload = payload

    def chat_with_retry(self, **kwargs):
        return _FakeResp(json.dumps(self._payload))


class _FakeEntry:
    def __init__(self, summary: str, tags=None):
        self.summary = summary
        self.tags = tags or []


class TestGenerateMergesDerived(unittest.TestCase):
    def test_generate_preserves_foreign_keys_in_derived(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            mgr = UserProfileManager(paths)
            profile = mgr.load()
            # 模拟 growth_advisor 已经写入过的字段
            profile.derived = {
                "growth_focus_areas": {"Python 工程实践": ["e1", "e2"]},
                "growth_focus_areas_updated_at": 123.0,
                "growth_topic_keywords": {"摄影": {"keywords": ["摄影"]}},
            }
            mgr.save()

            llm = _FakeLLMClient({
                "summary": "热衷后端开发",
                "tech_stack": ["python", "fastapi"],
                "habits": ["喜欢写测试"],
            })
            entries = [_FakeEntry("聊了 python packaging"), _FakeEntry("写了 pytest")]
            mgr.generate(llm, entries)

            reloaded = UserProfileManager(paths).load()
            self.assertEqual(
                reloaded.derived.get("growth_focus_areas"),
                {"Python 工程实践": ["e1", "e2"]},
            )
            self.assertEqual(reloaded.derived.get("growth_focus_areas_updated_at"), 123.0)
            self.assertEqual(
                reloaded.derived.get("growth_topic_keywords"),
                {"摄影": {"keywords": ["摄影"]}},
            )
            self.assertEqual(reloaded.derived.get("summary"), "热衷后端开发")
            self.assertEqual(reloaded.derived.get("tech_stack"), ["python", "fastapi"])
            self.assertEqual(reloaded.derived.get("habits"), ["喜欢写测试"])
            self.assertEqual(reloaded.derived.get("source_entry_count"), 2)

    def test_generate_only_overwrites_known_generated_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            mgr = UserProfileManager(paths)
            profile = mgr.load()
            profile.derived = {"summary": "旧的总结", "custom_marker": "keep-me"}
            mgr.save()

            llm = _FakeLLMClient({"summary": "新的总结", "tech_stack": [], "habits": []})
            mgr.generate(llm, [])

            reloaded = UserProfileManager(paths).load()
            self.assertEqual(reloaded.derived.get("summary"), "新的总结")
            self.assertEqual(reloaded.derived.get("custom_marker"), "keep-me")
            self.assertEqual(
                set(PROFILE_GENERATED_KEYS),
                {"summary", "tech_stack", "habits", "source_entry_count", "updated_at"},
            )


if __name__ == "__main__":
    unittest.main()
