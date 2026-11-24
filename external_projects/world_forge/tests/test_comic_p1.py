"""漫画质量升级 P1：节拍表、结构化分镜、版式模板、阅读方向、服务端排字与 PNG 导出。"""

import zipfile

import pytest
from PIL import Image

from world_forge import comic_layout as cl
from world_forge import comic_render, engine, generate, proposals, repo, store


# ---------- 设置

def test_normalize_opts_defaults_and_auto():
    assert cl.normalize_opts() == {"pages": 6, "reading": "ltr", "color": "color"}
    assert cl.normalize_opts(pages="auto")["pages"] == "auto"
    assert cl.normalize_opts(pages="8", reading="rtl", color="bw") == {"pages": 8, "reading": "rtl", "color": "bw"}
    assert cl.expected_pages({"pages": "auto"}) is None


@pytest.mark.parametrize("kwargs", [{"pages": 0}, {"pages": 21}, {"pages": "many"}, {"reading": "up"}, {"color": "sepia"}])
def test_normalize_opts_rejects_bad_values(kwargs):
    with pytest.raises(ValueError):
        cl.normalize_opts(**kwargs)


# ---------- 版式

def _panel(span=1, shot="中景", **kw):
    return {"span": span, "shot": shot, "dialogue": [], "narration": "", **kw}


def test_single_panel_fills_page():
    lay = cl.layout_page([_panel()])
    x, y, w, h = lay["rects"][0]
    assert lay["template"] == "single"
    assert w == pytest.approx(1 - 2 * cl.PAGE_MARGIN) and h == pytest.approx(1 - 2 * cl.PAGE_MARGIN)


def test_full_width_panel_takes_its_own_row_and_pairs_are_side_by_side():
    panels = [_panel(span=2), _panel(), _panel()]
    lay = cl.layout_page(panels, "ltr")
    big, a, b = lay["rects"]
    assert big[2] == pytest.approx(1 - 2 * cl.PAGE_MARGIN)          # 整行
    assert a[1] == pytest.approx(b[1]) and a[0] < b[0]               # 同一行，左到右
    assert big[1] + big[3] <= a[1] + 1e-9                             # 大格在上方
    assert big[3] > a[3]                                              # 整行格比半行格高（突出高潮）


def test_rtl_reverses_order_within_a_row():
    panels = [_panel(), _panel(), _panel(), _panel()]          # 两行两列；两格的页会上下排列
    ltr = cl.layout_page(panels, "ltr")["rects"]
    rtl = cl.layout_page(panels, "rtl")["rects"]
    assert ltr[0][0] < ltr[1][0]
    assert rtl[0][0] > rtl[1][0]                                      # 第一格在右边
    assert rtl[0][1] == pytest.approx(ltr[0][1])                      # 行的上下顺序不变


def test_two_half_panels_are_stacked_not_squeezed_side_by_side():
    rects = cl.layout_page([_panel(), _panel()], "ltr")["rects"]
    assert rects[0][1] < rects[1][1] and rects[0][2] == pytest.approx(1 - 2 * cl.PAGE_MARGIN)


def test_layout_rects_do_not_overlap_and_stay_on_page():
    panels = [_panel(span=2), _panel(), _panel(), _panel(span=2), _panel()]
    rects = cl.layout_page(panels, "ltr")["rects"]
    for x, y, w, h in rects:
        assert x >= 0 and y >= 0 and x + w <= 1 + 1e-9 and y + h <= 1 + 1e-9
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            overlap = a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]
            assert not overlap


# ---------- 排字

def test_close_up_keeps_centre_clear_for_the_face():
    panel = _panel(shot="特写", dialogue=[{"speaker": "甲", "text": "你好", "position": "中上", "speaker_side": "中"}])
    items = cl.plan_lettering(panel)
    assert items[0]["zone"][1] in ("left", "right")


def test_wide_shot_may_use_centre_top():
    panel = _panel(shot="远景", dialogue=[{"speaker": "甲", "text": "你好", "position": "中上", "speaker_side": "中"}])
    assert cl.plan_lettering(panel)[0]["zone"] == ("top", "center")


def test_tail_points_toward_speaker_side():
    panel = _panel(dialogue=[{"speaker": "甲", "text": "左边", "position": "右上", "speaker_side": "左"}])
    tail_x, _ = cl.plan_lettering(panel)[0]["tail"]
    assert tail_x == cl.SIDE_X["左"]


def test_lettering_kinds_caption_thought_shout_and_sfx():
    panel = _panel(narration="夜深了", sfx="咔嗒",
                   dialogue=[{"speaker": "甲", "text": "……", "position": "左上", "kind": "thought"},
                             {"speaker": "乙", "text": "停下！", "position": "右上", "kind": "shout"}])
    kinds = [i["kind"] for i in cl.plan_lettering(panel)]
    assert kinds == ["caption", "thought", "shout", "sfx"]


