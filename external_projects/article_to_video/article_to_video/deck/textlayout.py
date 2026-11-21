"""新方案的文字页网格排版（R32）：要点、列表、金句直接排在页面网格上，不再逐元素渲染成图后居中堆叠。

旧做法的问题（R30 之后仍存在）：每个元素各自渲染、裁掉留白、居中堆叠，于是
  * 内容挤在页面中间约 40% 的窄条里，左右大片空白，字很小；
  * 各元素宽度不同、各自居中，左边缘参差不齐；金句的引号与竖条悬在文字外面；
  * “于是：”“今天：”这类前言也占一整张卡片。

现做法：
  1. 占满页面：内容区取页面宽度的 88%（与页标题左对齐）、标题以下的全部高度；
  2. 搜索：字号从大到小，栏数 1–3，找到第一个能把全部条目放下的组合（即“能放下的最大字号”）；栏数多了字号能更大时自然分栏；
  3. 栏内统一：同一栏所有条目同宽同左边缘；前言排成无卡片的小标题，并与后面的条目同栏同页（不拆开）；
     要点是卡片，列表是编号行，金句是带强调底色与竖条的整宽标注；
  4. 顺序：按元素顺序先填满左栏（自上而下），再填右栏；按个数分栏，左栏最多比右栏多一个，整体垂直居中；
     一个列表不拆到左右两栏，整体放在一栏内；条目字数少时，列表在这一栏内部排成 2–4 列的编号网格（先填满左列自上而下，再填右列），
     这样比“左边一半右边一半”更紧凑、更易读；只有整个列表一栏也放不下时，才退回逐条排列（可跨栏）；
  5. 逐个出现：位置在整页上一次算定，每个状态只画已出现的元素，页面不会跳动。
只覆盖文字类元素（keypoint / list / quote）；含表格、流程、公式的页仍走 deck/layout.py。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..render.fonts import load_font
from ..render.text import wrap_text
from ..render.themes import get_theme, unit_for

TEXT_KINDS = ("keypoint", "list", "quote")
F_MAX = {"landscape": 56.0, "portrait": 50.0}     # 最大字号（按 1920 / 1080 宽为 1.0 的单位）
F_FLOOR = {"landscape": 30.0, "portrait": 26.0}   # 字号下限；低于下限才标记“偏小”
F_LAST = 18.0                                     # 最后手段：仍放不下时可降到的字号
MAX_COLUMNS = 3
MIN_COL_CHARS = 9                                 # 多栏时每栏至少约 9 个字宽，避免过窄


_NO_LINE_START = "。，、；：！？）》」』”’.,;:!?)]"


def _wrap(draw, text: str, font, width: int) -> List[str]:
    """换行；标点不单独成行（把上一行最后一个字带到下一行，避免“。”孤零零占一行）。"""
    lines = wrap_text(draw, text, font, width)
    for i in range(1, len(lines)):
        while lines[i] and lines[i][0] in _NO_LINE_START and len(lines[i - 1]) > 1:
            lines[i] = lines[i - 1][-1] + lines[i]
            lines[i - 1] = lines[i - 1][:-1]
            if len(lines[i]) > 1 and lines[i][1] not in _NO_LINE_START:
                break
    return lines


def supports(elements: List[Dict[str, Any]]) -> bool:
    return bool(elements) and all(e.get("kind") in TEXT_KINDS for e in elements)


def _entries(elements: List[Dict[str, Any]], split_lists: bool = False) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ei, el in enumerate(elements):
        kind = el.get("kind")
        if kind == "list" and not split_lists:           # 整个列表是一个条目：不拆到两栏
            items = [str(x) for x in el.get("items") or [] if str(x).strip()]
            if items:
                out.append({"kind": "list", "items": items, "text": "", "el": ei, "num": 1})
            continue
        for n, text in enumerate(str(x) for x in el.get("items") or [] if str(x).strip()):
            if kind == "keypoint" and el.get("lead_in"):
                k = "lead"
            elif kind == "keypoint":
                k = "card"
            elif kind == "list":
                k = "row"
            else:
                k = "quote"
            out.append({"kind": k, "text": text, "el": ei, "num": n + 1})
    return out


def _units(entries: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """前言（小标题）与它后面的条目成组，不拆开。"""
    units: List[List[Dict[str, Any]]] = []
    pending: List[Dict[str, Any]] = []
    for e in entries:
        if e["kind"] == "lead":
            pending.append(e)
            continue
        units.append(pending + [e])
        pending = []
    if pending:
        units.append(pending)
    return units


class _Metrics:
    """给定字号 f 与栏宽 cw，量出每个条目的行与高度。"""

    def __init__(self, draw, f: float, cw: float):
        self.draw, self.f, self.cw = draw, f, cw
        self.font = load_font(max(10, round(f)))
        self.fq = load_font(max(10, round(f * 1.12)))
        self.fl = load_font(max(10, round(f * 0.9)))
        self.pad_v, self.pad_h, self.bar = 0.42 * f, 0.6 * f, 0.22 * f
        self.gap = 0.32 * f

    def measure(self, e: Dict[str, Any]) -> None:
        d, f = self.draw, self.f
        k = e["kind"]
        if k == "list":
            self._measure_list(e)
            return
        if k == "card":
            font, inner, pv = self.font, self.cw - 2 * self.pad_h - self.bar, self.pad_v
        elif k == "row":
            font, inner, pv = self.font, self.cw - 1.5 * f, 0.2 * f
        elif k == "quote":
            font, inner, pv = self.fq, self.cw - 2 * 0.7 * f - self.bar, 0.5 * f
        else:
            font, inner, pv = self.fl, self.cw, 0.0
        lines = _wrap(d, e["text"], font, max(20, int(inner)))
        sz = font.size
        text_h = sz * len(lines) + int(sz * 0.25) * max(0, len(lines) - 1)
        e.update(lines=lines, font=font, pv=pv, h=text_h + 2 * pv + (0.3 * f if k == "lead" else 0.1 * f))

    def _measure_list(self, e: Dict[str, Any]) -> None:
        """列表整体放在一栏内：条目少用 1 列；条目多且字少时在栏内排成 2–4 列网格（先填满左列），取高度最小者。"""
        d, f, items = self.draw, self.f, e["items"]
        n = len(items)
        k_max = min(4, max(1, n // 3))
        gx = 0.8 * f
        pv = 0.2 * f
        best = None
        for k in range(1, k_max + 1):
            cell_w = (self.cw - gx * (k - 1)) / k
            inner = cell_w - 1.5 * f
            if k > 1 and inner < 4 * f:
                continue
            lines = [_wrap(d, t, self.font, max(20, int(inner))) for t in items]
            if k > 1 and any(len(ln) > 2 for ln in lines):       # 栏内分列后不要让条目折成三行以上
                continue
            sz = self.font.size
            th = [sz * len(ln) + int(sz * 0.25) * max(0, len(ln) - 1) + 2 * pv for ln in lines]
            cols = _count_split(n, k)
            h = max(sum(th[i] for i in col) for col in cols)
            if best is None or h < best["h"] - 1e-6:
                best = {"k": k, "cell_w": cell_w, "gx": gx, "lines": lines, "th": th, "cols": cols,
                        "h": h, "pv": pv}
        e.update(grid=best, h=best["h"] + 0.1 * f, font=self.font)

    def unit_h(self, u: List[Dict[str, Any]]) -> float:
        return sum(e["h"] for e in u) + self.gap * 0.5 * (len(u) - 1)


def _pack(heights: List[float], cap: float, gap: float, cols: int) -> Optional[List[List[int]]]:
    out: List[List[int]] = []
    cur: List[int] = []
    cur_h = 0.0
    for i, h in enumerate(heights):
        if h > cap:
            return None
        need = h if not cur else cur_h + gap + h
        if need <= cap:
            cur.append(i)
            cur_h = need
        else:
            out.append(cur)
            cur, cur_h = [i], h
    if cur:
        out.append(cur)
    return out if len(out) <= cols else None


def _count_split(n: int, cols: int) -> List[List[int]]:
    """按个数分栏：从左到右依次填满；左边每栏最多比右边多一个（靠左的栏多取一个）。
    例：7 个条目分 2 栏 → [0..3] 与 [4..6]；9 个分 3 栏 → 3、3、3。"""
    q, extra = divmod(n, cols)
    out: List[List[int]] = []
    i = 0
    for c in range(cols):
        size = q + (1 if c < extra else 0)
        out.append(list(range(i, i + size)))
        i += size
    return out


def build_text_layout(elements: List[Dict[str, Any]], canvas: Tuple[int, int], top: int, body_h: int,
                      orientation: str, theme_name: str, *, columns: bool = True) -> Optional[Dict[str, Any]]:
    """排好整页。返回 {"paint": 画到透明图上的函数 (img, visible_elements), meta...}；不适用返回 None。"""
    from PIL import Image, ImageDraw
    if not supports(elements):
        return None
    entries = _entries(elements)
    if not entries:
        return None
    result = _search(entries, elements, canvas, top, body_h, orientation, theme_name, columns)
    if result["small"]:                                   # 整个列表一栏放不下时，退回逐条排列（可跨栏）
        alt = _search(_entries(elements, split_lists=True), elements, canvas, top, body_h, orientation,
                      theme_name, columns)
        if alt["effective_px"] > result["effective_px"]:
            result = alt
    return result


def _search(entries, elements, canvas, top, body_h, orientation, theme_name, columns):
    from PIL import Image, ImageDraw
    W, H = canvas
    key = "portrait" if orientation == "portrait" else "landscape"
    u = unit_for(canvas, orientation)
    region_x, region_w = W * 0.06, W * 0.88
    probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    units = _units(entries)
    max_cols = MAX_COLUMNS if columns else 1
    fmax, floor = F_MAX[key] * u, F_FLOOR[key] * u
    chosen = None
    f = fmax
    sizes = []
    while f > F_LAST * u:
        sizes.append(f)
        f -= 2 * u
    sizes.append(F_LAST * u)
    for f in sizes:
        for c in range(1, max_cols + 1):
            gx = W * 0.04
            cw = (region_w - gx * (c - 1)) / c
            if c > 1 and cw < MIN_COL_CHARS * f:
                continue
            m = _Metrics(probe, f, cw)
            for e in entries:
                m.measure(e)
            if c > len(units):
                continue
            hs = [m.unit_h(un) for un in units]
            assign = None
            if max(hs) <= body_h:
                split = _count_split(len(units), c)
                col_h = [sum(hs[i] for i in col) + m.gap * (len(col) - 1) for col in split]
                if max(col_h) <= body_h:
                    assign = split
            if assign is not None:
                chosen = (f, c, cw, gx, m, hs, assign)
                break
        if chosen:
            break
    if chosen is None:                                  # 极端：放不下，用最小字号逐栏尽量排（调用方会标记偏小）
        f = F_LAST * u
        gx = W * 0.04
        cw = (region_w - gx * (max_cols - 1)) / max_cols
        m = _Metrics(probe, f, cw)
        for e in entries:
            m.measure(e)
        hs = [m.unit_h(un) for un in units]
        assign = _pack(hs, max(hs + [body_h]) * 10, m.gap, max_cols) or [list(range(len(units)))]
        chosen = (f, max_cols, cw, gx, m, hs, assign)
    f, c, cw, gx, m, hs, assign = chosen
    # 位置
    col_h = [sum(hs[i] for i in col) + m.gap * (len(col) - 1) for col in assign]
    block_h = max(col_h)
    y0 = top + max(0.0, (body_h - block_h) / 2)
    used_w = len(assign) * cw + gx * (len(assign) - 1)
    x0 = region_x + (region_w - used_w) / 2 if len(assign) < c else region_x
    placed: List[Tuple[Dict[str, Any], float, float, float]] = []   # (条目, x, y, 栏宽)
    for ci, col in enumerate(assign):
        y = y0
        x = x0 + ci * (cw + gx)
        for ui in col:
            for e in units[ui]:
                placed.append((e, x, y, cw))
                y += e["h"] + (m.gap * 0.5 if e["kind"] == "lead" or e is not units[ui][-1] else 0)
            y += m.gap

    theme = get_theme(theme_name)
    text_c, accent, card = theme["text"], theme["accent"], theme["card"]

    def paint(img, visible: int) -> None:
        ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(ov, "RGBA")
        for e, x, y, w in placed:
            if e["el"] >= visible:
                continue
            k = e["kind"]
            if k == "list":
                g, font = e["grid"], e["font"]
                sz = font.size
                step = sz + int(sz * 0.25)
                mf = load_font(max(10, round(f * 0.55)))
                r = 0.42 * f
                yy = y
                for ci, col in enumerate(g["cols"]):          # 先填满左列，再填右列
                    cx = x + ci * (g["cell_w"] + g["gx"])
                    yy = y
                    for i in col:
                        cy = yy + g["pv"] + sz * 0.5
                        d.ellipse([cx, cy - r, cx + 2 * r, cy + r], fill=accent)
                        d.text((cx + r, cy), str(i + 1), font=mf, fill=card[:3] + (255,), anchor="mm")
                        ty = yy + g["pv"] + 0.0
                        for ln in g["lines"][i]:
                            d.text((cx + 2 * r + 0.5 * f, ty + sz / 2), ln, font=font, fill=text_c, anchor="lm")
                            ty += step
                        yy += g["th"][i]
                continue
            font, lines, h = e["font"], e["lines"], e["h"]
            sz = font.size
            step = sz + int(sz * 0.25)
            if k == "card":
                d.rounded_rectangle([x, y, x + w, y + h], radius=0.3 * f, fill=card)
                d.rectangle([x, y + m.pad_v, x + m.bar * 0.4, y + h - m.pad_v], fill=accent)
                tx, ty = x + m.pad_h + m.bar * 0.4, y + e["pv"]
            elif k == "row":
                r = 0.42 * f
                cy = y + e["pv"] + sz * 0.5
                d.ellipse([x, cy - r, x + 2 * r, cy + r], fill=accent)
                mf = load_font(max(10, round(f * 0.55)))
                num = str(e["num"])
                d.text((x + r, cy), num, font=mf, fill=card[:3] + (255,), anchor="mm")
                tx, ty = x + 2 * r + 0.5 * f, y + e["pv"]
            elif k == "quote":
                d.rounded_rectangle([x, y, x + w, y + h], radius=0.3 * f, fill=accent[:3] + (38,))
                d.rectangle([x, y, x + m.bar * 0.55, y + h], fill=accent)
                tx, ty = x + m.bar + 0.7 * f, y + e["pv"]
            else:                                       # lead：无卡片的小标题（强调色）
                tx, ty = x + 0.1 * f, y + 0.18 * f
                for ln in lines:
                    d.text((tx, ty + sz / 2), ln, font=font, fill=accent, anchor="lm")
                    ty += step
                continue
            ty += 0.05 * f                              # 卡片高度多留 0.1f，文字上下对称
            for ln in lines:
                d.text((tx, ty + sz / 2), ln, font=font, fill=text_c, anchor="lm")   # 按行中线对齐，避免偏下
                ty += step
        img.alpha_composite(ov)

    return {"paint": paint, "placed": placed, "region": (region_x, region_x + region_w, top, top + body_h),
            "mode": "grid", "split": c, "columns": len(assign), "effective_px": round(f, 1),
            "floor_px": round(floor, 1), "small": f < floor}
