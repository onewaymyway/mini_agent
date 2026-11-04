"""灵感构思工作室 B2：对话、提议解析、确认前不落卡、采纳/拒绝/修改后采纳、会话续接。"""

import json
from pathlib import Path

import pytest

from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import chat as CH
from novel_writer.brainstorm import proposals as P
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace

PROJECT = Path(__file__).resolve().parent.parent


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "港")
    w.create("港")
    return w


def _reply(body, proposals=None):
    text = body
    if proposals is not None:
        text += "\n```proposals\n" + json.dumps(proposals, ensure_ascii=False) + "\n```"
    return text


class FakeRunner:
    """桩运行器：记录收到的 prompt 与 session id，回放预设回复；返回固定的会话 id。"""

    def __init__(self, replies, session_id="sess-1"):
        self.replies = list(replies)
        self.calls = []
        self.session_id = session_id

    def __call__(self, prompt, session_id):
        self.calls.append((prompt, session_id))
        return self.replies.pop(0), self.session_id


# ── 解析 ──

def test_parse_reply_extracts_block_and_counts_invalid():
    reply = _reply("你想让港口有怎样的代价？", [
        {"module": "premise", "op": "set", "value": "一个用记忆换航线的港口", "reason": "更具体"},
        {"module": "premise", "op": "set"},                  # 缺 value：无效
        "不是对象",
    ])
    body, items, invalid = CH.parse_reply(reply)
    assert body == "你想让港口有怎样的代价？"
    assert len(items) == 1 and items[0]["module"] == "premise"
    assert invalid == 2


def test_parse_reply_without_block_and_broken_json():
    body, items, invalid = CH.parse_reply("只是普通回复")
    assert (body, items, invalid) == ("只是普通回复", [], 0)
    broken = "正文\n```proposals\n[不是json\n```"
    body, items, invalid = CH.parse_reply(broken)
    assert body == "正文" and items == [] and invalid == 1


# ── 确认前不落卡（核心约束） ──

def test_send_creates_pending_proposals_and_does_not_touch_cards(ws):
    before = C.all_modules(ws)
    runner = FakeRunner([_reply("我想先问一个问题。", [
        {"module": "premise", "op": "set", "value": "一个用记忆换航线的港口", "reason": "更具体"},
        {"module": "ideas", "op": "add", "value": {"text": "潮汐会带回失踪者"}},
    ])])
    out = CH.send(ws, "我想写一个港口故事", "追问", runner=runner)
    assert out["reply"] == "我想先问一个问题。"
    assert len(out["proposals"]) == 2
    assert C.all_modules(ws) == before                                # 未确认：卡片一字不变
    assert len(P.list_items(ws, "pending")) == 2


def test_accept_applies_only_that_proposal(ws):
    runner = FakeRunner([_reply("好。", [
        {"module": "premise", "op": "set", "value": "命题A"},
        {"module": "ideas", "op": "add", "value": {"text": "灵感B"}},
    ])])
    out = CH.send(ws, "x", runner=runner)
    pid_premise = out["proposals"][0]["pid"]
    P.accept(ws, pid_premise)
    assert C.get_module(ws, "premise") == "命题A"
    assert C.get_module(ws, "ideas") == []                            # 另一条仍未采纳
    assert len(P.list_items(ws, "pending")) == 1


def test_reject_leaves_cards_unchanged(ws):
    runner = FakeRunner([_reply("好。", [{"module": "premise", "op": "set", "value": "不要的命题"}])])
    pid = CH.send(ws, "x", runner=runner)["proposals"][0]["pid"]
    P.reject(ws, pid)
    assert C.get_module(ws, "premise") == ""
    assert P.list_items(ws, "rejected")[0]["pid"] == pid
    with pytest.raises(BrainstormError):
        P.accept(ws, pid)                                              # 已拒绝不能再采纳


def test_accept_with_edited_value(ws):
    runner = FakeRunner([_reply("好。", [{"module": "premise", "op": "set", "value": "原文"}])])
    pid = CH.send(ws, "x", runner=runner)["proposals"][0]["pid"]
    P.accept(ws, pid, edited_value="改过的命题")
    assert C.get_module(ws, "premise") == "改过的命题"
    assert P.list_items(ws, "accepted")[0]["value"] == "改过的命题"


