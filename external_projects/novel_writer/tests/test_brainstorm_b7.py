"""灵感构思 B7：上下文范围（摘要 / 读取文件）与只读文件工具。
访问范围为整个构思项目目录；没有读取大小或调用次数的限制；没有写入工具。"""

import os
from pathlib import Path

import pytest

from novel_writer.brainstorm import chat as CH
from novel_writer.brainstorm import studio_tools as ST
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "港")
    w.create("港")
    (w.root / "notes").mkdir(exist_ok=True)
    (w.root / "notes" / "world.md").write_text("第一行\n钟楼在雾里\n第三行\n", encoding="utf-8")
    return w


def _tool(ws, name):
    return ST.build_registry(ws.root).get(name).fn


# ── 上下文范围设置 ──

def test_default_scope_is_summary_and_choice_persists(ws):
    assert CH.get_scope(ws) == "summary"
    CH.set_scope(ws, "read")
    assert CH.get_scope(ws) == "read"
    assert (ws.root / CH.SETTINGS_FILE).is_file()


def test_unknown_scopes_are_rejected(ws):
    with pytest.raises(BrainstormError):
        CH.set_scope(ws, "magic")
    assert CH.get_scope(ws) == "summary"


# ── 工具集合：只读，且只有三个 ──

def test_registry_has_exactly_three_read_only_tools(ws):
    reg = ST.build_registry(ws.root)
    assert sorted(reg.names) == sorted(ST.TOOL_NAMES)
    assert all(reg.get(n).requires_approval is False for n in ST.TOOL_NAMES)
    assert not any(w in n for n in reg.names for w in ("write", "edit", "delete", "remove"))


# ── 读取与搜索 ──

def test_list_read_grep_within_project(ws):
    listing = _tool(ws, "studio_list")("")
    assert "[目录] notes/" in listing
    assert "[目录] notes/" in _tool(ws, "studio_list")("")
    full = _tool(ws, "studio_read")("notes/world.md")
    assert "2\t钟楼在雾里" in full and "3\t第三行" in full
    part = _tool(ws, "studio_read")("notes/world.md", start_line=2, end_line=2)
    assert part == "2\t钟楼在雾里"
    hits = _tool(ws, "studio_grep")("钟楼")
    assert hits == "notes/world.md:2: 钟楼在雾里"


def test_reads_are_not_size_limited(ws):
    big = "甲" * 300_000
    (ws.root / "notes" / "big.md").write_text(big, encoding="utf-8")
    text = _tool(ws, "studio_read")("notes/big.md")
    assert big in text                            # 整篇读出，没有截断


def test_paths_outside_the_project_are_refused(ws, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("不能读", encoding="utf-8")
    read = _tool(ws, "studio_read")
    for bad in ("../secret.txt", str(secret), "notes/../../secret.txt"):
        with pytest.raises(BrainstormError):
            read(bad)
    with pytest.raises(BrainstormError):
        _tool(ws, "studio_list")("..")
    with pytest.raises(BrainstormError):
        _tool(ws, "studio_grep")("不能读", path="../")


def test_symlink_pointing_outside_is_refused(ws, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("外部内容", encoding="utf-8")
    link = ws.root / "notes" / "link.txt"
    os.symlink(outside, link)
    with pytest.raises(BrainstormError):
        _tool(ws, "studio_read")("notes/link.txt")


def test_scope_is_whole_project_including_internal_folders(ws):
    (ws.root / CH.RUNS_DIR).mkdir(parents=True, exist_ok=True)
    (ws.root / CH.RUNS_DIR / "x.jsonl").write_text("钟楼 日志", encoding="utf-8")
    assert "_studio/chat_runs/x.jsonl:1: 钟楼 日志" in _tool(ws, "studio_grep")("钟楼")
    CH.set_scope(ws, "read")
    assert "_studio/chat_runs/x.jsonl" in CH.build_prompt(ws, "x", "追问")


def test_grep_rejects_bad_regex(ws):
    with pytest.raises(BrainstormError):
        _tool(ws, "studio_grep")("(")


# ── 提示词：读取文件模式附文件索引，摘要模式不附 ──

def test_read_mode_prompt_lists_files_without_contents(ws):
    CH.set_scope(ws, "read")
    prompt = CH.build_prompt(ws, "看看世界观", "追问")
    assert "项目文件索引" in prompt and "notes/world.md" in prompt
    assert "第一行" not in prompt and "钟楼在雾里" not in prompt       # 内容没有直接附上
    assert "studio_read" in prompt


def test_summary_mode_prompt_has_no_file_index(ws):
    prompt = CH.build_prompt(ws, "看看世界观", "追问")
    assert "项目文件索引" not in prompt


# ── 运行器：读取模式使用只读 Agent，并续接会话 ──

class _FakeAgent:
    created = []

    def __init__(self, root):
        self.root = root
        self.prompts = []
        self.loaded = None
        self.session_id = "sess-read-1"
        _FakeAgent.created.append(self)

    def load_session(self, sid):
        self.loaded = sid
        return True

    def run_turn(self, prompt):
        self.prompts.append(prompt)
        return "我读了 notes/world.md"

    def save_session(self):
        return "path"


def test_read_mode_send_uses_read_agent_and_saves_session(ws, monkeypatch):
    _FakeAgent.created = []
    monkeypatch.setattr(ST, "build_read_agent", lambda root: _FakeAgent(root))
    CH.set_scope(ws, "read")
    out = CH.send(ws, "世界观是什么")
    agent = _FakeAgent.created[0]
    assert Path(agent.root) == ws.root
    assert "notes/world.md" in agent.prompts[0]
    assert out["reply"] == "我读了 notes/world.md"
    # 第二轮续接同一段对话的框架会话
    CH.send(ws, "再看一下")
    assert _FakeAgent.created[1].loaded == "sess-read-1"


def test_read_mode_session_is_per_conversation(ws, monkeypatch):
    _FakeAgent.created = []
    monkeypatch.setattr(ST, "build_read_agent", lambda root: _FakeAgent(root))
    CH.set_scope(ws, "read")
    CH.send(ws, "第一段")
    first = CH.current_conversation(ws)
    CH.new_chat(ws)
    CH.send(ws, "第二段")
    assert _FakeAgent.created[1].loaded is None          # 新对话不续接旧会话
    CH.switch_chat(ws, first)
    CH.send(ws, "回到第一段")
    assert _FakeAgent.created[2].loaded == "sess-read-1"


def test_read_mode_proposals_still_need_confirmation(ws, monkeypatch):
    import json
    from novel_writer.brainstorm import cards as C

    class _Proposing(_FakeAgent):
        def run_turn(self, prompt):
            return "建议改名。\n```proposals\n" + json.dumps(
                [{"module": "premise", "op": "set", "value": "新的命题"}], ensure_ascii=False) + "\n```"

    monkeypatch.setattr(ST, "build_read_agent", lambda root: _Proposing(root))
    CH.set_scope(ws, "read")
    before = C.content_hash(ws)
    out = CH.send(ws, "改命题")
    assert len(out["proposals"]) == 1
    assert C.content_hash(ws) == before                   # 读取模式也不能直接写入
