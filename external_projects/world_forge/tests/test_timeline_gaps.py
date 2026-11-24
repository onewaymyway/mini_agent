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


def test_complete_relations_adds_links_without_duplicates(world, llm):
    for name in ("北港", "南岬", "西原"):
        proposals.submit(world, "create_entity", payload={"kind": "地理", "name": name, "summary": "x"})
    before = len(repo.load_relations(world))
    res = engine.complete_relations(llm, world, limit=5)
    after = len(repo.load_relations(world))
    assert after > before
    pairs = [frozenset((r["src"], r["dst"])) for r in repo.load_relations(world)]
    assert len(pairs) == len(set(pairs)), "同一对实体不应重复建立关系"
    again = engine.complete_relations(llm, world, limit=5)
    assert len(repo.load_relations(world)) >= after


def test_graph_data_contains_entities_characters_and_faction_edges(world, llm):
    from world_forge import graph
    rec = proposals.submit(world, "create_entity", payload={"kind": "势力", "name": "赤潮会", "summary": "x"})
    assert rec["status"] == "pending"
    proposals.approve(world, rec["id"])
    fac = next(e for e in repo.load_entities(world) if e["name"] == "赤潮会")
    ch = proposals.submit(world, "create_character", payload={"name": "潮使", "role": "次要", "faction_id": fac["id"]})
    g = graph.build_graph(world)
    ids = {n["id"] for n in g["nodes"]}
    assert ch["result"]["id"] in ids and fac["id"] in ids
    assert any(e["label"] == "隶属" and e["target"] == fac["id"] for e in g["edges"])
    focused = graph.build_graph(world, focus=fac["id"], depth=1)
    assert ch["result"]["id"] in {n["id"] for n in focused["nodes"]}


def test_graph_html_renders_and_escapes(world):
    from world_forge import graph
    proposals.submit(world, "create_entity", payload={"kind": "地理", "name": "</script>危险", "summary": "x"})
    html_text = graph.render_html(graph.build_graph(world))
    assert "</script>危险" not in html_text
    assert "<svg" in html_text
    assert graph.render_html({"nodes": [], "edges": []}).startswith("<div")


def test_complete_relations_targets_sparse_entities_and_terminates(world, llm):
    """回归：剩余数应只统计仍关系不足的实体；反复点击应收敛到 0，而不是永远有剩余。"""
    for name in ("北港", "南岬", "西原", "东林"):
        proposals.submit(world, "create_entity", payload={"kind": "地理", "name": name, "summary": "x"})
    total_rounds = 0
    res = None
    while total_rounds < 20:
        res = engine.complete_relations(llm, world, limit=5, min_degree=2)
        total_rounds += 1
        if res["remaining"] == 0:
            break
    assert res["remaining"] == 0, "多次运行后应全部达到目标"
    from world_forge.engine import _degrees
    deg = _degrees(repo.load_entities(world), repo.load_relations(world))
    assert all(v >= 2 for v in deg.values())
    pairs = [frozenset((r["src"], r["dst"])) for r in repo.load_relations(world)]
    assert len(pairs) == len(set(pairs))


def test_complete_relations_does_not_count_well_connected_entities(world, llm):
    """已经关系充足的实体不应被计入剩余或被再次处理。"""
    ids = []
    for name in ("甲", "乙", "丙"):
        rec = proposals.submit(world, "create_entity", payload={"kind": "地理", "name": name, "summary": "x"})
        ids.append(rec["result"]["id"])
    for a in ids:
        for b in ids:
            if a < b:
                proposals.submit(world, "create_relation", payload={"src": a, "dst": b, "type": "相邻"})
    res = engine.complete_relations(llm, world, limit=5, min_degree=2)
    sources = {r["payload"]["src"] for r in res["records"]}
    assert not (sources & set(ids)), "关系已充足的实体不应作为处理目标（被其它实体连接是允许的）"
