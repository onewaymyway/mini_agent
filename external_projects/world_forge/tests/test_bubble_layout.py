"""气泡避让：同一格内的旁白、对白、音效互不重叠；近景不占正中；放不下时给出警告而不是静默重叠。"""

import pytest

from world_forge import comic_render as cr
from world_forge.comic_layout import plan_lettering

BOX = (20, 20, 580, 820)   # 一个 560×800 的格子


def _items(n_dialogue=4, sfx=True, caption=True, shot="中景"):
    panel = {"shot": shot, "narration": "夜深了，港口只剩灯光。" if caption else "",
             "sfx": "轰" if sfx else "",
             "dialogue": [{"speaker": f"人{i}", "text": "这是一句比较长的对白用来占据空间" * (1 + i % 2),
                           "position": ["左上", "右上", "左下", "右下", "中上"][i % 5],
                           "speaker_side": ["左", "右", "左", "右", "中"][i % 5], "kind": "speech"}
                          for i in range(n_dialogue)]}
    return plan_lettering(panel)


def _rects(placed):
    out = []
    for item, blk, zone, x, y in placed:
        out.append(cr._rect_of(item, blk, zone, BOX, x=x, y=y))
    return out


def test_crowded_panel_has_no_overlapping_elements():
    fonts = cr.Fonts()
    warnings = []
    placed = cr._layout_panel(_items(3), fonts, BOX, close=False, warnings=warnings)
    rects = _rects(placed)
    assert not warnings
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert not cr._overlaps(a, b), f"重叠：{a} 与 {b}"


def test_all_elements_stay_inside_the_panel():
    fonts = cr.Fonts()
    placed = cr._layout_panel(_items(3), fonts, BOX, close=False)
    for r in _rects(placed):
        assert r[0] >= BOX[0] and r[1] >= BOX[1] and r[2] <= BOX[2] and r[3] <= BOX[3]


@pytest.mark.parametrize("shot", ["特写", "近景"])
def test_close_up_never_places_bubbles_in_the_centre(shot):
    fonts = cr.Fonts()
    placed = cr._layout_panel(_items(3, sfx=False, caption=False, shot=shot), fonts, BOX, close=True)
    for item, blk, zone, x, y in placed:
        assert zone[1] != "center"


def test_impossible_fit_is_reported_not_silent():
    fonts = cr.Fonts()
    tiny = (0, 0, 120, 120)
    items = _items(6, sfx=True, caption=True)
    warnings = []
    cr._layout_panel(items, fonts, tiny, close=False, warnings=warnings)
    assert warnings, "放不下的元素必须给出警告，供用户调整对白"
