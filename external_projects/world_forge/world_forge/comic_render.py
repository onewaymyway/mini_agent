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

from .comic_layout import GUTTER_PX, PAGE_H, PAGE_W

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


def _place(zone: Tuple[str, str], w: int, h: int, box: Tuple[int, int, int, int], cursor: Dict[str, int]) -> Tuple[int, int]:
    """按区域与已占用高度计算左上角坐标。上区域自上而下堆叠，下区域自下而上堆叠。"""
    x0, y0, x1, y1 = box
    vert, horiz = zone
    if horiz == "left":
        x = x0 + EDGE
    elif horiz == "right":
        x = x1 - EDGE - w
    else:
        x = x0 + (x1 - x0 - w) // 2
    key = f"{vert}:{horiz}"
    used = cursor.get(key, 0)
    if vert == "top":
        y = y0 + EDGE + used
        cursor[key] = used + h + 10
    else:
        y = y1 - EDGE - used - h
        cursor[key] = used + h + 10
    return x, y


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


def render_page(plan: Dict[str, Any], base_dir: Path, fonts: Fonts, color: str = "color") -> Image.Image:
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
        cursor: Dict[str, int] = {}
        max_w = int((x1 - x0) * BUBBLE_MAX_RATIO)
        for item in pan["items"]:
            if item["kind"] == "sfx":
                blk = _block(item, fonts, max_w)
                x, y = _place(item["zone"], blk["w"], blk["h"], box, cursor)
                _draw_sfx(draw, item, blk, x, y)
                continue
            blk = _block(item, fonts, max_w)
            x, y = _place(item["zone"], blk["w"], blk["h"], box, cursor)
            _draw_bubble(draw, item, blk, x, y, box)
    draw.rectangle((2, 2, PAGE_W - 3, PAGE_H - 3), outline="black", width=6)
    return page


def write_pages(plans: List[Dict[str, Any]], base_dir: Path, out_dir: Path, *, color: str = "color",
                fonts: Optional[Fonts] = None) -> List[Path]:
    """把所有页写为 page_01.png … 并打包为 comic_pages.zip（与 out_dir 同级）。返回 PNG 路径列表。"""
    fonts = fonts or Fonts()
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for plan in plans:
        img = render_page(plan, base_dir, fonts, color)
        p = out_dir / f"page_{int(plan.get('no') or len(paths) + 1):02d}.png"
        img.save(p, format="PNG", optimize=True)
        paths.append(p)
    zip_path = out_dir.parent / "comic_pages.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            zf.write(p, arcname=p.name)
    return paths
