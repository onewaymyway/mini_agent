"""compose/post.py — 成片后期（P6）：拼接 → 主版本（下三分之一条、角标、BGM）→ 最后一步（水印）。

每一步都有输入指纹（key）。改水印只改变最后一步的指纹，前两步直接复用，镜头不重编码、不重渲染。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

from ..media.ffmpeg import AUDIO_RATE  # noqa: F401  (与章音轨同一采样率)
from .config import FPS
from .ffrun import ffmpeg
from .branding import drift_expressions, watermark_xy

ENC_V = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
ENC_A = ["-c:a", "aac", "-b:a", "160k"]


def fingerprint(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _q(path) -> str:
    return str(Path(path).resolve()).replace("'", "'\\''")


def write_concat_list(paths: Sequence[Path], lst: Path) -> None:
    lines = [f"file '{_q(p)}'" for p in paths]
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_still_clip(png: Path, seconds: float, out: Path, canvas) -> None:
    """静帧片段：PNG + 静音音轨（与章音轨同为 24kHz 单声道 AAC，便于流复制拼接）。"""
    ffmpeg(["-loop", "1", "-i", str(png), "-f", "lavfi", "-i", f"anullsrc=r={AUDIO_RATE}:cl=mono",
            "-t", f"{seconds:.3f}", "-vf", f"fps={FPS},format=yuv420p"] + ENC_V + ENC_A
           + ["-ar", str(AUDIO_RATE), str(out)])


def join(seq: Sequence[Tuple[Path, float]], mode: str, d: float, out: Path, lst: Path,
         meta: List[str], holds: Optional[Sequence[float]] = None) -> None:
    """按转场拼接。cut 流复制；fade 先逐段加黑场再流复制；dissolve 用 xfade 整体重编码。

    holds[i]：第 i 段开头是封面（片头卡、章封面、片尾卡）的秒数。封面不做淡入：这段从第一帧起直接显示，
    只保留它结尾的淡出；否则别人预览时看到的会是黑屏。未给出或为 0 的段落保持原有的淡入淡出。
    """
    paths = [p for p, _ in seq]
    durations = [dur for _, dur in seq]
    if mode == "cut" or len(seq) == 1:
        write_concat_list(paths, lst)
        ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy"] + meta + [str(out)])
        return
    if mode == "fade":
        faded = []
        for i, (src, dur) in enumerate(seq):
            dst = lst.parent / f"fade_{i:02d}.mp4"
            hold = float(holds[i]) if holds is not None and i < len(holds) else 0.0
            vin = "" if hold > 0 else f"fade=t=in:st=0:d={d:.3f},"
            ain = "" if hold > 0 else f"afade=t=in:st=0:d={d:.3f},"
            ffmpeg(["-i", str(src),
                    "-vf", f"{vin}fade=t=out:st={dur - d:.3f}:d={d:.3f}",
                    "-af", f"{ain}afade=t=out:st={dur - d:.3f}:d={d:.3f}"]
                   + ENC_V + ENC_A + [str(dst)])
            faded.append(dst)
        write_concat_list(faded, lst)
        ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy"] + meta + [str(out)])
        return
    args: List[str] = []
    for p in paths:
        args += ["-i", str(p)]
    chain: List[str] = []
    vprev, aprev = "[0:v]", "[0:a]"
    cur = durations[0]
    for k in range(1, len(paths)):
        off = cur - d
        chain.append(f"{vprev}[{k}:v]xfade=transition=fade:duration={d:.3f}:offset={off:.3f}[v{k}]")
        chain.append(f"{aprev}[{k}:a]acrossfade=d={d:.3f}[a{k}]")
        vprev, aprev = f"[v{k}]", f"[a{k}]"
        cur = off + durations[k]
    ffmpeg(args + ["-filter_complex", ";".join(chain), "-map", vprev, "-map", aprev] + ENC_V + ENC_A
           + meta + [str(out)])


def master_step(joined: Path, out: Path, total: float, *, lower: Optional[Tuple[Path, float]],
                corner: Optional[Path], bgm: Optional[Tuple[Path, float]], meta: List[str]) -> None:
    """主版本：叠加下三分之一条（淡入淡出、限定时段）、角标，混入 BGM（按配音侧链压低）。"""
    args = ["-i", str(joined)]
    idx = 1
    vparts: List[str] = []
    vlabel = "0:v"
    if lower:
        png, (a, b) = lower[0], lower[1]
        args += ["-loop", "1", "-i", str(png)]
        vparts.append(f"[{idx}:v]format=rgba,fade=t=in:st={a:.3f}:d=0.5:alpha=1,"
                      f"fade=t=out:st={b - 0.5:.3f}:d=0.5:alpha=1[lt]")
        vparts.append(f"[{vlabel}][lt]overlay=enable='between(t,{a:.3f},{b:.3f})'[v{idx}]")
        vlabel = f"v{idx}"
        idx += 1
    if corner:
        args += ["-loop", "1", "-i", str(corner)]
        vparts.append(f"[{vlabel}][{idx}:v]overlay=0:0:format=auto[v{idx}]")
        vlabel = f"v{idx}"
        idx += 1
    amap = "0:a?"
    aparts: List[str] = []
    if bgm:
        bpath, vol = bgm
        args += ["-stream_loop", "-1", "-i", str(bpath)]
        aparts.append(f"[{idx}:a]volume={vol:.3f},afade=t=out:st={max(0.0, total - 2):.3f}:d=2[bg]")
        aparts.append("[bg][0:a]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[duck]")
        aparts.append("[0:a][duck]amix=inputs=2:duration=first:normalize=0[aout]")
        amap = "[aout]"
    fc = ";".join(vparts + aparts)
    cmd = list(args)
    if fc:
        cmd += ["-filter_complex", fc]
    cmd += ["-map", f"[{vlabel}]" if vparts else "0:v", "-map", amap, "-t", f"{total:.3f}"]
    cmd += (ENC_V + ENC_A) if (vparts or bgm) else ["-c", "copy"]
    cmd += meta + [str(out)]
    ffmpeg(cmd)


def watermark_step(master: Path, out: Path, total: float, wm_png: Path, *, mode: str,
                   canvas, size: Tuple[int, int], position: str, margin: float, meta: List[str]) -> None:
    """最后一步：水印叠加（一次重编码）。static 固定；drift 缓慢往复；diagonal_tile 为整幅平铺图。"""
    if mode == "diagonal_tile":
        ov = "overlay=0:0:format=auto"
    elif mode == "drift":
        px, py = drift_expressions(canvas, size, margin)
        ov = f"overlay=x='{px}':y='{py}':format=auto"
    else:
        x, y = watermark_xy(canvas, size, position, margin)
        ov = f"overlay={x}:{y}:format=auto"
    ffmpeg(["-i", str(master), "-loop", "1", "-i", str(wm_png),
            "-filter_complex", f"[0:v][1:v]{ov}[vout]",
            "-map", "[vout]", "-map", "0:a?", "-t", f"{total:.3f}"] + ENC_V + ["-c:a", "copy"] + meta + [str(out)])


def copy_with_meta(src: Path, out: Path, meta: List[str]) -> None:
    ffmpeg(["-i", str(src), "-c", "copy"] + meta + [str(out)])
