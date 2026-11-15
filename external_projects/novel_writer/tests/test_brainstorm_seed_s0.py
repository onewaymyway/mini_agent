"""一句话起稿 S0：构思稿校验、版本、差异、卡片映射。"""
import copy

import pytest

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import seed_draft as SD
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace


def good():
    return {
        "premise": "记忆是港口的货币",
        "storyline": {"logline": "渔女追查被典当的记忆", "opening": "她发现母亲的记忆被卖",
                      "development": "深入黑市", "turn": "真相：港口靠吞噬记忆维持", "ending": "她选择赎回或毁掉港口"},
        "world": {"rules": ["记忆可以典当", {"text": "典当越多越健忘", "cost": "失去自我"}],
                  "locations": ["雾港"], "factions": ["典当行"], "timeline": ["三十年前大潮"], "taboos": ["不可典当名字"]},
        "cast": [{"name": "海棠", "role": "主角", "want": "找回母亲"}, {"name": "老钟", "role": "当铺掌柜"}],
        "plot": {"acts": [{"title": "起", "beats": ["发现"]}], "twists": ["母亲自愿典当"], "endings": ["毁港"]},
        "open_questions": ["港口为何吞噬记忆？"],
    }


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "p")
    w.create("p")
    return w


def test_normalize_and_validate_loose_shapes():
    d = SD.validate_draft(good())
    assert list(d) == list(SD.SECTIONS)
    assert set(d["cast"]) == {"c1", "c2"} and d["cast"]["c1"]["name"] == "海棠"
    assert d["open_questions"] == [{"id": "q1", "text": "港口为何吞噬记忆？", "closed": False}]
    assert d["world"]["locations"] == [{"text": "雾港"}]


@pytest.mark.parametrize("mut,frag", [
    (lambda d: d.pop("world"), "缺少部分"),
    (lambda d: d.update(extra=1), "未知部分"),
    (lambda d: d.update(premise="  "), "核心命题"),
    (lambda d: d["storyline"].pop("turn"), "转"),
    (lambda d: d.update(cast=[]), "至少需要一个角色"),
    (lambda d: d.update(plot={"acts": [], "twists": [], "endings": []}), "至少需要一幕"),
    (lambda d: d["cast"].append({"role": "无名"}), "名字"),
])
def test_invalid_drafts_rejected(mut, frag):
    d = good()
    mut(d)
    with pytest.raises(BrainstormError) as e:
        SD.validate_draft(d)
    assert frag in str(e.value)


def test_all_errors_reported_together():
    d = good()
    d["premise"] = ""
    d.pop("world")
    with pytest.raises(BrainstormError) as e:
        SD.validate_draft(d)
    assert "核心命题" in str(e.value) and "世界观" in str(e.value)


def test_duplicate_cast_ids_rejected():
    d = good()
    d["cast"] = [{"id": "x", "name": "甲"}, {"id": "x", "name": "乙"}]
    with pytest.raises(BrainstormError):
        SD.validate_draft(d)


def test_versions_append_only_and_seed_carried(ws):
    n1 = SD.save_version(ws, good(), kind="seed", reason="起稿", seed={"idea": "记忆当货币", "constraints": ""})
    d2 = good()
    d2["premise"] = "新命题"
    n2 = SD.save_version(ws, d2, kind="revise", reason="改命题")
    assert (n1, n2) == (1, 2) and [v["ver"] for v in SD.versions(ws)] == [1, 2]
    assert SD.load(ws)["seed"]["idea"] == "记忆当货币"           # 反馈版本沿用起稿想法
    assert SD.load(ws, 1)["draft"]["premise"] == "记忆是港口的货币"


def test_invalid_save_writes_nothing(ws):
    bad = good()
    bad.pop("plot")
    with pytest.raises(BrainstormError):
        SD.save_version(ws, bad, kind="seed", reason="x", seed={"idea": "a"})
    assert SD.versions(ws) == []


def test_seed_requires_idea_and_kind_checked(ws):
    with pytest.raises(BrainstormError):
        SD.save_version(ws, good(), kind="seed", reason="x", seed={"idea": " "})
    with pytest.raises(BrainstormError):
        SD.save_version(ws, good(), kind="bogus", reason="x", seed={"idea": "a"})


def test_load_missing(ws):
    with pytest.raises(BrainstormError):
        SD.load(ws)
    SD.save_version(ws, good(), kind="seed", reason="x", seed={"idea": "a"})
    with pytest.raises(BrainstormError):
        SD.load(ws, 9)


def test_restore_creates_new_version_with_old_content(ws):
    SD.save_version(ws, good(), kind="seed", reason="x", seed={"idea": "a"})
    d2 = good()
    d2["premise"] = "改"
    SD.save_version(ws, d2, kind="revise", reason="r")
    n = SD.restore(ws, 1)
    assert n == 3 and SD.load(ws)["kind"] == "restore"
    assert SD.load(ws)["draft"] == SD.load(ws, 1)["draft"] and len(SD.versions(ws)) == 3


def test_changed_sections_and_diff(ws):
    a = SD.validate_draft(good())
    b = copy.deepcopy(a)
    b["premise"] = "另一个命题"
    b["plot"]["twists"].append("新反转")
    assert SD.changed_sections(a, b) == ["premise", "plot"]
    assert SD.changed_sections(a, a) == []
    assert "新反转" in SD.section_diff(a, b, "plot") and SD.section_diff(a, a, "world") == ""


def test_to_card_modules_valid_against_card_validators():
    mods = SD.to_card_modules(good())
    assert set(mods) == {"premise", "world", "cast", "plot", "open_questions"}
    for name, data in mods.items():
        assert C.validate_module(name, data) is not None


def test_storyline_text():
    t = SD.storyline_text(SD.validate_draft(good())["storyline"])
    assert t.startswith("一句话主线：") and "转：真相" in t


def test_does_not_touch_cards(ws):
    before = C.content_hash(ws)
    SD.save_version(ws, good(), kind="seed", reason="x", seed={"idea": "a"})
    assert C.content_hash(ws) == before
