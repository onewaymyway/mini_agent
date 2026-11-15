"""一句话起稿 S3：采用 = 转成待确认提议；不写卡片；冲突标记；去重；无效不登记。"""
import copy

import pytest

from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import proposals as P
from novel_writer.brainstorm import seed_adopt as AD
from novel_writer.brainstorm import seed_draft as SD
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace
from test_brainstorm_seed_s0 import good


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "p")
    w.create("p")
    SD.save_version(w, good(), kind="seed", reason="x", seed={"idea": "记忆当货币"})
    return w


def accept_all(ws, batch=None):
    for p in P.list_items(ws, "pending"):
        if batch is None or p["batch"] == batch:
            P.accept(ws, p["pid"])


def test_adopt_into_empty_cards_creates_proposals_without_writing(ws):
    before = C.content_hash(ws)
    out = AD.adopt(ws)
    assert C.content_hash(ws) == before                         # 确认前卡片不变
    mods = [p["module"] for p in out["proposals"]]
    assert {"premise", "ideas", "world", "cast", "plot", "open_questions"} <= set(mods)
    assert out["conflicts"] == 0 and out["batch"].startswith("seed-v001-")
    assert all(p["status"] == "pending" and p["batch"] == out["batch"] for p in out["proposals"])


def test_accepting_everything_fills_cards_validly(ws):
    AD.adopt(ws)
    accept_all(ws)
    d = SD.to_card_modules(good())
    assert C.get_module(ws, "premise") == d["premise"]
    assert {c["name"] for c in C.get_module(ws, "cast").values()} == {"海棠", "老钟"}
    assert len(C.get_module(ws, "world")["rules"]) == 2 and C.get_module(ws, "world")["taboos"] == ["不可典当名字"]
    assert C.get_module(ws, "plot")["endings"] == ["毁港"]
    assert [q["text"] for q in C.get_module(ws, "open_questions")] == ["港口为何吞噬记忆？"]
    ideas = C.get_module(ws, "ideas")
    assert len(ideas) == 1 and "故事走向" in ideas[0]["tags"] and ideas[0]["source"] == "ai"
    for name in C.MODULE_FILES:
        C.validate_module(name, C.get_module(ws, name))


def test_existing_premise_is_conflict_and_not_overwritten(ws):
    C.put_module(ws, "premise", "我自己的命题")
    out = AD.adopt(ws, sections=["premise"])
    p = out["proposals"][0]
    assert p["conflict"] and "我自己的命题" in p["conflict"] and out["conflicts"] == 1
    assert C.get_module(ws, "premise") == "我自己的命题"


def test_identical_content_skipped(ws):
    d = SD.to_card_modules(good())
    C.put_module(ws, "premise", d["premise"])
    out = AD.plan(ws, sections=["premise"])
    assert out["items"] == [] and any("相同" in s for s in out["skipped"])
    with pytest.raises(BrainstormError) as e:
        AD.adopt(ws, sections=["premise"])
    assert "没有可采用的内容" in str(e.value)


def test_world_items_deduped_by_text(ws):
    C.put_module(ws, "world", {"rules": [{"text": "记忆可以典当"}], "locations": [], "factions": [],
                               "timeline": [], "taboos": []})
    out = AD.plan(ws, sections=["world"])
    texts = [AD._item_text(i["value"]) for i in out["items"]]
    assert "记忆可以典当" not in texts and "典当越多越健忘" in texts
    assert any("已有" in s for s in out["skipped"])


def test_cast_matched_by_name_with_conflict_and_new_added(ws):
    C.put_module(ws, "cast", {"char_x": {"name": "海棠", "role": "配角", "want": "想要别的"}})
    out = AD.plan(ws, sections=["cast"])
    s = [i for i in out["items"] if i["op"] == "set"]
    a = [i for i in out["items"] if i["op"] == "add"]
    assert len(s) == 1 and s[0]["target"] == "char_x" and s[0]["conflict"] and s[0]["value"]["role"] == "主角"
    assert len(a) == 1 and a[0]["value"]["name"] == "老钟"


