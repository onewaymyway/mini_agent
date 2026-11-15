"""media/motion.py — 动态漫模式的镜内动效（C-8b）。

单张场景图 + 配音 → 一段带动效的片段（画面即场景图，不做视频生成）。
- 动效滤镜由纯函数生成（zoom_filter），便于用字符串做 golden 测试。
- 先把场景图放大到 2 倍再做 zoompan，减少缩放抖动。
- S4：auto_alternate（推近/拉远交替）、push_in、pull_out、none。
- S5：平移 pan_left / pan_right / pan_up / pan_down（画面缩放固定，取景框沿可移动余量线性移动），
  晃动 drift（取景框沿余量做正弦摆动，幅度不超出余量）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import ffmpeg as mf

MOTIONS = ("auto_alternate", "push_in", "pull_out", "none",
           "pan_left", "pan_right", "pan_up", "pan_down", "drift")
ZOOM_MOTIONS = ("push_in", "pull_out", "none")
PAN_MOTIONS = ("pan_left", "pan_right", "pan_up", "pan_down")
STRENGTHS = {"slow": 0.08, "medium": 0.15}   # 放大比例增量：slow 约 8%，medium 约 15%；平移与晃动的可移动余量由它决定
UPSCALE = 2                                   # 先放大到 2 倍再裁剪缩放，减少抖动
DRIFT_SWING = 0.6                             # 晃动幅度占余量的比例（取景框在余量的 20%–80% 之间摆动）


def resolve_motion(name: str, index: int) -> str:
    """把设置里的动效名解析为单个镜头实际使用的动效。auto_alternate 按镜头序号交替：偶数推近、奇数拉远。"""
    if name == "auto_alternate":
        return "push_in" if index % 2 == 0 else "pull_out"
    if name in MOTIONS:
        return name
    raise ValueError(f"未知的动效：{name!r}，可选：{', '.join(MOTIONS)}")


def zoom_filter(motion: str, *, frames: int, w: int, h: int, fps: int, strength: str = "slow") -> str:
    """生成动效的 ffmpeg 滤镜链（输入为单张静态图，每帧由 zoompan 输出）。"""
    if motion not in ZOOM_MOTIONS + PAN_MOTIONS + ("drift",):
        raise ValueError(f"该动效不支持：{motion!r}")
    if strength not in STRENGTHS:
        raise ValueError(f"未知的动效强度：{strength!r}，可选：{', '.join(STRENGTHS)}")
    zmax = 1 + STRENGTHS[strength]
    step = (zmax - 1) / max(frames, 1)
    n = max(frames - 1, 1)                      # 最后一帧的序号，用于把位置映射到 0–1
    slack_x = "(iw-iw/zoom)"                    # 放大后画面中可水平移动的余量
    slack_y = "(ih-ih/zoom)"
    x = "iw/2-(iw/zoom/2)"                      # 默认居中
    y = "ih/2-(ih/zoom/2)"
    if motion == "push_in":
        z = f"min(zoom+{step:.6f},{zmax:.4f})"
    elif motion == "pull_out":
        z = f"max({zmax:.4f}-{step:.6f}*on,1)"
    elif motion == "none":
        z = "1"
    elif motion in PAN_MOTIONS:
        z = f"{zmax:.4f}"                       # 平移时缩放固定，只改变位置
        if motion == "pan_left":
            x = f"{slack_x}*(1-on/{n})"
        elif motion == "pan_right":
            x = f"{slack_x}*on/{n}"
        elif motion == "pan_up":
            y = f"{slack_y}*(1-on/{n})"
        else:                                   # pan_down
            y = f"{slack_y}*on/{n}"
    else:                                       # drift：轻微正弦晃动
        z = f"{zmax:.4f}"
        x = f"{slack_x}/2*(1+{DRIFT_SWING}*sin(on*0.12))"
        y = f"{slack_y}/2*(1+{DRIFT_SWING}*cos(on*0.09))"
    uw, uh = w * UPSCALE, h * UPSCALE
    return (f"scale={uw}:{uh}:force_original_aspect_ratio=increase,crop={uw}:{uh},"
            f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={w}x{h}:fps={fps},setsar=1")


def render_segment(still: Path, audio: Optional[Path], out: Path, *, seconds: float, motion: str,
                   strength: str, w: int, h: int, fps: int) -> Path:
    """场景图 + 配音（无配音则静音）→ 片段。时长由调用方给定（配音时长 + 余量），不做裁剪。"""
    frames = max(1, round(seconds * fps))
    vf = zoom_filter(motion, frames=frames, w=w, h=h, fps=fps, strength=strength)
    out.parent.mkdir(parents=True, exist_ok=True)
    if audio is None:
        audio_in = ["-f", "lavfi", "-i", f"anullsrc=r={mf.AUDIO_RATE}:cl=mono"]
    else:
        audio_in = ["-i", str(audio)]
    mf._run([mf.ffmpeg(), "-y", "-loop", "1", "-framerate", str(fps), "-i", str(still), *audio_in,
             "-map", "0:v:0", "-map", "1:a:0",
             "-vf", vf, "-af", "apad", "-t", f"{seconds:.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-ar", str(mf.AUDIO_RATE), "-ac", "1", str(out)])
    return out
