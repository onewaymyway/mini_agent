"""音效排版：与网页版一致地按宽度换行；不压住对白；放不下时缩小字号，仍放不下则省略并警告。"""

from world_forge import comic_render as cr
from world_forge.comic_layout import plan_lettering

BOX = (20, 20, 580, 820)


def _panel(sfx, n_dialogue=2):
    return {"shot": "中景", "narration": "", "sfx": sfx,
            "dialogue": [{"speaker": f"人{i}", "text": "一句对白用来占据空间",
                          "position": ["左上", "右上"][i % 2], "speaker_side": ["左", "右"][i % 2],
                          "kind": "speech"} for i in range(n_dialogue)]}


def _sfx_block(fonts, text, size, max_w):
    return cr._block({"kind": "sfx", "text": text, "speaker": "", "zone": ("bottom", "right"), "_size": size},
                     fonts, max_w)


def test_long_sound_effect_wraps_like_the_web_version():
    fonts = cr.Fonts()
    blk = _sfx_block(fonts, "咔嗒咔嗒咔嗒咔嗒", 84, 160)
    assert len(blk["lines"]) > 1
    assert all(fonts.get(84).getlength(line) <= 160 + 84 for line in blk["lines"])


def test_sound_effect_never_covers_dialogue():
    fonts = cr.Fonts()
    items = plan_lettering(_panel("轰隆隆"))
    warnings = []
    placed = cr._layout_panel(items, fonts, BOX, close=False, warnings=warnings)
    rects = [cr._rect_of(it, blk, zone, BOX, x=x, y=y) for it, blk, zone, x, y in placed]
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert not cr._overlaps(a, b)


def test_crowded_sound_effect_is_shrunk_or_omitted_with_warning():
    fonts = cr.Fonts()
    tiny = (0, 0, 150, 150)
    warnings = []
    placed = cr._layout_panel(plan_lettering(_panel("轰隆隆隆隆", n_dialogue=0)), fonts, tiny,
                              close=False, warnings=warnings)
    kinds = [it["kind"] for it, *_ in placed]
    if "sfx" not in kinds:
        assert any("音效" in w and "省略" in w for w in warnings)


def test_sfx_wraps_when_wider_than_52_percent_of_panel_like_the_web():
    """网页版 .bz 的宽度上限是格宽的 52%；PNG 与之一致：超过时换行，而不是一整行溢出。"""
    fonts = cr.Fonts()
    pw = 560                                   # 半行格的宽度
    blk = _sfx_block(fonts, "咔嗒咔嗒", 84, int(pw * cr.BUBBLE_MAX_RATIO))
    assert len(blk["lines"]) == 2
