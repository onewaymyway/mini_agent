"""render/text.py — 文字排版：按像素宽度换行（中英文混排不拆英文单词）、强调词着色、省略。"""

from __future__ import annotations

import re
from typing import List, Sequence, Tuple

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-\.%/]*|\s+|.", re.S)


def wrap_text(draw, text: str, font, max_width: int, max_lines: int = 99) -> List[str]:
    """贪心换行。超过 max_lines 时最后一行加省略号。"""
    lines: List[str] = []
    cur = ""
    for tok in _TOKEN.findall(text.replace("\n", " ")):
        trial = cur + tok
        if draw.textlength(trial.rstrip(), font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur.rstrip())
            cur = tok.lstrip()
    if cur.strip():
        lines.append(cur.rstrip())
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > max_width:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


def split_segments(line: str, emphasis: Sequence[str]) -> List[Tuple[str, bool]]:
    """把一行按强调词切成 (文本, 是否强调) 段。"""
    words = sorted({w for w in emphasis if w and w in line}, key=len, reverse=True)
    if not words:
        return [(line, False)]
    pattern = "(" + "|".join(re.escape(w) for w in words) + ")"
    out: List[Tuple[str, bool]] = []
    for part in re.split(pattern, line):
        if part:
            out.append((part, part in words))
    return out


def draw_rich_line(draw, xy, line: str, font, color, accent, emphasis: Sequence[str]) -> int:
    """绘制一行（强调词用 accent 色）。返回本行宽度。"""
    x, y = xy
    for seg, is_em in split_segments(line, emphasis):
        draw.text((x, y), seg, font=font, fill=accent if is_em else color)
        x += draw.textlength(seg, font=font)
    return int(x - xy[0])


def measure_block(draw, lines: Sequence[str], font, line_gap: float) -> int:
    if not lines:
        return 0
    lh = font.size
    return int(lh * len(lines) + line_gap * (len(lines) - 1))
