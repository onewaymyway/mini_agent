import json

import pytest

from world_forge import engine, proposals, repo


def _entity(world, name, kind="地理", summary="原描述"):
    rec = proposals.submit(world, "create_entity", payload={"kind": kind, "name": name, "summary": summary})
    assert rec["status"] == "applied"
    return rec["result"]["id"]


def _char(world, name, role="次要"):
    rec = proposals.submit(world, "create_character", payload={"name": name, "role": role, "summary": "旧简介"})
    assert rec["status"] == "applied"
    return rec["result"]["id"]


class Scripted:
    def __init__(self, changes):
        self.changes = changes

    def ask(self, prompt, system=""):
        return json.dumps({"changes": self.changes}, ensure_ascii=False)


def test_modify_selected_entity_minor_applied_with_before_after(world, llm):
    eid = _entity(world, "北港")
    res = engine.modify_world(Scripted([{"target_name": "北港", "target_type": "entity",
                                         "payload": {"summary": "冬季封冻，只能靠破冰船通行"},
                                         "reason": "按要求改为封冻"}]), world, "北港冬季封冻", [eid])
    assert res["counts"] == {"applied": 1}
    r = res["results"][0]
    assert r["before"]["summary"] == "原描述"
    assert r["after"]["summary"] == "冬季封冻，只能靠破冰船通行"
    ent = repo.find_by_id(repo.load_entities(world), eid)
    assert ent["summary"].startswith("冬季封冻")


def test_selected_scope_ignores_other_objects(world, llm):
    a = _entity(world, "甲地")
    _entity(world, "乙地")
    res = engine.modify_world(Scripted([{"target_name": "乙地", "target_type": "entity",
                                         "payload": {"summary": "被改了"}, "reason": "越界"}]),
                              world, "修改", [a])
    assert res["results"] == []
    assert res["skipped"][0]["reason"].startswith("不在所选对象范围内")


def test_unscoped_modification_can_touch_multiple_objects(world, llm):
    _entity(world, "丙地")
    _char(world, "守城人")
    res = engine.modify_world(llm, world, "把所有与北方有关的设定改为寒冷")  # 规则模型，自动判断
    assert res["results"], "自动判断时至少应给出一个修改"
    known = {e["name"] for e in repo.load_entities(world)} | {c["name"] for c in repo.load_characters(world)}
    assert res["results"][0]["target"] in known   # 目标必须是世界中真实存在的对象
    assert all(r["proposal"]["source"] == "modify" for r in res["results"])


def test_major_change_goes_pending(world):
    cid = _char(world, "老兵", role="次要")
    res = engine.modify_world(Scripted([{"target_name": "老兵", "target_type": "character",
                                         "payload": {"status": "dead"}, "reason": "剧情需要"}]),
                              world, "让老兵死亡", [cid])
    assert res["counts"] == {"pending": 1}
    assert repo.find_by_id(repo.load_characters(world), cid)["status"] == "alive"


def test_rename_to_existing_name_is_blocked(world):
    a = _entity(world, "甲地")
    _entity(world, "乙地")
    res = engine.modify_world(Scripted([{"target_name": "甲地", "target_type": "entity",
                                         "payload": {"name": "乙地"}, "reason": "改名"}]), world, "改名", [a])
    assert res["counts"] == {"blocked": 1}


def test_invalid_fields_are_dropped(world):
    a = _entity(world, "甲地")
    res = engine.modify_world(Scripted([{"target_name": "甲地", "target_type": "entity",
                                         "payload": {"id": "hack", "foo": 1}, "reason": "x"}]), world, "x", [a])
    assert res["results"] == []
    assert res["skipped"][0]["reason"] == "没有可修改的有效字段"


def test_faction_name_resolves_to_id(world):
    cid = _char(world, "游侠")
    fac = proposals.submit(world, "create_entity", payload={"kind": "势力", "name": "青松盟", "summary": "x"})
    proposals.approve(world, fac["id"])
    res = engine.modify_world(Scripted([{"target_name": "游侠", "target_type": "character",
                                         "payload": {"faction_name": "青松盟"}, "reason": "加入"}]),
                              world, "加入青松盟", [cid])
    assert res["results"][0]["proposal"]["status"] == "applied"   # 归属变更属次要字段
    ch = repo.find_by_id(repo.load_characters(world), cid)
    fid = next(e["id"] for e in repo.load_entities(world) if e["name"] == "青松盟")
    assert ch["faction_id"] == fid


def test_empty_request_rejected(world, llm):
    with pytest.raises(engine.EngineError):
        engine.modify_world(llm, world, "   ")
