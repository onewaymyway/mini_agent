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

from .. import cancel, joblog
from ..compose.audio_track import build_part_audio
from ..compose.config import FPS, load_compose_config  # noqa: F401  (配置来源与现有方案一致)
from ..compose.ffrun import ComposeError, ffmpeg, probe
from ..compose.export import safe_filename
from ..planning.runner import load_spine
from ..project_store import append_progress, now_iso, read_json, write_json_atomic
from ..render.fonts import load_font
from ..render.renderers import (render_background, render_chapter_card, render_illustration_background,
                                render_scene_frames)
from ..render.text import wrap_text
from ..render.themes import canvas_size, get_theme
from ..validators.compose import check_video
from ..visual.config import load_visual_config
from .background import (DEFAULT_DARKEN, ensure_section_backgrounds, page_background_params, plan_sections,
                         section_key)
from .script import load_script
from .voice import load_deck_timeline
from .store import deck_root, load_deck

RENDER_VERSION = "deck-render-v4"   # v4（R34）：页面背景插图（按节共用）


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


def _fit_title(canvas, orientation: str, theme_name: str, title: str):
    """页标题：字号自动缩小，使标题完整显示（不截断），标题区高度随行数变化。返回标题图。"""
    from PIL import Image, ImageDraw
    W, H = canvas
    theme = get_theme(theme_name)
    scale = 1.0 if orientation != "portrait" else 0.8
    max_w = int(W * 0.88)
    max_band = H * 0.26                     # 标题区最高不超过画面的 26%
    probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    chosen = None
    for base in range(64, 27, -4):
        font = load_font(max(20, round(W / 1920 * base * scale)))
        lines = wrap_text(probe, title, font, max_w)
        lh = font.size * 1.2
        band = len(lines) * lh + H * 0.06
        if band <= max_band or base == 28:
            chosen = (font, lines, lh, band)
            break
    font, lines, lh, band = chosen
    img = Image.new("RGBA", (W, int(round(band))), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    y = H * 0.03
    for ln in lines:
        d.text((W * 0.06, y), ln, font=font, fill=theme["text"])
        y += lh
    d.rectangle([W * 0.06, img.size[1] - 6, W * 0.06 + W * 0.12, img.size[1] - 2], fill=theme["accent"])
    return img


def _crop_to_content(img):
    """裁掉透明留白，返回只含内容的图（无内容返回 None）。"""
    box = img.getchannel("A").getbbox()
    return img.crop(box) if box else None


def page_state_images(page: Dict[str, Any], slide: Dict[str, Any], canvas, orientation: str,
                      theme_name: str, *, columns: bool = True, meta: Optional[Dict[str, Any]] = None,
                      background: Optional[Path] = None, bg_focus: float = 0.5,
                      bg_darken: float = DEFAULT_DARKEN) -> List[Any]:
    """页面的状态图：第 k 张显示前 k+1 个元素（背景 + 标题 + 已出现的元素）。

    排版见 deck/layout.py（R30）：先按高度预算让渲染器自己缩字号/分栏，不够再把元素分成左右两栏，
    最后才整体缩小；每个元素完整可见。meta 非空时写入排版信息（mode、实际字号、是否过小）。
    """
    from PIL import Image
    from .layout import layout_page
    W, H = canvas
    if background is not None:              # 背景插图（R34）：按取景与压暗度铺满画面
        bg = render_illustration_background(Path(background), canvas, bg_focus, theme_name,
                                            darken=bg_darken).convert("RGBA")
    else:                                   # 没有插图：原来的渐变背景
        bg = render_background(canvas, theme_name).convert("RGBA")
    if meta is not None:
        meta["background"] = background is not None
    title = _fit_title(canvas, orientation, theme_name, slide.get("title", page.get("title", "")))
    top = int(H * 0.03) + title.size[1] + int(H * 0.03)
    body_h = H - top - int(H * 0.04)
    gap = int(H * 0.035)
    elements = slide.get("elements") or []
    from .textlayout import build_text_layout
    tl_ = build_text_layout(elements, canvas, top, body_h, orientation, theme_name, columns=columns)
    if tl_ is not None:                      # 文字页（要点/列表/金句）：整页网格排版（R32）
        if meta is not None:
            meta.update({k: tl_[k] for k in ("mode", "split", "effective_px", "floor_px", "small")})
            meta["columns"] = tl_["columns"]
        states = []
        for k in range(len(elements)):
            img = bg.copy()
            img.alpha_composite(title, (0, int(H * 0.03)))
            tl_["paint"](img, k + 1)
            states.append(img.convert("RGB"))
        return states
    lay = layout_page(elements, canvas, body_h, gap, orientation, theme_name, _scene_for, columns=columns)
    if meta is not None:
        meta.update({k: lay[k] for k in ("mode", "split", "effective_px", "floor_px", "small")})
    pieces = lay["pieces"] or [(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), 0, 0)]
    states = []
    for k in range(len(pieces)):
        img = bg.copy()
        img.alpha_composite(title, (0, int(H * 0.03)))
        for p, x, y in pieces[:k + 1]:
            img.alpha_composite(p, (int(x), int(top + y)))
        states.append(img.convert("RGB"))
    return states


