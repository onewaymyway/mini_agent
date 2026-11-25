"""漫画 P4：网页阅读器、CBZ 与条漫长图导出、看板内文字编辑（不调用图像模型）。"""

import zipfile

import pytest
from PIL import Image

from world_forge import comic_layout as cl
from world_forge import engine, proposals, repo, store


def _story(world, llm):
    proposals.submit(world, "create_character", payload={"name": "守灯人", "role": "次要", "summary": "沉默的老人"})
    engine.advance(llm, world, 2)
    ids = [e["id"] for e in repo.load_events(world, "main")]
    return engine.make_arc(llm, world, event_ids=ids)


def _rendered(world, llm, img, pages=2, **kw):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=pages, **kw)
    engine.render_comic(img, world, sid)
    return sid, repo.story_dir(world, sid)


def test_reader_html_lists_pages_and_direction(world, llm, img):
    sid, d = _rendered(world, llm, img, reading="rtl")
    text = (d / "reader.html").read_text(encoding="utf-8")
    assert '"pages/page_01.png"' in text and '"pages/page_02.png"' in text
    assert "const rtl = true" in text
    assert "ArrowLeft" in text and "连续滚动" in text


def test_reader_is_left_to_right_by_default(world, llm, img):
    sid, d = _rendered(world, llm, img)
    assert "const rtl = false" in (d / "reader.html").read_text(encoding="utf-8")


def test_cbz_contains_pages_and_reading_direction_metadata(world, llm, img):
    sid, d = _rendered(world, llm, img, pages=2, reading="rtl")
    res = engine.export_comic_files(world, sid)
    with zipfile.ZipFile(res["cbz"]) as zf:
        names = zf.namelist()
        assert "page_01.png" in names and "page_02.png" in names and "ComicInfo.xml" in names
        assert "<Manga>YesAndRightToLeft</Manga>" in zf.read("ComicInfo.xml").decode("utf-8")


def test_cbz_for_left_to_right_marks_not_manga_direction(world, llm, img):
    sid, d = _rendered(world, llm, img)
    res = engine.export_comic_files(world, sid)
    with zipfile.ZipFile(res["cbz"]) as zf:
        assert "<Manga>No</Manga>" in zf.read("ComicInfo.xml").decode("utf-8")


def test_strip_is_pages_stacked_vertically(world, llm, img):
    sid, d = _rendered(world, llm, img, pages=2)
    res = engine.export_comic_files(world, sid)
    with Image.open(res["strip"]) as strip:
        assert strip.size == (cl.PAGE_W, cl.PAGE_H * 2)


def test_export_requires_rendered_pages(world, llm):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=3)
    with pytest.raises(engine.EngineError):
        engine.export_comic_files(world, sid)


def test_editing_text_rerenders_page_without_calling_image_model(world, llm, img):
    sid, d = _rendered(world, llm, img, pages=3)
    before = (d / "pages" / "page_01.png").read_bytes()
    calls_before = len(img.calls)
    board = store.read_json(d / "storyboard.json")
    pn = board["pages"][0]["panels"][0]
    n_dl = len(pn["dialogue"])
    engine.edit_panel_text(world, sid, 1, 1, narration="新的旁白文字", sfx="咔",
                           dialogue=[f"改写第{k + 1}句" for k in range(n_dl)])
    assert len(img.calls) == calls_before, "文字编辑不得调用图像模型"
    after = (d / "pages" / "page_01.png").read_bytes()
    assert after != before
    board = store.read_json(d / "storyboard.json")
    assert board["pages"][0]["panels"][0]["narration"] == "新的旁白文字"
    assert board["pages"][0]["panels"][0]["sfx"] == "咔"
    assert board["pages"][0]["panels"][0]["edited"] is True


def test_edit_rejects_wrong_dialogue_count_and_overlong_text(world, llm, img):
    sid, d = _rendered(world, llm, img, pages=3)
    pn = store.read_json(d / "storyboard.json")["pages"][0]["panels"][0]
    with pytest.raises(engine.EngineError):
        engine.edit_panel_text(world, sid, 1, 1, dialogue=["a"] * (len(pn["dialogue"]) + 1))
    if pn["dialogue"]:
        with pytest.raises(engine.EngineError):
            engine.edit_panel_text(world, sid, 1, 1, dialogue=["字" * 60] * len(pn["dialogue"]))


def test_edit_unknown_panel_raises(world, llm, img):
    sid, d = _rendered(world, llm, img, pages=3)
    with pytest.raises(engine.EngineError):
        engine.edit_panel_text(world, sid, 9, 9, narration="x")


def test_rerender_does_not_call_image_model_and_applies_new_direction(world, llm, img):
    sid, d = _rendered(world, llm, img, pages=3)
    calls_before = len(img.calls)
    before = (d / "pages" / "page_01.png").read_bytes()
    res = engine.rerender_comic(world, sid, reading="rtl", color="bw")
    assert len(img.calls) == calls_before, "仅重新渲染不得调用图像模型"
    assert res["image_calls"] == 0
    assert res["reading"] == "rtl" and res["color"] == "bw"
    assert (d / "pages" / "page_01.png").read_bytes() != before
    assert len(list((d / "pages").glob("page_*.png"))) == 3


def test_rerender_keeps_storyboard_text_and_updates_existing_exports(world, llm, img):
    sid, d = _rendered(world, llm, img, pages=2)
    engine.export_comic_files(world, sid)
    board_before = store.read_json(d / "storyboard.json")
    engine.rerender_comic(world, sid)
    board_after = store.read_json(d / "storyboard.json")
    assert board_after["pages"] == board_before["pages"]
    assert (d / "comic.cbz").is_file() and (d / "comic_strip.png").is_file()


def test_rerender_without_storyboard_raises(world, llm):
    sid = _story(world, llm)
    with pytest.raises(engine.EngineError):
        engine.rerender_comic(world, sid)
