"""整理对话（手动）：范围与检查点、按模块整理、冲突标记与对话处理、Markdown 纪要。"""

import json
from pathlib import Path

import pytest

from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import chat as CH
from novel_writer.brainstorm import distill as DS
from novel_writer.brainstorm import proposals as P
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace

PROJECT = Path(__file__).resolve().parent.parent


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "岛")
    w.create("岛")
    C.put_module(w, "premise", "一座会移动的岛")
    return w


def _talk(ws, rounds=3):
    """用桩 agent 产生若干轮对话（作者一句 + 助手一句）。"""
    for i in range(rounds):
        CH.send(ws, f"作者第{i}句", "自由", runner=lambda p, s, i=i: (f"助手第{i}句", None))


def _gen(payload):
    def gen(prompt):
        gen.prompt = prompt
        return json.dumps(payload, ensure_ascii=False)
    return gen


BASE = {"candidates": [
    {"module": "cast", "op": "add", "value": {"name": "岛主", "role": "守岛人"}, "reason": "对话中确定", "source_turns": [0, 1]},
    {"module": "premise", "op": "set", "value": "一座沉睡的岛", "reason": "与现有命题不同", "source_turns": [2]},
    {"module": "world", "op": "add", "target": "rules", "value": {"text": "潮涨时岛会移动", "cost": "失去一个港口"}, "reason": "", "source_turns": [4]},
], "minutes": "## 讨论要点\n- 岛会移动 [0]\n## 已形成的决定\n- 有守岛人 [1]\n## 被否定的方向\n- 无\n## 仍待定的问题\n- 无"}


# ── 范围与检查点 ──

def test_pending_range_then_checkpoint_advances(ws):
    _talk(ws, 3)                                             # 6 条记录
    out = DS.run(ws, generator=_gen(BASE))
    assert out["range"] == [0, 6]
    kinds = {(p["module"], p["op"]) for p in out["proposals"]}
    assert ("cast", "add") in kinds and ("premise", "set") in kinds
    assert all(p["batch"] == out["batch"] and p["status"] == "pending" for p in out["proposals"])
    assert [p["pid"] for p in out["proposals"]]                            # 进入待确认列表
    with pytest.raises(BrainstormError):                                    # 没有新对话
        DS.run(ws, generator=_gen(BASE))
    _talk(ws, 1)
    out2 = DS.run(ws, generator=_gen({"candidates": [], "minutes": "空"}))
    assert out2["range"] == [6, 8]


def test_recent_scope_takes_last_rounds(ws):
    _talk(ws, 4)
    out = DS.run(ws, scope="recent", recent_rounds=1, generator=_gen({"candidates": [], "minutes": ""}))
    assert out["range"] == [6, 8]
    assert DS._checkpoint(ws) == 8


def test_checkpoint_not_advanced_on_failure(ws):
    _talk(ws, 2)
    with pytest.raises(BrainstormError):
        DS.run(ws, generator=lambda p: "没有 JSON")
    assert DS._checkpoint(ws) == 0
    assert DS.select_range(ws)[0] == 0


def test_no_proposals_applied_to_cards(ws):
    _talk(ws, 2)
    before = C.all_modules(ws)
    DS.run(ws, generator=_gen(BASE))
    assert C.all_modules(ws) == before


# ── 按模块整理 ──

def test_module_scope_drops_other_modules(ws):
    _talk(ws, 3)
    out = DS.run(ws, modules=["cast"], generator=_gen(BASE))
    assert {p["module"] for p in out["proposals"]} == {"cast"}
    assert any("模块不在所选范围内：premise" in s for s in out["skipped"])
    prompt = DS.build_prompt(ws, CH.history(ws), 0, ["cast"])
    assert "只提取以下模块：角色" in prompt and "核心命题（模块名" not in prompt


def test_unknown_module_rejected(ws):
    _talk(ws, 1)
    with pytest.raises(BrainstormError):
        DS.run(ws, modules=["magic"], generator=_gen(BASE))


# ── 冲突 ──

def test_conflicts_are_flagged_not_applied(ws):
    _talk(ws, 3)
    out = DS.run(ws, generator=_gen(BASE))
    premise = next(p for p in out["proposals"] if p["module"] == "premise")
    assert premise["conflict"] and "一座会移动的岛" in premise["conflict"]
    cast = next(p for p in out["proposals"] if p["module"] == "cast")
    assert cast["conflict"] == ""                                          # 新增角色不冲突
    assert C.get_module(ws, "premise") == "一座会移动的岛"