def build_segments(pdir: Path, part_id: str, deck: Dict[str, Any], tl: Dict[str, Any], canvas, orientation: str,
                   theme_name: str, chapter: Dict[str, str], *, columns: bool = True,
                   layout_log: Optional[List[Dict[str, Any]]] = None,
                   backgrounds: Optional[Dict[str, Optional[Path]]] = None,
                   bg_darken: float = DEFAULT_DARKEN) -> Tuple[List[Tuple[Path, float]], float]:
    """生成 PNG 图层与各自的显示时长（concat 列表用）。返回 (图层列表, 总时长)。
    layout_log 非空时追加每页的排版信息（页号、分栏与否、实际字号、是否过小）。"""
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
    seen: Dict[str, int] = {}               # 同一节内第几页（用于取景与压暗度的变化）
    for pi, page in enumerate(pages):
        slide = slides[page["id"]]
        meta: Dict[str, Any] = {}
        key = section_key(page, slide)
        j = seen.get(key, 0)
        seen[key] = j + 1
        focus, dark = page_background_params(j, bg_darken)
        states = page_state_images(page, slide, canvas, orientation, theme_name, columns=columns, meta=meta,
                                   background=(backgrounds or {}).get(key), bg_focus=focus, bg_darken=dark)
        if layout_log is not None:
            layout_log.append(dict(meta, id=page["id"]))
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


