"""tests/test_user_requirement_capture.py

对应 next_doc/user_requirement_notepad_capture_plan.md。
"""
from __future__ import annotations

import json
import tempfile
import types
from pathlib import Path

import pytest

from mini_agent.history.user_requirement_capture import maybe_capture_user_requirement
from mini_agent.tools.notepad import (
    configure_notepad_store,
    reset_notepad_cache,
    get_current_notepad,
)
from mini_agent.storage.paths import AgentPaths


class _FakeLLMHelper:
    def __init__(self, response_text: str):
        self.response_text = response_text
        self.calls = []

    def ask(self, prompt, *, system="", max_retries=3):
        self.calls.append({"prompt": prompt, "system": system})
        return self.response_text


class _FakeCfg:
    def __init__(self, notepad_enabled=True, auto_capture_enabled=True):
        self.notepad_enabled = notepad_enabled
        self.auto_capture_user_requirement_enabled = auto_capture_enabled


class _FakeAgent:
    def __init__(self, response_text="", cfg=None):
        self.cfg = cfg or _FakeCfg()
        self.llm_helper = _FakeLLMHelper(response_text)


@pytest.fixture()
def notepad_env():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = AgentPaths(root)
        session_id = "test-session"
        configure_notepad_store(lambda: paths, lambda: session_id, lambda: True)
        yield paths, session_id
        reset_notepad_cache(session_id)


def _resp(should_update, action="add", entry_id=None, content=""):
    return json.dumps(
        {
            "should_update": should_update,
            "action": action,
            "entry_id": entry_id,
            "content": content,
        }
    )


def test_disabled_by_config_skips_llm_call(notepad_env):
    agent = _FakeAgent(response_text=_resp(True, content="x"), cfg=_FakeCfg(auto_capture_enabled=False))
    maybe_capture_user_requirement(agent, "请一定不要删除 config.json，这是硬性约束")
    assert agent.llm_helper.calls == []
    store = get_current_notepad()
    assert store.is_empty()


def test_notepad_globally_disabled_skips_llm_call(notepad_env):
    agent = _FakeAgent(response_text=_resp(True, content="x"), cfg=_FakeCfg(notepad_enabled=False))
    maybe_capture_user_requirement(agent, "请一定不要删除 config.json，这是硬性约束")
    assert agent.llm_helper.calls == []


def test_too_short_input_skips_llm_call(notepad_env):
    agent = _FakeAgent(response_text=_resp(True, content="x"))
    maybe_capture_user_requirement(agent, "继续")
    assert agent.llm_helper.calls == []


def test_should_update_false_no_write(notepad_env):
    agent = _FakeAgent(response_text=_resp(False))
    maybe_capture_user_requirement(agent, "今天天气怎么样啊随便聊聊")
    assert len(agent.llm_helper.calls) == 1
    store = get_current_notepad()
    assert store.is_empty()


def test_add_new_entry(notepad_env):
    agent = _FakeAgent(response_text=_resp(True, action="add", content="必须使用中文注释"))
    maybe_capture_user_requirement(agent, "以后所有代码注释都必须用中文写，这是硬性要求")
    store = get_current_notepad()
    entries = store.to_list()
    assert len(entries) == 1
    assert entries[0]["tag"] == "auto_requirement"
    assert entries[0]["content"] == "必须使用中文注释"


def test_update_existing_entry(notepad_env):
    paths, session_id = notepad_env
    store = get_current_notepad()
    existing = store.add("旧内容：只在 dev 分支改动", tag="auto_requirement")

    agent = _FakeAgent(
        response_text=_resp(True, action="update", entry_id=existing.id, content="更新：dev+staging 分支都可以改")
    )
    maybe_capture_user_requirement(agent, "更正一下，dev 和 staging 分支都可以直接改")

    store2 = get_current_notepad()
    entries = store2.to_list()
    assert len(entries) == 1
    assert entries[0]["id"] == existing.id
    assert "staging" in entries[0]["content"]


def test_update_with_unknown_entry_id_falls_back_to_add(notepad_env):
    agent = _FakeAgent(
        response_text=_resp(True, action="update", entry_id="doesnotexist", content="新的要求内容")
    )
    maybe_capture_user_requirement(agent, "这是一条新的明确要求，请务必记住它")
    store = get_current_notepad()
    entries = store.to_list()
    assert len(entries) == 1
    assert entries[0]["content"] == "新的要求内容"


def test_manual_entries_not_visible_or_touched(notepad_env):
    store = get_current_notepad()
    manual = store.add("agent 自己记的笔记，不应该被自动捕获碰到", tag="manual")

    agent = _FakeAgent(response_text=_resp(True, action="add", content="自动捕获的新条目"))
    maybe_capture_user_requirement(agent, "这是一条需要记住的明确要求，请照做")

    store2 = get_current_notepad()
    entries = {e["id"]: e for e in store2.to_list()}
    assert manual.id in entries
    assert entries[manual.id]["content"] == "agent 自己记的笔记，不应该被自动捕获碰到"
    # prompt 里不应包含 manual tag 的条目内容
    prompt = agent.llm_helper.calls[0]["prompt"]
    assert "agent 自己记的笔记" not in prompt


def test_malformed_llm_response_is_silently_ignored(notepad_env):
    agent = _FakeAgent(response_text="not a json at all, just prose")
    maybe_capture_user_requirement(agent, "这是一句正常长度的用户输入内容")
    store = get_current_notepad()
    assert store.is_empty()


def test_llm_exception_is_silently_ignored(notepad_env):
    class _RaisingHelper:
        def ask(self, prompt, *, system="", max_retries=3):
            raise RuntimeError("boom")

    agent = _FakeAgent()
    agent.llm_helper = _RaisingHelper()
    # 不应抛出异常
    maybe_capture_user_requirement(agent, "这是一句正常长度的用户输入内容")
    store = get_current_notepad()
    assert store.is_empty()


def test_no_notepad_store_configured_is_noop():
    reset_notepad_cache()
    configure_notepad_store(lambda: None, lambda: "")
    agent = _FakeAgent(response_text=_resp(True, content="x"))
    # 不应抛出异常，且不应调用 LLM（store 为 None 提前返回）
    maybe_capture_user_requirement(agent, "这是一句正常长度的用户输入内容")
    assert agent.llm_helper.calls == []
