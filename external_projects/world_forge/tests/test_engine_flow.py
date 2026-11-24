import json

import pytest

from world_forge import engine, generate, proposals, repo, store
from world_forge.providers.image import StubImageProvider


def test_advance_creates_events_and_moves_clock(world, llm):
    out = engine.advance(llm, world, 2)
    assert out["clock"]["year"] == 20
    evs = repo.load_events(world, "main")
    assert len(evs) >= 2
    assert all(e["year"] in (0, 10) for e in evs)


def test_advance_resolves_names_to_ids(world, llm):
    engine.advance(llm, world, 1)
    evs = repo.load_events(world, "main")
    ids = {e["id"] for e in repo.load_entities(world)} | {c["id"] for c in repo.load_characters(world)}
    for ev in evs:
        assert set(ev["involves"]) <= ids


def test_advance_with_unknown_name_is_blocked(world):
    class Scripted:
        def ask(self, prompt, system=""):
            return json.dumps({"new_characters": [], "entities": [], "relations": [], "canon_updates": [],
                               "events": [{"title": "幽灵事件", "summary": "", "involves": ["不存在的人"],
                                           "caused_by": [], "tags": [], "changes": [], "violates_rules": []}]})

    out = engine.advance(Scripted(), world, 1)
    rec = out["steps"][0]["records"][0]
    assert rec["status"] == "blocked"


def test_violation_reported_by_model_blocks_event(world):
    class Scripted:
        def ask(self, prompt, system=""):
            return json.dumps({"new_characters": [], "entities": [], "relations": [], "canon_updates": [],
                               "events": [{"title": "违规", "summary": "", "involves": [], "caused_by": [],
                                           "tags": [], "changes": [], "violates_rules": ["违反蒸汽规则"]}]})

    out = engine.advance(Scripted(), world, 1)
    assert out["steps"][0]["records"][0]["status"] == "blocked"


def test_invalid_model_output_retried_then_fails(world):
    class Bad:
        def ask(self, prompt, system=""):
            return "这不是 JSON"

    with pytest.raises(generate.GenerationError):
        engine.advance(Bad(), world, 1)
    assert len(repo.load_events(world, "main")) == 0  # 失败时不写入任何内容


def test_full_story_chain_with_stub_image(world, llm, img):
    engine.advance(llm, world, 2)
    evs = repo.load_events(world, "main")
    arc = engine.make_arc(llm, world, event_ids=[e["id"] for e in evs])
    assert (repo.story_dir(world, arc) / "arc.json").is_file()
    short = engine.write_short(llm, world, arc_sid=arc)
    assert (repo.story_dir(world, short) / "story.md").read_text(encoding="utf-8")
    direct = engine.write_short(llm, world, brief="守灯人的最后一夜", event_ids=[evs[0]["id"]])
    assert repo.load_story_meta(world, direct)["kind"] == "short"
    board = engine.make_storyboard(llm, world, arc)
    assert board["pages"][0]["panels"]
    res = engine.render_comic(img, world, arc)
    assert res["ok"] == sum(len(p["panels"]) for p in board["pages"]) and res["failed"] == 0
    assert res["stub"] is True
    assert (repo.story_dir(world, arc) / "comic.html").is_file()
    assert len(img.calls) == res["ok"]
    out = engine.export_bundle(world, arc)
    for name in ("outline.md", "setting.md", "characters.json", "bundle.json"):
        assert (out / name).is_file()


def test_comic_uses_portrait_as_reference(world, llm):
    engine.advance(llm, world, 1)
    evs = repo.load_events(world, "main")
    arc = engine.make_arc(llm, world, event_ids=[e["id"] for e in evs])
    engine.make_storyboard(llm, world, arc)
    img = StubImageProvider()
    # 给一个出场角色生成立绘，再渲染分镜
    board = store.read_json(repo.story_dir(world, arc) / "storyboard.json")
    names = {c["name"] for c in repo.load_characters(world)}
    board["pages"][0]["panels"][0]["characters"] = [next(iter(names))] if names else []
    store.write_json(repo.story_dir(world, arc) / "storyboard.json", board)
    cid = repo.load_characters(world)[0]["id"]
    res = engine.make_portrait(llm, img, world, cid)
    assert res["ok"]
    engine.render_comic(img, world, arc)
    assert any(c["refs"] for c in img.calls)


def test_make_arc_requires_events(world, llm):
    with pytest.raises(engine.EngineError):
        engine.make_arc(llm, world, event_ids=["evt_none"])


def test_create_world_requires_concept(llm):
    with pytest.raises(engine.EngineError):
        engine.create_world(llm, concept="   ")
