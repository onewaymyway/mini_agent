"""灵感构思工作室 B4：剧情骨架编辑、备选方案（创建/对比/合并/撤销/放弃）、备选提议、收敛模式。"""

from pathlib import Path

import pytest

from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import chat as CH
from novel_writer.brainstorm import edit as E
from novel_writer.brainstorm import options as O
from novel_writer.brainstorm import proposals as P
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace

PROJECT = Path(__file__).resolve().parent.parent


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "塔")
    w.create("塔")
    C.put_module(w, "plot", {"acts": [{"title": "上升", "beats": ["入塔"]}],
                             "twists": ["守塔人是父亲"], "endings": ["开放式结局"]})
    C.put_module(w, "premise", "一座会记住访客的塔")
    return w


# ── 剧情骨架 ──

def test_plot_act_and_beat_editing(ws):
    idx = E.add_act(ws, "下降")
    assert idx == 1
    E.add_beat(ws, 1, "失去塔的钥匙")
    E.remove_beat(ws, 0, 0)
    plot = C.get_module(ws, "plot")
    assert plot["acts"][1] == {"title": "下降", "beats": ["失去塔的钥匙"]}
    assert plot["acts"][0]["beats"] == []
    E.remove_act(ws, 1)
    assert len(C.get_module(ws, "plot")["acts"]) == 1


def test_plot_lists_editing_and_errors(ws):
    E.add_plot_item(ws, "twists", "塔本身是活的")
    E.remove_plot_item(ws, "endings", 0)
    plot = C.get_module(ws, "plot")
    assert plot["twists"] == ["守塔人是父亲", "塔本身是活的"] and plot["endings"] == []
    with pytest.raises(BrainstormError):
        E.add_plot_item(ws, "magic", "x")
    with pytest.raises(BrainstormError):
        E.add_beat(ws, 9, "x")
    with pytest.raises(BrainstormError):
        E.add_act(ws, "  ")
    with pytest.raises(BrainstormError):
        E.remove_beat(ws, 0, 9)


# ── 备选方案 ──

def test_create_option_copies_main_by_default_and_does_not_touch_main(ws):
    main_before = C.get_module(ws, "plot")
    opt = O.create(ws, "开放式结局", "plot", notes="留下余地")
    assert opt["status"] == "open" and opt["module"] == "plot"
    assert O.content(ws, opt["oid"]) == main_before
    assert C.get_module(ws, "plot") == main_before                     # 创建备选不改主线


def test_create_option_with_explicit_content(ws):
    alt = {"acts": [{"title": "另一条路", "beats": []}], "twists": [], "endings": ["闭合结局"]}
    opt = O.create(ws, "闭合结局", "plot", content=alt)
    assert O.content(ws, opt["oid"])["endings"] == ["闭合结局"]


def test_create_option_validates(ws):
    with pytest.raises(BrainstormError):
        O.create(ws, "  ", "plot")
    with pytest.raises(BrainstormError):
        O.create(ws, "x", "cast")                                        # 不支持的模块
    with pytest.raises(BrainstormError):
        O.create(ws, "x", "plot", content={"acts": "坏了"})             # 内容不合法


def test_diff_shows_difference_against_main(ws):
    opt = O.create(ws, "改结局", "plot", content={"acts": [{"title": "上升", "beats": ["入塔"]}],
                                                   "twists": ["守塔人是父亲"], "endings": ["闭合结局"]})
    text = O.diff(ws, opt["oid"])
    assert "-" in text and "闭合结局" in text and "开放式结局" in text
    same = O.create(ws, "同样", "plot")
    assert O.diff(ws, same["oid"]) == "（与主线相同）"


def test_merge_replaces_main_and_keeps_backup_then_undo(ws):
    original = C.get_module(ws, "plot")
    opt = O.create(ws, "闭合", "plot", content={"acts": [], "twists": [], "endings": ["闭合结局"]})
    merged = O.merge(ws, opt["oid"])
    assert merged["status"] == "merged"
    assert C.get_module(ws, "plot")["endings"] == ["闭合结局"]
    assert O.content(ws, opt["oid"])["endings"] == ["闭合结局"]          # 备选保留
    with pytest.raises(BrainstormError):
        O.merge(ws, opt["oid"])                                          # 不能重复合并
    O.undo_merge(ws, opt["oid"])
    assert C.get_module(ws, "plot") == original
    assert O.list_options(ws)[0]["status"] == "open"


