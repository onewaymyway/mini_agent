"""新方案第四阶段：按页渲染、合成与导出。

* 每页一张背景 + 页标题；页内元素按 deck_timeline 的出现时刻逐个显示（元素以整块出现，不逐条）；
* 元素在页内的高度按条目数分配，每个元素用现有卡片渲染器绘制（自适应布局，全部条目可见）；
* 章首为章封面（与现有方案的章封面相同）；音轨复用现有的句级音轨拼接；编码与成片校验与现有方案一致；
* 产物在 parts/<章>/deck_video/（与现有方案的 video/ 分开）；多章可拼接为新方案成片。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import cancel
from ..compose.audio_track import build_part_audio
from ..compose.config import FPS, load_compose_config  # noqa: F401  (配置来源与现有方案一致)
from ..compose.ffrun import ComposeError, ffmpeg, probe
from ..compose.export import safe_filename
from ..planning.runner import load_spine
from ..project_store import append_progress, now_iso, read_json, write_json_atomic
from ..render.fonts import load_font
from ..render.renderers import render_background, render_chapter_card, render_scene_frames
from ..render.text import wrap_text
from ..render.themes import canvas_size, get_theme
from ..validators.compose import check_video
from ..visual.config import load_visual_config
from .script import load_script
from .voice import load_deck_timeline
from .store import deck_root, load_deck

RENDER_VERSION = "deck-render-v1"
TITLE_BAND = 0.14        # 页标题区高度占画面高度的比例
PAD = 0.04               # 元素之间的留白（占画面高度）
MIN_ELEMENT_SHARE = 0.12  # 单个元素最少占用的画面高度比例


def _out_dir(pdir: Path, part_id: str) -> Path:
    return deck_root(pdir, part_id) / "video"


def _scene_for(el: Dict[str, Any]) -> Dict[str, Any]:
    k = el.get("kind")
    if k == "table":
        rows = el.get("rows") or []
        # 表格的状态数取自 items（与现有分镜一致：每行一条）
        return {"type": "table", "header": el.get("header") or [], "rows": rows,
                "items": ["、".join(r) for r in rows], "emphasis": []}
    if k == "formula":
        return {"type": "formula", "formula": el.get("text", ""), "items": [], "emphasis": []}
    if k == "diagram":
        return {"type": "diagram", "items": el.get("items") or [], "loop": bool(el.get("loop")), "emphasis": []}
    if k == "quote":
        return {"type": "quote", "items": el.get("items") or [], "emphasis": []}
    typ = "list" if k == "list" else "keypoint"
    return {"type": typ, "items": el.get("items") or [], "emphasis": []}


def _element_weights(elements: List[Dict[str, Any]]) -> List[float]:
    """元素占用的相对高度。表格按（表头 + 行数）加权，因为表格的行高固定，需要足够的区域。"""
    from .plan import element_count
    out = []
    for e in elements:
        if e.get("kind") == "table":
            out.append(max(2.0, 1.4 * (element_count(e) + 1)))
        else:
            out.append(max(1.0, float(element_count(e))))
    return out


def _min_share(el: Dict[str, Any]) -> float:
    return 0.26 if el.get("kind") == "table" else MIN_ELEMENT_SHARE


def _page_title(canvas, orientation: str, theme_name: str, title: str):
    from PIL import Image, ImageDraw
    W, H = canvas
    img = Image.new("RGBA", (W, int(H * TITLE_BAND)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    theme = get_theme(theme_name)
    scale = 1.0 if orientation != "portrait" else 0.85
    font = load_font(round(W / 1920 * 64 * scale))
    lines = wrap_text(d, title, font, int(W * 0.84), max_lines=2)
    y = img.size[1] * 0.25
    for ln in lines:
        d.text((W * 0.08, y), ln, font=font, fill=theme["text"])
        y += font.size * 1.2
    d.rectangle([W * 0.08, img.size[1] - 6, W * 0.08 + W * 0.12, img.size[1] - 2], fill=theme["accent"])
    return img


def page_state_images(page: Dict[str, Any], slide: Dict[str, Any], canvas, orientation: str,
                      theme_name: str) -> List[Any]:
    """页面的状态图：第 k 张显示前 k+1 个元素（背景 + 标题 + 已出现的元素）。元素高度按条目数分配。"""
    from PIL import Image
    W, H = canvas
    bg = render_background(canvas, theme_name).convert("RGBA")
    title = _page_title(canvas, orientation, theme_name, slide.get("title", page.get("title", "")))
    elements = slide.get("elements") or []
    weights = _element_weights(elements)
    body_top = H * (TITLE_BAND + PAD)
    body_h = H - body_top - H * PAD
    gap = H * PAD * 0.5
    total_gap = gap * max(0, len(elements) - 1)
    unit = (body_h - total_gap) / max(1e-6, sum(weights))
    heights = [max(H * _min_share(el), w * unit) for w, el in zip(weights, elements)]
    scale = min(1.0, (body_h - total_gap) / max(1e-6, sum(heights)))
    heights = [h * scale for h in heights]
    rendered: List[Any] = []
    for i, el in enumerate(elements):
        h = int(round(heights[i]))
        frames = render_scene_frames(_scene_for(el), (W, h), orientation, theme_name)
        rendered.append(frames[-1].convert("RGBA"))
    states = []
    for k in range(max(1, len(elements))):
        img = bg.copy()
        img.alpha_composite(title, (0, 0))
        y = body_top
        for i in range(min(k + 1, len(rendered))):
            img.alpha_composite(rendered[i], (0, int(round(y))))
            y += heights[i] + gap
        states.append(img.convert("RGB"))
    return states


def build_segments(pdir: Path, part_id: str, deck: Dict[str, Any], tl: Dict[str, Any], canvas, orientation: str,
                   theme_name: str, chapter: Dict[str, str]) -> Tuple[List[Tuple[Path, float]], float]:
    """生成 PNG 图层与各自的显示时长（concat 列表用）。返回 (图层列表, 总时长)。"""
    vdir = _out_dir(pdir, part_id)
    fdir = vdir / "frames"
    fdir.mkdir(parents=True, exist_ok=True)
    slides = {s["id"]: s for s in deck["slides"]}
    lead = float(tl["lead_in_sec"])
    total = float(tl["total_sec"])
    segs: List[Tuple[Path, float]] = []
    card = render_chapter_card(chapter, canvas, orientation, theme_name).convert("RGB")
    cpath = fdir / "chapter.png"
    card.save(cpath)
    segs.append((cpath, lead))
    pages = tl["pages"]
    for pi, page in enumerate(pages):
        slide = slides[page["id"]]
        states = page_state_images(page, slide, canvas, orientation, theme_name)
        reveals = page["reveals"] or [{"element": 0, "at_sec": page["start"]}]
        times = [max(page["start"], reveals[k]["at_sec"] if k < len(reveals) else page["start"])
                 for k in range(len(states))]
        times[0] = page["start"]
        next_start = pages[pi + 1]["start"] if pi + 1 < len(pages) else total
        ends = times[1:] + [next_start]
        for k, img in enumerate(states):
            path = fdir / f"{page['id'].replace('.', '_')}_{k:02d}.png"
            img.save(path)
            dur = max(0.0, ends[k] - times[k])
            if dur > 1e-3:
                segs.append((path, dur))
    # 时长按时间线精确凑齐（浮点误差归到最后一张）
    got = sum(d for _, d in segs)
    if segs and abs(got - total) > 1e-6:
        p, d = segs[-1]
        segs[-1] = (p, d + (total - got))
    return segs, total


def _concat_list(segs: List[Tuple[Path, float]], path: Path) -> None:
    lines = []
    for p, dur in segs:
        q = str(p.resolve()).replace("'", "'\\''")
        lines.append(f"file '{q}'\nduration {dur:.6f}")
    last = str(segs[-1][0].resolve()).replace("'", "'\\''")
    lines.append(f"file '{last}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def compose_deck_part(pdir: Path, part_id: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """渲染并合成一章的新方案视频（parts/<章>/deck_video/part.mp4）。"""
    pdir = Path(pdir)
    deck = load_deck(pdir, part_id)
    tl = load_deck_timeline(pdir, part_id)
    doc = load_script(pdir, part_id)
    if deck is None or not deck.get("confirmed"):
        raise ComposeError(f"{part_id} 的幻灯片规划还没有确认")
    if tl is None or doc is None or not doc.get("confirmed"):
        raise ComposeError(f"{part_id} 还没有配音时间线（请先确认讲稿并配音）")
    from ..app_settings import load_effective_project
    video = load_effective_project(pdir)["settings"].get("video", {})
    orientation = video.get("orientation", "landscape")
    canvas = canvas_size(orientation, video.get("resolution", "1080P"))
    theme_name = load_visual_config(pdir)["theme"]
    spine = load_spine(pdir) or {"parts": []}
    idx = next((i for i, p in enumerate(spine["parts"]) if p["id"] == part_id), 0)
    title = next((p["title"] for p in spine["parts"] if p["id"] == part_id), part_id)
    chapter = {"number": f"第 {idx + 1} 章", "title": title, "one_line": ""}

    vdir = _out_dir(pdir, part_id)
    vdir.mkdir(parents=True, exist_ok=True)
    segs, total = build_segments(pdir, part_id, deck, tl, canvas, orientation, theme_name, chapter)
    cancel.check()
    part_dir = deck_root(pdir, part_id)        # 新方案的配音在 parts/<章>/deck/audio/
    audio_tl = {"total_sec": total,
                "sentences": [{"id": s["id"], "start": s["start"]} for p in tl["pages"] for s in p["sentences"]]}
    wav = vdir / "audio.wav"
    build_part_audio(part_dir, audio_tl, wav)
    lst = vdir / "concat.txt"
    _concat_list(segs, lst)
    out = vdir / "part.mp4"
    ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(wav),
            "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)])
    pr = probe(out)
    verdict = check_video(pr, canvas=canvas, fps=FPS, expected_sec=total)
    if verdict["errors"]:
        raise ComposeError(f"{part_id} 新方案成片校验未通过：" + "；".join(verdict["errors"]))
    info = {"schema": 1, "part": part_id, "title": title, "canvas": list(canvas), "fps": FPS,
            "duration": round(total, 3), "pages": len(tl["pages"]), "segments": len(segs),
            "render_version": RENDER_VERSION, "rendered_at": now_iso(now), "deck_sha": deck.get("input_sha")}
    write_json_atomic(vdir / "info.json", info)
    append_progress(pdir, f"合成新方案章 {part_id}：{total:.1f} 秒，{len(tl['pages'])} 页", now)
    return info


def deck_part_ready(pdir: Path, part_id: str) -> Optional[Dict[str, Any]]:
    p = _out_dir(Path(pdir), part_id) / "info.json"
    return read_json(p) if p.is_file() else None


def export_deck_video(pdir: Path, *, now: Optional[datetime] = None) -> Path:
    """把各章的新方案视频按章节顺序拼接为一个成片（exports/<标题>_新方案.mp4）。"""
    pdir = Path(pdir)
    spine = load_spine(pdir)
    if spine is None:
        raise ComposeError("还没有大纲")
    files = []
    for p in spine["parts"]:
        f = _out_dir(pdir, p["id"]) / "part.mp4"
        if not f.is_file():
            raise ComposeError(f"{p['id']} 还没有新方案视频，请先合成本章")
        files.append(f)
    from .store import deck_home
    exports = deck_home(pdir) / "exports"          # 新方案的成片在 deck/exports/
    exports.mkdir(parents=True, exist_ok=True)
    name = safe_filename(spine.get("title") or "video") + "_新方案"
    lst = exports / f".{name}_concat.txt"
    lst.write_text("\n".join(f"file '{str(f.resolve()).replace(chr(39), chr(39) * 2 + chr(39))}'" for f in files) + "\n",
                   encoding="utf-8")
    out = exports / f"{name}.mp4"
    ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)])
    lst.unlink(missing_ok=True)
    append_progress(pdir, f"导出新方案成片：{out.name}", now)
    return out
