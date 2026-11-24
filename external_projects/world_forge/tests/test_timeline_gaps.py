import pytest

from world_forge import engine, gaps, proposals, repo, timeline


def test_fork_keeps_original_and_switch(world, llm):
    engine.advance(llm, world, 2)
    main_before = repo.load_events(world, "main")
    first = main_before[0]["id"]
    bid = timeline.fork(world, "如果当年", from_event_id=first)
    assert [e["id"] for e in repo.load_events(world, "main")] == [e["id"] for e in main_before]
    assert len(repo.load_events(world, bid)) == 1
    assert repo.load_meta(world)["active_branch"] == bid
    timeline.switch(world, repo.MAIN)
    assert repo.load_meta(world)["active_branch"] == repo.MAIN


def test_advance_on_branch_does_not_touch_main(world, llm):
    engine.advance(llm, world, 1)
    main_ids = [e["id"] for e in repo.load_events(world, "main")]
    bid = timeline.fork(world, "替代", from_event_id=main_ids[0])
    engine.advance(llm, world, 1)
    assert [e["id"] for e in repo.load_events(world, "main")] == main_ids
    assert len(repo.load_events(world, bid)) >= 2


def test_compare_reports_divergence(world, llm):
    engine.advance(llm, world, 2)
    evs = repo.load_events(world, "main")
    bid = timeline.fork(world, "分歧线", from_event_id=evs[0]["id"])
    engine.advance(llm, world, 1)
    res = timeline.compare(world, repo.MAIN, bid)
    assert res["common"] == 1
    assert res["diverged_after"] == evs[0]["id"]
    assert len(res["only_a"]) == 1 and len(res["only_b"]) == 1


def test_fork_from_unknown_event_raises(world):
    with pytest.raises(timeline.TimelineError):
        timeline.fork(world, "x", from_event_id="evt_nope")


def test_gap_detection_finds_dangling_and_faction_without_relation(world):
    rec = proposals.submit(world, "create_entity", payload={"kind": "势力", "name": "孤岛联盟", "summary": "ok"})
    assert rec["status"] == "pending"  # 势力属重大变更，需先批准
    proposals.approve(world, rec["id"])
    gaps_found = {g["kind"] for g in gaps.find_gaps(world)}
    assert "faction_without_relation" in gaps_found
    rec = proposals.submit(world, "create_character", payload={"name": "散兵", "role": "次要", "faction_id": None,
                                                              "attrs": {"faction_hint": "不存在的组织"}})
    assert rec["status"] == "applied"
    kinds = {g["kind"] for g in gaps.find_gaps(world)}
    assert "unlinked_faction_hint" in kinds


def test_fill_gaps_summary_and_hint_link(world, llm):
    proposals.submit(world, "create_entity", payload={"kind": "地理", "name": "无名湾", "summary": ""})
    res = engine.fill_gaps(llm, world, limit=10)
    assert any(f["gap"]["kind"] == "missing_summary" for f in res["filled"])
    ent = next(e for e in repo.load_entities(world) if e["name"] == "无名湾")
    assert ent["summary"].startswith("（规则占位）")


def test_auto_fill_handles_relation_gaps_not_just_first_five(world, llm):
    """回归：前面都是无法处理的缺口时，自动补全仍应补全可处理的关系缺口。"""
    for i in range(6):   # 制造多个“孤立地理”缺口（关系类，可补全）
        proposals.submit(world, "create_entity", payload={"kind": "地理", "name": f"甲地{i}", "summary": "x"})
    res = engine.fill_gaps(llm, world, limit=5)
    assert len(res["filled"]) >= 1
    assert res["fixable_total"] >= 1


def test_overview_generated_and_persisted(world, llm):
    assert engine.get_overview(world) is None
    ov = engine.generate_overview(llm, world)
    assert ov["text"].strip()
    assert "世界概况" in ov["text"]
    assert repo.load_meta(world)["overview"]["year"] == 0
    engine.advance(llm, world, 1)
    assert engine.get_overview(world)["text"] == ov["text"]   # 推进不会自动改写
