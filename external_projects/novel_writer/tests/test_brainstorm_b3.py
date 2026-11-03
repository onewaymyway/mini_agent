"""灵感构思工作室 B3：世界观与角色编辑、结构质检、AI 审阅（桩）、质检不改卡片。"""

import json
from pathlib import Path

import pytest

from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import checks as K
from novel_writer.brainstorm import edit as E
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace

PROJECT = Path(__file__).resolve().parent.parent


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "城")
    w.create("城")
    return w


# ── 世界观编辑 ──

def test_world_add_and_remove(ws):
    E.add_world_item(ws, "rules", {"text": "雾中看见死者", "cost": "失去一段记忆"})
    E.add_world_item(ws, "locations", "钟楼")
    E.add_world_item(ws, "timeline", {"text": "三百年前沉没"})
    w = C.get_module(ws, "world")
    assert w["rules"][0]["cost"] == "失去一段记忆"
    assert w["locations"] == [{"text": "钟楼"}]
    E.remove_world_item(ws, "rules", 0)
    assert C.get_module(ws, "world")["rules"] == []


def test_world_rejects_bad_key_and_index(ws):
    with pytest.raises(BrainstormError):
        E.add_world_item(ws, "magic", "x")
    with pytest.raises(BrainstormError):
        E.remove_world_item(ws, "rules", 0)
    with pytest.raises(BrainstormError):
        E.add_world_item(ws, "rules", "")                               # 空文字不合法


# ── 角色编辑 ──

def test_cast_add_update_remove(ws):
    cid = E.add_cast(ws, {"name": "林", "role": "主角"}, cid="c1")
    assert cid == "c1"
    E.update_cast(ws, "c1", {"want": "找回弟弟", "relations": ["c2:弟弟"]})
    card = C.get_module(ws, "cast")["c1"]
    assert card["want"] == "找回弟弟" and card["relations"] == ["c2:弟弟"]
    E.remove_cast(ws, "c1")
    assert C.get_module(ws, "cast") == {}


def test_cast_generated_id_and_errors(ws):
    cid = E.add_cast(ws, {"name": "甲"})
    assert cid.startswith("char_")
    with pytest.raises(BrainstormError):
        E.add_cast(ws, {"name": "乙"}, cid=cid)
    with pytest.raises(BrainstormError):
        E.add_cast(ws, {"role": "没有名字"})                            # validate 拒绝
    with pytest.raises(BrainstormError):
        E.update_cast(ws, "c_missing", {"role": "x"})
    with pytest.raises(BrainstormError):
        E.remove_cast(ws, "c_missing")


# ── 结构质检（确定性） ──

def test_structural_finds_common_problems(ws):
    E.add_cast(ws, {"name": "林", "role": "主角", "relations": ["c_ghost:不存在的人"]}, cid="c1")
    E.add_cast(ws, {"name": "林", "role": "对手"}, cid="c2")
    E.add_cast(ws, {"name": "石"}, cid="c3")                            # 没有定位
    E.add_world_item(ws, "rules", {"text": "雾中见死者"})                # 没有代价
    C.put_module(ws, "plot", {"acts": [{"title": "第一幕", "beats": []}], "twists": [], "endings": []})
    C.put_module(ws, "open_questions", [{"id": "q1", "text": "弟弟还活着吗", "closed": False}])
    issues = {(f["severity"], f["where"]): f["issue"] for f in K.structural(ws)}
    assert any("c_ghost" in v for v in issues.values())
    assert any("重复" in v for v in issues.values())
    assert any("没有定位" in v for v in issues.values())
    assert any("没有写代价" in v for v in issues.values())
    assert any("转折点" in v for v in issues.values())
    assert any("开放问题" in v for v in issues.values())
    assert ("warn", "premise") in issues                                 # 命题为空


def test_structural_clean_project_has_no_warnings(ws):
    C.put_module(ws, "premise", "一个用记忆换航线的港口")
    E.add_cast(ws, {"name": "林", "role": "主角", "relations": []}, cid="c1")
    E.add_world_item(ws, "rules", {"text": "雾中见死者", "cost": "一段记忆"})
    assert [f for f in K.structural(ws) if f["severity"] == "warn"] == []


