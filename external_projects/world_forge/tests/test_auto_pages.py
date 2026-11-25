"""自动页数必须由故事长度（关键情节数）决定，而不是固定或由模型随意决定。"""

import pytest

from world_forge import comic_layout as cl
from world_forge import engine, proposals, repo


@pytest.mark.parametrize("n,expected", [(0, 1), (1, 1), (3, 1), (4, 2), (6, 2), (7, 3), (18, 6), (19, 7), (100, 34)])
def test_auto_pages_is_ceiling_of_essential_events_per_page(n, expected):
    assert cl.auto_pages(n) == expected


def test_auto_pages_has_no_upper_cap():
    assert cl.auto_pages(300) == 100


def _story(world, llm):
    proposals.submit(world, "create_character", payload={"name": "守灯人", "role": "次要", "summary": "沉默的老人"})
    engine.advance(llm, world, 2)
    ids = [e["id"] for e in repo.load_events(world, "main")]
    return engine.make_arc(llm, world, event_ids=ids)


def _write_story(world, sid, paragraphs, size):
    body = "\n".join(f"第{i + 1}段情节：守灯人与商人争执，结果是灯油被夺。" * (size // 20) for i in range(paragraphs))
    (repo.story_dir(world, sid) / "story.md").write_text(body, encoding="utf-8")


def test_auto_page_count_follows_essential_events_not_a_fixed_six(world, llm, img):
    sid = _story(world, llm)
    _write_story(world, sid, paragraphs=4, size=200)
    board = engine.make_storyboard(llm, world, sid, pages="auto")
    ess = board["coverage"]["essential"]
    assert len(board["pages"]) == cl.auto_pages(ess)
    assert board["opts"]["pages_resolved"] == len(board["pages"])


def test_longer_story_gets_more_pages_than_shorter_one(world, llm, img):
    sid_short = _story(world, llm)
    _write_story(world, sid_short, paragraphs=2, size=200)
    short = engine.make_storyboard(llm, world, sid_short, pages="auto")
    sid_long = _story(world, llm)
    _write_story(world, sid_long, paragraphs=20, size=600)
    long_ = engine.make_storyboard(llm, world, sid_long, pages="auto")
    assert len(long_["pages"]) > len(short["pages"])
    assert len(short["pages"]) != 6 or len(long_["pages"]) != 6
