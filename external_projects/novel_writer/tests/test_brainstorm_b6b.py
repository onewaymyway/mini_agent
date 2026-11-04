"""灵感构思 B6-2：对话区上方的成果总览（只读）与「针对某一项的修改请求」。"""

import json
from pathlib import Path

import pytest

from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import chat as CH
from novel_writer.brainstorm import options as O
from novel_writer.brainstorm import proposals as PR
from novel_writer.brainstorm import outline as OL
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace

PROJECT = Path(__file__).resolve().parent.parent


class FakeRunner:
    def __init__(self, replies, session_id="s"):
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


def _fill(ws):
    C.put_module(ws, "premise", "一座会吞噬记忆的港口")
    C.put_module(ws, "brief", {"genre": "悬疑", "theme": "遗忘"})
    C.put_module(ws, "ideas", [
        {"id": "i1", "text": "雾里的钟", "tags": [], "source": "author", "status": "keep"},
        {"id": "i2", "text": "被丢弃的点子", "tags": [], "source": "author", "status": "drop"},
    ])
    C.put_module(ws, "world", {"rules": [{"text": "记忆换潮汐"}], "locations": [], "factions": [],
                               "timeline": [], "taboos": ["不能直呼海神之名"]})
    C.put_module(ws, "cast", {"lin": {"name": "林", "role": "主角", "relations": []}})
    C.put_module(ws, "plot", {"acts": [{"title": "起", "beats": ["上岸"]}], "twists": ["船长是幽灵"],
                              "endings": ["留下"]})
    C.put_module(ws, "open_questions", [{"id": "q1", "text": "钟为何停摆", "closed": False}])
    O.create(ws, "悲剧结局", "plot", None, "更黑暗")


def _section(secs, key):
    return next(s for s in secs if s["key"] == key)


# ── 成果总览 ──

def test_outputs_empty_project_has_all_sections_with_zero_counts(ws):
    secs = OL.outputs(ws)
    assert [s["key"] for s in secs] == list(OL.SECTIONS)
    assert all(s["count"] == 0 for s in secs)


def test_outputs_reflects_every_module(ws):
    _fill(ws)
    secs = OL.outputs(ws)
    assert _section(secs, "premise")["lines"] == ["一座会吞噬记忆的港口"]
    assert _section(secs, "brief")["count"] == 2
    assert _section(secs, "ideas")["lines"] == ["[保留] 雾里的钟"]        # 已丢弃的不展示
    assert _section(secs, "world")["lines"] == ["规则：记忆换潮汐", "禁忌：不能直呼海神之名"]
    assert _section(secs, "cast")["lines"] == ["林（主角）"]
    plot = _section(secs, "plot")["lines"]
    assert "第1幕 起" in plot and "    · 上岸" in plot and "反转：船长是幽灵" in plot
    assert _section(secs, "options")["lines"] == ["悲剧结局（plot）"]
    assert _section(secs, "open_questions")["lines"] == ["钟为何停摆"]


def test_outputs_is_read_only(ws):
    _fill(ws)
    before = C.content_hash(ws)
    OL.outputs(ws)
    assert C.content_hash(ws) == before


# ── 修改对象（focus） ──

def test_focus_block_carries_the_module_content(ws):
    _fill(ws)
    block = OL.focus_block(ws, "cast")
    assert "林（主角）" in block and "cast" in block and "只针对这一项" in block


def test_unknown_focus_rejected(ws):
    with pytest.raises(BrainstormError):
        OL.focus_block(ws, "nope")
    with pytest.raises(BrainstormError):
        CH.start_turn(ws, "x", "追问", runner=lambda p, s: ("ok", None), focus="nope")


def test_send_with_focus_puts_module_into_prompt_and_records_it(ws):
    _fill(ws)
    runner = FakeRunner(["好的"])
    CH.send(ws, "把主角改成女性", runner=runner, focus="cast")
    prompt = runner.calls[0][0]
    assert "本次修改请求针对：角色" in prompt and "林（主角）" in prompt
    assert CH.history(ws)[0]["focus"] == "cast"


def test_send_without_focus_has_no_focus_block(ws):
    runner = FakeRunner(["好"])
    CH.send(ws, "随便聊聊", runner=runner)
    assert "本次修改请求针对" not in runner.calls[0][0]


def test_focused_request_still_only_proposes_never_writes(ws):
    _fill(ws)
    before = C.content_hash(ws)
    reply = "我建议改名。\n```proposals\n" + json.dumps(
        [{"module": "cast", "op": "set", "target": "lin", "value": {"name": "林薇"}}], ensure_ascii=False) + "\n```"
    CH.send(ws, "改名", runner=FakeRunner([reply]), focus="cast")
    assert C.content_hash(ws) == before                              # 确认前不落卡
    assert len(PR.list_items(ws, "pending")) == 1


def test_start_turn_with_focus_is_recorded_and_used(ws):
    _fill(ws)
    import time
    turn = CH.start_turn(ws, "改一下", "追问", runner=lambda p, s: ("好", None), focus="premise")
    for _ in range(300):
        if CH.run_state(ws, turn)["status"] != "running":
            break
        time.sleep(0.01)
    assert CH.run_state(ws, turn)["status"] == "done"
    assert CH.history(ws)[0]["focus"] == "premise"


# ── 接口与页面 ──

def test_api_outputs(tmp_path):
    root = BA.create_project("雾", base=str(tmp_path))["data"]["root"]
    res = BA.outputs(root)
    assert res["ok"] and len(res["data"]) == len(OL.SECTIONS)


def test_page_shows_outputs_and_focus_target(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    root = BA.create_project("岛", base=str(tmp_path))["data"]["root"]
    BA.save_brief(root, {"genre": "悬疑"})
    monkeypatch.setenv("NOVEL_PROJECT", str(PROJECT))
    monkeypatch.setattr("novel_writer.brainstorm.api.default_root", lambda: tmp_path / "none")
    at = AppTest.from_file(str(PROJECT / "tests" / "apps" / "brainstorm_page_app.py"), default_timeout=30)
    at.session_state["bs_root"] = root
    at.session_state["bs_focus"] = "brief"
    at.run()
    assert not at.exception, at.exception
    assert any(e.label.startswith("📋 当前构思成果") for e in at.expander)
    assert any(b.label == "让AI修改" for b in at.button)
    assert any("本次修改对象：题材卡" in i.value for i in at.info)
