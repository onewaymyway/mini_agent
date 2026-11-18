"""media/ffmpeg.py — 所有视频/音频处理统一走这里。

统一输出参数：画面尺寸与帧率由项目的视频格式决定（默认横屏 1280x720、24fps），H.264 + AAC 单声道 24kHz。
每个小场景片段都带音轨（无对白时为静音），保证拼接时流结构一致。
"""

from __future__ import annotations

import subprocess
import os
import wave
from pathlib import Path
from typing import List, Optional, Sequence

from . import binaries
from .. import cancel

W, H, FPS = 1280, 720, 24          # 默认横屏 720P 24fps；实际值由 apply_format() 按项目设置改写


def apply_format(fmt) -> None:
    """把当前项目的视频格式（横屏/竖屏、分辨率、帧率）写入合成参数。进入任何媒体合成前调用。"""
    global W, H, FPS
    W, H, FPS = int(fmt.width), int(fmt.height), int(fmt.fps)


def use_project_format(project_dir) -> None:
    from ..video_format import load
    apply_format(load(project_dir))
AUDIO_RATE = 24000


class MediaError(RuntimeError):
    pass


WIN_CMD_LIMIT = 32767   # Windows CreateProcess 命令行总长上限（字符）


def _launch_error(args: Sequence[str], cwd, exc: OSError) -> str:
    """ffmpeg 无法启动时的诊断信息。Windows 上命令行超长会报“文件名或扩展名太长”（WinError 206），
    这条信息与文件名无关，因此这里把命令行长度、参数个数和输出路径一并写出来。"""
    cmd_len = len(subprocess.list2cmdline([str(a) for a in args]))
    hint = ""
    if getattr(exc, "winerror", None) == 206 or cmd_len > WIN_CMD_LIMIT:
        hint = (f"（命令行 {cmd_len} 字符，超过 Windows 上限 {WIN_CMD_LIMIT}；"
                "滤镜应走 -filter_complex_script，输入应使用相对路径）")
    out = str(args[-1]) if args else ""
    return (f"{Path(args[0]).name} 无法启动：{exc}{hint}。"
            f"参数 {len(args)} 个，工作目录 {cwd or '未指定'}，输出 {out}")


def _run(args: Sequence[str], cwd=None) -> str:
    # 经 cancel.run_process 运行：用户请求停止时立即终止 ffmpeg，而不是等它跑完
    try:
        returncode, stdout, stderr = cancel.run_process(list(args), cwd=cwd)
    except OSError as exc:
        raise MediaError(_launch_error(args, cwd, exc)) from exc
    if returncode != 0:
        tail = (stderr or "")[-400:]
        raise MediaError(f"{Path(args[0]).name} 失败：{tail.strip()}")
    return stdout


def ffmpeg() -> str:
    """ffmpeg 路径，统一由 media.binaries 查找（环境变量 → imageio_ffmpeg → conda → PATH）。"""
    try:
        return binaries.find_ffmpeg()
    except binaries.BinaryNotFound as exc:
        raise MediaError(str(exc)) from exc


def ffprobe() -> str:
    """ffprobe 路径，统一由 media.binaries 查找。"""
    try:
        return binaries.find_ffprobe()
    except binaries.BinaryNotFound as exc:
        raise MediaError(str(exc)) from exc


def probe_duration(path: Path) -> float:
    out = _run([ffprobe(), "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", str(path)])
    try:
        return float(out.strip().splitlines()[0])
    except (ValueError, IndexError) as exc:
        raise MediaError(f"无法读取时长：{path.name}") from exc


def write_silence_wav(path: Path, seconds: float, rate: int = AUDIO_RATE) -> float:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(round(seconds * rate)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * frames)
    return frames / rate


def _scale_filter() -> str:
    return (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},setsar=1,fps={FPS}")