# ── AI 审阅（桩） ──

def test_llm_review_parses_findings_and_skips_empty(ws):
    reply = "```json\n" + json.dumps([
        {"severity": "warn", "where": "world:rules[0]", "issue": "规则与剧情冲突", "suggestion": "改成……"},
        {"severity": "bogus", "where": "cast:c1", "issue": "动机站不住"},
        {"where": "x", "issue": ""},
    ], ensure_ascii=False) + "\n```"
    out = K.llm_review(ws, lambda prompt: reply)
    assert len(out) == 2
    assert out[0]["severity"] == "warn" and out[1]["severity"] == "info"     # 非法等级降为 info
    assert all(f["source"] == "llm" for f in out)


def test_llm_review_rejects_non_json(ws):
    with pytest.raises(BrainstormError):
        K.llm_review(ws, lambda prompt: "没有列表")


def test_llm_prompt_says_report_only(ws):
    seen = {}

    def gen(prompt):
        seen["p"] = prompt
        return "[]"
    K.llm_review(ws, gen)
    assert "不要改写卡片" in seen["p"]


# ── 质检不改卡片（核心约束） ──

def test_run_never_changes_cards(ws):
    E.add_cast(ws, {"name": "林", "role": "主角", "relations": ["c_x:谁"]}, cid="c1")
    before = C.all_modules(ws)
    h_before = C.content_hash(ws)
    rec = K.run(ws, use_llm=True, generator=lambda prompt: json.dumps(
        [{"severity": "warn", "where": "cast:c1", "issue": "关系无效", "suggestion": "删除"}]))
    assert C.all_modules(ws) == before
    assert C.content_hash(ws) == h_before
    assert rec["findings"] and rec["content_hash"] == h_before
    assert any(f["source"] == "llm" for f in rec["findings"])


def test_run_structural_only_makes_no_model_call(ws):
    called = []
    rec = K.run(ws, use_llm=False, generator=lambda p: called.append(p) or "[]")
    assert called == [] and rec["use_llm"] is False
    assert K.history(ws)[0]["id"] == rec["id"]


def test_llm_failure_raises_and_saves_nothing(ws):
    with pytest.raises(BrainstormError):
        K.run(ws, use_llm=True, generator=lambda p: "坏输出")
    assert K.history(ws) == []


# ── 接口 ──

def test_api_world_cast_and_check(tmp_path):
    root = BA.create_project("海", base=str(tmp_path))["data"]["root"]
    assert BA.world_add(root, "rules", {"text": "潮水带回名字", "cost": "一个朋友"})["ok"]
    assert BA.world_add(root, "bogus", "x")["ok"] is False
    cid = BA.cast_add(root, {"name": "潮"})["data"]
    assert BA.cast_update(root, cid, {"role": "主角"})["ok"]
    assert BA.get_project(root)["data"]["cards"]["cast"][cid]["role"] == "主角"
    res = BA.run_check(root, use_llm=False)
    assert res["ok"] and BA.check_history(root)["data"][0]["id"] == res["data"]["id"]
    assert BA.cast_remove(root, cid)["ok"]


def test_page_renders_new_tabs_with_project(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    root = BA.create_project("森", base=str(tmp_path))["data"]["root"]
    BA.world_add(root, "locations", "老林子")
    monkeypatch.setenv("NOVEL_PROJECT", str(PROJECT))
    monkeypatch.setattr("novel_writer.brainstorm.api.default_root", lambda: tmp_path / "none")
    at = AppTest.from_file(str(PROJECT / "tests" / "apps" / "brainstorm_page_app.py"), default_timeout=30)
    at.session_state["bs_root"] = root
    at.run()
    assert not at.exception, at.exception
    labels = [t.label for t in at.tabs]
    assert labels == ["对话", "题材卡", "灵感池", "发散", "世界观", "角色", "质检"]
