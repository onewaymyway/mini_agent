"""media/cover.py — 封面：背景图与标题叠字分离（沿用 v7 方案）。

  * cover_bg.png：生图接口生成的纯背景（提示词要求无文字），调用 API，成本高；
  * cover.png：本地用 PIL 把标题叠加到背景上，免费、秒级。
改标题或布局只需重跑叠字，不必重新生成背景图。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import ffmpeg as _mf

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


_CTRL_RE = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u2064\ufeff]")


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list:
    """按像素宽度折行。PIL 的 textlength 不能度量含换行的文本（会抛 can't measure length of multiline text），
    所以先按换行拆成段落、清掉控制/零宽字符，再逐段折行；换行本身作为强制分行。"""
    lines: list = []
    for para in re.split(r"[\r\n]+", _CTRL_RE.sub("", text or "").replace("\t", " ")):
        para = para.strip()
        cur = ""
        for ch in para:
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
    img = img.resize((_mf.W, _mf.H), Image.LANCZOS)
    draw = ImageDraw.Draw(img, "RGBA")
    font = ImageFont.truetype(resolve_font(), size=72)
    max_w = int(_mf.W * 0.84)
    lines = _wrap(draw, title, font, max_w)[:4]
    line_h = int(font.size * 1.35)
    block_h = line_h * len(lines)

    if layout == "center":
        top = int(_mf.H * 0.30) - block_h // 2
        veil = (0, int(top - 40), _mf.W, int(top + block_h + 40))
        draw.rectangle((0, veil[1], _mf.W, veil[3]), fill=(0, 0, 0, 120))
    elif layout == "bottom_bar":
        top = int(_mf.H * 0.84) - block_h // 2
        draw.rectangle((0, top - 36, _mf.W, top + block_h + 36), fill=(0, 0, 0, 150))
    else:  # top_classic：描边，无蒙层
        top = int(_mf.H * 0.16) - block_h // 2

    y = top
    for line in lines:
        w = draw.textlength(line, font=font)
        x = (_mf.W - w) / 2
        if layout == "top_classic":
            for dx in (-3, 0, 3):
                for dy in (-3, 0, 3):
                    draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 255))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_h

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="PNG")
    return out


def render_chapter_cover(bg: Path, out: Path, book_title: str, chapter_no: int, chapter_title: str,
                         layout: str = DEFAULT_LAYOUT) -> Path:
    """章节封面叠字：小说名（小）、「第 N 章」（大）、章节标题（大，自动换行）。文字全部由程序绘制。
    布局与整书封面一致（center / bottom_bar / top_classic）；背景图由生图接口生成，要求无文字。"""
    if layout not in LAYOUTS:
        raise CoverError(f"未知封面布局：{layout}（可选 {', '.join(LAYOUTS)}）")
    if not bg.is_file():
        raise CoverError(f"缺少背景图：{bg.name}")
    book_title = (book_title or "").strip()
    chapter_title = (chapter_title or "").strip()
    no_text = f"第{int(chapter_no)}章"

    img = Image.open(bg).convert("RGB").resize((_mf.W, _mf.H), Image.LANCZOS)
    draw = ImageDraw.Draw(img, "RGBA")
    path = resolve_font()
    f_book = ImageFont.truetype(path, size=44)
    f_no = ImageFont.truetype(path, size=88)
    f_title = ImageFont.truetype(path, size=64)
    max_w = int(_mf.W * 0.84)

    rows = []      # (文字, 字体, 行高)
    if book_title:
        for ln in _wrap(draw, f"《{book_title}》", f_book, max_w)[:2]:
            rows.append((ln, f_book, int(f_book.size * 1.5)))
    rows.append((no_text, f_no, int(f_no.size * 1.35)))
    for ln in _wrap(draw, chapter_title, f_title, max_w)[:3]:
        rows.append((ln, f_title, int(f_title.size * 1.35)))
    block_h = sum(r[2] for r in rows)

    if layout == "center":
        top = int(_mf.H * 0.40) - block_h // 2
        draw.rectangle((0, top - 44, _mf.W, top + block_h + 44), fill=(0, 0, 0, 120))
    elif layout == "bottom_bar":
        top = int(_mf.H * 0.80) - block_h // 2
        draw.rectangle((0, top - 40, _mf.W, top + block_h + 40), fill=(0, 0, 0, 150))
    else:  # top_classic：描边，无蒙层
        top = int(_mf.H * 0.22) - block_h // 2

    y = top
    for text, font, lh in rows:
        w = draw.textlength(text, font=font)
        x = (_mf.W - w) / 2
        if layout == "top_classic":
            for dx in (-3, 0, 3):
                for dy in (-3, 0, 3):
                    draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 255))
        draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
        y += lh

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="PNG")
    return out
