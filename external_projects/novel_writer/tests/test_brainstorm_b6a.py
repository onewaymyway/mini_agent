"""灵感构思 B6-1：历史对话列表、切换、新开对话（仅限当前构思项目）。"""

import json
from pathlib import Path

import pytest

from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import chat as CH
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace

PROJECT = Path(__file__).resolve().parent.parent


class FakeRunner:
    def __init__(self, replies, session_id="sess-1"):
        self.replies = list(replies)
        self.session_id = session_id
        self.calls = []

    def __call__(self, prompt, sid):
        self.calls.append((prompt, sid))
        return self.replies.pop(0), self.session_id


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "港")
    w.create("港")
    return w


def _say(ws, text, reply="好。", conv=None, sid="s"):
    return CH.send(ws, text, runner=FakeRunner([reply], session_id=sid), conv=conv)


# ── 迁移与索引 ──

def test_empty_project_has_no_conversations(ws):
    assert CH.history(ws) == []
    assert CH.list_conversations(ws) == {"current": None, "items": []}
    assert CH.current_conversation(ws) is None


def _write_legacy(ws, records):
    """模拟旧版代码写下的单段对话文件（不经过新的索引）。"""
    path = ws.root / CH.TRANSCRIPT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")


def test_legacy_single_transcript_is_adopted_without_moving_files(ws):
    _write_legacy(ws, [{"role": "user", "text": "旧版的话", "at": "x"},
                       {"role": "assistant", "text": "旧版的回复", "at": "x"}])
    res = CH.list_conversations(ws)
    assert len(res["items"]) == 1
    only = res["items"][0]
    assert only["id"] == CH.LEGACY_ID and only["current"] is True
    assert only["turns"] == 2 and only["title"] == "旧版的话"
    assert (ws.root / CH.TRANSCRIPT).is_file()                       # 旧文件原地保留
    assert [h["text"] for h in CH.history(ws)] == ["旧版的话", "旧版的回复"]


def test_first_send_creates_a_conversation_with_title(ws):
    _say(ws, "我想写一座会移动的岛")
    items = CH.list_conversations(ws)["items"]
    assert len(items) == 1
    assert items[0]["title"] == "我想写一座会移动的岛"
    assert items[0]["turns"] == 2
    assert CH.current_conversation(ws) == items[0]["id"]


def test_long_title_is_truncated(ws):
    _say(ws, "这是一段非常非常长的开场白用来测试标题截断效果是否生效")
    title = CH.list_conversations(ws)["items"][0]["title"]
    assert title.endswith("…") and len(title) == CH.TITLE_MAX + 1


# ── 新开与切换 ──

def test_new_chat_switches_and_keeps_old_one(ws):
    _say(ws, "第一段", reply="回复甲")
    first = CH.current_conversation(ws)
    created = CH.new_chat(ws)
    second = CH.current_conversation(ws)
    assert created["id"] == second and second != first
    assert CH.history(ws) == []                                      # 新对话是空的
    _say(ws, "第二段", reply="回复乙")
    assert [h["text"] for h in CH.history(ws, conv=first)] == ["第一段", "回复甲"]
    assert [h["text"] for h in CH.history(ws)] == ["第二段", "回复乙"]


def test_new_chat_reuses_an_empty_current_conversation(ws):
    _say(ws, "有内容")
    CH.new_chat(ws)
    before = len(CH.list_conversations(ws)["items"])
    CH.new_chat(ws)                                                  # 当前是空的，不再堆积
    assert len(CH.list_conversations(ws)["items"]) == before


def test_switch_back_restores_history_and_agent_session(ws):
    _say(ws, "甲的话", sid="sess-A")
    a = CH.current_conversation(ws)
    CH.new_chat(ws)
    _say(ws, "乙的话", sid="sess-B")
    CH.switch_chat(ws, a)
    assert CH.current_conversation(ws) == a
    runner = FakeRunner(["又回来了"])
    CH.send(ws, "接着说", runner=runner)
    assert runner.calls[0][1] == "sess-A"                            # 续接的是甲自己的框架会话
    assert "甲的话" in runner.calls[0][0]                             # 提示词只带甲的最近记录
    assert "乙的话" not in runner.calls[0][0]


def test_list_is_most_recent_first(ws):
    _say(ws, "甲")
    a = CH.current_conversation(ws)
    CH.new_chat(ws)
    _say(ws, "乙")
    b = CH.current_conversation(ws)
    CH.switch_chat(ws, a)
    _say(ws, "甲再说一句")                                            # 甲被更新，应排到最前
    ids = [i["id"] for i in CH.list_conversations(ws)["items"]]
    assert ids[0] == a and ids[1] == b
    assert CH.list_conversations(ws)["current"] == a


