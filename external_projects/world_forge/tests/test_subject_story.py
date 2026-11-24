import pytest

from world_forge import engine, proposals, repo, runlog


def _setup(world):
    a = proposals.submit(world, "create_character", payload={"name": "守灯人", "role": "次要"})["result"]["id"]
    b = proposals.submit(world, "create_character", payload={"name": "渔女", "role": "次要"})["result"]["id"]
    proposals.submit(world, "add_event", payload={"title": "守灯人独自守夜", "involves": [a]})
    proposals.submit(world, "add_event", payload={"title": "渔女出海", "involves": [b]})
    proposals.submit(world, "add_event", payload={"title": "守灯人与渔女相遇", "involves": [a, b]})
    return a, b


def test_arc_from_subject_uses_only_its_events_and_sets_focus(world, llm):
    a, b = _setup(world)
    sid = engine.make_arc(llm, world, subject_id=a)
    meta = repo.load_story_meta(world, sid)
    assert meta["focus"]["name"] == "守灯人"
    titles = [e["title"] for e in repo.load_events(world, "main") if e["id"] in meta["source_event_ids"]]
    assert titles == ["守灯人独自守夜", "守灯人与渔女相遇"]     # 不含“渔女出海”
    arc = repo.story_dir(world, sid) / "arc.json"
    import json
    assert json.loads(arc.read_text(encoding="utf-8"))["protagonist_names"][0] == "守灯人"


def test_subject_without_events_is_refused(world, llm):
    lonely = proposals.submit(world, "create_character", payload={"name": "孤独者", "role": "次要"})["result"]["id"]
    with pytest.raises(engine.EngineError):
        engine.make_arc(llm, world, subject_id=lonely)


def test_unknown_subject_is_refused(world, llm):
    with pytest.raises(engine.EngineError):
        engine.make_arc(llm, world, subject_id="chr_nope")


def test_focus_propagates_to_short_and_storyboard(world, llm):
    a, _ = _setup(world)
    arc = engine.make_arc(llm, world, subject_id=a)
    short = engine.write_short(llm, world, arc_sid=arc)
    assert repo.load_story_meta(world, short)["focus"]["name"] == "守灯人"
    log = runlog.RunLog(world, "board")
    engine.make_storyboard(llm, world, short, log=log)
    assert any("分镜以「守灯人」为中心" in e["msg"] for e in log.entries)


def test_direct_short_with_subject(world, llm):
    a, _ = _setup(world)
    sid = engine.write_short(llm, world, brief="一个夜晚", subject_id=a)
    meta = repo.load_story_meta(world, sid)
    assert meta["focus"]["name"] == "守灯人"
    assert meta["request"]["subject_id"] == a
    assert len(meta["source_event_ids"]) == 2


def test_regenerate_keeps_focus(world, llm):
    a, _ = _setup(world)
    sid = engine.make_arc(llm, world, subject_id=a)
    engine.regenerate_arc(llm, world, sid)
    assert repo.load_story_meta(world, sid)["focus"]["name"] == "守灯人"