def test_plan_page_mirrors_caption_for_rtl():
    page = {"no": 1, "panels": [_panel(narration="旁白")]}
    assert cl.plan_page(page, "rtl")["panels"][0]["items"][0]["zone"] == ("top", "right")
    assert cl.plan_page(page, "ltr")["panels"][0]["items"][0]["zone"] == ("top", "left")


# ---------- 校验

def test_storyboard_rejects_overlong_dialogue():
    d = {"pages": [{"no": 1, "panels": [{"visual_en": "x", "dialogue": [{"speaker": "甲", "text": "字" * 60}]}]}]}
    assert any("对白过长" in e for e in generate._validate_storyboard(d))


def test_storyboard_fills_kind_and_speaker_side_defaults():
    p = {"visual_en": "x", "dialogue": [{"speaker": "甲", "text": "你好", "position": "左下"}]}
    assert generate.normalize_panel(p) == []
    dl = p["dialogue"][0]
    assert dl["kind"] == "speech" and dl["speaker_side"] == "左"


def test_storyboard_page_count_must_match_expectation():
    d = {"pages": [{"no": 1, "panels": [{"visual_en": "x"}]}]}
    assert generate._validate_storyboard(d, expect=2)
    assert generate._validate_storyboard(d, expect=1) == []


def test_beats_validation_requires_purpose_and_focus():
    assert generate._validate_beats({"pages": [{"purpose": "铺垫", "focus": ""}]})
    assert generate._validate_beats({"pages": [{"purpose": "铺垫", "focus": "门"}]}, expect=2)
    ok = {"pages": [{"purpose": "铺垫：开场", "focus": "门", "hook": ""}]}
    assert generate._validate_beats(ok, expect=1) == [] and ok["pages"][0]["no"] == 1


# ---------- 引擎集成（离线 stub）

def _story(world, llm):
    proposals.submit(world, "create_character", payload={"name": "守灯人", "role": "次要", "summary": "沉默的老人"})
    engine.advance(llm, world, 2)
    ids = [e["id"] for e in repo.load_events(world, "main")]
    return engine.make_arc(llm, world, event_ids=ids)


def test_storyboard_follows_fixed_page_count_and_writes_beats(world, llm):
    sid = _story(world, llm)
    board = engine.make_storyboard(llm, world, sid, pages=3, reading="rtl", color="bw")
    assert len(board["pages"]) == 3
    assert all(pg.get("beat") and "hook" in pg for pg in board["pages"])
    d = repo.story_dir(world, sid)
    assert (d / "beats.json").is_file()
    assert repo.load_story_meta(world, sid)["comic_opts"] == {"pages": 3, "reading": "rtl", "color": "bw"}


def test_storyboard_auto_pages_follows_story_length(world, llm):
    sid = _story(world, llm)
    board = engine.make_storyboard(llm, world, sid, pages="auto")
    assert board["opts"]["pages"] == "auto"
    assert len(board["pages"]) >= 1


def test_render_writes_lettered_png_pages_and_zip(world, llm, img):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=2)
    res = engine.render_comic(img, world, sid)
    d = repo.story_dir(world, sid)
    pngs = sorted((d / "pages").glob("page_*.png"))
    assert len(pngs) == 2
    with Image.open(pngs[0]) as im:
        assert im.size == (cl.PAGE_W, cl.PAGE_H)
    with zipfile.ZipFile(d / "comic_pages.zip") as zf:
        assert sorted(zf.namelist()) == [p.name for p in pngs]
    assert res["pages_zip"].endswith("comic_pages.zip") and len(res["pages"]) == 2


def test_render_can_switch_reading_direction_and_color_without_new_storyboard(world, llm, img):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=2)
    res = engine.render_comic(img, world, sid, reading="rtl", color="bw")
    assert res["reading"] == "rtl" and res["color"] == "bw"
    board = store.read_json(repo.story_dir(world, sid) / "storyboard.json")
    assert board["opts"]["reading"] == "rtl" and board["opts"]["color"] == "bw"


def test_html_preview_uses_same_layout_as_png(world, llm, img):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=2)
    engine.render_comic(img, world, sid)
    html_text = (repo.story_dir(world, sid) / "comic.html").read_text(encoding="utf-8")
    assert html_text.count("class='page'") == 2
    assert "class='bubble'" in html_text


# ---------- 渲染器

def test_render_page_without_images_shows_placeholder(tmp_path):
    plan = cl.plan_page({"no": 1, "panels": [_panel(narration="旁白", sfx="轰",
                                                   dialogue=[{"speaker": "甲", "text": "你好", "position": "左上"}],
                                                   image=None, image_error="额度不足")]}, "ltr")
    fonts = comic_render.Fonts()
    page = comic_render.render_page(plan, tmp_path, fonts, "color")
    assert page.size == (cl.PAGE_W, cl.PAGE_H)


def test_wrap_breaks_chinese_text_by_width():
    fonts = comic_render.Fonts()
    lines = comic_render._wrap("这是一段很长的中文对白用于测试折行" * 3, fonts.get(30), 200)
    assert len(lines) > 1
    assert all(fonts.get(30).getlength(s) <= 200 or len(s) == 1 for s in lines)