def test_switch_to_unknown_conversation_is_rejected(ws):
    _say(ws, "x")
    with pytest.raises(BrainstormError):
        CH.switch_chat(ws, "c_nope")


def test_conversations_never_leak_across_projects(tmp_path):
    a = BrainstormWorkspace(tmp_path / "甲项目")
    a.create("甲项目")
    b = BrainstormWorkspace(tmp_path / "乙项目")
    b.create("乙项目")
    _say(a, "只属于甲")
    _say(b, "只属于乙")
    titles_a = [i["title"] for i in CH.list_conversations(a)["items"]]
    titles_b = [i["title"] for i in CH.list_conversations(b)["items"]]
    assert titles_a == ["只属于甲"] and titles_b == ["只属于乙"]


def test_turn_reply_stays_in_the_conversation_it_started_in(ws, monkeypatch):
    """后台轮次绑定开始时的对话：即使之后切换，回复仍写回原来那一段。"""
    import time
    gate = {"go": False}

    def slow(prompt, sid):
        while not gate["go"]:
            time.sleep(0.01)
        return ("迟到的回复", "sess-slow")

    CH.send(ws, "先", runner=FakeRunner(["ok"]))
    origin = CH.current_conversation(ws)
    turn = CH.start_turn(ws, "后台的话", "追问", runner=slow)
    CH.new_chat(ws)                                                  # 中途切到新对话
    gate["go"] = True
    for _ in range(300):
        if CH.run_state(ws, turn)["status"] != "running":
            break
        time.sleep(0.01)
    assert CH.run_state(ws, turn)["status"] == "done"
    assert CH.run_state(ws, turn)["conversation"] == origin
    assert [h["text"] for h in CH.history(ws, conv=origin)][-1] == "迟到的回复"
    assert CH.history(ws) == []                                      # 新对话仍是空的


def test_corrupt_index_is_rebuilt_from_disk(ws):
    _say(ws, "新对话的话", reply="新回复")
    cid = CH.current_conversation(ws)
    (ws.root / CH.CONV_INDEX).write_text("{坏的", encoding="utf-8")
    assert CH.current_conversation(ws) == cid                        # 从对话目录重建，不丢对话
    assert [h["text"] for h in CH.history(ws)] == ["新对话的话", "新回复"]


def test_missing_index_adopts_legacy_and_continues_it(ws):
    _write_legacy(ws, [{"role": "user", "text": "旧", "at": "x"}])
    _say(ws, "新")                                                   # 旧版对话即当前对话：继续写在它后面，不另开一段
    items = CH.list_conversations(ws)["items"]
    assert len(items) == 1 and items[0]["id"] == CH.LEGACY_ID
    assert [h["text"] for h in CH.history(ws)] == ["旧", "新", "好。"]


# ── 接口与页面 ──

def test_api_list_switch_new(tmp_path):
    root = BA.create_project("雾", base=str(tmp_path))["data"]["root"]
    BA.chat_send(root, "第一段", "追问", runner=FakeRunner(["好"]))
    first = BA.chat_list(root)["data"]["current"]
    assert BA.chat_new(root)["ok"]
    listed = BA.chat_list(root)["data"]
    assert len(listed["items"]) == 2 and listed["current"] != first
    assert BA.chat_switch(root, first)["ok"]
    assert BA.chat_list(root)["data"]["current"] == first
    assert BA.chat_history(root)["data"][0]["text"] == "第一段"
    assert BA.chat_switch(root, "c_nope")["ok"] is False


def test_page_renders_conversation_picker(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    root = BA.create_project("岛", base=str(tmp_path))["data"]["root"]
    CH.send(BrainstormWorkspace(root), "第一段的话", runner=FakeRunner(["回复"]))
    CH.new_chat(BrainstormWorkspace(root))
    monkeypatch.setenv("NOVEL_PROJECT", str(PROJECT))
    monkeypatch.setattr("novel_writer.brainstorm.api.default_root", lambda: tmp_path / "none")
    at = AppTest.from_file(str(PROJECT / "tests" / "apps" / "brainstorm_page_app.py"), default_timeout=30)
    at.session_state["bs_root"] = root
    at.run()
    assert not at.exception, at.exception
    labels = [sb.label for sb in at.selectbox]
    assert "历史对话（仅本项目）" in labels
    assert any(b.label == "新对话" for b in at.button)
