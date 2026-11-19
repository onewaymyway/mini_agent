"""compose/cover.py — 成片封面（R16）：标题、作者、一句话副标题、AI 声明与水印。

画面来源（按优先级）：
  1. 本项目里第一张真实生成的插图（取自分镜渲染清单 background_src），铺满画面并压暗，保证文字可读；
  2. 没有插图时，使用程序绘制的浅色渐变底（不是纯黑底）。
  不额外调用生图接口：封面复用已生成的插图，不增加费用。
文字：标题（最多 3 行）、强调线、作者、副标题（全片论点，缺省取第一章的一句话）、AI 声明（如启用）。
水印：与成片相同的水印设置（文字或图片、位置或斜向平铺），保证封面与成片署名一致。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..project_store import read_json
from ..render.fonts import load_font
from ..render.text import wrap_text
from ..render.themes import unit_for
from .branding import render_watermark, watermark_xy

COVER_TOP = (44, 70, 110)        # 程序绘制底：比正文主题更亮，避免成黑色
COVER_BOTTOM = (20, 34, 58)
TEXT = (248, 244, 234, 255)
MUTED = (200, 210, 226, 255)
ACCENT = (226, 172, 78, 255)


def pick_cover_image(pdir: Path, part_ids: List[str]) -> Optional[Path]:
    """按章节顺序找第一张可用的插图（已生成、文件存在）。找不到返回 None。"""
    pdir = Path(pdir)
    for pid in part_ids:
        mp = pdir / "parts" / pid / "visuals" / "manifest.json"
        if not mp.is_file():
            continue
        for sc in read_json(mp).get("scenes", []):
            src = sc.get("background_src")
            if sc.get("type") != "illustration" or sc.get("fallback_reason") or not src:
                continue
            p = Path(src)
            if not p.is_absolute():
                p = pdir / p
            if p.is_file():
                return p
    return None


def _gradient(canvas: Tuple[int, int]):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", canvas, COVER_TOP)
    draw = ImageDraw.Draw(img)
    for y in range(canvas[1]):
        t = y / max(1, canvas[1] - 1)
        draw.line([(0, y), (canvas[0], y)],
                  fill=tuple(int(a + (b - a) * t) for a, b in zip(COVER_TOP, COVER_BOTTOM)))
    return img


def _photo_background(src: Path, canvas: Tuple[int, int]):
    """铺满画布（中心裁切），叠加自下而上的暗色渐变，保证下方文字可读。"""
    from PIL import Image
    img = Image.open(src).convert("RGB")
    W, H = canvas
    scale = max(W / img.width, H / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left, top = (img.width - W) // 2, (img.height - H) // 2
    img = img.crop((left, top, left + W, top + H)).convert("RGBA")
    from PIL import ImageDraw
    shade = Image.new("RGBA", canvas, (0, 0, 0, 0))
    d = ImageDraw.Draw(shade)
    for y in range(H):
        t = y / max(1, H - 1)
        a = int(60 + 170 * max(0.0, (t - 0.25) / 0.75))     # 上部轻压暗，下部重压暗
        d.line([(0, y), (W, y)], fill=(10, 16, 30, a))
    return Image.alpha_composite(img, shade)


def build_cover(*, canvas: Tuple[int, int], orientation: str, title: str, author: str,
                subtitle: str, ai_text: str, wm: Dict[str, Any], pdir: Path,
                source_image: Optional[Path] = None):
    """返回 RGB 封面图。"""
    from PIL import Image, ImageDraw
    W, H = canvas
    u = unit_for(canvas, orientation)
    base = _photo_background(source_image, canvas) if source_image else _gradient(canvas).convert("RGBA")
    layer = Image.new("RGBA", canvas, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")

    maxw = int(W * (0.84 if orientation != "portrait" else 0.9))
    title_font = load_font(round((112 if orientation != "portrait" else 88) * u))
    sub_font = load_font(round((46 if orientation != "portrait" else 40) * u))
    small_font = load_font(round((34 if orientation != "portrait" else 30) * u))
    title_lines = wrap_text(draw, title or "未命名", title_font, maxw, max_lines=3)
    sub_lines = wrap_text(draw, subtitle, sub_font, maxw, max_lines=2) if subtitle else []
    author_text = f"作者：{author}" if author else ""

    gap_title = 22 * u
    block_h = (title_font.size + gap_title) * len(title_lines)
    block_h += 60 * u + 40 * u                       # 强调线与间距
    block_h += (sub_font.size + 12 * u) * len(sub_lines) + (30 * u if sub_lines else 0)
    block_h += (small_font.size + 10 * u) if author_text else 0

    y = H * (0.5 if source_image is None else 0.56) - block_h / 2
    for line in title_lines:
        lw = draw.textlength(line, font=title_font)
        draw.text(((W - lw) / 2, y), line, font=title_font, fill=TEXT)
        y += title_font.size + gap_title
    y += 20 * u
    draw.line([(W / 2 - 120 * u, y), (W / 2 + 120 * u, y)], fill=ACCENT, width=max(3, round(4 * u)))
    y += 40 * u
    for line in sub_lines:
        lw = draw.textlength(line, font=sub_font)
        draw.text(((W - lw) / 2, y), line, font=sub_font, fill=MUTED)
        y += sub_font.size + 12 * u
    if author_text:
        y += 30 * u if sub_lines else 0
        lw = draw.textlength(author_text, font=small_font)
        draw.text(((W - lw) / 2, y), author_text, font=small_font, fill=TEXT)
    if ai_text:
        af = load_font(round(26 * u))
        aw = draw.textlength(ai_text, font=af)
        draw.text(((W - aw) / 2, H - 70 * u), ai_text, font=af, fill=MUTED)

    out = Image.alpha_composite(base, layer)
    if wm.get("enabled"):
        tile = wm.get("mode") == "diagonal_tile"
        mark, size = render_watermark(canvas, orientation, "ink", wm, Path(pdir), tile=tile)
        if tile:
            out = Image.alpha_composite(out, mark.convert("RGBA"))
        else:
            xy = watermark_xy(canvas, size, wm.get("position", "bottom_right"), wm.get("margin", 0.03))
            out.alpha_composite(mark.convert("RGBA"), dest=xy)
    return out.convert("RGB")


def cover_subtitle(spine: Dict[str, Any]) -> str:
    """副标题：全片论点；没有则取第一章的一句话。"""
    thesis = str(spine.get("thesis") or "").strip()
    if thesis:
        return thesis
    parts = spine.get("parts") or []
    return str(parts[0].get("one_line", "")).strip() if parts else ""