def test_duplicate_idea_is_skipped(ws):
    from novel_writer.brainstorm import ideas as I
    I.add_idea(ws, "潮水带回名字")
    _talk(ws, 1)
    payload = {"candidates": [{"module": "ideas", "op": "add", "value": {"text": "潮水带回名字"}, "source_turns": [0]}],
               "minutes": ""}
    out = DS.run(ws, modules=["ideas"], generator=_gen(payload))
    assert out["proposals"] == [] and any("已存在" in s for s in out["skipped"])


def test_conflict_prompt_carries_both_sides(ws):
    _talk(ws, 3)
    out = DS.run(ws, generator=_gen(BASE))
    pid = next(p["pid"] for p in out["proposals"] if p["conflict"])
    text = DS.conflict_prompt(ws, pid)
    assert "一座会移动的岛" in text and "一座沉睡的岛" in text and "proposals" in text


def test_conflict_prompt_refuses_non_conflict(ws):
    _talk(ws, 3)
    out = DS.run(ws, generator=_gen(BASE))
    pid = next(p["pid"] for p in out["proposals"] if not p["conflict"])
    with pytest.raises(BrainstormError):
        DS.conflict_prompt(ws, pid)


def test_conflict_resolved_through_dialogue(ws):
    _talk(ws, 3)
    out = DS.run(ws, generator=_gen(BASE))
    pid = next(p["pid"] for p in out["proposals"] if p["conflict"])
    msg = DS.conflict_prompt(ws, pid)

    class Resolver:
        def __call__(self, prompt, sid):
            assert "关于整理出的一条冲突" in prompt
            return ('我倾向保留原命题，但可以吸收“沉睡”。\n```proposals\n'
                    '[{"module": "premise", "op": "set", "value": "一座会移动、夜里沉睡的岛", "reason": "合并两者"}]\n```', None)
    CH.send(ws, msg, "自由", runner=Resolver())
    new = [p for p in P.list_items(ws, "pending") if p["module"] == "premise" and p["reason"] == "合并两者"]
    assert len(new) == 1
    P.accept(ws, new[0]["pid"])
    assert C.get_module(ws, "premise") == "一座会移动、夜里沉睡的岛"


# ── 纪要 ──

def test_minutes_written_listed_and_readable(ws):
    _talk(ws, 3)
    out = DS.run(ws, generator=_gen(BASE))
    assert out["minutes"].startswith("output/纪要/")
    listed = DS.list_minutes(ws)
    assert out["minutes"] in listed
    body = DS.read_minutes(ws, out["minutes"])
    assert body.startswith("# 构思纪要") and "## 讨论要点" in body and "条与现有内容冲突" in body


def test_read_minutes_rejects_outside_paths(ws):
    with pytest.raises(BrainstormError):
        DS.read_minutes(ws, "../meta.json")
    with pytest.raises(BrainstormError):
        DS.read_minutes(ws, "output/纪要/不存在.md")


# ── 接口与页面 ──

def test_api_distill_flow(tmp_path, monkeypatch):
    root = BA.create_project("湾", base=str(tmp_path))["data"]["root"]
    w = BrainstormWorkspace(root)
    _talk(w, 2)
    r = BA.distill_run(root, modules=["cast", "premise"], generator=_gen(BASE))
    assert r["ok"] and r["data"]["minutes"]
    assert BA.distill_minutes(root)["data"]
    assert BA.distill_pending_range(root)["data"] == [4, 4]                  # 检查点已推进到末尾


def test_page_shows_distill_box(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    root = BA.create_project("湾边", base=str(tmp_path))["data"]["root"]
    monkeypatch.setenv("NOVEL_PROJECT", str(PROJECT))
    monkeypatch.setattr("novel_writer.brainstorm.api.default_root", lambda: tmp_path / "none")
    at = AppTest.from_file(str(PROJECT / "tests" / "apps" / "brainstorm_page_app.py"), default_timeout=30)
    at.session_state["bs_root"] = root
    at.run()
    assert not at.exception, at.exception
    assert any("整理对话" in e.label for e in at.expander)


# ── 错误结构：界面的 show_error 必须能读取（回归：曾因 error 为字符串而崩溃） ──

def test_api_errors_follow_project_shape(tmp_path):
    root = BA.create_project("湾口", base=str(tmp_path))["data"]["root"]
    res = BA.distill_run(root, modules=["magic"], generator=_gen(BASE))
    assert res["ok"] is False
    assert isinstance(res["error"], dict) and res["error"]["message"]
    assert res["error_type"] == "BrainstormError"


def test_show_error_accepts_both_shapes(monkeypatch):
    from ui import components

    shown = []
    monkeypatch.setattr(components.st, "error", lambda m: shown.append(m))
    components.show_error({"ok": False, "error": {"type": "X", "message": "甲"}})
    components.show_error({"ok": False, "error": "乙"})
    components.show_error({"ok": False})
    assert shown == ["甲", "乙", "未知错误"]
