"""R16 成片封面：不再是黑底；有插图时铺满并压暗；带标题、作者、副标题、AI 声明与水印。"""
import json

from PIL import Image, ImageStat

from article_to_video.compose.cover import build_cover, cover_subtitle, pick_cover_image

CANVAS = (1920, 1080)
WM_ON = {"enabled": True, "type": "text", "text": "测试水印", "mode": "static",
         "position": "bottom_right", "margin": 0.03, "opacity": 0.35, "scale": 0.06}
WM_OFF = dict(WM_ON, enabled=False)


def _cover(tmp_path, **kw):
    args = dict(canvas=CANVAS, orientation="landscape", title="一篇关于 AI 组织的文章", author="作者甲",
                subtitle="技术增长的几十年周期", ai_text="", wm=WM_OFF, pdir=tmp_path)
    args.update(kw)
    return build_cover(**args)


def _mean(img):
    return sum(ImageStat.Stat(img).mean) / 3


def test_cover_without_illustration_is_not_black(tmp_path):
    img = _cover(tmp_path)
    assert img.size == CANVAS and img.mode == "RGB"
    assert _mean(img) > 45                       # 原来的黑底约为 20 左右
    bright = img.convert("L").point(lambda v: 255 if v > 200 else 0).histogram()[255]
    assert bright > 500                          # 标题文字可见


def test_cover_uses_illustration_as_full_background(tmp_path):
    src = tmp_path / "ill.png"
    Image.new("RGB", (800, 800), (200, 60, 40)).save(src)
    img = _cover(tmp_path, source_image=src)
    assert img.size == CANVAS
    top = ImageStat.Stat(img.crop((0, 0, CANVAS[0], 60))).mean
    assert top[0] > top[2]                       # 插图的红色在上部可见
    bottom = ImageStat.Stat(img.crop((0, CANVAS[1] - 60, CANVAS[0], CANVAS[1]))).mean
    assert bottom[0] < 100                       # 下部压暗（原色红通道约 200），文字可读


def test_watermark_is_drawn_on_cover(tmp_path):
    off = _cover(tmp_path, wm=WM_OFF)
    on = _cover(tmp_path, wm=WM_ON)
    box = (CANVAS[0] - 400, CANVAS[1] - 120, CANVAS[0], CANVAS[1])
    assert ImageStat.Stat(on.crop(box)).mean != ImageStat.Stat(off.crop(box)).mean


def test_ai_disclosure_and_missing_author_are_optional(tmp_path):
    img = _cover(tmp_path, author="", ai_text="本视频含 AI 生成内容", subtitle="")
    assert img.size == CANVAS


def test_pick_cover_image_takes_first_real_illustration(tmp_path):
    part_dir = tmp_path / "parts" / "P01" / "visuals"
    part_dir.mkdir(parents=True)
    real = tmp_path / "parts" / "P01" / "ill.png"
    Image.new("RGB", (64, 64), (1, 2, 3)).save(real)
    (part_dir / "manifest.json").write_text(json.dumps({"scenes": [
        {"type": "keypoint", "background": "visuals/bg.png"},
        {"type": "illustration", "fallback_reason": "生图失败", "background_src": str(real)},
        {"type": "illustration", "fallback_reason": None, "background_src": str(real)},
    ]}), encoding="utf-8")
    assert pick_cover_image(tmp_path, ["P01"]) == real
    assert pick_cover_image(tmp_path, ["P02"]) is None


def test_subtitle_prefers_thesis_then_first_chapter_line():
    assert cover_subtitle({"thesis": "论点", "parts": [{"one_line": "x"}]}) == "论点"
    assert cover_subtitle({"parts": [{"one_line": "第一章一句话"}]}) == "第一章一句话"
    assert cover_subtitle({}) == ""
