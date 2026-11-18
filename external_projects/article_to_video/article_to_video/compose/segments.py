"""compose/segments.py — 分段合成：把时间轴切成若干段，每段合成一张整幅 PNG（底图 + 画面状态 + 字幕 + 进度条 + 章标签）。

切点 = 场景起止、画面状态时刻、句子起止、章标题卡结束。同一段内画面不变，因此 ffmpeg 可用 concat 时长列表编码。
每段的图层选择只看段中点，保证切点恰好落在变化时刻上。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from .. import cancel
from ..render.fonts import load_font
from ..render.themes import get_theme, unit_for
from .subtitles import render_subtitle

MIN_SEG = 0.02  # 短于此的切片并入前一段（避免几乎为零时长的图片）


def _boundaries(timeline: Dict[str, Any], manifest: Dict[str, Any]) -> List[float]:
    total = float(timeline["total_sec"])
    pts = {0.0, total, float(manifest["chapter"]["end"])}
    for s in manifest["scenes"]:
        pts.add(float(s["start"]))
        pts.add(float(s["end"]))
        for f in s.get("frames", []):
            pts.add(float(f["start"]))
    for it in timeline["sentences"]:
        pts.add(float(it["start"]))
        pts.add(float(it["end"]))
    clean = sorted(min(total, max(0.0, p)) for p in pts)
    out: List[float] = []
    for p in clean:
        if out and p - out[-1] < MIN_SEG:
            continue
        out.append(p)
    if out[-1] < total:
        out[-1] = total
    return out


def _chapter_label(storyboard: Dict[str, Any]) -> str:
    ch = storyboard.get("chapter") or {}
    title = str(ch.get("title") or "")
    return f"{ch.get('number', '')}　{title[:24]}".strip()


def build_segments(part_dir: Path, *, timeline: Dict[str, Any], manifest: Dict[str, Any],
                   subs: List[Dict[str, Any]], storyboard: Dict[str, Any], cfg: Dict[str, Any],
                   canvas: Tuple[int, int], orientation: str, seg_dir: Path) -> List[Tuple[Path, float]]:
    from PIL import Image, ImageDraw

    seg_dir.mkdir(parents=True, exist_ok=True)
    for old in seg_dir.glob("seg_*.png"):
        old.unlink()
    total = float(timeline["total_sec"])
    bounds = _boundaries(timeline, manifest)
    theme = get_theme(manifest.get("theme", "ink"))
    u = unit_for(canvas, orientation)
    chap_end = float(manifest["chapter"]["end"])
    scenes = manifest["scenes"]
    sub_visible = [s for s in subs if s["visible"]]
    label = _chapter_label(storyboard)
    label_font = load_font(round(34 * u))

    img_cache: Dict[str, Any] = {}

    def layer(rel: str, rgba: bool):
        key = rel
        if key not in img_cache:
            im = Image.open(part_dir / rel)
            img_cache[key] = im.convert("RGBA") if rgba or im.mode == "RGBA" else im.convert("RGB").convert("RGBA")
        return img_cache[key]

    sub_cache: Dict[str, Any] = {}

    def sub_layer(s):
        if s["id"] not in sub_cache:
            sub_cache[s["id"]] = render_subtitle(s["text"], s["emphasis"], canvas, orientation,
                                                 manifest.get("theme", "ink"))
        return sub_cache[s["id"]]

    segs: List[Tuple[Path, float]] = []
    for i in range(len(bounds) - 1):
        cancel.check()
        a, b = bounds[i], bounds[i + 1]
        if b - a <= 0:
            continue
        m = (a + b) / 2
        layers: List[Any] = []
        sub = None
        if m < chap_end:
            layers = [layer(manifest["chapter"]["background"], False), layer(manifest["chapter"]["file"], True)]
        else:
            scene = next((s for s in scenes if float(s["start"]) <= m < float(s["end"])), None)
            if scene is None:
                layers = [layer("visuals/bg.png", False)]
            else:
                layers.append(layer(scene["background"], False))
                frames = scene.get("frames") or []
                cur = None
                for f in frames:
                    if float(f["start"]) <= m:
                        cur = f
                if cur is not None:
                    layers.append(layer(cur["file"], True))
            for s in sub_visible:
                if float(s["start"]) <= m < float(s["end"]):
                    sub = s
                    break
            if sub is not None:
                layers.append(sub_layer(sub))

        base = layers[0].copy()
        for ly in layers[1:]:
            base.alpha_composite(ly)
        draw = ImageDraw.Draw(base, "RGBA")
        W, H = canvas
        if cfg.get("progress_bar", True):
            bar_h = max(4, round(6 * u))
            draw.rectangle([0, H - bar_h, W, H], fill=(255, 255, 255, 40))
            frac = min(1.0, m / total) if total > 0 else 0.0
            draw.rectangle([0, H - bar_h, W * frac, H], fill=theme["accent"])
        if cfg.get("chapter_label", True) and label and m >= chap_end:
            draw.text((W * 0.04, H * 0.04), label, font=label_font, fill=theme["muted"])
        path = seg_dir / f"seg_{len(segs):05d}.png"
        base.convert("RGB").save(path, compress_level=1)
        segs.append((path, b - a))
    return segs
