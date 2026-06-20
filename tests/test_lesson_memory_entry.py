"""
tests/test_lesson_memory_entry.py — Stage 1.1 验证

对应 self_evolution_implementation_plan.md Stage 1.1：
  MemoryEntry 新增 lesson 专属字段（entry_type/trigger/outcome/root_cause/
  suggested_action/confidence/occurrence_count/source），全部带默认值保证
  现有 summary 型条目零迁移成本继续工作。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mini_agent.perception.memory_store import MemoryEntry, MemoryStore


def test_default_entry_type_is_summary():
    entry = MemoryEntry(
        session_id="s1", summary="测试摘要", key_outcomes=["完成了X"],
        tags=["python"], model="claude-opus-4-5",
    )
    assert entry.entry_type == "summary"
    assert entry.source == "self_reflection"
    assert entry.confidence == 0.5
    assert entry.occurrence_count == 1


def test_old_format_data_backward_compatible():
    """模拟磁盘上旧格式数据（无新字段），反序列化应使用默认值兜底。"""
    old_data = {
        "session_id": "s1", "summary": "旧摘要", "key_outcomes": [],
        "tags": [], "model": "claude-opus-4-5",
    }
    entry = MemoryEntry(**old_data)
    assert entry.entry_type == "summary"
    assert entry.trigger == ""
    assert entry.confidence == 0.5


def test_lesson_entry_fields():
    entry = MemoryEntry(
        session_id="s2", summary="", key_outcomes=[], tags=["lesson"],
        model="claude-opus-4-5", entry_type="lesson",
        trigger="bash 连续失败 3 次", outcome="权限不足",
        root_cause="目标目录无写权限", suggested_action="先检查权限",
        confidence=0.7, occurrence_count=3, source="self_reflection",
    )
    assert entry.entry_type == "lesson"
    assert entry.occurrence_count == 3


def test_to_search_text_summary_unchanged():
    """summary 型条目的 to_search_text() 行为应与扩展前完全一致。"""
    entry = MemoryEntry(
        session_id="s1", summary="修复了登录bug", key_outcomes=["用户认证修复"],
        tags=["auth", "bugfix"], model="claude-opus-4-5",
    )
    text = entry.to_search_text()
    assert text == "修复了登录bug 用户认证修复 auth bugfix"


def test_to_search_text_lesson_includes_extra_fields():
    """lesson 型条目应额外纳入 trigger/outcome/root_cause/suggested_action。"""
    entry = MemoryEntry(
        session_id="s2", summary="", key_outcomes=[], tags=["lesson"],
        model="claude-opus-4-5", entry_type="lesson",
        trigger="连续失败", outcome="权限问题", root_cause="目录权限",
        suggested_action="先检查权限",
    )
    text = entry.to_search_text()
    assert "连续失败" in text
    assert "权限问题" in text
    assert "目录权限" in text
    assert "先检查权限" in text


def test_entry_id_auto_generated():
    entry = MemoryEntry(session_id="s1", summary="x", key_outcomes=[], tags=[], model="m")
    assert len(entry.entry_id) == 12


@pytest.fixture
def tmp_store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(path=tmp_path / "memory.jsonl")


def test_lesson_entry_roundtrip_through_disk(tmp_store: MemoryStore):
    """lesson 条目写入磁盘后重新加载，所有字段应完整保留。"""
    entry = MemoryEntry(
        session_id="s3", summary="", key_outcomes=[], tags=["lesson"],
        model="claude-opus-4-5", entry_type="lesson",
        trigger="T", outcome="O", root_cause="R", suggested_action="A",
        confidence=0.65, occurrence_count=2, source="human_feedback",
    )
    tmp_store.add(entry)

    # 重新构造一个 store 实例，强制从磁盘加载
    reloaded = MemoryStore(path=tmp_store._path)
    reloaded._ensure_loaded()
    loaded_entries = [e for e in reloaded._entries if e.session_id == "s3"]
    assert len(loaded_entries) == 1
    loaded = loaded_entries[0]
    assert loaded.entry_type == "lesson"
    assert loaded.trigger == "T"
    assert loaded.outcome == "O"
    assert loaded.root_cause == "R"
    assert loaded.suggested_action == "A"
    assert loaded.confidence == 0.65
    assert loaded.occurrence_count == 2
    assert loaded.source == "human_feedback"


def test_mixed_summary_and_lesson_entries_coexist(tmp_store: MemoryStore):
    """同一存储文件中混合 summary 和 lesson 型条目应互不干扰。"""
    summary_entry = MemoryEntry(
        session_id="s4", summary="会话摘要", key_outcomes=["做了事情"],
        tags=["x"], model="m",
    )
    lesson_entry = MemoryEntry(
        session_id="s4", summary="", key_outcomes=[], tags=["lesson"],
        model="m", entry_type="lesson", trigger="t", outcome="o",
    )
    tmp_store.add(summary_entry)
    tmp_store.add(lesson_entry)

    tmp_store._ensure_loaded()
    types = {e.entry_type for e in tmp_store._entries if e.session_id == "s4"}
    assert types == {"summary", "lesson"}
