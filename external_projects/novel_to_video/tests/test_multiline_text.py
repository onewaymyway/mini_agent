"""口播/标题文本含换行时，字幕与封面渲染不应抛 can't measure length of multiline text。"""
from PIL import Image, ImageDraw, ImageFont

from novel_to_video.media import cover, subtitles
from novel_to_video.media.cover import _wrap


def _font():
    try:
        return ImageFont.truetype(cover.resolve_font(), 30)
    except Exception:
        return ImageFont.load_default()


def test_wrap_handles_newlines_and_control_chars():
    d = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    lines = _wrap(d, "第一行\n第二行\r\n​第三行\x07", _font(), 2000)
    assert lines == ["第一行", "第二行", "第三行"]


def test_cue_text_flattens_newlines():
    t = subtitles.cue_text({"type": "narration", "text": "他没有回头。\n声音平稳。"}, {})
    assert "\n" not in t and t == "他没有回头。 声音平稳。"


def test_render_cue_png_with_multiline_text(tmp_path):
    f = _font()
    out = subtitles.render_cue_png("甲\n乙\n丙\n丁", 640, 360, f, 30, tmp_path / "c.png")
    assert out.is_file()
