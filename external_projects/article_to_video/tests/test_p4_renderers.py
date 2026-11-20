"""P4 渲染器：每类场景的状态数、透明底、逐步覆盖面递增、两种画幅、换行不越界。"""
import pytest

from article_to_video.render.fonts import FontError, find_cjk_font
from article_to_video.visual.states import ALL_AT_ONCE_TYPES, uniform_reveal

try:
    find_cjk_font({})
except FontError:
    pytest.skip("本机没有中文字体", allow_module_level=True)

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from article_to_video.render.fonts import load_font  # noqa: E402
from article_to_video.render.renderers import (render_background, render_chapter_card,  # noqa: E402
                                               render_scene_frames)
from article_to_video.render.text import split_segments, wrap_text  # noqa: E402
from article_to_video.render.themes import canvas_size  # noqa: E402
from article_to_video.visual.states import ALL_AT_ONCE_TYPES, expected_state_count  # noqa: E402


def _scene(t, items, **kw):
    d = {"id": "s", "beat": "s", "type": t, "items": items, "emphasis": ["关键"], "header": [], "rows": [],
         "loop": False, "formula": "", "image": None, "refs": []}
    d.update(kw)
    d["reveal"] = uniform_reveal(d, 3)
    return d


def _coverage(img):
    return sum(img.getchannel("A").histogram()[1:])


CASES = {
    "keypoint": _scene("keypoint", ["这是第一条要点，讲清关键", "第二条很短", "第三条"]),
    "list": _scene("list", ["AI写代码", "AI写文章", "AI做PPT"]),
    "timeline": _scene("timeline", ["2022 年 爆发", "2027 年 普及", "2035 年 之后"]),
    "diagram": _scene("diagram", ["现实世界", "信息化", "数字化", "网络化"], loop=True),
    "table": _scene("table", ["蒸汽、机械", "电力、化工"], header=["革命", "技术", "改变"],
                    rows=[["第一次", "蒸汽", "体力"], ["第二次", "电力", "规模"]]),
    "quote": _scene("quote", ["AI 是生产组织方式的革命。"]),
    "formula": _scene("formula", ["产出由资本与劳动决定", "技术项决定效率"], formula="Y = F(K, L, A)"),
}


@pytest.mark.parametrize("orientation", ["landscape", "portrait"])
@pytest.mark.parametrize("kind", list(CASES))
def test_frames_match_states_and_are_transparent_rgba(kind, orientation):
    sc = CASES[kind]
    canvas = canvas_size(orientation, "720P")
    frames = render_scene_frames(sc, canvas, orientation, "ink")
    assert len(frames) == expected_state_count(sc)
    for f in frames:
        assert f.mode == "RGBA" and f.size == canvas
        assert f.getpixel((0, 0))[3] == 0          # 四角透明：只绘制内容层
    if kind in ALL_AT_ONCE_TYPES:                 # 结构化画面整体一次显示（第一期起）
        assert len(frames) == 1
    else:
        assert _coverage(frames[0]) < _coverage(frames[-1]) or kind == "quote"


@pytest.mark.parametrize("kind", ["keypoint"])
def test_more_revealed_items_cover_more_pixels(kind):
    sc = CASES[kind]
    frames = render_scene_frames(sc, canvas_size("landscape", "720P"), "landscape", "ink")
    cov = [_coverage(f) for f in frames]
    assert cov == sorted(cov) and cov[-1] > cov[0]


@pytest.mark.parametrize("kind", ["list", "timeline", "diagram", "table"])
def test_structured_items_are_drawn_all_at_once(kind):
    sc = CASES[kind]
    frames = render_scene_frames(sc, canvas_size("landscape", "720P"), "landscape", "ink")
    assert len(frames) == 1 and _coverage(frames[0]) > 0


def test_formula_first_state_shows_formula_only():
    sc = CASES["formula"]
    frames = render_scene_frames(sc, canvas_size("landscape", "720P"), "landscape", "ink")
    assert _coverage(frames[0]) > 0
    assert _coverage(frames[0]) < _coverage(frames[1]) < _coverage(frames[-1])


def test_illustration_falls_back_to_card_when_asked():
    sc = dict(CASES["keypoint"], type="illustration")
    frames = render_scene_frames(sc, canvas_size("landscape", "720P"), "landscape", "ink", as_type="keypoint")
    assert len(frames) == 3 and _coverage(frames[-1]) > 0


def test_wrap_text_never_exceeds_width_and_marks_truncation():
    img = Image.new("RGBA", (10, 10))
    draw = ImageDraw.Draw(img)
    font = load_font(40)
    text = "这是一段很长很长的文字用于测试换行是否会超出宽度限制AI-Agent与GPU混排" * 3
    lines = wrap_text(draw, text, font, 400, max_lines=99)
    assert all(draw.textlength(line, font=font) <= 400 for line in lines)
    cut = wrap_text(draw, text, font, 400, max_lines=2)
    assert len(cut) == 2 and cut[-1].endswith("…")


def test_english_words_are_not_split_across_lines():
    img = Image.new("RGBA", (10, 10))
    draw = ImageDraw.Draw(img)
    font = load_font(40)
    lines = wrap_text(draw, "前缀文字 GPU-Cluster 后缀", font, 150, max_lines=9)
    assert any("GPU-Cluster" in line for line in lines)


def test_emphasis_split_marks_only_matching_words():
    assert split_segments("电力与化工", ["电力"]) == [("电力", True), ("与化工", False)]
    assert split_segments("没有强调", []) == [("没有强调", False)]


def test_background_and_chapter_card_have_canvas_size():
    canvas = canvas_size("portrait", "720P")
    bg = render_background(canvas, "ink")
    assert bg.size == canvas and bg.mode == "RGB"
    card = render_chapter_card({"number": "第 1 章", "title": "一个很长很长的章标题用于测试换行", "one_line": "副标题"},
                               canvas, "portrait", "ink")
    assert card.size == canvas and _coverage(card) > 0