def test_invalid_accept_keeps_pending_and_records_error(ws):
    runner = FakeRunner([_reply("好。", [{"module": "ideas", "op": "set", "target": "i_nope", "value": {"text": "x"}}])])
    pid = CH.send(ws, "x", runner=runner)["proposals"][0]["pid"]
    with pytest.raises(BrainstormError):
        P.accept(ws, pid)
    item = P.list_items(ws, "pending")[0]
    assert item["pid"] == pid and "找不到灵感" in item["error"]
    assert C.get_module(ws, "ideas") == []


def test_unsupported_module_or_op_is_rejected_at_creation(ws):
    with pytest.raises(BrainstormError):
        P.create(ws, "nope", "set", "x")
    with pytest.raises(BrainstormError):
        P.create(ws, "premise", "add", "x")
    with pytest.raises(BrainstormError):
        P.create(ws, "ideas", "set", {"text": "x"})                     # 缺 target
    with pytest.raises(BrainstormError):
        P.create(ws, "world", "add", {"text": "x"})                     # 缺列表名


def test_send_skips_unsupported_items_and_counts_them(ws):
    runner = FakeRunner([_reply("好。", [{"module": "premise", "op": "add", "value": "x"},
                                          {"module": "premise", "op": "set", "value": "有效"}])])
    out = CH.send(ws, "x", runner=runner)
    assert len(out["proposals"]) == 1 and out["invalid"] == 1


# ── 各模块的应用 ──

def test_apply_each_module_kind(ws):
    cases = [
        ("brief", "set", None, {"genre": "悬疑"}, lambda w: C.get_module(w, "brief")["genre"] == "悬疑"),
        ("ideas", "add", None, {"text": "新灵感", "tags": ["标签"]}, lambda w: C.get_module(w, "ideas")[0]["source"] == "ai"),
        ("open_questions", "add", None, {"text": "弟弟还活着吗"}, lambda w: C.get_module(w, "open_questions")[0]["text"] == "弟弟还活着吗"),
        ("world", "add", "rules", {"text": "雾中可见死者", "cost": "代价"}, lambda w: C.get_module(w, "world")["rules"][0]["cost"] == "代价"),
        ("world", "add", "timeline", "三百年前港口沉没", lambda w: C.get_module(w, "world")["timeline"]),
        ("cast", "add", "c1", {"name": "林", "role": "主角"}, lambda w: C.get_module(w, "cast")["c1"]["name"] == "林"),
        ("plot", "set", None, {"acts": [{"title": "第一幕", "beats": ["出港"]}]}, lambda w: C.get_module(w, "plot")["acts"][0]["title"] == "第一幕"),
    ]
    for module, op, target, value, check in cases:
        item = P.create(ws, module, op, value, target=target)
        P.accept(ws, item["pid"])
        assert check(ws), module


def test_set_existing_idea_cast_and_question(ws):
    idea = {"id": "i1", "text": "旧", "tags": [], "source": "author", "status": "keep"}
    C.put_module(ws, "ideas", [idea])
    C.put_module(ws, "cast", {"c1": {"name": "林"}})
    C.put_module(ws, "open_questions", [{"id": "q1", "text": "问", "closed": False}])
    P.accept(ws, P.create(ws, "ideas", "set", {"text": "新", "status": "used"}, target="i1")["pid"])
    P.accept(ws, P.create(ws, "cast", "set", {"role": "主角"}, target="c1")["pid"])
    P.accept(ws, P.create(ws, "open_questions", "set", {"closed": True}, target="q1")["pid"])
    assert C.get_module(ws, "ideas")[0]["text"] == "新" and C.get_module(ws, "ideas")[0]["status"] == "used"
    assert C.get_module(ws, "cast")["c1"]["role"] == "主角"
    assert C.get_module(ws, "open_questions")[0]["closed"] is True


def test_set_missing_target_fails(ws):
    item = P.create(ws, "cast", "set", {"role": "x"}, target="c_missing")
    with pytest.raises(BrainstormError):
        P.accept(ws, item["pid"])


def test_duplicate_cast_id_rejected(ws):
    C.put_module(ws, "cast", {"c1": {"name": "林"}})
    item = P.create(ws, "cast", "add", {"name": "另一个"}, target="c1")
    with pytest.raises(BrainstormError):
        P.accept(ws, item["pid"])


