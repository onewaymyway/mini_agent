"""world_forge/comic_render.py — 服务端排字：把 comic_layout 的规划画成 PNG 漫画页。

  * 格子按规划矩形裁切（cover），黑白模式下转灰度并加强对比；
  * 对白气泡：圆角框 + 尾巴指向说话人；心声为云朵式椭圆；呼喊为粗边框；
  * 旁白为黄色说明框；音效为粗体描边字，直接叠在画面上；
  * 画面未生成时显示占位框与失败原因。

中文字体：优先环境变量 WORLD_FORGE_FONT，其次常见的 Noto/文泉驿/微软雅黑/苹方路径。
找不到中文字体时仍会输出，但中文会显示为方框，调用方应在日志中提示。
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .comic_layout import CLOSE_SHOTS, GUTTER_PX, PAGE_H, PAGE_W

FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
)

SIZE_TEXT = 30
SIZE_NAME = 21
SIZE_CAPTION = 26
SIZE_SFX = 84
SIZE_PLACEHOLDER = 22
PAD = 16             # 气泡内边距
EDGE = 12            # 气泡与格边的距离
BUBBLE_MAX_RATIO = 0.52   # 气泡最大宽度占格宽的比例


def find_font() -> Optional[str]:
    env = os.environ.get("WORLD_FORGE_FONT", "").strip()
    cands = [env] if env else []
    cands += list(FONT_CANDIDATES)
    for c in cands:
        if c and Path(c).is_file():
            return c
    return None


class Fonts:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path if path is not None else find_font()
        self._cache: Dict[int, ImageFont.FreeTypeFont] = {}

    def get(self, size: int):
        if size in self._cache:
            return self._cache[size]
        if self.path:
            font = ImageFont.truetype(self.path, size)
        else:
            try:
                font = ImageFont.load_default(size=size)
            except TypeError:   # 旧版 Pillow 不支持 size 参数
                font = ImageFont.load_default()
        self._cache[size] = font
        return font


def _wrap(text: str, font, max_w: int) -> List[str]:
    """按字符折行（中文无空格，逐字累加测宽）。"""
    lines: List[str] = []
    cur = ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        trial = cur + ch
        if not cur or font.getlength(trial) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = ch
    if cur or not lines:
        lines.append(cur)
    return lines


def _line_h(size: int) -> int:
    return int(size * 1.35)


def _block(item: Dict[str, Any], fonts: Fonts, max_w: int) -> Dict[str, Any]:
    """测量一个排字条目的尺寸，返回 {w, h, lines, name, font, name_font}。"""
    kind = item["kind"]
    if kind == "sfx":
        font = fonts.get(SIZE_SFX)
        w = int(font.getlength(item["text"])) + 12
        h = SIZE_SFX + 12
        return {"w": w, "h": h, "lines": [item["text"]], "font": font, "name": "", "name_font": None}
    size = SIZE_CAPTION if kind == "caption" else SIZE_TEXT
    font = fonts.get(size)
    lines = _wrap(item["text"], font, max(40, max_w - 2 * PAD))
    name = item.get("speaker") or ""
    name_font = fonts.get(SIZE_NAME) if name else None
    text_w = max(int(font.getlength(s)) for s in lines)
    name_w = int(name_font.getlength(name)) if name else 0
    w = max(text_w, name_w) + 2 * PAD
    h = len(lines) * _line_h(size) + 2 * PAD - (PAD // 2)
    if name:
        h += _line_h(SIZE_NAME)
    return {"w": w, "h": h, "lines": lines, "font": font, "name": name, "name_font": name_font, "size": size}


ALL_ZONES = [(v, h) for v in ("top", "bottom") for h in ("left", "right", "center")]
GAP = 8                   # 元素之间的最小间距
SCAN_STEP = 6             # 垂直扫描步长（像素）
WIDTH_SCALES = (1.0, 0.8, 0.65)   # 放不下时依次缩小的宽度比例


def _tail_reach(box: Tuple[int, int, int, int]) -> int:
    return min(72, int((box[3] - box[1]) * 0.22))


def _rect_of(item: Dict[str, Any], blk: Dict[str, Any], zone: Tuple[str, str], box, *, x: int, y: int) -> Tuple[int, int, int, int]:
    """元素占用的矩形（含尾巴/心声小圆的延伸），用于碰撞检测。"""
    x1, y1 = x + blk["w"], y + blk["h"]
    reach = _tail_reach(box) if item["kind"] in ("speech", "shout", "thought") else 0
    if zone[0] == "top":
        y1 += reach
    else:
        y -= reach
    return (x, y, x1, y1)


def _overlaps(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] + GAP and b[0] < a[2] + GAP and a[1] < b[3] + GAP and b[1] < a[3] + GAP


def _zone_order(preferred: Tuple[str, str], close: bool) -> List[Tuple[str, str]]:
    """候选区域：先原区域，再同一垂直位置的其它区域，最后其余区域。近景/特写不使用正中（人脸所在）。"""
    rest = [z for z in ALL_ZONES if z != preferred]
    same_row = [z for z in rest if z[0] == preferred[0]]
    others = [z for z in rest if z[0] != preferred[0]]
    order = [preferred] + same_row + others
    if close:
        order = [z for z in order if z[1] != "center"] or order
    return order


def _find_spot(item, blk, box, placed, close: bool):
    x0, y0, x1, y1 = box
    for zone in _zone_order(item["zone"], close):
        vert, horiz = zone
        if horiz == "left":
            x = x0 + EDGE
        elif horiz == "right":
            x = x1 - EDGE - blk["w"]
        else:
            x = x0 + (x1 - x0 - blk["w"]) // 2
        if blk["w"] > (x1 - x0) - 2 * EDGE:
            continue
        if vert == "top":
            ys = range(y0 + EDGE, y1 - EDGE - blk["h"] + 1, SCAN_STEP)
        else:
            ys = range(y1 - EDGE - blk["h"], y0 + EDGE - 1, -SCAN_STEP)
        for y in ys:
            rect = _rect_of(item, blk, zone, box, x=x, y=y)
            if rect[0] < x0 or rect[1] < y0 or rect[2] > x1 or rect[3] > y1:
                continue
            if any(_overlaps(rect, other) for other in placed):
                continue
            return zone, x, y, rect
    return None


def _layout_panel(items: List[Dict[str, Any]], fonts: Fonts, box: Tuple[int, int, int, int],
                  close: bool, warnings: Optional[List[str]] = None) -> List[Tuple[Dict[str, Any], Dict[str, Any], Tuple[str, str], int, int]]:
    """避让式排版：按优先级（旁白、对白、音效）逐个放置，检查与已放置元素及格子边界的碰撞。"""
    x0, y0, x1, y1 = box
    pw = x1 - x0
    rank = {"caption": 0, "speech": 1, "thought": 1, "shout": 1, "sfx": 2}
    placed: List[Tuple[int, int, int, int]] = []
    out = []
    for item in sorted(items, key=lambda it: rank.get(it["kind"], 1)):
        spot = None
        blk = None
        for scale in WIDTH_SCALES:
            blk = _block(item, fonts, max(40, int(pw * BUBBLE_MAX_RATIO * scale)))
            spot = _find_spot(item, blk, box, placed, close)
            if spot is not None:
                break
        if spot is None:
            # 放不下：退回原区域贴边放置，并记录警告（提示用户缩短对白）
            zone = item["zone"]
            x = x0 + EDGE if zone[1] == "left" else (x1 - EDGE - blk["w"] if zone[1] == "right"
                                                    else x0 + (pw - blk["w"]) // 2)
            y = y0 + EDGE if zone[0] == "top" else y1 - EDGE - blk["h"]
            rect = _rect_of(item, blk, zone, box, x=x, y=y)
            if warnings is not None:
                warnings.append(f"「{item.get('text', '')[:12]}」无法在格内完全避让，已重叠（可缩短对白后重绘）")
            spot = (zone, x, y, rect)
        zone, x, y, rect = spot
        placed.append(rect)
        out.append((item, blk, zone, x, y))
    return out


def _draw_bubble(draw: ImageDraw.ImageDraw, item: Dict[str, Any], blk: Dict[str, Any],
                 x: int, y: int, box: Tuple[int, int, int, int]) -> None:
    kind = item["kind"]
    w, h = blk["w"], blk["h"]
    bx0, by0, bx1, by1 = x, y, x + w, y + h
    zone_vert = item["zone"][0]
    fill = "white"
    if kind == "caption":
        draw.rectangle((bx0, by0, bx1, by1), fill="#fff3a3", outline="black", width=2)
    else:
        tail_x_rel, _ = item.get("tail", (0.5, 0.5))
        x0, y0, x1, y1 = box
        ph = y1 - y0
        reach = min(72, int(ph * 0.22))          # 尾巴只向说话人方向短促延伸，不穿过整格
        tip_x = x0 + (x1 - x0) * tail_x_rel
        tip_y = (by1 + reach) if zone_vert == "top" else (by0 - reach)
        tip = (tip_x, tip_y)
        if kind in ("speech", "shout"):
            base_x = min(max(tip_x, bx0 + 24), bx1 - 24)
            base_y = by1 if zone_vert == "top" else by0
            poly = [(base_x - 14, base_y), tip, (base_x + 14, base_y)]
            draw.polygon(poly, fill=fill)
            draw.line([poly[0], poly[1]], fill="black", width=3)
            draw.line([poly[1], poly[2]], fill="black", width=3)
            draw.rounded_rectangle((bx0, by0, bx1, by1), radius=22, fill=fill,
                                   outline="black", width=5 if kind == "shout" else 3)
        else:   # thought：云朵式椭圆，尾端两个小圆指向说话人
            draw.ellipse((bx0, by0, bx1, by1), fill=fill, outline="black", width=3)
            for r, dist in ((9, 14), (5, 32)):
                cx = min(max(tip_x, bx0 + 20), bx1 - 20)
                cy = (by1 + dist) if zone_vert == "top" else (by0 - dist)
                draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill, outline="black", width=2)
    # 文字
    ty = by0 + PAD // 2
    if blk.get("name"):
        draw.text((bx0 + PAD, ty), blk["name"], font=blk["name_font"], fill="#555555")
        ty += _line_h(SIZE_NAME)
    for line in blk["lines"]:
        draw.text((bx0 + PAD, ty), line, font=blk["font"], fill="black")
        ty += _line_h(blk["size"])


def _draw_sfx(draw: ImageDraw.ImageDraw, item: Dict[str, Any], blk: Dict[str, Any],
              x: int, y: int) -> None:
    draw.text((x, y), item["text"], font=blk["font"], fill="black", stroke_width=4, stroke_fill="white")


def _draw_panel_image(canvas: Image.Image, box: Tuple[int, int, int, int], src: Optional[Image.Image],
                      error: Optional[str], fonts: Fonts, color: str) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if src is not None:
        img = ImageOps.fit(src, (w, h), method=Image.LANCZOS)
        if color == "bw":
            img = ImageOps.autocontrast(ImageOps.grayscale(img)).convert("RGB")
        canvas.paste(img, (x0, y0))
        return
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill="#e4e4e4")
    msg = f"（画面未生成：{error}）" if error else "（画面未生成）"
    font = fonts.get(SIZE_PLACEHOLDER)
    lines = _wrap(msg, font, w - 2 * PAD)
    ty = y0 + h // 2 - len(lines) * _line_h(SIZE_PLACEHOLDER) // 2
    for line in lines:
        tw = int(font.getlength(line))
        draw.text((x0 + (w - tw) // 2, ty), line, font=font, fill="#666666")
        ty += _line_h(SIZE_PLACEHOLDER)


def render_page(plan: Dict[str, Any], base_dir: Path, fonts: Fonts, color: str = "color",
                warnings: Optional[List[str]] = None) -> Image.Image:
    """把一页规划画成 PIL 图像。"""
    page = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    draw = ImageDraw.Draw(page)
    half_g = GUTTER_PX // 2
    for pan in plan["panels"]:
        rx, ry, rw, rh = pan["rect"]
        x0 = round(rx * PAGE_W) + half_g
        y0 = round(ry * PAGE_H) + half_g
        x1 = round((rx + rw) * PAGE_W) - half_g
        y1 = round((ry + rh) * PAGE_H) - half_g
        box = (x0, y0, x1, y1)
        src = None
        if pan.get("image"):
            p = base_dir / pan["image"]
            if p.is_file():
                try:
                    with Image.open(p) as im:
                        im.load()
                        src = im.convert("RGB")
                except OSError:
                    src = None
        _draw_panel_image(page, box, src, pan.get("image_error"), fonts, color)
        draw = ImageDraw.Draw(page)
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline="black", width=4)
        close = (pan.get("shot") or "中景") in CLOSE_SHOTS
        for item, blk, zone, x, y in _layout_panel(pan["items"], fonts, box, close, warnings):
            if item["kind"] == "sfx":
                _draw_sfx(draw, item, blk, x, y)
            else:
                _draw_bubble(draw, {**item, "zone": zone}, blk, x, y, box)
    draw.rectangle((2, 2, PAGE_W - 3, PAGE_H - 3), outline="black", width=6)
    return page


def write_pages(plans: List[Dict[str, Any]], base_dir: Path, out_dir: Path, *, color: str = "color",
                fonts: Optional[Fonts] = None, warnings: Optional[List[str]] = None) -> List[Path]:
    """把所有页写为 page_01.png … 并打包为 comic_pages.zip（与 out_dir 同级）。返回 PNG 路径列表。"""
    fonts = fonts or Fonts()
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for plan in plans:
        page_warn: List[str] = []
        img = render_page(plan, base_dir, fonts, color, page_warn)
        for w in page_warn:
            if warnings is not None:
                warnings.append(f"第 {plan.get('no')} 页：{w}")
        p = out_dir / f"page_{int(plan.get('no') or len(paths) + 1):02d}.png"
        img.save(p, format="PNG", optimize=True)
        paths.append(p)
    zip_path = out_dir.parent / "comic_pages.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            zf.write(p, arcname=p.name)
    return paths
