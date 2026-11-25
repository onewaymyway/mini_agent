"""漫画上限可配置：图像调用上限与候选数上限的默认值、保存、范围校验与生效。"""

import pytest

from world_forge import comic_layout as cl
from world_forge import engine, proposals, repo, store


def _story(world, llm):
    proposals.submit(world, "create_character", payload={"name": "守灯人", "role": "次要", "summary": "沉默的老人"})
    engine.advance(llm, world, 2)
    ids = [e["id"] for e in repo.load_events(world, "main")]
    return engine.make_arc(llm, world, event_ids=ids)


def test_defaults_when_nothing_is_configured(world, monkeypatch):
    monkeypatch.delenv("WORLD_FORGE_IMAGE_BUDGET", raising=False)
    assert engine.comic_limits(world) == {"image_budget": 80, "candidate_max": 3}


def test_environment_sets_default_budget_only_when_world_has_no_setting(world, monkeypatch):
    monkeypatch.setenv("WORLD_FORGE_IMAGE_BUDGET", "7")
    assert engine.comic_limits(world)["image_budget"] == 7
    engine.set_comic_limits(world, image_budget=5)
    assert engine.comic_limits(world)["image_budget"] == 5      # 世界设置优先于环境变量


def test_set_limits_persists_and_keeps_untouched_field(world):
    engine.set_comic_limits(world, image_budget=120)
    engine.set_comic_limits(world, candidate_max=4)
    assert engine.comic_limits(world) == {"image_budget": 120, "candidate_max": 4}
    assert repo.load_meta(world)["comic_limits"] == {"image_budget": 120, "candidate_max": 4}


@pytest.mark.parametrize("kwargs", [{"image_budget": 0}, {"image_budget": 501},
                                    {"candidate_max": 0}, {"candidate_max": 6}])
def test_out_of_range_limits_are_rejected_and_not_saved(world, kwargs):
    with pytest.raises(engine.EngineError):
        engine.set_comic_limits(world, **kwargs)
    assert "comic_limits" not in repo.load_meta(world)


def test_lowering_candidate_max_caps_candidates(world, llm, img):
    engine.set_comic_limits(world, candidate_max=1)
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=3)
    engine.render_comic(img, world, sid, candidates=3)
    pn = store.read_json(repo.story_dir(world, sid) / "storyboard.json")["pages"][0]["panels"][0]
    assert pn.get("candidates") is None or len(pn["candidates"]) == 1


def test_raising_candidate_max_allows_more_candidates(world, llm, img):
    engine.set_comic_limits(world, candidate_max=5)
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=3)
    engine.render_comic(img, world, sid, candidates=5)
    pn = store.read_json(repo.story_dir(world, sid) / "storyboard.json")["pages"][0]["panels"][0]
    assert len(pn["candidates"]) == 5


def test_budget_setting_limits_render_calls(world, llm, img):
    engine.set_comic_limits(world, image_budget=3)
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=3)
    res = engine.render_comic(img, world, sid)
    assert res["budget_exhausted"] is True and res["image_calls"] == 3 and len(img.calls) == 3
