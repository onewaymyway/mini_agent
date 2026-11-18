"""render/renderers.py — 各类场景的程序绘制（PIL，RGBA 透明底，逐状态出图）。

输入的场景字典结构见 visual/director.py。文字颜色、强调色来自主题；
强调词（emphasis）在任何卡片中都以 accent 色显示，与口播字幕时间对齐由 P5 完成。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from ..visual.states import visible_counts
from .fonts import load_font
from .text import draw_rich_line, wrap_text
from .themes import get_theme, unit_for

Canvas = Tuple[int, int]
SHOT_ZOOM = 1.35  # 插图底图的放大倍数，为水平取景留出余量


class _Ctx:
    def __init__(self, canvas: Canvas, orientation: str, theme_name: str):
        self.W, self.H = canvas
        self.landscape = orientation != "portrait"
        self.u = unit_for(canvas, orientation)
        self.theme = get_theme(theme_name)
        self.mx = self.W * 0.08
        self.my = self.H * 0.10
        self.content_w = self.W - 2 * self.mx

    def font(self, base: float):
        return load_font(round(base * self.u))

    def c(self, key: str):
        return self.theme[key]


def _items(scene: Dict[str, Any]) -> List[str]:
    return [str(x) for x in (scene.get("items") or []) if str(x).strip()]


def _center_x(ctx: _Ctx, w: float) -> float:
    return (ctx.W - w) / 2


# ---------- 各类型 ----------

def _draw_keypoint(draw, ctx: _Ctx, scene, vis: int) -> None:
    items = _items(scene)[:vis]
    if not items:
        return
    font = ctx.font(66 if ctx.landscape else 54)
    pad = 36 * ctx.u
    gap = 30 * ctx.u
    maxw = (ctx.content_w * 0.82 if ctx.landscape else ctx.content_w) - 2 * pad
    blocks = []
    for t in items:
        lines = wrap_text(draw, t, font, int(maxw), max_lines=2)
        h = font.size * len(lines) + int(font.size * 0.25) * max(0, len(lines) - 1)
        blocks.append((lines, h))
    total = sum(h + 2 * pad for _, h in blocks) + gap * (len(blocks) - 1)
    y = (ctx.H - total) / 2
    for lines, h in blocks:
        box_w = maxw + 2 * pad
        x0 = _center_x(ctx, box_w)
        draw.rounded_rectangle([x0, y, x0 + box_w, y + h + 2 * pad], radius=18 * ctx.u, fill=ctx.c("card"))
        draw.rectangle([x0, y + pad, x0 + 8 * ctx.u, y + h + pad], fill=ctx.c("accent"))
        ty = y + pad
        for line in lines:
            draw_rich_line(draw, (x0 + pad + 10 * ctx.u, ty), line, font, ctx.c("text"),
                           ctx.c("accent"), scene.get("emphasis") or [])
            ty += font.size + int(font.size * 0.25)
        y += h + 2 * pad + gap


def _draw_list(draw, ctx: _Ctx, scene, vis: int) -> None:
    items = _items(scene)[:vis]
    if not items:
        return
    font = ctx.font(52 if ctx.landscape else 44)
    marker = ctx.font(40 if ctx.landscape else 34)
    r = 30 * ctx.u
    row_gap = 34 * ctx.u
    text_w = ctx.content_w * (0.7 if ctx.landscape else 0.9) - 2 * r
    rows = []
    for t in items:
        lines = wrap_text(draw, t, font, int(text_w), max_lines=2)
        rows.append((lines, font.size * len(lines) + int(font.size * 0.2) * max(0, len(lines) - 1)))
    total = sum(h for _, h in rows) + row_gap * (len(rows) - 1)
    y = (ctx.H - total) / 2
    x = _center_x(ctx, ctx.content_w * (0.7 if ctx.landscape else 0.9))
    for i, (lines, h) in enumerate(rows, start=1):
        cy = y + min(h, font.size) / 2
        draw.ellipse([x, cy - r, x + 2 * r, cy + r], fill=ctx.c("accent"))
        num = str(i)
        nw = draw.textlength(num, font=marker)
        draw.text((x + r - nw / 2, cy - marker.size * 0.6), num, font=marker, fill=ctx.c("card")[:3] + (255,))
        ty = y
        for line in lines:
            draw_rich_line(draw, (x + 2 * r + 36 * ctx.u, ty), line, font, ctx.c("text"),
                           ctx.c("accent"), scene.get("emphasis") or [])
            ty += font.size + int(font.size * 0.2)
        y += h + row_gap


def _draw_timeline(draw, ctx: _Ctx, scene, vis: int) -> None:
    items = _items(scene)[:vis]
    if not items:
        return
    n = len(_items(scene))
    font = ctx.font(38 if ctx.landscape else 34)
    line_c = ctx.c("line")
    if ctx.landscape:
        y_axis = ctx.H * 0.52
        x0, x1 = ctx.mx + 60 * ctx.u, ctx.W - ctx.mx - 60 * ctx.u
        draw.line([(x0, y_axis), (x1, y_axis)], fill=line_c, width=max(3, round(5 * ctx.u)))
        step = (x1 - x0) / max(1, n - 1) if n > 1 else 0
        label_w = max(180 * ctx.u, step * 1.8) if n > 1 else 600 * ctx.u
        for i, t in enumerate(items):
            cx = x0 + i * step
            r = 16 * ctx.u
            draw.ellipse([cx - r, y_axis - r, cx + r, y_axis + r], fill=ctx.c("accent"))
            lines = wrap_text(draw, t, font, int(label_w), max_lines=3)
            lh = font.size + int(font.size * 0.2)
            block_h = lh * len(lines)
            ty = y_axis - 60 * ctx.u - block_h if i % 2 == 0 else y_axis + 60 * ctx.u
            for k, line in enumerate(lines):
                lw = draw.textlength(line, font=font)
                lx = min(max(cx - lw / 2, ctx.mx * 0.5), ctx.W - ctx.mx * 0.5 - lw)  # 不越出画面
                draw_rich_line(draw, (lx, ty + k * lh), line, font, ctx.c("text"),
                               ctx.c("accent"), scene.get("emphasis") or [])
    else:
        x_axis = ctx.W * 0.22
        y0, y1 = ctx.my, ctx.H - ctx.my
        draw.line([(x_axis, y0), (x_axis, y1)], fill=line_c, width=max(3, round(5 * ctx.u)))
        step = (y1 - y0) / max(1, n - 1) if n > 1 else 0
        for i, t in enumerate(items):
            cy = y0 + i * step
            r = 14 * ctx.u
            draw.ellipse([x_axis - r, cy - r, x_axis + r, cy + r], fill=ctx.c("accent"))
            lines = wrap_text(draw, t, font, int(ctx.W * 0.6), max_lines=3)
            lh = font.size + int(font.size * 0.2)
            ty = cy - lh * len(lines) / 2
            for k, line in enumerate(lines):
                draw_rich_line(draw, (x_axis + 50 * ctx.u, ty + k * lh), line, font, ctx.c("text"),
                               ctx.c("accent"), scene.get("emphasis") or [])


def _arrow(draw, p0, p1, color, width: int, head: float) -> None:
    draw.line([p0, p1], fill=color, width=width)
    ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
    a1 = (p1[0] - head * math.cos(ang - 0.45), p1[1] - head * math.sin(ang - 0.45))
    a2 = (p1[0] - head * math.cos(ang + 0.45), p1[1] - head * math.sin(ang + 0.45))
    draw.polygon([p1, a1, a2], fill=color)


def _draw_diagram(draw, ctx: _Ctx, scene, vis: int) -> None:
    nodes = _items(scene)
    if not nodes:
        return
    n = len(nodes)
    cols = 2 if not ctx.landscape else min(n, 3)
    cols = max(1, cols)
    rows = math.ceil(n / cols)
    font = ctx.font(42 if ctx.landscape else 36)
    gx = 90 * ctx.u
    gy = 70 * ctx.u
    node_w = (ctx.content_w * (0.78 if ctx.landscape else 1.0) - (cols - 1) * gx) / cols
    node_h = 150 * ctx.u
    total_h = rows * node_h + (rows - 1) * gy
    ox = (ctx.W - (cols * node_w + (cols - 1) * gx)) / 2
    oy = (ctx.H - total_h - (110 * ctx.u if scene.get("loop") else 0)) / 2
    boxes = []
    for i in range(n):
        r_, c_ = divmod(i, cols)
        x = ox + c_ * (node_w + gx)
        y = oy + r_ * (node_h + gy)
        boxes.append((x, y, x + node_w, y + node_h))
    color = ctx.c("accent")
    lw = max(3, round(4 * ctx.u))
    for i in range(min(vis, n)):
        x0, y0, x1, y1 = boxes[i]
        draw.rounded_rectangle([x0, y0, x1, y1], radius=20 * ctx.u, fill=ctx.c("card"),
                               outline=ctx.c("line"), width=max(2, round(3 * ctx.u)))
        lines = wrap_text(draw, nodes[i], font, int(node_w - 40 * ctx.u), max_lines=3)
        lh = font.size + int(font.size * 0.15)
        ty = (y0 + y1) / 2 - lh * len(lines) / 2
        for k, line in enumerate(lines):
            lwid = draw.textlength(line, font=font)
            draw_rich_line(draw, (x0 + (node_w - lwid) / 2, ty + k * lh), line, font, ctx.c("text"),
                           ctx.c("accent"), scene.get("emphasis") or [])
    for i in range(min(vis, n) - 1):
        a, b = boxes[i], boxes[i + 1]
        if (i + 1) // cols == i // cols:  # 同一行：水平箭头
            p0 = (a[2] + 12 * ctx.u, (a[1] + a[3]) / 2)
            p1 = (b[0] - 12 * ctx.u, (b[1] + b[3]) / 2)
        else:  # 换行：从上一行末尾斜向到下一行开头
            p0 = ((a[0] + a[2]) / 2, a[3] + 12 * ctx.u)
            p1 = ((b[0] + b[2]) / 2, b[1] - 12 * ctx.u)
        _arrow(draw, p0, p1, color, lw, 20 * ctx.u)
    if scene.get("loop") and min(vis, n) == n and n >= 2:
        # 回路：末尾向下绕到图的下方，再向左绕出左侧，最后从左侧水平射入第一个节点
        first, last = boxes[0], boxes[-1]
        y_low = max(b[3] for b in boxes) + 60 * ctx.u
        x_out = ox - 50 * ctx.u
        x_last = (last[0] + last[2]) / 2
        y_first = (first[1] + first[3]) / 2
        pts = [(x_last, last[3] + 12 * ctx.u), (x_last, y_low), (x_out, y_low), (x_out, y_first),
               (first[0] - 12 * ctx.u, y_first)]
        draw.line(pts, fill=color, width=lw)
        _arrow(draw, pts[-2], pts[-1], color, lw, 20 * ctx.u)


def _draw_table(draw, ctx: _Ctx, scene, vis: int) -> None:
    header = [str(h) for h in (scene.get("header") or [])]
    rows = [[str(c) for c in r] for r in (scene.get("rows") or [])]
    if not header:
        return
    ncol = len(header)
    font = ctx.font(40 if ctx.landscape else 34)
    pad = 18 * ctx.u
    table_w = ctx.content_w * (0.86 if ctx.landscape else 1.0)
    colw = table_w / ncol
    lh = font.size + int(font.size * 0.2)
    x0 = _center_x(ctx, table_w)

    def row_h(cells: Sequence[str]) -> float:
        mx = 1
        for cell in cells:
            mx = max(mx, len(wrap_text(draw, cell, font, int(colw - 2 * pad), max_lines=2)))
        return mx * lh + 2 * pad

    heights = [row_h(header)] + [row_h(r) for r in rows[:vis]]
    total = sum(heights)
    y = max(ctx.my, (ctx.H - total) / 2)

    def draw_row(cells: Sequence[str], top: float, h: float, head: bool) -> None:
        fill = ctx.c("card") if not head else (217, 164, 65, 60)
        draw.rectangle([x0, top, x0 + table_w, top + h], fill=fill)
        for j, cell in enumerate(cells):
            cx = x0 + j * colw
            draw.line([(cx, top), (cx, top + h)], fill=ctx.c("line"), width=max(1, round(2 * ctx.u)))
            ty = top + pad
            for line in wrap_text(draw, cell, font, int(colw - 2 * pad), max_lines=2):
                draw_rich_line(draw, (cx + pad, ty), line, font, ctx.c("text"), ctx.c("accent"),
                               scene.get("emphasis") or [])
                ty += lh
        draw.line([(x0, top + h), (x0 + table_w, top + h)], fill=ctx.c("line"), width=max(1, round(2 * ctx.u)))

    draw_row(header, y, heights[0], True)
    y += heights[0]
    for i, r in enumerate(rows[:vis]):
        draw_row(r + [""] * (ncol - len(r)), y, heights[i + 1], False)
        y += heights[i + 1]


def _draw_quote(draw, ctx: _Ctx, scene, vis: int) -> None:
    items = _items(scene)[:vis]
    if not items:
        return
    font = ctx.font(64 if ctx.landscape else 50)
    mark = ctx.font(220 if ctx.landscape else 160)
    maxw = ctx.content_w * (0.72 if ctx.landscape else 0.86)
    lines = wrap_text(draw, items[0], font, int(maxw), max_lines=5)
    lh = font.size + int(font.size * 0.3)
    block_h = lh * len(lines)
    top = (ctx.H - block_h) / 2
    x0 = _center_x(ctx, maxw)
    draw.text((x0 - 20 * ctx.u, top - mark.size * 0.75), "\u201c", font=mark, fill=ctx.c("accent"))
    draw.rectangle([x0 - 36 * ctx.u, top, x0 - 26 * ctx.u, top + block_h], fill=ctx.c("accent"))
    for k, line in enumerate(lines):
        draw_rich_line(draw, (x0, top + k * lh), line, font, ctx.c("text"), ctx.c("accent"),
                       scene.get("emphasis") or [])


def _draw_formula(draw, ctx: _Ctx, scene, vis: int) -> None:
    formula = str(scene.get("formula") or "").strip()
    font = ctx.font(60 if ctx.landscape else 48)
    maxw = ctx.content_w * (0.76 if ctx.landscape else 0.9)
    lines = wrap_text(draw, formula or "（公式）", font, int(maxw - 60 * ctx.u), max_lines=3)
    lh = font.size + int(font.size * 0.2)
    box_h = lh * len(lines) + 60 * ctx.u
    top = ctx.H * 0.22
    x0 = _center_x(ctx, maxw)
    draw.rounded_rectangle([x0, top, x0 + maxw, top + box_h], radius=16 * ctx.u, fill=ctx.c("card"),
                           outline=ctx.c("accent"), width=max(2, round(3 * ctx.u)))
    for k, line in enumerate(lines):
        lw = draw.textlength(line, font=font)
        draw.text((x0 + (maxw - lw) / 2, top + 30 * ctx.u + k * lh), line, font=font, fill=ctx.c("text"))
    if vis > 0:
        exp = ctx.font(42 if ctx.landscape else 36)
        y = top + box_h + 60 * ctx.u
        for t in _items(scene)[:vis]:
            for line in wrap_text(draw, t, exp, int(maxw), max_lines=2):
                draw_rich_line(draw, (x0, y), line, exp, ctx.c("text"), ctx.c("accent"),
                               scene.get("emphasis") or [])
                y += exp.size + int(exp.size * 0.25)
            y += 20 * ctx.u


_DRAWERS = {
    "keypoint": _draw_keypoint,
    "illustration": _draw_keypoint,  # 插图降级（无图或生图失败）时按卡片绘制
    "list": _draw_list,
    "timeline": _draw_timeline,
    "diagram": _draw_diagram,
    "table": _draw_table,
    "quote": _draw_quote,
    "formula": _draw_formula,
}


def render_scene_frames(scene: Dict[str, Any], canvas: Canvas, orientation: str, theme_name: str,
                        *, as_type: str = "") -> list:
    """返回该场景的所有状态图（RGBA，整幅透明底）。as_type 可强制按某类型绘制（插图降级用）。"""
    from PIL import Image, ImageDraw

    ctx = _Ctx(canvas, orientation, theme_name)
    kind = as_type or scene.get("type", "keypoint")
    drawer = _DRAWERS.get(kind)
    if drawer is None:
        raise ValueError(f"未知的场景类型：{kind}")
    frames = []
    for vis in visible_counts(scene):
        img = Image.new("RGBA", canvas, (0, 0, 0, 0))
        drawer(ImageDraw.Draw(img, "RGBA"), ctx, scene, vis)
        frames.append(img)
    return frames


def render_background(canvas: Canvas, theme_name: str):
    from PIL import Image, ImageDraw

    ctx = _Ctx(canvas, "landscape", theme_name)
    top, bot = ctx.c("bg_top"), ctx.c("bg_bottom")
    img = Image.new("RGB", canvas, top)
    draw = ImageDraw.Draw(img)
    for y in range(canvas[1]):
        t = y / max(1, canvas[1] - 1)
        draw.line([(0, y), (canvas[0], y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bot)))
    return img


def render_chapter_card(chapter: Dict[str, Any], canvas: Canvas, orientation: str, theme_name: str):
    from PIL import Image, ImageDraw

    ctx = _Ctx(canvas, orientation, theme_name)
    img = Image.new("RGBA", canvas, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")
    label_font = ctx.font(48 if ctx.landscape else 40)
    title_font = ctx.font(104 if ctx.landscape else 84)
    sub_font = ctx.font(42 if ctx.landscape else 36)
    maxw = ctx.content_w * (0.8 if ctx.landscape else 0.9)
    label = str(chapter.get("number") or "")
    title_lines = wrap_text(draw, str(chapter.get("title") or ""), title_font, int(maxw), max_lines=3)
    sub_lines = wrap_text(draw, str(chapter.get("one_line") or ""), sub_font, int(maxw), max_lines=2)
    total = label_font.size + 40 * ctx.u + (title_font.size + 20 * ctx.u) * len(title_lines) \
        + 50 * ctx.u + (sub_font.size + 10 * ctx.u) * len(sub_lines)
    y = (ctx.H - total) / 2
    if label:
        lw = draw.textlength(label, font=label_font)
        draw.text(((ctx.W - lw) / 2, y), label, font=label_font, fill=ctx.c("accent"))
    y += label_font.size + 40 * ctx.u
    for line in title_lines:
        lw = draw.textlength(line, font=title_font)
        draw.text(((ctx.W - lw) / 2, y), line, font=title_font, fill=ctx.c("text"))
        y += title_font.size + 20 * ctx.u
    y += 80 * ctx.u
    draw.line([(ctx.W / 2 - 120 * ctx.u, y - 40 * ctx.u), (ctx.W / 2 + 120 * ctx.u, y - 40 * ctx.u)],
              fill=ctx.c("accent"), width=max(3, round(4 * ctx.u)))
    for line in sub_lines:
        lw = draw.textlength(line, font=sub_font)
        draw.text(((ctx.W - lw) / 2, y), line, font=sub_font, fill=ctx.c("muted"))
        y += sub_font.size + 10 * ctx.u
    return img


def render_illustration_background(src: Path, canvas: Canvas, focus_x: float, theme_name: str,
                                   darken: float = 0.45):
    """插图底图：等比铺满画幅后按 focus_x（0..1）水平取景，并压暗以保证叠字可读。"""
    from PIL import Image

    with Image.open(src) as im:
        im = im.convert("RGB")
        # 放大 SHOT_ZOOM 倍后再取景：正方形图按画幅铺满时宽度刚好相等，没有余量水平移动，
        # 一图多用的不同取景会完全相同。放大后横向留出余量，focus_x 才有效果。
        scale = max(canvas[0] * SHOT_ZOOM / im.width, canvas[1] / im.height)
        big = im.resize((math.ceil(im.width * scale), math.ceil(im.height * scale)), Image.LANCZOS)
    fx = min(1.0, max(0.0, float(focus_x)))
    left = round((big.width - canvas[0]) * fx)
    top = round((big.height - canvas[1]) / 2)
    out = big.crop((left, top, left + canvas[0], top + canvas[1]))
    if darken > 0:
        shade = Image.new("RGB", canvas, (0, 0, 0))
        out = Image.blend(out, shade, darken)
    return out
