"""灵感构思 B6-3：产物的全部内容都能经对话修改（删除、按序号修改世界观条目、剧情的幕/转折点等）。
所有操作都只是提议：采纳前卡片不变。"""

import json
from pathlib import Path

import pytest

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import chat as CH
from novel_writer.brainstorm import options as O
from novel_writer.brainstorm import proposals as P
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "港")
    w.create("港")
    C.put_module(w, "ideas", [{"id": "i1", "text": "甲", "tags": [], "source": "author", "status": "keep"},
                              {"id": "i2", "text": "乙", "tags": [], "source": "author", "status": "keep"}])
    C.put_module(w, "open_questions", [{"id": "q1", "text": "问一", "closed": False}])
    C.put_module(w, "world", {"rules": [{"text": "规则甲"}, {"text": "规则乙"}], "locations": [],
                              "factions": [], "timeline": ["公元前一年"], "taboos": ["禁忌甲"]})
    C.put_module(w, "cast", {"lin": {"name": "林", "role": "主角", "relations": []},
                             "bo": {"name": "伯", "role": "配角", "relations": []}})
    C.put_module(w, "plot", {"acts": [{"title": "起", "beats": ["上岸", "遇见"]},
                                      {"title": "承", "beats": ["追逐"]}],
                             "twists": ["船长是幽灵", "钟会说话"], "endings": ["留下", "离开"]})
    return w


def _accept(ws, module, op, target=None, value=None):
    item = P.create(ws, module, op, value, target=target)
    return P.accept(ws, item["pid"])


# ── 删除 ──

def test_delete_idea_and_open_question(ws):
    _accept(ws, "ideas", "delete", "i1")
    assert [i["id"] for i in C.get_module(ws, "ideas")] == ["i2"]
    _accept(ws, "open_questions", "delete", "q1")
    assert C.get_module(ws, "open_questions") == []


def test_delete_cast_member(ws):
    _accept(ws, "cast", "delete", "bo")
    assert list(C.get_module(ws, "cast")) == ["lin"]


def test_delete_missing_target_keeps_proposal_pending(ws):
    item = P.create(ws, "cast", "delete", None, target="nope")
    with pytest.raises(BrainstormError):
        P.accept(ws, item["pid"])
    assert P.list_items(ws, "pending")[0]["error"]
    assert "bo" in C.get_module(ws, "cast")                          # 卡片未变


# ── 世界观：按序号修改与删除 ──

def test_world_set_and_delete_by_index(ws):
    _accept(ws, "world", "set", "rules:1", {"text": "规则乙改"})
    assert [r["text"] for r in C.get_module(ws, "world")["rules"]] == ["规则甲", "规则乙改"]
    _accept(ws, "world", "delete", "rules:0")
    assert [r["text"] for r in C.get_module(ws, "world")["rules"]] == ["规则乙改"]


def test_world_timeline_and_taboo_set_and_delete(ws):
    _accept(ws, "world", "set", "timeline:0", "公元一年")
    _accept(ws, "world", "delete", "taboos:0")
    world = C.get_module(ws, "world")
    assert world["timeline"] == ["公元一年"] and world["taboos"] == []


def test_world_index_out_of_range_rejected(ws):
    item = P.create(ws, "world", "delete", None, target="rules:9")
    with pytest.raises(BrainstormError):
        P.accept(ws, item["pid"])
    assert len(C.get_module(ws, "world")["rules"]) == 2


def test_world_set_or_delete_without_index_is_refused_at_creation(ws):
    with pytest.raises(BrainstormError):
        P.create(ws, "world", "delete", None, target="rules")
    with pytest.raises(BrainstormError):
        P.create(ws, "world", "set", {"text": "x"}, target="nolist:0")


def test_world_add_still_appends(ws):
    _accept(ws, "world", "add", "locations", {"text": "雾岬"})
    assert C.get_module(ws, "world")["locations"] == [{"text": "雾岬"}]


# ── 角色修改（已有 set） ──