# ── 对话记录与会话续接 ──

def test_transcript_and_session_continuity(ws):
    r1 = FakeRunner([_reply("第一轮回复")], session_id="sess-A")
    CH.send(ws, "第一句", runner=r1)
    assert r1.calls[0][1] is None                                       # 首轮没有会话
    r2 = FakeRunner([_reply("第二轮回复")], session_id="sess-A")
    CH.send(ws, "第二句", runner=r2)
    assert r2.calls[0][1] == "sess-A"                                  # 续接同一会话
    hist = CH.history(ws)
    assert [h["role"] for h in hist] == ["user", "assistant", "user", "assistant"]
    assert hist[0]["mode"] == "追问"


def test_prompt_carries_cards_and_recent_turns_even_without_session(ws):
    C.put_module(ws, "brief", {"genre": "悬疑"})
    CH.send(ws, "第一句", runner=FakeRunner([_reply("回复一")]))
    runner = FakeRunner([_reply("回复二")])
    CH.send(ws, "第二句", runner=runner)
    prompt = runner.calls[0][0]
    assert "悬疑" in prompt and "回复一" in prompt and "第二句" in prompt
    assert "```proposals" in prompt                                    # 告诉模型提议的格式


def test_new_chat_starts_fresh_session_and_keeps_old_conversation(ws):
    CH.send(ws, "a", runner=FakeRunner([_reply("b")], session_id="sess-B"))
    old = CH.current_conversation(ws)
    CH.new_chat(ws)                                  # B6-1：新开一段对话，旧的保留在列表里
    runner = FakeRunner([_reply("c")])
    CH.send(ws, "d", runner=runner)
    assert runner.calls[0][1] is None                # 新对话不续接旧会话
    assert [h["text"] for h in CH.history(ws)] == ["d", "c"]
    assert [h["text"] for h in CH.history(ws, conv=old)] == ["a", "b"]


def test_empty_message_and_bad_mode_rejected(ws):
    with pytest.raises(BrainstormError):
        CH.send(ws, "  ", runner=FakeRunner([]))
    with pytest.raises(BrainstormError):
        CH.send(ws, "x", mode="发散", runner=FakeRunner([]))


def test_corrupt_transcript_line_is_skipped(ws):
    CH.append_turn(ws, "user", "好的一行")
    with (ws.root / CH.TRANSCRIPT).open("a", encoding="utf-8") as fh:
        fh.write("{坏行\n")
    assert [h["text"] for h in CH.history(ws)] == ["好的一行"]


# ── 接口与页面 ──

def test_api_chat_and_proposal_flow(tmp_path):
    root = BA.create_project("岛", base=str(tmp_path))["data"]["root"]
    sent = BA.chat_send(root, "我想写一座岛", "追问", runner=FakeRunner([_reply("好。", [{"module": "premise", "op": "set", "value": "一座会移动的岛"}])]))
    assert sent["ok"] and len(sent["data"]["proposals"]) == 1
    pid = sent["data"]["proposals"][0]["pid"]
    assert BA.list_proposals(root)["data"][0]["pid"] == pid
    assert BA.accept_proposal(root, pid)["ok"]
    assert BA.get_project(root)["data"]["cards"]["premise"] == "一座会移动的岛"
    assert BA.reject_proposal(root, pid)["ok"] is False                 # 已采纳不能再拒绝
    assert BA.chat_history(root)["data"][0]["text"] == "我想写一座岛"


def test_page_dialogue_tab_renders_with_project(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    root = BA.create_project("雾", base=str(tmp_path))["data"]["root"]
    CH.send(BrainstormWorkspace(root), "你好", runner=FakeRunner([_reply("你好，想聊什么？")]))
    monkeypatch.setenv("NOVEL_PROJECT", str(PROJECT))
    monkeypatch.setattr("novel_writer.brainstorm.api.default_root", lambda: tmp_path / "none")
    at = AppTest.from_file(str(PROJECT / "tests" / "apps" / "brainstorm_page_app.py"), default_timeout=30)
    at.session_state["bs_root"] = root
    at.run()
    assert not at.exception, at.exception
    assert any("你好，想聊什么" in m.value for m in at.markdown) or any(
        "你好，想聊什么" in str(getattr(m, "value", "")) for m in at.main)