def compose_deck_part(pdir: Path, part_id: str, *, now: Optional[datetime] = None,
                      image=None, llm=None) -> Dict[str, Any]:
    """渲染并合成一章的新方案视频（parts/<章>/deck_video/part.mp4）。

    image：生图服务（None 表示无 key，不生图）；llm：文本模型，用于为各节写画面主体（可省略）。
    背景插图按节生成并缓存（R34）；关闭或无生图服务时使用渐变背景。"""
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
    joblog.bound_log(f"{part_id}：开始渲染 {len(tl['pages'])} 页画面（{canvas[0]}×{canvas[1]}）")
    vcfg = load_visual_config(pdir)
    columns = bool(vcfg.get("deck_layout", {}).get("columns", True))
    bg_cfg = vcfg.get("deck_background", {})
    bg_darken = float(bg_cfg.get("darken", DEFAULT_DARKEN))
    backgrounds: Dict[str, Optional[Path]] = {}
    if bg_cfg.get("enabled", True) and vcfg.get("images", {}).get("enabled", True):
        slides_by_id = {s["id"]: s for s in deck["slides"]}
        sections = plan_sections(tl["pages"], slides_by_id)
        backgrounds = ensure_section_backgrounds(
            pdir, part_id, sections, provider=image, style_anchor=vcfg["bible"]["style_anchor"],
            orientation=orientation, size=vcfg["images"]["size"], llm=llm)
        ok = sum(1 for v in backgrounds.values() if v)
        joblog.bound_log(f"{part_id}：背景 {len(sections)} 节，插图 {ok} 节，渐变 {len(sections) - ok} 节")
    else:
        why = []
        if not bg_cfg.get("enabled", True):
            why.append("背景插图开关（设置 → 视觉 → 新方案：页面背景插图）为关闭")
        if not vcfg.get("images", {}).get("enabled", True):
            why.append("「启用 AI 插图」为关闭")
        joblog.bound_log(f"{part_id} 背景：未生图，原因：{'；'.join(why)}，使用渐变色", level="warn")
    layout_log: List[Dict[str, Any]] = []
    segs, total = build_segments(pdir, part_id, deck, tl, canvas, orientation, theme_name, chapter,
                                 columns=columns, layout_log=layout_log,
                                 backgrounds=backgrounds, bg_darken=bg_darken)
    for m in layout_log:
        if m.get("mode") == "columns":
            joblog.bound_log(f"{m['id']}：内容较多，分两栏显示（实际字号约 {m['effective_px']:.0f}px）")
        if m.get("small"):
            joblog.bound_log(f"{m['id']}：内容过多，文字偏小（约 {m['effective_px']:.0f}px，低于 {m['floor_px']:.0f}px），"
                             "建议在幻灯片规划中拆页", level="warn")
    joblog.bound_log(f"{part_id}：画面 {len(segs)} 段渲染完成，共 {total:.1f} 秒；开始编码合成")
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
            "layout": layout_log, "small_text_pages": [m["id"] for m in layout_log if m.get("small")],
            "backgrounds": {k: (v.name if v else None) for k, v in backgrounds.items()},
            "column_pages": [m["id"] for m in layout_log if m.get("mode") == "columns"],
            "render_version": RENDER_VERSION, "rendered_at": now_iso(now), "deck_sha": deck.get("input_sha")}
    write_json_atomic(vdir / "info.json", info)
    append_progress(pdir, f"合成新方案章 {part_id}：{total:.1f} 秒，{len(tl['pages'])} 页", now)
    return info


def deck_part_ready(pdir: Path, part_id: str) -> Optional[Dict[str, Any]]:
    p = _out_dir(Path(pdir), part_id) / "info.json"
    return read_json(p) if p.is_file() else None


def _deck_hold(pdir: Path, part_id: str) -> float:
    """章开头章封面的秒数：取新方案时间线的 lead_in_sec。"""
    tl = load_deck_timeline(pdir, part_id)
    return float(tl.get("lead_in_sec", 0.0)) if tl else 0.0


def export_deck_video(pdir: Path, *, now: Optional[datetime] = None) -> Path:
    """把各章的新方案视频导出为成片 deck/exports/<文章标题>.mp4。

    与现有方案共用同一套导出（R29）：封面合成进成片开头；片头/片尾卡、下三分之一条、角标、BGM、水印按同样的设置叠加；
    同时输出无水印版 _clean、字幕、封面图与报告。文件名与文章标题一致（两种方案在各自的目录里，互不覆盖）。
    """
    from ..compose.export import ExportSource, export_final
    from .store import deck_home
    pdir = Path(pdir)
    spine = load_spine(pdir)
    if spine is None:
        raise ComposeError("还没有大纲")
    for p in spine["parts"]:
        if not (_out_dir(pdir, p["id"]) / "part.mp4").is_file():
            raise ComposeError(f"{p['id']} 还没有新方案视频，请先合成本章")
    source = ExportSource(video_dir_of=_out_dir, hold_of=_deck_hold, exports_dir=deck_home(pdir) / "exports",
                          cover_in_video=True, mark_stage=False)
    rep = export_final(pdir, now=now, source=source)
    out = deck_home(pdir) / "exports" / rep["files"]["video"]
    append_progress(pdir, f"导出新方案成片：{out.name}", now)
    return out