def test_cast_set_partial_fields(ws):
    _accept(ws, "cast", "set", "lin", {"role": "反派"})
    assert C.get_module(ws, "cast")["lin"]["role"] == "反派"


# ── 剧情：幕、转折点、反转、结局 ──

def test_plot_act_title_and_beat_crud(ws):
    _accept(ws, "plot", "set", "act:0", {"title": "起（改）"})
    _accept(ws, "plot", "add", "act:1", "新的转折")
    _accept(ws, "plot", "set", "act:0:beat:1", "重逢")
    _accept(ws, "plot", "delete", "act:0:beat:0")
    acts = C.get_module(ws, "plot")["acts"]
    assert acts[0] == {"title": "起（改）", "beats": ["重逢"]}
    assert acts[1]["beats"] == ["追逐", "新的转折"]


def test_plot_delete_whole_act(ws):
    _accept(ws, "plot", "delete", "act:1")
    assert [a["title"] for a in C.get_module(ws, "plot")["acts"]] == ["起"]


def test_plot_twists_and_endings_set_add_delete(ws):
    _accept(ws, "plot", "set", "twists:0", "船长其实是孩子")
    _accept(ws, "plot", "add", "endings", "开放结局")
    _accept(ws, "plot", "delete", "endings:0")
    plot = C.get_module(ws, "plot")
    assert plot["twists"] == ["船长其实是孩子", "钟会说话"]
    assert plot["endings"] == ["离开", "开放结局"]


def test_plot_bad_targets_are_refused(ws):
    with pytest.raises(BrainstormError):
        P.create(ws, "plot", "delete", None, target="scene:0")
    with pytest.raises(BrainstormError):
        P.create(ws, "plot", "add", "文字", target=None)
    item = P.create(ws, "plot", "set", "act:7", target="act:7")
    item2 = P.create(ws, "plot", "delete", None, target="act:0:beat:9")
    with pytest.raises(BrainstormError):
        P.accept(ws, item["pid"])
    with pytest.raises(BrainstormError):
        P.accept(ws, item2["pid"])


def test_plot_whole_replace_still_works(ws):
    _accept(ws, "plot", "set", None, {"endings": ["只剩一个"]})
    plot = C.get_module(ws, "plot")
    assert plot["endings"] == ["只剩一个"] and len(plot["acts"]) == 2


# ── 备选 ──

def test_option_delete_discards_it(ws):
    meta = O.create(ws, "悲剧", "plot", None, "")
    _accept(ws, "options", "delete", meta["oid"])
    assert O.list_options(ws)[0]["status"] == "discarded"


# ── 对话中的提议 ──

def test_parse_reply_accepts_delete_without_value_but_not_set():
    body = ("好。\n```proposals\n" + json.dumps([
        {"module": "cast", "op": "delete", "target": "bo"},
        {"module": "cast", "op": "set", "target": "lin"},
    ], ensure_ascii=False) + "\n```")
    text, items, invalid = CH.parse_reply(body)
    assert text == "好。"
    assert [i["op"] for i in items] == ["delete"] and invalid == 1


def test_prompt_documents_every_operation(ws):
    prompt = CH.build_prompt(ws, "你好", "追问")
    for needle in ("delete", "列表名:序号", "act:i", "twists:k", "endings:k"):
        assert needle in prompt


class _Runner:
    def __init__(self, reply):
        self.reply = reply

    def __call__(self, prompt, sid):
        return self.reply, "s"


def test_chat_delete_proposal_waits_for_confirmation(ws):
    before = C.content_hash(ws)
    reply = "这条规则与后文冲突，建议删除。\n```proposals\n" + json.dumps(
        [{"module": "world", "op": "delete", "target": "rules:0", "reason": "冲突"}], ensure_ascii=False) + "\n```"
    out = CH.send(ws, "规则太多了", runner=_Runner(reply))
    assert C.content_hash(ws) == before                              # 确认前不落卡
    pid = out["proposals"][0]["pid"]
    P.accept(ws, pid)
    assert [r["text"] for r in C.get_module(ws, "world")["rules"]] == ["规则乙"]