def test_discard_is_final_and_blocks_merge(ws):
    opt = O.create(ws, "弃", "premise", content="另一个命题")
    O.discard(ws, opt["oid"])
    assert O.list_options(ws)[0]["status"] == "discarded"
    with pytest.raises(BrainstormError):
        O.merge(ws, opt["oid"])
    with pytest.raises(BrainstormError):
        O.undo_merge(ws, opt["oid"])                                     # 没合并过，不能撤销
    assert C.get_module(ws, "premise") == "一座会记住访客的塔"


def test_premise_option_merge(ws):
    opt = O.create(ws, "命题乙", "premise", content="一座会还债的塔")
    O.merge(ws, opt["oid"])
    assert C.get_module(ws, "premise") == "一座会还债的塔"


def test_list_options_filters_by_module(ws):
    O.create(ws, "剧情甲", "plot")
    O.create(ws, "命题甲", "premise", content="x")
    assert {o["name"] for o in O.list_options(ws, "plot")} == {"剧情甲"}
    assert len(O.list_options(ws)) == 2


# ── 备选提议（AI） ──

def test_option_proposal_creates_option_only_after_accept(ws):
    class R:
        def __call__(self, prompt, sid):
            return ('好。\n```proposals\n[{"module": "options", "op": "add", '
                    '"value": {"name": "AI 的第二条路", "module": "plot", "notes": "更黑暗"}, "reason": "对照"}]\n```', None)
    out = CH.send(ws, "给我两个结局", "收敛", runner=R())
    pid = out["proposals"][0]["pid"]
    assert O.list_options(ws) == []                                      # 未确认：没有备选
    P.accept(ws, pid)
    opts = O.list_options(ws)
    assert len(opts) == 1 and opts[0]["name"] == "AI 的第二条路"
    assert C.get_module(ws, "plot")["endings"] == ["开放式结局"]          # 主线不变


def test_option_proposal_validation(ws):
    with pytest.raises(BrainstormError):
        P.create(ws, "options", "add", {"name": "x", "module": "cast"})
    with pytest.raises(BrainstormError):
        P.create(ws, "options", "add", {"module": "plot"})
    with pytest.raises(BrainstormError):
        P.create(ws, "options", "set", {"name": "x", "module": "plot"}, target="o1")


def test_converge_mode_is_available_and_prompted(ws):
    seen = {}

    class R:
        def __call__(self, prompt, sid):
            seen["p"] = prompt
            return ("收敛回复", None)
    out = CH.send(ws, "帮我收敛", "收敛", runner=R())
    assert out["reply"] == "收敛回复"
    assert "收束成 2–3 个备选方向" in seen["p"]
    assert "options 的格式" in seen["p"]


# ── 接口与页面 ──

def test_api_plot_and_options(tmp_path):
    root = BA.create_project("灯", base=str(tmp_path))["data"]["root"]
    assert BA.plot_add_act(root, "起")["ok"]
    assert BA.plot_add_beat(root, 0, "点亮")["ok"]
    oid = BA.option_create(root, "备", "plot")["data"]["oid"]
    assert BA.option_diff(root, oid)["ok"]
    assert BA.option_merge(root, oid)["ok"]
    assert BA.option_undo_merge(root, oid)["ok"]
    assert BA.option_discard(root, oid)["ok"]
    assert BA.option_merge(root, oid)["ok"] is False


def test_page_has_plot_tab(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    root = BA.create_project("灯塔", base=str(tmp_path))["data"]["root"]
    BA.plot_add_act(root, "起")
    monkeypatch.setenv("NOVEL_PROJECT", str(PROJECT))
    monkeypatch.setattr("novel_writer.brainstorm.api.default_root", lambda: tmp_path / "none")
    at = AppTest.from_file(str(PROJECT / "tests" / "apps" / "brainstorm_page_app.py"), default_timeout=30)
    at.session_state["bs_root"] = root
    at.run()
    assert not at.exception, at.exception
    assert [t.label for t in at.tabs] == ["对话", "题材卡", "灵感池", "发散", "世界观", "角色", "剧情", "质检", "交接"]
