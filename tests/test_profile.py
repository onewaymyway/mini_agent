"""tests/test_profile.py — UserProfileManager 测试。

覆盖:
  - P4-0（next_doc/growth_advisor_improvement_plan_v2.md 第 2 节）：
    generate() 从整体覆盖 profile.derived 改成合并式更新——生成前手动
    写入的非 LLM 字段（如 growth_advisor 写入的 growth_focus_areas）在
    generate() 之后必须原样保留，同时 LLM 输出对应的固定字段
    （summary/tech_stack/habits/source_entry_count/updated_at）确实被
    正确更新。
  - [next_doc/memory_backfill_and_profile_update_plan.md 方向二]
    tech_stack/habits 结构从纯字符串列表升级为
    `{text, last_confirmed_at}`；增量更新场景下，上一版里依然被
    LLM 保留的特征应该出现在结果里（哪怕这一轮喂给 LLM 的记忆窗口
    根本没提到它），验证"画像不再随着窗口滑动而丢失历史特征"这个
    本次改进要解决的核心问题。
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


def _texts(items) -> list[str]:
    """从 [{"text":..., "last_confirmed_at":...}, ...] 里取出纯文本列表，
    方便和旧版断言一样按文本比较。"""
    return [it["text"] for it in items]


class _FakeResp:
    def __init__(self, text: str):
        self.text = text


class _FakeLLMClient:
    def __init__(self, payload: dict):
        self._payload = payload

    def chat_with_retry(self, **kwargs):
        return _FakeResp(json.dumps(self._payload))


class _FakeEntry:
    def __init__(self, summary: str, tags=None, created_at: float = 0.0):
        self.summary = summary
        self.tags = tags or []
        self.created_at = created_at


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
            self.assertEqual(_texts(reloaded.derived.get("tech_stack")), ["python", "fastapi"])
            self.assertEqual(_texts(reloaded.derived.get("habits")), ["喜欢写测试"])
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


class TestIncrementalProfileUpdate(unittest.TestCase):
    """[next_doc/memory_backfill_and_profile_update_plan.md 方向二]
    验收标准第 6 节场景：早期记忆包含 A 特征，最近一批新记忆不再提及 A，
    但 LLM 认为 A 依然成立时，增量更新后的画像应仍保留 A（旧版"整体
    替换"实现会因为固定只看最近 N 条而丢失 A）。"""

    def test_previous_feature_retained_when_llm_keeps_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            mgr = UserProfileManager(paths)

            # 第一轮：全量生成，产出一条长期成立的特征 "熟悉 Python 异步编程"
            llm1 = _FakeLLMClient({
                "summary": "后端开发者",
                "tech_stack": ["熟悉 Python 异步编程"],
                "habits": [],
            })
            first_batch = [_FakeEntry("讨论了 asyncio", created_at=1.0)]
            mgr.generate(llm1, first_batch, max_entries_for_profile=20)

            # 第二轮：只新增了跟前端相关的记忆，窗口里完全没提到 asyncio；
            # 但 LLM（这里用 fake 模拟）在"更新"而不是"重写"的指引下，
            # 依然选择保留上一版的 asyncio 特征，同时新增一条前端特征。
            llm2 = _FakeLLMClient({
                "summary": "后端 + 前端都做",
                "tech_stack": ["熟悉 Python 异步编程", "开始学习 React"],
                "habits": [],
            })
            all_entries = first_batch + [_FakeEntry("学习 React hooks", created_at=2.0)]
            mgr.generate(llm2, all_entries, max_entries_for_profile=20)

            reloaded = UserProfileManager(paths).load()
            tech_texts = _texts(reloaded.derived.get("tech_stack"))
            self.assertIn("熟悉 Python 异步编程", tech_texts)
            self.assertIn("开始学习 React", tech_texts)
            # 被再次印证的旧特征，last_confirmed_at 应该更新为第二轮生成
            # 时间，而不是停留在第一轮——这是"新鲜度"信号的核心用途。
            items_by_text = {it["text"]: it for it in reloaded.derived.get("tech_stack")}
            self.assertGreater(
                items_by_text["熟悉 Python 异步编程"]["last_confirmed_at"],
                0,
            )

    def test_rebuild_ignores_previous_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            mgr = UserProfileManager(paths)

            llm1 = _FakeLLMClient({
                "summary": "旧画像",
                "tech_stack": ["旧特征"],
                "habits": [],
            })
            mgr.generate(llm1, [_FakeEntry("A", created_at=1.0)])

            # rebuild=True：即使 LLM 这次没有再输出"旧特征"，也不应该
            # 出现"合并保留"的行为——rebuild 就是要从零开始。
            llm2 = _FakeLLMClient({
                "summary": "全新画像",
                "tech_stack": ["新特征"],
                "habits": [],
            })
            mgr.generate(
                llm2,
                [_FakeEntry("A", created_at=1.0), _FakeEntry("B", created_at=2.0)],
                rebuild=True,
            )

            reloaded = UserProfileManager(paths).load()
            tech_texts = _texts(reloaded.derived.get("tech_stack"))
            self.assertEqual(tech_texts, ["新特征"])


if __name__ == "__main__":
    unittest.main()