def still_clip(image: Path, out: Path, seconds: float) -> Path:
    """静帧缓慢推近，生成带静音轨的片段（离线 stub 视频用）。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([ffmpeg(), "-y", "-loop", "1", "-i", str(image),
          "-f", "lavfi", "-i", f"anullsrc=r={AUDIO_RATE}:cl=mono",
          "-t", f"{seconds:.3f}",
          "-vf", _scale_filter() + ",zoompan=z='min(zoom+0.0008,1.12)':d=1:"
                 f"s={W}x{H}:fps={FPS}",
          "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-ar", str(AUDIO_RATE), "-ac", "1", str(out)])
    return out


def fit_clip(video: Path, audio: Path, out: Path) -> float:
    """把生成的画面缩放到配音的真实时长（同 novel-video-studio：setpts 快放或慢放），音频保持原样。
    返回画面的缩放系数：<1 为快放，>1 为慢放。时长差几秒可以接受，这里只做对齐，不做截断。"""
    v_dur = probe_duration(video)
    a_dur = probe_duration(audio)
    if v_dur <= 0 or a_dur <= 0:
        raise MediaError(f"无法对齐时长：画面 {v_dur:.2f}s，配音 {a_dur:.2f}s")
    scale = a_dur / v_dur
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([ffmpeg(), "-y", "-i", str(video), "-i", str(audio),
          "-map", "0:v:0", "-map", "1:a:0",
          "-vf", f"setpts={scale:.6f}*PTS," + _scale_filter(),
          "-af", "apad", "-t", f"{a_dur:.3f}",
          "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-ar", str(AUDIO_RATE), "-ac", "1", str(out)])
    return scale


def mux_clip(video: Path, audio: Optional[Path], out: Path, seconds: float) -> Path:
    """把视频统一到目标尺寸/帧率，替换为给定音频（无音频则静音），并裁到 seconds。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    if audio is None:
        audio_in = ["-f", "lavfi", "-i", f"anullsrc=r={AUDIO_RATE}:cl=mono"]
        audio_map = ["-map", "1:a:0"]
    else:
        audio_in = ["-i", str(audio)]
        audio_map = ["-map", "1:a:0"]
    _run([ffmpeg(), "-y", "-i", str(video), *audio_in,
          "-map", "0:v:0", *audio_map,
          "-vf", _scale_filter(),
          "-af", "apad", "-t", f"{seconds:.3f}",
          "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-ar", str(AUDIO_RATE), "-ac", "1", str(out)])
    return out


def concat(clips: List[Path], out: Path) -> Path:
    if not clips:
        raise MediaError("没有可拼接的片段")
    out.parent.mkdir(parents=True, exist_ok=True)
    listing = out.with_suffix(".concat.txt")
    listing.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in clips), encoding="utf-8")
    _run([ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
          "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-ar", str(AUDIO_RATE), "-ac", "1", str(out)])
    listing.unlink(missing_ok=True)
    return out


def _input_layout(paths: Sequence[Path]):
    """返回 (工作目录, 输入参数路径列表)。输入改为相对于公共父目录的短路径，避免命令行超长。
    无法求公共目录（如跨盘符）时退回绝对路径。"""
    resolved = [Path(p).resolve() for p in paths]
    try:
        base = Path(os.path.commonpath([str(p.parent) for p in resolved]))
    except ValueError:
        return None, [str(p) for p in resolved]
    return base, [Path(os.path.relpath(p, base)).as_posix() for p in resolved]


def _run_filter_graph(clips: Sequence[Path], fc: str, out: Path, maps: Sequence[str],
                      codec: Sequence[str]) -> None:
    """用 filter_complex 合成。滤镜图写入临时文件（-filter_complex_script），
    不放在命令行上：片段多时滤镜串很长，会超过 Windows 命令行上限。"""
    cwd, names = _input_layout(clips)
    inputs: List[str] = []
    for n in names:
        inputs += ["-i", n]
    script = out.with_name(out.stem + ".filter.txt").resolve()
    script.write_text(fc, encoding="utf-8")
    try:
        _run([ffmpeg(), "-y", *inputs, "-filter_complex_script", str(script), *maps, *codec, str(out)], cwd=cwd)
    finally:
        script.unlink(missing_ok=True)


_CODEC = ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-ar", str(AUDIO_RATE), "-ac", "1"]
FADE_AUDIO_SEC = 0.02   # 音频只做极短的淡变，防止爆音；不做明显淡化，避免吃掉台词的起头