def test_cast_new_id_when_id_taken_by_other_name_and_relations_remapped(ws):
    C.put_module(ws, "cast", {"c1": {"name": "别人"}})
    d = good()
    d["cast"] = [{"id": "c1", "name": "海棠", "relations": ["c2:师徒"]}, {"id": "c2", "name": "老钟"}]
    SD.save_version(ws, d, kind="revise", reason="x")
    out = AD.plan(ws, sections=["cast"])
    by = {i["value"]["name"]: i for i in out["items"]}
    assert by["海棠"]["target"] == "c1_2" and by["老钟"]["target"] == "c2"
    assert by["海棠"]["value"]["relations"] == ["c2:师徒"]
    AD.adopt(ws, sections=["cast"])
    accept_all(ws)
    assert C.get_module(ws, "cast")["c1"]["name"] == "别人" and C.get_module(ws, "cast")["c1_2"]["name"] == "海棠"


def test_plot_conflict_only_for_nonempty_keys(ws):
    empty = AD.plan(ws, sections=["plot"])["items"][0]
    assert empty["conflict"] == "" and set(empty["value"]) == {"acts", "twists", "endings"}
    C.put_module(ws, "plot", {"acts": [], "twists": ["旧反转"], "endings": []})
    p = AD.plan(ws, sections=["plot"])["items"][0]
    assert "twists" in p["conflict"] and "acts" not in p["conflict"]


def test_open_questions_and_storyline_deduped(ws):
    AD.adopt(ws)
    accept_all(ws)
    out = AD.plan(ws, sections=["open_questions", "storyline"])
    assert out["items"] == [] and len(out["skipped"]) == 2


def test_pending_batch_blocks_readopt_but_other_version_ok(ws):
    AD.adopt(ws, sections=["premise"])
    with pytest.raises(BrainstormError) as e:
        AD.adopt(ws, sections=["open_questions"])
    assert "待确认" in str(e.value)
    d = good()
    d["premise"] = "新命题"
    SD.save_version(ws, d, kind="revise", reason="x")                # v2：不同版本不受阻
    assert AD.adopt(ws, sections=["premise"])["ver"] == 2


def test_section_selection_and_validation(ws):
    out = AD.adopt(ws, sections=["open_questions"])
    assert {p["module"] for p in out["proposals"]} == {"open_questions"}
    with pytest.raises(BrainstormError):
        AD.plan(ws, sections=["bogus"])


def test_adopt_older_version(ws):
    d = good()
    d["premise"] = "v2 命题"
    SD.save_version(ws, d, kind="revise", reason="x")
    out = AD.adopt(ws, ver=1, sections=["premise"])
    assert out["ver"] == 1 and out["proposals"][0]["value"] == "记忆是港口的货币"


def test_invalid_items_not_registered(ws, monkeypatch):
    calls = []
    orig = AD.DS._dry_run

    def fake(w, module, op, target, value):
        if module == "open_questions":
            calls.append(1)
            raise BrainstormError("模拟无效")
        return orig(w, module, op, target, value)
    monkeypatch.setattr(AD.DS, "_dry_run", fake)
    out = AD.adopt(ws)
    assert calls and "open_questions" not in {p["module"] for p in out["proposals"]}
    assert any("模拟无效" in s for s in out["skipped"])


def test_api(ws):
    r = BA.seed_adopt_plan(str(ws.root), None, ["premise"])
    assert r["ok"] and len(r["data"]["items"]) == 1 and P.list_items(ws) == []
    a = BA.seed_adopt(str(ws.root), None, ["premise"])
    assert a["ok"] and len(P.list_items(ws, "pending")) == 1
    assert BA.seed_adopt(str(ws.root), None, ["premise"])["ok"] is False
