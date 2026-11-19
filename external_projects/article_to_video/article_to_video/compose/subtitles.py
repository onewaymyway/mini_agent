"""compose/subtitles.py — 字幕：句级时间窗、可见性（auto 时卡片已完整呈现则隐藏）、关键词高亮、SRT。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..render.fonts import load_font
from ..render.text import draw_rich_line, wrap_text
from ..render.themes import get_theme, unit_for
from ..visual.shots import shot_range

_PUNCT = re.compile(r"[\s，,。．.！!？?；;：:、“”\"'‘’（）()《》\-—…]")


def _norm(s: str) -> str:
    return _PUNCT.sub("", s)


def covered_by_card(sentence: str, scene: Optional[Dict[str, Any]]) -> bool:
    """卡片（非插图）已完整呈现本句：句子去标点后是条目拼接文本的子串。"""
    if not scene or scene.get("type") in ("illustration",):
        return False
    shown = _norm("".join(str(x) for x in scene.get("items") or []))
    if scene.get("type") == "table":
        shown += _norm("".join("".join(r) for r in scene.get("rows") or []))
    return bool(shown) and _norm(sentence) in shown


def _scene_of_sentence(scenes_by_beat: Dict[str, List[Dict[str, Any]]], counts: Dict[str, int],
                       sid: str) -> Optional[Dict[str, Any]]:
    """句子所在的镜头（第一期起一段可有多个镜头，按句子范围查找）。"""
    beat = sid.rsplit(".s", 1)[0]
    idx = int(sid.rsplit(".s", 1)[1]) - 1
    for sc in scenes_by_beat.get(beat, []):
        a, b = shot_range(sc, counts.get(beat, 0))
        if a <= idx <= b:
            return sc
    return None


def subtitle_plan(timeline: Dict[str, Any], script: Dict[str, Any], storyboard: Dict[str, Any],
                  mode: str) -> List[Dict[str, Any]]:
    """每句一条：{id, start, end, text, emphasis, visible}。"""
    scenes_by_beat: Dict[str, List[Dict[str, Any]]] = {}
    for s in storyboard["scenes"]:
        scenes_by_beat.setdefault(s["beat"], []).append(s)
    counts = {b["id"]: len(b["sentences"]) for b in script["beats"]}
    texts = {}
    emph = {}
    for b in script["beats"]:
        for i, s in enumerate(b["sentences"]):
            sid = f"{b['id']}.s{i + 1:02d}"
            texts[sid] = s["text"]
            emph[sid] = [e for e in s.get("emphasis") or [] if e and e in s["text"]]
    out = []
    for item in timeline["sentences"]:
        sid = item["id"]
        text = texts.get(sid, item.get("text", ""))
        if mode == "off":
            visible = False
        elif mode == "always":
            visible = True
        else:
            visible = not covered_by_card(text, _scene_of_sentence(scenes_by_beat, counts, sid))
        out.append({"id": sid, "start": item["start"], "end": item["end"], "text": text,
                    "emphasis": emph.get(sid, []), "visible": visible})
    return out


def render_subtitle(text: str, emphasis, canvas, orientation: str, theme_name: str):
    """整幅 RGBA 字幕层：底部居中的半透明底框，关键词用强调色。"""
    from PIL import Image, ImageDraw

    W, H = canvas
    u = unit_for(canvas, orientation)
    theme = get_theme(theme_name)
    img = Image.new("RGBA", canvas, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")
    font = load_font(round((40 if orientation == "portrait" else 46) * u))
    maxw = W * (0.86 if orientation == "portrait" else 0.78)
    lines = wrap_text(draw, text, font, int(maxw - 40 * u), max_lines=2)
    if not lines:
        return img
    lh = font.size + int(font.size * 0.2)
    box_w = max(draw.textlength(line, font=font) for line in lines) + 40 * u
    box_h = lh * len(lines) + 24 * u
    x0 = (W - box_w) / 2
    y0 = H - H * 0.10 - box_h
    draw.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h], radius=14 * u, fill=(0, 0, 0, 150))
    for k, line in enumerate(lines):
        lw = draw.textlength(line, font=font)
        draw_rich_line(draw, ((W - lw) / 2, y0 + 12 * u + k * lh), line, font, theme["text"],
                       theme["accent"], emphasis)
    return img


def _ts(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(entries: List[Dict[str, Any]]) -> str:
    """entries：{start, end, text}（全片时间）。"""
    blocks = []
    for i, e in enumerate(entries, start=1):
        blocks.append(f"{i}\n{_ts(e['start'])} --> {_ts(e['end'])}\n{e['text']}\n")
    return "\n".join(blocks)
