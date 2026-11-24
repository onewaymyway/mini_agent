import pytest

from world_forge import engine, proposals, repo


def test_charter_is_pending_until_approved(llm):
    wid = engine.create_world(llm, concept="测试")["world_id"]
    cp = next(p for p in repo.load_proposals(wid) if p["op"] == "set_charter")
    assert cp["status"] == "pending" and cp["risk"] == "major"
    assert repo.load_meta(wid)["charter"] is None
    with pytest.raises(engine.EngineError):
        engine.advance(llm, wid, 1)
    proposals.approve(wid, cp["id"])
    assert repo.load_meta(wid)["charter"]["hard_rules"]


def test_minor_entity_auto_applied_and_major_pending():
    wid = _blank_world()
    minor = proposals.submit(wid, "create_entity", payload={"kind": "地理", "name": "北岸港", "summary": "x"})
    major = proposals.submit(wid, "create_entity", payload={"kind": "势力", "name": "铁幕会", "summary": "x"})
    assert minor["status"] == "applied"
    assert major["status"] == "pending"
    names = [e["name"] for e in repo.load_entities(wid)]
    assert "北岸港" in names and "铁幕会" not in names


def test_auto_minor_off_makes_everything_pending():
    wid = _blank_world()
    meta = repo.load_meta(wid)
    meta["settings"]["auto_minor"] = False
    repo.save_meta(meta)
    rec = proposals.submit(wid, "create_entity", payload={"kind": "地理", "name": "湖心岛", "summary": ""})
    assert rec["status"] == "pending"


def test_duplicate_name_is_blocked_and_recorded():
    wid = _blank_world()
    proposals.submit(wid, "create_entity", payload={"kind": "地理", "name": "北岸港", "summary": ""})
    dup = proposals.submit(wid, "create_entity", payload={"kind": "物品", "name": "北岸港", "summary": ""})
    assert dup["status"] == "blocked"
    assert any("同名" in r for r in dup["reasons"])
    assert sum(e["name"] == "北岸港" for e in repo.load_entities(wid)) == 1


def test_event_involving_dead_character_is_blocked():
    wid = _blank_world()
    ch = proposals.submit(wid, "create_character", payload={"name": "老兵", "role": "次要"})
    cid = ch["result"]["id"]
    proposals.submit(wid, "update_character", cid, {"status": "dead"})  # 状态变更属重大，进入待确认
    p = next(x for x in repo.load_proposals(wid) if x["op"] == "update_character")
    proposals.approve(wid, p["id"])
    ev = proposals.submit(wid, "add_event", payload={"title": "夜袭", "involves": [cid]})
    assert ev["status"] == "blocked"
    assert any("已死亡" in r for r in ev["reasons"])


def test_event_violating_rules_is_blocked():
    wid = _blank_world()
    ev = proposals.submit(wid, "add_event", payload={"title": "瞬移", "involves": [],
                                                    "violates_rules": ["宪章规定不得瞬移"]})
    assert ev["status"] == "blocked"


def test_reviving_dead_character_blocked():
    wid = _blank_world()
    cid = proposals.submit(wid, "create_character", payload={"name": "甲", "role": "次要"})["result"]["id"]
    chars = repo.load_characters(wid)
    chars[0]["status"] = "dead"
    repo.save_characters(wid, chars)
    rec = proposals.submit(wid, "update_character", cid, {"status": "alive"})
    assert rec["status"] == "blocked"


def test_charter_locked_after_history(world):
    rec = proposals.submit(world, "add_event", payload={"title": "开端", "involves": []})
    assert rec["status"] == "applied"
    again = proposals.submit(world, "set_charter", payload={"hard_rules": ["新规则"]})
    assert again["status"] == "blocked"


def test_delete_entity_referenced_by_relation_is_blocked():
    wid = _blank_world()
    a = proposals.submit(wid, "create_entity", payload={"kind": "地理", "name": "甲地"})["result"]["id"]
    b = proposals.submit(wid, "create_entity", payload={"kind": "地理", "name": "乙地"})["result"]["id"]
    proposals.submit(wid, "create_relation", payload={"src": a, "dst": b, "type": "相邻"})
    rec = proposals.submit(wid, "delete_entity", a)
    assert rec["status"] == "blocked"


def test_reject_pending():
    wid = _blank_world()
    rec = proposals.submit(wid, "create_entity", payload={"kind": "势力", "name": "霜潮会"})
    assert rec["status"] == "pending"
    assert proposals.reject(wid, rec["id"])["status"] == "rejected"
    with pytest.raises(ValueError):
        proposals.approve(wid, rec["id"])


def test_classify_table():
    assert proposals.classify("create_entity", {"kind": "地理"}) == "minor"
    assert proposals.classify("create_entity", {"kind": "势力"}) == "major"
    assert proposals.classify("create_entity", {"kind": "其它", "attrs": {"leader": "x"}}) == "major"
    assert proposals.classify("update_entity", {"summary": "x"}) == "minor"
    assert proposals.classify("update_entity", {"name": "x"}) == "major"
    assert proposals.classify("create_character", {"role": "主角"}) == "major"
    assert proposals.classify("create_character", {"role": "路人"}) == "minor"


def _blank_world() -> str:
    """直接建一个无宪章的世界骨架，用于提案层的单元测试。"""
    from world_forge.engine import DEFAULT_SETTINGS
    from world_forge import store

    wid = store.new_id("wld")
    meta = {"id": wid, "title": "测试", "status": "active", "created_at": store.now_iso(),
            "updated_at": store.now_iso(), "charter": {"hard_rules": ["r"]}, "clock": {"year": 0},
            "active_branch": repo.MAIN, "settings": dict(DEFAULT_SETTINGS)}
    repo.save_meta(meta)
    repo.save_branches(wid, repo.load_branches(wid))
    return wid


def test_attrs_as_list_or_string_is_normalized_not_crashing():
    from world_forge.proposals import coerce_attrs, normalize_payload

    assert coerce_attrs(["沙漠", {"气候": "干旱"}]) == {"要点1": "沙漠", "气候": "干旱"}
    assert coerce_attrs("未知") == {"说明": "未知"}
    assert coerce_attrs(None) == {}
    wid = _blank_world()
    rec = proposals.submit(wid, "create_entity",
                           payload={"kind": "地理", "name": "赤砂原", "summary": "x", "attrs": ["干旱", "无水源"]})
    assert rec["status"] == "applied"
    ent = next(e for e in repo.load_entities(wid) if e["name"] == "赤砂原")
    assert ent["attrs"] == {"要点1": "干旱", "要点2": "无水源"}
    # 列表形式的 attrs 不应被误判为“宪章级”权力字段，也不应崩溃
    rec2 = proposals.submit(wid, "update_entity", ent["id"], {"attrs": ["另一要点"]})
    assert rec2["status"] == "applied"


def test_event_change_attrs_normalized():
    p = normalize_payload_for_test()
    assert p["changes"][0]["payload"]["attrs"] == {"要点1": "x"}


def normalize_payload_for_test():
    from world_forge.proposals import normalize_payload

    return normalize_payload("add_event", {"title": "t", "changes": [
        {"op": "update_entity", "target": "ent_1", "payload": {"attrs": ["x"]}}]})
