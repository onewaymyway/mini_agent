"""media/cover.py — 封面：背景图与标题叠字分离（沿用 v7 方案）。

  * cover_bg.png：生图接口生成的纯背景（提示词要求无文字），调用 API，成本高；
  * cover.png：本地用 PIL 把标题叠加到背景上，免费、秒级。
改标题或布局只需重跑叠字，不必重新生成背景图。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .ffmpeg import H, W

LAYOUTS = ("center", "bottom_bar", "top_classic")
DEFAULT_LAYOUT = "center"

_FONT_CANDIDATES = [
    os.environ.get("NOVEL_TO_VIDEO_FONT", ""),
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
]


class CoverError(RuntimeError):
    pass


def resolve_font() -> str:
    for p in _FONT_CANDIDATES:
        if p and Path(p).is_file():
            return p
    raise CoverError("找不到中文字体，请设置环境变量 NOVEL_TO_VIDEO_FONT 指向 .ttf/.ttc 文件")


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list:
    lines, cur = [], ""
    for ch in text:
        trial = cur + ch
        if draw.textlength(trial, font=font) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def render_cover(bg: Path, out: Path, title: str, layout: str = DEFAULT_LAYOUT) -> Path:
    if layout not in LAYOUTS:
        raise CoverError(f"未知封面布局：{layout}（可选 {', '.join(LAYOUTS)}）")
    title = (title or "").strip()
    if not title:
        raise CoverError("封面标题不能为空")
    if not bg.is_file():
        raise CoverError(f"缺少背景图：{bg.name}，请先生成 cover_bg.png")

    img = Image.open(bg).convert("RGB")
    img = img.resize((W, H), Image.LANCZOS)
    draw = ImageDraw.Draw(img, "RGBA")
    font = ImageFont.truetype(resolve_font(), size=72)
    max_w = int(W * 0.84)
    lines = _wrap(draw, title, font, max_w)[:4]
    line_h = int(font.size * 1.35)
    block_h = line_h * len(lines)

    if layout == "center":
        top = int(H * 0.30) - block_h // 2
        veil = (0, int(top - 40), W, int(top + block_h + 40))
        draw.rectangle((0, veil[1], W, veil[3]), fill=(0, 0, 0, 120))
    elif layout == "bottom_bar":
        top = int(H * 0.84) - block_h // 2
        draw.rectangle((0, top - 36, W, top + block_h + 36), fill=(0, 0, 0, 150))
    else:  # top_classic：描边，无蒙层
        top = int(H * 0.16) - block_h // 2

    y = top
    for line in lines:
        w = draw.textlength(line, font=font)
        x = (W - w) / 2
        if layout == "top_classic":
            for dx in (-3, 0, 3):
                for dy in (-3, 0, 3):
                    draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 255))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_h

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="PNG")
    return out
