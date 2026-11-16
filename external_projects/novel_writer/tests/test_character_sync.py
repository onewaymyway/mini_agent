"""人物档案一致性：人设卡 / 角色档案 / 索引对不上时，依赖人物的步骤先报清楚；索引过期自动刷新；id 还原为姓名。"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_writer import brief as B
from novel_writer import character_store as CST
from novel_writer import character_sync as CSY


@pytest.fixture
def ws(tmp_path):
    (tmp_path / CSY.CARD_DIR_REL).mkdir(parents=True)
    paths = {"characters_dir": tmp_path / "大纲规划/人物设定/角色", "characters": tmp_path / "大纲规划/人物设定/角色索引.md"}
    paths["characters_dir"].mkdir(parents=True)
    return SimpleNamespace(root=tmp_path, path=lambda k: paths[k])


def _cards(ws, *names):
    for n in names:
        (Path(ws.root) / CSY.CARD_DIR_REL / f"{n}.md").write_text("card", encoding="utf-8")


def _store(monkeypatch, recs):
    monkeypatch.setattr(CST, "load_records", lambda base: (recs, []))


def test_missing_profiles_block_with_named_list_and_no_retry(ws, monkeypatch):
    _cards(ws, "林远", "沈砚", "顾晚")
    _store(monkeypatch, {"char_001": {"name": "林远"}})
    with pytest.raises(CSY.CharacterSyncError) as ei:
        CSY.ensure(ws)
    assert ei.value.no_retry is True
    assert "沈砚" in str(ei.value) and "顾晚" in str(ei.value) and "建立人物档案" in str(ei.value)


def test_stale_index_is_refreshed_deterministically(ws, monkeypatch):
    _cards(ws, "林远")
    _store(monkeypatch, {"char_001": {"name": "林远"}})
    calls = []
    monkeypatch.setattr(CST, "refresh", lambda base, index: calls.append(index) or ({}, True))
    ws.path("characters").write_text("| id | 姓名 |\n|---|---|\n| char_009 | 旧名 |\n", encoding="utf-8")
    rep = CSY.ensure(ws)
    assert calls and rep.get("refreshed_index") is True


def test_corrupt_profile_is_reported_not_auto_fixed(ws, monkeypatch):
    monkeypatch.setattr(CST, "load_records", lambda base: ({}, ["char_003.md: key 缺失"]))
    with pytest.raises(CSY.CharacterSyncError, match="损坏"):
        CSY.ensure(ws)


def test_names_are_real_names_not_ids(ws, monkeypatch):
    _store(monkeypatch, {"char_002": {"name": "林远"}, "char_005": {"name": "沈砚"}})
    assert B.known_names(ws) == ["林远", "沈砚"]
    assert CSY.id_to_name(ws) == {"char_002": "林远", "char_005": "沈砚"}


def test_model_id_in_who_is_mapped_to_name_before_validation():
    import json
    item = {"chapter": 1, "title": "甲", "narrative": "甲" * 400, "deviations": [],
            "key": {"must_events": ["事件"], "must_states": [{"who": "char_002", "change": "变化"}],
                    "must_foreshadow": [], "must_hook": "钩子"}}
    raw = json.dumps({"chapters": [item]}, ensure_ascii=False)
    good, bad, _ = B._split_output(raw, [1], {1: "甲"}, ["林远"], [], 1, id_map={"char_002": "林远"})
    assert 1 in good and not bad
    assert good[1]["key"]["must_states"][0]["who"] == "林远"
