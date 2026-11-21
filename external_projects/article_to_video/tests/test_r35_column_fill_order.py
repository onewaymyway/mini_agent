"""R35：分栏先填满左边再填右边，左栏最多比右栏多一个（列表网格与条目分栏都适用）。"""
from article_to_video.deck.textlayout import _count_split, build_text_layout
from article_to_video.render.themes import DEFAULT_THEME

CANVAS = (1920, 1080)


def test_count_split_fills_left_first_and_differs_by_at_most_one():
    for n in range(1, 30):
        for c in range(1, 5):
            if c > n:
                continue
            cols = _count_split(n, c)
            sizes = [len(x) for x in cols]
            assert sum(sizes) == n
            assert sizes == sorted(sizes, reverse=True)           # 左边不少于右边
            assert sizes[0] - sizes[-1] <= 1                      # 左边最多比右边多一个
            flat = [i for col in cols for i in col]
            assert flat == list(range(n))                         # 按顺序：左列在前


def _kp(text):
    return {"kind": "keypoint", "items": [text], "emphasis": [], "lead_in": False}


def test_split_entries_take_left_column_before_right():
    # 条目多、字多，逼出分栏：左栏的条目序号必须全部小于右栏的
    els = [_kp(f"第{i}条：这是一段需要占用一定高度的要点内容，用来检验分栏顺序") for i in range(10)]
    lay = build_text_layout(els, CANVAS, 260, 780, "landscape", DEFAULT_THEME, columns=True)
    assert lay is not None and lay["columns"] >= 2
    xs = sorted({round(x) for _, x, _, _ in lay["placed"]})
    left = [e for e, x, _, _ in lay["placed"] if round(x) == xs[0]]
    right = [e for e, x, _, _ in lay["placed"] if round(x) != xs[0]]
    order = {id(e): i for i, e in enumerate(els)}
    assert max(order.get(id(e), -1) for e in left) < min(order.get(id(e), 99) for e in right)
    assert abs(len(left) - len(right)) <= 1 or len(left) >= len(right)


def test_list_grid_fills_left_column_first():
    els = [{"kind": "list", "items": [f"第{i}项" for i in range(1, 9)], "emphasis": []}]
    lay = build_text_layout(els, CANVAS, 260, 780, "landscape", DEFAULT_THEME, columns=True)
    assert lay is not None and lay["placed"]
    grid = lay["placed"][0][0]["grid"]
    assert grid["k"] == 2                                          # 8 条短文字：两列网格
    assert grid["cols"] == [[0, 1, 2, 3], [4, 5, 6, 7]]            # 左列 1–4，右列 5–8
    xs = {}
    for e, x, _, _ in lay["placed"]:
        xs.setdefault(round(x), []).append(e)
    assert len(xs) == 1                                            # 一个列表整体在一栏内
