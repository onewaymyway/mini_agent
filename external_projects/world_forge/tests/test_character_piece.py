"""以角色为主角创作小故事与小笑话：视角为该角色、字数限制、保存为短篇，并可接续分镜与漫画。"""

import pytest

from world_forge import engine, generate, proposals, repo


def _world_with_character(world, llm):
    rec = proposals.submit(world, "create_character", payload={"name": "守灯人", "role": "主角", "summary": "沉默的老人"})
    proposals.approve(world, rec["id"])
    engine.advance(llm, world, 2)
    return next(c["id"] for c in repo.load_characters(world) if c["name"] == "守灯人")


def test_joke_is_saved_as_short_story_from_the_character_perspective(world, llm):
    cid = _world_with_character(world, llm)
    sid = engine.write_character_piece(llm, world, subject_id=cid, kind="joke", brief="被小孩问灯塔为什么不亮")
    meta = repo.load_story_meta(world, sid)
    assert meta["kind"] == "short" and meta["form"] == "piece_joke"
    assert meta["piece"] == {"kind": "joke", "subject_id": cid}
    assert meta["focus"]["name"] == "守灯人" and meta["focus"]["kind"] == "角色"
    body = (repo.story_dir(world, sid) / "story.md").read_text(encoding="utf-8")
    assert body.strip() and not body.lstrip().startswith("#")   # 标题已拆出


def test_story_form_is_saved_with_its_kind(world, llm):
    cid = _world_with_character(world, llm)
    sid = engine.write_character_piece(llm, world, subject_id=cid, kind="story")
    assert repo.load_story_meta(world, sid)["piece"]["kind"] == "story"


def test_piece_requires_a_character_not_an_item(world, llm):
    _world_with_character(world, llm)
    place = next(e for e in repo.load_entities(world) if e.get("kind") == "地理")
    with pytest.raises(engine.EngineError):
        engine.write_character_piece(llm, world, subject_id=place["id"], kind="joke")


def test_unknown_kind_and_missing_subject_are_rejected(world, llm):
    cid = _world_with_character(world, llm)
    with pytest.raises(engine.EngineError):
        engine.write_character_piece(llm, world, subject_id=cid, kind="poem")
    with pytest.raises(engine.EngineError):
        engine.write_character_piece(llm, world, subject_id="nobody", kind="joke")


class _Repeat:
    name = "repeat"

    def __init__(self, texts):
        self.texts = list(texts)
        self.prompts = []

    def ask(self, prompt, system=""):
        self.prompts.append(prompt)
        return self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]


def test_joke_over_the_length_limit_is_retried_with_feedback():
    long_joke = "字" * 400
    llm = _Repeat([long_joke, "# 标题\n\n短笑话。"])
    out = generate.piece_draft(llm, context={"character": {"name": "守灯人"}}, kind="joke", attempts=3)
    assert "短笑话" in out
    assert "超过 200 字" in llm.prompts[1]


def test_piece_gives_up_after_attempts(world):
    llm = _Repeat(["字" * 400])
    with pytest.raises(generate.GenerationError):
        generate.piece_draft(llm, context={"character": {"name": "守灯人"}}, kind="joke", attempts=2)


def test_joke_can_continue_into_storyboard_and_comic(world, llm, img):
    cid = _world_with_character(world, llm)
    sid = engine.write_character_piece(llm, world, subject_id=cid, kind="joke", brief="被小孩问灯塔为什么不亮")
    board = engine.make_storyboard(llm, world, sid, pages="auto", source="short")
    assert board["source"] == "short"
    assert len(board["pages"]) == 1, "一个小笑话只需一页"
    res = engine.render_comic(img, world, sid)
    assert res["ok"] >= 1 and res["pages"]


def test_regenerating_a_piece_keeps_the_same_story_id(world, llm):
    cid = _world_with_character(world, llm)
    sid = engine.write_character_piece(llm, world, subject_id=cid, kind="joke", brief="提示")
    before = (repo.story_dir(world, sid) / "story.md").read_text(encoding="utf-8")
    assert engine.regenerate_short(llm, world, sid) == sid
    assert (repo.story_dir(world, sid) / "history").exists()
    assert before  # 旧正文已归档
