"""灵感构思工作室 B1：灵感池、发散（桩生成器）、采纳、接口与页面冒烟。"""

import json
from pathlib import Path

import pytest

from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import ideas as I
from novel_writer.brainstorm import spark as S
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace

PROJECT = Path(__file__).resolve().parent.parent


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "雾港")
    w.create("雾港")
    return w


# ── 灵感池 ──

def test_add_list_update_idea(ws):
    a = I.add_idea(ws, "  雾天才出现的港口 ", tags=["悬疑", "悬疑", " "])
    assert a["text"] == "雾天才出现的港口" and a["tags"] == ["悬疑"] and a["source"] == "author"
    I.add_idea(ws, "另一个想法", status="park")
    assert len(I.list_ideas(ws)) == 2
    assert [i["text"] for i in I.list_ideas(ws, "park")] == ["另一个想法"]
    I.update_idea(ws, a["id"], status="drop")
    assert I.list_ideas(ws, "drop")[0]["id"] == a["id"]          # 丢弃保留记录，不删除


def test_empty_idea_rejected_and_unknown_id(ws):
    with pytest.raises(BrainstormError):
        I.add_idea(ws, "   ")
    with pytest.raises(BrainstormError):
        I.update_idea(ws, "i_missing", status="keep")
    a = I.add_idea(ws, "x")
    with pytest.raises(BrainstormError):
        I.update_idea(ws, a["id"], text="")


def test_bad_status_rejected_by_cards(ws):
    a = I.add_idea(ws, "x")
    with pytest.raises(BrainstormError):
        I.update_idea(ws, a["id"], status="maybe")


# ── 发散：解析与提示 ──

def test_parse_candidates_tolerates_fences_and_prose():
    reply = "好的，这是候选：\n```json\n[{\"text\": \"灯塔守夜人失去了影子\", \"tags\": [\"奇幻\"]}, {\"text\": \"  \"}, \"纯文字的候选\"]\n```\n祝写作顺利"
    out = S.parse_candidates(reply)
    assert [c["text"] for c in out] == ["灯塔守夜人失去了影子", "纯文字的候选"]
    assert out[0]["tags"] == ["奇幻"] and all(c["cid"].startswith("c") for c in out)


def test_parse_candidates_rejects_garbage():
    with pytest.raises(BrainstormError):
        S.parse_candidates("没有任何列表")
    with pytest.raises(BrainstormError):
        S.parse_candidates("[{\"text\": \"\"}]")
    with pytest.raises(BrainstormError):
        S.parse_candidates("[不是 json ]")


def test_prompt_contains_brief_existing_and_direction(ws):
    C.put_module(ws, "brief", {"genre": "悬疑", "theme": "记忆与代价"})
    I.add_idea(ws, "旧的想法A")
    prompt = S.build_prompt(C.get_module(ws, "brief"), ["旧的想法A"], "反派动机", 5)
    assert "悬疑" in prompt and "旧的想法A" in prompt and "反派动机" in prompt and "5 条" in prompt
    assert "JSON" in prompt


# ── 发散：生成、历史、采纳 ──

def _stub(payload):
    seen = {}

    def gen(prompt):
        seen["prompt"] = prompt
        return json.dumps(payload, ensure_ascii=False)
    gen.seen = seen
    return gen


def test_generate_saves_record_but_does_not_touch_idea_pool(ws):
    gen = _stub([{"text": "修钟表的女人能听见雾里的钟声", "tags": ["奇幻"]}, {"text": "港口的最后一班船", "tags": []}])
    rec = S.generate(ws, "意象", count=2, generator=gen)
    assert len(rec["candidates"]) == 2 and rec["direction"] == "意象"
    assert I.list_ideas(ws) == []                                  # 候选不直接进入灵感池
    assert S.history(ws)[0]["id"] == rec["id"]
    assert "意象" in gen.seen["prompt"]