def concat_fade(clips: List[Path], durations: Sequence[float], out: Path, fade_sec: float) -> Path:
    """逐段淡入淡出（画面从黑场淡入、淡出到黑场）后拼接。时长不变：淡化不重叠，字幕时间轴保持一致。
    淡化时长不超过片段时长的一半。"""
    if not clips:
        raise MediaError("没有可拼接的片段")
    if len(clips) != len(durations):
        raise MediaError("片段数与时长数不一致")
    out.parent.mkdir(parents=True, exist_ok=True)
    n = len(clips)
    parts: List[str] = []
    labels: List[str] = []
    for i, d in enumerate(durations):
        f = min(float(fade_sec), float(d) / 2)
        if f > 0:
            vf = (f"[{i}:v]fade=t=in:st=0:d={f:.3f},"
                  f"fade=t=out:st={max(d - f, 0):.3f}:d={f:.3f},setsar=1[v{i}]")
        else:
            vf = f"[{i}:v]setsar=1[v{i}]"
        af = (f"[{i}:a]afade=t=in:st=0:d={FADE_AUDIO_SEC},"
              f"afade=t=out:st={max(d - FADE_AUDIO_SEC, 0):.3f}:d={FADE_AUDIO_SEC}[a{i}]")
        parts += [vf, af]
        labels += [f"[v{i}]", f"[a{i}]"]
    fc = ";".join(parts) + ";" + "".join(labels) + f"concat=n={n}:v=1:a=1[outv][outa]"
    _run_filter_graph(clips, fc, out, maps=["-map", "[outv]", "-map", "[outa]"], codec=_CODEC)
    return out


def effective_dissolve(durations: Sequence[float], dissolve_sec: float) -> float:
    """实际使用的叠化时长：不超过最短镜头的一半（极短镜头不会被叠化吃掉）。拼接与字幕共用这一个值。"""
    if not durations:
        return 0.0
    return min(float(dissolve_sec), min(float(d) for d in durations) / 2)


def dissolve_timeline(durations: Sequence[float], dissolve_sec: float) -> List[float]:
    """叠化时各镜头在章节时间轴上占用的时长：除最后一个镜头外，每个镜头都让出实际叠化时长
    （下一个镜头从这里开始）。返回值之和即章节总时长 = sum(d) - (n-1) * t。"""
    if not durations:
        return []
    t = effective_dissolve(durations, dissolve_sec)
    out = [float(d) - t for d in durations[:-1]]
    return out + [float(durations[-1])]


def concat_dissolve(clips: List[Path], durations: Sequence[float], out: Path, dissolve_sec: float) -> Path:
    """相邻镜头画面叠化（xfade dissolve）。画面重叠 dissolve_sec 秒；音频把前一个镜头的尾部
    （静音余量）裁掉同样的时长，使每个镜头的配音仍从它在时间轴上的起点开始。
    要求 dissolve_sec 不超过每个镜头的静音余量（见 render_mode.TAIL_MARGIN_SEC），否则会切到台词。"""
    if not clips:
        raise MediaError("没有可拼接的片段")
    if len(clips) != len(durations):
        raise MediaError("片段数与时长数不一致")
    out.parent.mkdir(parents=True, exist_ok=True)
    n = len(clips)
    if n == 1:
        return concat(clips, out)
    t = effective_dissolve(durations, dissolve_sec)
    parts: List[str] = []
    for i in range(n):
        parts.append(f"[{i}:v]setsar=1[v{i}]")
        if i < n - 1:
            parts.append(f"[{i}:a]atrim=0:{float(durations[i]) - t:.3f},asetpts=PTS-STARTPTS[a{i}]")
        else:
            parts.append(f"[{i}:a]asetpts=PTS-STARTPTS[a{i}]")
    cur, length = "[v0]", float(durations[0])
    for k in range(1, n):
        offset = length - t                       # 第 k 个镜头在时间轴上的起点
        label = "[outv]" if k == n - 1 else f"[vx{k}]"
        parts.append(f"{cur}[v{k}]xfade=transition=dissolve:duration={t:.3f}:offset={offset:.3f}{label}")
        cur = label
        length += float(durations[k]) - t
    parts.append("".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[outa]")
    fc = ";".join(parts)
    _run_filter_graph(clips, fc, out, maps=["-map", "[outv]", "-map", "[outa]"], codec=_CODEC)
    return out


def to_wav(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([ffmpeg(), "-y", "-i", str(src), "-ar", str(AUDIO_RATE), "-ac", "1", str(dst)])
    return dst
