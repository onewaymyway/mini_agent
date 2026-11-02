"""media/subtitles.py — 字幕烧录（章节合成时叠加到画面上）。

做法参考 .claude/skills/novel-video-studio/scripts/compose_macro_scene.py：
字幕文字用 PIL 渲染成透明 PNG，再由 ffmpeg 的 concat 时间表逐段叠加到画面上，
不依赖 libass（很多 ffmpeg 构建没有 subtitles/ass 滤镜）。

与参考实现的差异：
  * 时间轴按**台词块**（旁白/每句对白）生成，而不是按整个小场景一张字幕。
    每个块的起止时间取自配音的真实时长（shots.yaml 的 blocks[].duration），
    块之间的间隔与配音拼接时一致（chapter_audio.GAP_SEC），因此字幕与声音对齐。
  * 对白前加说话人名（“林远：我们走。”），便于观众分辨是谁在说话。
  * 时间轴以镜头的真实视频时长为准：配音与画面不一致时按比例缩放块时长。

字体：优先 NOVEL_TO_VIDEO_FONT 环境变量，其次封面模块的中文字体候选。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import ffmpeg as mf
from .cover import _wrap, resolve_font

FONT_ENV = "NOVEL_TO_VIDEO_FONT"
GAP_SEC = 0.2                 # 与 steps/chapter_audio.GAP_SEC 保持一致（配音拼接的块间隔）
CHARS_PER_SEC = 4.2           # 没有配音时长时的估算语速，与剧本字数预算一致
BOTTOM_MARGIN = 0.07          # 字幕底边距，占画面高度的比例
FONT_RATIO = 0.042            # 字号占画面高度的比例（720P 约 30 px）


class SubtitleError(RuntimeError):
    pass


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


# ---------------------------------------------------------------- 文案

def cue_text(block: Dict[str, Any], speaker_names: Dict[str, str]) -> str:
    """一个台词块的字幕文案：旁白原样；对白前加说话人名。"""
    text = (block.get("text") or "").strip()
    if not text:
        return ""
    if block.get("type") == "dialogue":
        name = speaker_names.get(block.get("speaker_id") or "") or (block.get("speaker_name") or "").strip()
        return f"{name}：{text}" if name else text
    return text


def _block_seconds(block: Dict[str, Any]) -> float:
    d = block.get("duration")
    try:
        d = float(d)
    except (TypeError, ValueError):
        d = 0.0
    if d > 0:
        return d
    # 没有配音时长（例如离线测试或配音未完成）：按字数估算
    return max(0.5, len((block.get("text") or "").strip()) / CHARS_PER_SEC)


def build_cues(shots: Sequence[Dict[str, Any]], clip_seconds: Sequence[float],
               speaker_names: Dict[str, str], gap: float = GAP_SEC) -> Tuple[List[Cue], float]:
    """按镜头顺序生成字幕时间轴。返回 (cues, 总时长)。

    clip_seconds 与 shots 一一对应，是每个镜头在章节视频里的真实时长；镜头内的块时长
    按镜头时长等比缩放，保证字幕不会超出画面、也不会在画面结束后仍显示。
    """
    if len(shots) != len(clip_seconds):
        raise SubtitleError(f"镜头数 {len(shots)} 与视频片段数 {len(clip_seconds)} 不一致")
    cues: List[Cue] = []
    t = 0.0
    for sh, clip_dur in zip(shots, clip_seconds):
        clip_dur = float(clip_dur)
        blocks = [b for b in (sh.get("blocks") or []) if (b.get("text") or "").strip()]
        durs = [_block_seconds(b) for b in blocks]
        speech = sum(durs) + gap * max(0, len(durs) - 1)
        scale = clip_dur / speech if speech > 0 and clip_dur > 0 else 1.0
        cur = t
        for b, d in zip(blocks, durs):
            start = cur
            end = min(cur + d * scale, t + clip_dur)
            text = cue_text(b, speaker_names)
            if text and end > start + 1e-3:
                cues.append(Cue(round(start, 3), round(end, 3), text))
            cur = cur + d * scale + gap * scale
        t += clip_dur
    return cues, round(t, 3)


def timeline_segments(cues: Sequence[Cue], total: float) -> List[Tuple[Optional[str], float]]:
    """把时间轴补齐为连续段：(文字或 None 表示空白, 时长)，覆盖 [0, total]。"""
    segs: List[Tuple[Optional[str], float]] = []
    t = 0.0
    for c in cues:
        if c.start > t + 1e-3:
            segs.append((None, round(c.start - t, 3)))
        segs.append((c.text, round(c.end - c.start, 3)))
        t = c.end
    if total > t + 1e-3:
        segs.append((None, round(total - t, 3)))
    return [(text, dur) for text, dur in segs if dur > 0]


# ---------------------------------------------------------------- 渲染

def default_font_size(height: int) -> int:
    return max(18, round(height * FONT_RATIO))


def resolve_subtitle_font() -> str:
    env = os.environ.get(FONT_ENV)
    if env:
        if not Path(env).is_file():
            raise SubtitleError(f"环境变量 {FONT_ENV} 指向的字体不存在：{env}")
        return env
    try:
        return resolve_font()
    except Exception as exc:  # cover.CoverError
        raise SubtitleError(f"字幕需要中文字体：{exc}") from exc


def render_cue_png(text: Optional[str], width: int, height: int, font, font_size: int, path: Path) -> Path:
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if text:
        draw = ImageDraw.Draw(img)
        lines = _wrap(draw, text, font, int(width * 0.9))[:3]
        line_h = int(font_size * 1.35)
        pad_x, pad_y = max(8, font_size // 3), max(4, font_size // 6)
        block_h = line_h * len(lines) + pad_y * 2
        y0 = height - int(height * BOTTOM_MARGIN) - block_h
        for i, line in enumerate(lines):
            tw = draw.textlength(line, font=font)
            box_w = int(tw) + pad_x * 2
            x = (width - box_w) // 2
            y = y0 + pad_y + i * line_h
            draw.rectangle([x, y - pad_y // 2, x + box_w, y + line_h - pad_y // 2], fill=(0, 0, 0, 165))
            draw.text((x + pad_x, y), line, font=font, fill=(255, 255, 255, 255),
                      stroke_width=max(1, font_size // 18), stroke_fill=(0, 0, 0, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return path


# ---------------------------------------------------------------- 烧录

def burn(video_in: Path, out: Path, cues: Sequence[Cue], total: float, width: int, height: int,
         workdir: Path, font_size: Optional[int] = None) -> Dict[str, Any]:
    """把字幕叠加到 video_in 上，音轨原样保留，输出 out。无字幕时也会正常输出（空白叠加）。"""
    from PIL import ImageFont

    size = int(font_size or default_font_size(height))
    font = ImageFont.truetype(resolve_subtitle_font(), size)
    workdir.mkdir(parents=True, exist_ok=True)
    segs = timeline_segments(cues, total)
    pngs: Dict[Optional[str], Path] = {}
    for text, _ in segs:
        if text not in pngs:
            pngs[text] = render_cue_png(text, width, height, font, size,
                                        workdir / f"cue_{len(pngs):04d}.png")
    listing = workdir / "subs_list.txt"
    with listing.open("w", encoding="utf-8") as f:
        for text, dur in segs:
            f.write(f"file '{pngs[text].resolve().as_posix()}'\n")
            f.write(f"duration {dur:.3f}\n")
        # concat 解复用器要求最后一个文件再写一次（不带 duration）
        f.write(f"file '{pngs[segs[-1][0]].resolve().as_posix()}'\n")
    out.parent.mkdir(parents=True, exist_ok=True)
    mf._run([mf.ffmpeg(), "-y", "-i", str(video_in), "-f", "concat", "-safe", "0", "-i", str(listing),
             "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[vout]",
             "-map", "[vout]", "-map", "0:a?", "-c:a", "copy",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
             "-t", f"{total:.3f}", str(out)])
    return {"cues": len(cues), "unique_images": len(pngs), "font_size": size}