def test_generate_clamps_count(ws):
    gen = _stub([{"text": "a"}])
    S.generate(ws, "", count=999, generator=gen)
    assert "10 条" in gen.seen["prompt"]


def test_adopt_moves_selected_candidates_as_ai_ideas(ws):
    gen = _stub([{"text": "甲候选"}, {"text": "乙候选", "tags": ["标签"]}, {"text": "丙候选"}])
    rec = S.generate(ws, "", count=3, generator=gen)
    cid_a, cid_b = rec["candidates"][0]["cid"], rec["candidates"][1]["cid"]
    added = S.adopt(ws, rec["id"], [cid_a, cid_b])
    assert [i["text"] for i in added] == ["甲候选", "乙候选"]
    assert all(i["source"] == "ai" and i["status"] == "keep" for i in I.list_ideas(ws))
    again = S.adopt(ws, rec["id"], [cid_a])                         # 重复采纳不会重复入池
    assert again == [] and len(I.list_ideas(ws)) == 2
    marked = {c["cid"]: c["adopted"] for c in S.history(ws)[0]["candidates"]}
    assert marked[cid_a] and marked[cid_b] and not marked[rec["candidates"][2]["cid"]]


def test_adopt_rejects_unknown_candidate_and_record(ws):
    rec = S.generate(ws, "", count=1, generator=_stub([{"text": "a"}]))
    with pytest.raises(BrainstormError):
        S.adopt(ws, rec["id"], ["c_nope"])
    with pytest.raises(BrainstormError):
        S.adopt(ws, "s_missing", [])


def test_generator_failure_is_reported_and_nothing_saved(ws):
    def broken(prompt):
        return "模型没回复列表"
    with pytest.raises(BrainstormError):
        S.generate(ws, "", generator=broken)
    assert S.history(ws) == []


# ── 对外接口 ──

def test_api_wrappers_return_ok_and_errors(tmp_path):
    created = BA.create_project("海风", base=str(tmp_path))
    assert created["ok"] is True
    root = created["data"]["root"]
    assert BA.add_idea(root, "灵感")["ok"] is True
    res = BA.add_idea(root, "  ")
    assert res["ok"] is False and res["error_type"] == "BrainstormError"
    assert BA.list_ideas(root)["data"][0]["text"] == "灵感"
    assert BA.get_project(root)["data"]["meta"]["title"] == "海风"
    bad = BA.get_project(str(tmp_path / "not_a_project"))
    assert bad["ok"] is False
    assert BA.list_projects(str(tmp_path))["data"][0]["title"] == "海风"


def test_api_spark_and_adopt_roundtrip(tmp_path):
    root = BA.create_project("潮", base=str(tmp_path))["data"]["root"]
    rec = BA.spark(root, "测试", 2, generator=_stub([{"text": "一"}, {"text": "二"}]))
    assert rec["ok"] is True
    sid = rec["data"]["id"]
    cid = rec["data"]["candidates"][0]["cid"]
    adopted = BA.adopt_candidates(root, sid, [cid])
    assert adopted["ok"] and adopted["data"][0]["text"] == "一"
    assert BA.spark_history(root)["data"][0]["id"] == sid


def test_save_brief_validates(tmp_path):
    root = BA.create_project("岛", base=str(tmp_path))["data"]["root"]
    assert BA.save_brief(root, {"genre": "科幻"})["ok"] is True
    assert BA.save_brief(root, {"nonsense": "x"})["ok"] is False


# ── 页面冒烟（AppTest，不触发任何 LLM 调用） ──

def test_brainstorm_page_renders_without_projects(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("NOVEL_PROJECT", str(PROJECT))
    monkeypatch.setattr("novel_writer.brainstorm.api.default_root", lambda: tmp_path / "none")
    at = AppTest.from_file(str(PROJECT / "tests" / "apps" / "brainstorm_page_app.py"), default_timeout=30).run()
    assert not at.exception, at.exception
    assert any("还没有构思项目" in m.value for m in at.info)
