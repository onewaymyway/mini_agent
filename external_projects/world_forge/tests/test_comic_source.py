"""分镜素材可选：自动 / 短篇正文 / 小故事线 / 原始事件。"""

import pytest

from world_forge import engine, proposals, repo, store


def _story(world, llm):
    proposals.submit(world, "create_character", payload={"name": "守灯人", "role": "次要", "summary": "沉默的老人"})
    engine.advance(llm, world, 2)
    ids = [e["id"] for e in repo.load_events(world, "main")]
    return engine.make_arc(llm, world, event_ids=ids)


def _with_short(world, sid):
    (repo.story_dir(world, sid) / "story.md").write_text(
        "短篇正文的独特句子：灯塔熄灭的那一夜。\n第二段：守灯人下山。", encoding="utf-8")


def test_auto_uses_short_story_when_present(world, llm):
    sid = _story(world, llm)
    _with_short(world, sid)
    kind, text = engine._coverage_source(world, sid, "auto")
    assert kind == "short" and "灯塔熄灭" in text


def test_auto_falls_back_to_arc_without_short_story(world, llm):
    sid = _story(world, llm)
    kind, _ = engine._coverage_source(world, sid, "auto")
    assert kind == "arc"


def test_arc_option_ignores_short_story_text(world, llm):
    sid = _story(world, llm)
    _with_short(world, sid)
    kind, text = engine._coverage_source(world, sid, "arc")
    assert kind == "arc" and "灯塔熄灭的那一夜" not in text


def test_short_option_requires_short_story(world, llm):
    sid = _story(world, llm)
    with pytest.raises(engine.EngineError):
        engine._coverage_source(world, sid, "short")


def test_events_option_uses_source_events_only(world, llm):
    sid = _story(world, llm)
    _with_short(world, sid)
    kind, text = engine._coverage_source(world, sid, "events")
    assert kind == "events" and "灯塔熄灭的那一夜" not in text
    assert text.strip()


def test_invalid_source_is_rejected(world, llm):
    sid = _story(world, llm)
    with pytest.raises(engine.EngineError):
        engine._coverage_source(world, sid, "novel")


def test_storyboard_records_chosen_source_and_reuses_cache_per_source(world, llm, img):
    sid = _story(world, llm)
    _with_short(world, sid)
    board = engine.make_storyboard(llm, world, sid, pages="auto", source="short")
    assert board["source"] == "short"
    cov_path = repo.story_dir(world, sid) / "coverage.json"
    assert store.read_json(cov_path)["source"] == "short"
    board2 = engine.make_storyboard(llm, world, sid, pages="auto", source="arc")
    assert board2["source"] == "arc"
    assert store.read_json(cov_path)["source"] == "arc"
