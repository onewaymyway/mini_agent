#!/usr/bin/env python3
"""compose_final_video_v2.py — 小说转视频 v2 六段流程的最后一步：把所有
`status: done` 的 `macro_scene_XX/macro_scene_XX.mp4` 按 `macro_scenes.yaml`
顺序拼接成最终 `video.mp4`。

改造自 v1 `compose_novel_video.py`，核心差异（方案文档
`next_doc/novel_video_generator_plan_v2.md` 第 7 节）：
  - 输入单位从 v1 的 scene clip 换成 v2 的"已完整合成好音轨+字幕"的大场景
    视频，本脚本只做"大场景之间"的拼接，不再处理逐场景音轨/字幕（那些
    在 `novel-scene-video-generator` 的 `compose_macro_scene.py` 里已经
    做完）；
  - 新增 `transition_mode`：`cut`（默认，硬切，行为等价于 v1）/ `fade`
    （每两个大场景之间插入一段可配置时长的黑场淡入淡出，会增加总时长）；
  - 封面效果沿用 v1：挤压/替换第一个大场景视频的前几秒，不改变总时长；
  - 仍不接 BGM（`bgm_enabled` 恒 false，理由同 v1/v2 方案）。

用法：
    python compose_final_video_v2.py <output_dir> \
        [--output video.mp4] [--transition-mode cut|fade] \
        [--transition-duration 0.5] [--cover-duration 3] [--no-cover] \
        [--font-path <字体路径>] [--allow-missing-macro-scenes]

产物：<output_dir>/video.mp4（或 --output 指定路径）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

_FFMPEG_CANDIDATES = [
    r"C:\Users\onewa\.conda\envs\mv_env\Library\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
]
_FFPROBE_CANDIDATES = [
    r"C:\Users\onewa\.conda\envs\mv_env\Library\bin\ffprobe.exe",
    r"C:\Program Files\ffmpeg\bin\ffprobe.exe",
    r"C:\ffmpeg\bin\ffprobe.exe",
]
try:
    import imageio_ffmpeg
    _IMGIO_FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
    _FFMPEG_CANDIDATES.insert(0, _IMGIO_FFMPEG)
    _imgio_dir = Path(_IMGIO_FFMPEG).parent
    _imgio_name = Path(_IMGIO_FFMPEG).name.replace("ffmpeg", "ffprobe")
    _FFPROBE_CANDIDATES.insert(0, str(_imgio_dir / _imgio_name))
except ImportError:
    pass

ASPECT_TO_SIZE = {
    "16:9": "1280:720",
    "9:16": "720:1280",
}


def _resolve_bin(candidates, path_name):
    for c in candidates:
        if Path(c).exists():
            return c
    found = shutil.which(path_name)
    if found:
        return found
    return candidates[-1]


FFMPEG = _resolve_bin(_FFMPEG_CANDIDATES, "ffmpeg")
FFPROBE = _resolve_bin(_FFPROBE_CANDIDATES, "ffprobe")


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"命令失败:\n{' '.join(cmd)}\nstderr:\n{result.stderr[-3000:]}")
    return result


def get_dur(path):
    probe_cmd = [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_format", str(path)]
    r = subprocess.run(probe_cmd, capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return float(json.loads(r.stdout)["format"]["duration"])
    raise RuntimeError(f"无法获取媒体时长: {path}")


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_target_size(aspect_ratio: str) -> str:
    if aspect_ratio in ASPECT_TO_SIZE:
        return ASPECT_TO_SIZE[aspect_ratio]
    try:
        wr, hr = [float(x) for x in aspect_ratio.split(":")]
        base_h = 720
        w = int(round(base_h * wr / hr / 2) * 2)
        return f"{w}:{base_h}"
    except Exception:
        return "1280:720"


def load_macro_scenes(output_dir: Path):
    data = _load_yaml(output_dir / "macro_scenes.yaml")
    raw = data.get("macro_scenes", [])
    if not raw:
        raise RuntimeError("macro_scenes.yaml 里没有任何 macro_scenes，无法合成")

    scenes = []
    for m in raw:
        mid = m["id"]
        suffix = mid[len("macro_"):] if mid.startswith("macro_") else mid
        macro_dir = output_dir / f"macro_scene_{suffix}"
        video_path = macro_dir / f"macro_scene_{suffix}.mp4"
        scenes.append({
            "id": mid,
            "title": m.get("title"),
            "video": video_path if video_path.exists() else None,
            "status": m.get("status"),
        })
    return scenes


def normalize_clip(src: Path, dst: Path, target_size: str, fps: int, preset: str) -> None:
    """统一分辨率/帧率/像素格式/音频采样率，避免不同大场景视频（可能由
    不同批次跑出来）参数不完全一致导致后续 filter_complex concat 报错。"""
    W, H = [int(x) for x in target_size.split(":")]
    _run([
        FFMPEG, "-y", "-i", str(src),
        "-vf", (f"fps={fps},scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1"),
        "-ar", "44100", "-ac", "2",
        "-c:v", "libx264", "-preset", preset, "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        str(dst),
    ])


def make_black_silence(dst: Path, target_size: str, duration: float, fps: int, preset: str) -> None:
    W, H = [int(x) for x in target_size.split(":")]
    _run([
        FFMPEG, "-y",
        "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={fps}:d={duration}",
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", preset, "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(dst),
    ])


def apply_fade(src: Path, dst: Path, fade_in: bool, fade_out: bool, fade_dur: float, preset: str) -> None:
    dur = get_dur(src)
    vf_parts = []
    af_parts = []
    if fade_in:
        vf_parts.append(f"fade=t=in:st=0:d={fade_dur:.3f}")
        af_parts.append(f"afade=t=in:st=0:d={fade_dur:.3f}")
    if fade_out:
        st = max(0.0, dur - fade_dur)
        vf_parts.append(f"fade=t=out:st={st:.3f}:d={fade_dur:.3f}")
        af_parts.append(f"afade=t=out:st={st:.3f}:d={fade_dur:.3f}")
    if not vf_parts:
        shutil.copyfile(src, dst)
        return
    _run([
        FFMPEG, "-y", "-i", str(src),
        "-vf", ",".join(vf_parts),
        "-af", ",".join(af_parts),
        "-c:v", "libx264", "-preset", preset, "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        str(dst),
    ])


def apply_cover(video_path: Path, cover_image: Path, cover_duration: float,
                 target_size: str, target_fps: int, preset_scale: str, workdir: Path) -> float:
    clip_dur = get_dur(video_path)
    max_allowed = min(clip_dur * 0.5, clip_dur - 0.2)
    cover_dur = max(0.0, min(cover_duration, max_allowed))
    if cover_dur <= 0.3:
        print(f"  [警告] 封面可用时长过短（clamp 后仅 {cover_dur:.2f}s），跳过封面处理", file=sys.stderr)
        return 0.0

    W, H = [int(x) for x in target_size.split(":")]
    frame_count = max(1, int(round(cover_dur * target_fps)))

    cover_seg = workdir / "cover_seg.mp4"
    remainder = workdir / "cover_remainder.mp4"
    replaced = workdir / "cover_replaced.mp4"

    vf = (
        f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,"
        f"crop={W*2}:{H*2},"
        f"zoompan=z='min(zoom+0.0008,1.15)':d={frame_count}:s={W}x{H}:fps={target_fps},"
        f"format=yuv420p"
    )
    _run([FFMPEG, "-y", "-loop", "1", "-i", str(cover_image), "-vf", vf,
          "-t", f"{cover_dur:.3f}", "-c:v", "libx264", "-preset", preset_scale, "-crf", "20",
          "-an", str(cover_seg)])
    _run([FFMPEG, "-y", "-ss", f"{cover_dur:.3f}", "-i", str(video_path),
          "-c:v", "libx264", "-preset", preset_scale, "-crf", "20", "-an", str(remainder)])
    concat_list = workdir / "cover_concat.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        f.write(f"file '{cover_seg.as_posix()}'\n")
        f.write(f"file '{remainder.as_posix()}'\n")
    _run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
          "-c:v", "libx264", "-preset", preset_scale, "-crf", "20", "-an", str(replaced)])

    # 视频轨换成带封面版本，但原视频的音轨（对话/旁白）要保留、且和新
    # 视频轨对齐（封面只替换画面，不改变整体时长，也不打断音频）
    final = workdir / "cover_applied.mp4"
    _run([FFMPEG, "-y", "-i", str(replaced), "-i", str(video_path),
          "-map", "0:v:0", "-map", "1:a:0",
          "-c:v", "copy", "-c:a", "copy",
          "-shortest", str(final)])
    shutil.move(str(final), str(video_path))
    print(f"  Cover applied: {cover_image} ({cover_dur:.2f}s), total unchanged: {clip_dur:.2f}s")
    return cover_dur


def main():
    parser = argparse.ArgumentParser(description="小说转视频 v2 最终合成（大场景拼接+转场）")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--output", default=None, help="默认 <output_dir>/video.mp4")
    parser.add_argument("--transition-mode", default=None, choices=["cut", "fade"],
                         help="不传则读取 novel_project.json.transition_mode，默认 cut")
    parser.add_argument("--transition-duration", type=float, default=None,
                         help="fade 模式下每次转场的黑场时长（秒），不传则读取 "
                              "novel_project.json.transition_duration_sec，默认 0.5")
    parser.add_argument("--target-fps", type=int, default=24)
    parser.add_argument("--preset-scale", default="veryfast")
    parser.add_argument("--cover-duration", type=float, default=3.0)
    parser.add_argument("--no-cover", action="store_true")
    parser.add_argument("--allow-missing-macro-scenes", action="store_true",
                         help="[默认关闭] 允许跳过还没有合成出 macro_scene_XX.mp4 的大场景，"
                              "正常流程应确保所有 status:done 的大场景都已生成视频")
    parser.add_argument("--font-path", default=None, help="保留参数位（本脚本不渲染字幕），兼容调用方传入")
    args = parser.parse_args()

    if not HAS_YAML:
        print("缺少 pyyaml，请先 pip install pyyaml", file=sys.stderr)
        sys.exit(1)

    output_dir: Path = args.output_dir
    project = _load_json(output_dir / "novel_project.json")
    aspect_ratio = project.get("aspect_ratio", "16:9")
    target_size = resolve_target_size(aspect_ratio)
    transition_mode = args.transition_mode or project.get("transition_mode", "cut")
    transition_dur = args.transition_duration
    if transition_dur is None:
        transition_dur = project.get("transition_duration_sec", 0.5)
    output = Path(args.output) if args.output else (output_dir / "video.mp4")
    output.parent.mkdir(parents=True, exist_ok=True)

    macro_scenes = load_macro_scenes(output_dir)
    print(f"Loaded {len(macro_scenes)} macro scenes, target_size={target_size}, "
          f"transition_mode={transition_mode}"
          + (f", transition_duration={transition_dur}s" if transition_mode == "fade" else ""))

    missing = [m["id"] for m in macro_scenes if m["video"] is None]
    if missing and not args.allow_missing_macro_scenes:
        print(f"❌ 缺少已合成视频的大场景（共 {len(missing)} 个）：{missing}", file=sys.stderr)
        print("请先用 novel-scene-video-generator 的 compose_macro_scene.py 逐个大场景合成，"
              "确认 macro_scenes.yaml 里对应 status 为 done 后再执行本命令。", file=sys.stderr)
        sys.exit(1)
    ready_scenes = [m for m in macro_scenes if m["video"] is not None]
    if not ready_scenes:
        raise RuntimeError("没有任何已合成的大场景视频，无法合成最终视频")
    if missing:
        print(f"  [警告] 跳过 {len(missing)} 个尚未合成的大场景：{missing}", file=sys.stderr)

    not_done = [m["id"] for m in ready_scenes if m["status"] != "done"]
    if not_done:
        print(f"  [警告] 以下大场景视频文件已存在但 status 不是 done：{not_done}（不影响合成，"
              f"建议检查 compose_macro_scene.py 是否正常回写了状态）", file=sys.stderr)

    workdir = Path(tempfile.mkdtemp(prefix="novel_final_compose_"))
    print(f"Work dir: {workdir}")

    # ── 1. 统一规格 ──────────────────────────────────────────────────
    norm_dir = workdir / "normalized"
    norm_dir.mkdir()
    normalized_paths = []
    macro_durations = []
    for m in ready_scenes:
        dst = norm_dir / f"{m['id']}.mp4"
        normalize_clip(m["video"], dst, target_size, args.target_fps, args.preset_scale)
        normalized_paths.append(dst)
        macro_durations.append(get_dur(dst))
    macro_dur_sum = sum(macro_durations)
    print(f"Normalized {len(normalized_paths)} macro videos, total {macro_dur_sum:.2f}s")

    # ── 2. 封面：作用在第一个大场景视频前几秒 ────────────────────────
    cover_image = output_dir / "global" / "assets" / "cover.png"
    if cover_image.exists() and not args.no_cover:
        apply_cover(normalized_paths[0], cover_image, args.cover_duration,
                    target_size, args.target_fps, args.preset_scale, workdir)
    elif not cover_image.exists():
        print("  未找到 global/assets/cover.png，跳过封面效果", file=sys.stderr)

    # ── 3. 拼接（cut 直接拼，fade 插入黑场转场 + 首尾淡入淡出） ────────
    segments = []
    if transition_mode == "fade" and len(normalized_paths) > 1:
        half = transition_dur / 2.0
        for i, p in enumerate(normalized_paths):
            faded = workdir / f"faded_{i:02d}.mp4"
            apply_fade(p, faded, fade_in=(i > 0), fade_out=(i < len(normalized_paths) - 1),
                       fade_dur=half, preset=args.preset_scale)
            segments.append(faded)
            if i < len(normalized_paths) - 1:
                black = workdir / f"black_{i:02d}.mp4"
                make_black_silence(black, target_size, transition_dur, args.target_fps, args.preset_scale)
                segments.append(black)
    else:
        segments = normalized_paths

    list_file = workdir / "concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for s in segments:
            f.write(f"file '{s.as_posix()}'\n")
    _run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
          "-c:v", "libx264", "-preset", args.preset_scale, "-crf", "18",
          "-c:a", "aac", "-b:a", "192k", str(output)])

    expected_dur = macro_dur_sum
    if transition_mode == "fade" and len(normalized_paths) > 1:
        expected_dur += transition_dur * (len(normalized_paths) - 1)

    report = validate_output(output, target_size, expected_dur)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    shutil.rmtree(workdir, ignore_errors=True)
    print(f"\nFinal video: {output}")
    if not report["ok"]:
        sys.exit(1)


def validate_output(output: Path, target_size: str, expected_dur: float) -> dict:
    errors = []
    warnings = []
    W, H = [int(x) for x in target_size.split(":")]

    final_dur = get_dur(output)
    if abs(final_dur - expected_dur) > 2.0:
        errors.append(f"总时长 {final_dur:.1f}s 与预期 {expected_dur:.1f}s（各大场景时长之和"
                      f"[+转场时长]）相差过大")

    probe_cmd = [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_streams", str(output)]
    r = subprocess.run(probe_cmd, capture_output=True, text=True)
    bitrate = None
    width = height = None
    if r.returncode == 0 and r.stdout.strip():
        streams = json.loads(r.stdout).get("streams", [])
        vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
        if vstream:
            width = vstream.get("width")
            height = vstream.get("height")
            fmt = subprocess.run([FFPROBE, "-v", "quiet", "-print_format", "json",
                                   "-show_format", str(output)], capture_output=True, text=True)
            bitrate = vstream.get("bit_rate") or json.loads(fmt.stdout or "{}").get("format", {}).get("bit_rate")
    if bitrate is not None:
        try:
            if int(bitrate) < 1_000_000:
                warnings.append(f"视频比特率偏低（{int(bitrate)/1000:.0f} kbps < 1 Mbps）")
        except (TypeError, ValueError):
            pass
    else:
        warnings.append("未能读取到视频比特率信息")
    if width and height and (int(width) != W or int(height) != H):
        errors.append(f"最终分辨率 {width}x{height} 与目标 {W}x{H} 不一致")

    file_size_mb = output.stat().st_size / 1024 / 1024
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "duration_sec": round(final_dur, 2),
            "expected_duration_sec": round(expected_dur, 2),
            "bitrate_bps": bitrate,
            "resolution": f"{width}x{height}" if width and height else None,
            "file_size_mb": round(file_size_mb, 1),
        },
    }


if __name__ == "__main__":
    main()
