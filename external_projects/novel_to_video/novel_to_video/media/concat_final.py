"""media/concat_final.py — 全书拼接（F-3）与成片校验（F-4）。

  * 封面作为独立片段前置，静音，不替换正片任何画面（v7 方案）；
  * 章节之间默认硬切；transition 为 fade 时使用 ffmpeg xfade 与 acrossfade，
    转场会占用两段的重叠时长，因此期望总时长需要相应减去；
  * 所有输入均已统一为 720x1280 / 24fps / AAC 24kHz 单声道（见 ffmpeg.py）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from . import ffmpeg as mf

TRANSITIONS = ("cut", "fade")
FADE_SEC = 0.5


def build_intro(cover_png: Path, out: Path, seconds: float) -> Path:
    """封面片段：静帧缓慢推近，静音音轨（不借用正片音轨）。"""
    return mf.still_clip(cover_png, out, seconds)


def expected_duration(durations: List[float], intro_sec: float, transition: str) -> float:
    total = sum(durations) + intro_sec
    if transition == "fade" and len(durations) > 1:
        total -= FADE_SEC * (len(durations) - 1)
    return round(total, 2)


def _concat_fade(clips: List[Path], durs: List[float], out: Path) -> Path:
    """逐段 xfade 叠化。输入全部同规格，音频用 acrossfade 保持同步。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    args: List[str] = [mf.ffmpeg(), "-y"]
    for c in clips:
        args += ["-i", str(c)]
    vf_parts, af_parts = [], []
    v_prev, a_prev = "[0:v]", "[0:a]"
    offset = 0.0
    for i in range(1, len(clips)):
        offset += durs[i - 1] - FADE_SEC
        v_out, a_out = f"[v{i}]", f"[a{i}]"
        vf_parts.append(f"{v_prev}[{i}:v]xfade=transition=fade:duration={FADE_SEC}:offset={offset:.3f}{v_out}")
        af_parts.append(f"{a_prev}[{i}:a]acrossfade=d={FADE_SEC}{a_out}")
        v_prev, a_prev = v_out, a_out
    fc = ";".join(vf_parts + af_parts)
    args += ["-filter_complex", fc, "-map", v_prev, "-map", a_prev,
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-ar", str(mf.AUDIO_RATE), "-ac", "1", str(out)]
    mf._run(args)
    return out


def compose_final(chapter_files: List[Path], out: Path, *, cover_png: Optional[Path] = None,
                  cover_sec: float = 3.0, transition: str = "cut") -> Dict[str, Any]:
    if not chapter_files:
        raise mf.MediaError("没有可合成的章节视频")
    if transition not in TRANSITIONS:
        raise mf.MediaError(f"未知转场：{transition}")
    for c in chapter_files:
        if not c.is_file():
            raise mf.MediaError(f"缺少章节视频：{c}")
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent / ".final_work"
    work.mkdir(exist_ok=True)
    try:
        clips: List[Path] = []
        intro_sec = 0.0
        if cover_png is not None:
            intro = build_intro(cover_png, work / "intro.mp4", cover_sec)
            clips.append(intro)
            intro_sec = mf.probe_duration(intro)
        clips += chapter_files
        durs = [mf.probe_duration(c) for c in clips]
        # 转场只发生在章节之间；封面与第一章之间始终硬切
        n_intro = 1 if intro_sec else 0
        ch_durs = durs[n_intro:]
        if transition == "fade" and len(chapter_files) > 1:
            # 先把各章叠化为一段，再与封面硬拼接
            body = work / "body.mp4"
            _concat_fade(chapter_files, ch_durs, body)
            parts = ([clips[0]] if n_intro else []) + [body]
            mf.concat(parts, out)
        else:
            mf.concat(clips, out)
        actual = mf.probe_duration(out)
        expect = expected_duration(ch_durs, intro_sec, transition)
        return {"output": str(out), "duration": round(actual, 2), "expected": expect,
                "intro_sec": round(intro_sec, 2), "segments": len(clips), "transition": transition}
    finally:
        for p in work.glob("*"):
            p.unlink(missing_ok=True)
        try:
            work.rmdir()
        except OSError:
            pass
