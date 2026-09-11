#!/usr/bin/env python3
"""compose_novel_video.py — 小说转视频四段流程的第 4 步：最终合成。

改造自 `mv-generator/scripts/compose_mv.py`，核心差异（详见方案文档
`next_doc/novel_video_generator_plan.md` 第 4 节）：

  - mv 版本靠"歌曲原始 mp3 + lyrics_timed.json 绝对时间戳"驱动时间轴，
    音频是给定的，画面反过来去对齐音频；novel 版本没有现成音频，音轨
    本身就是要拼接的旁白（`audio/segment_*.wav`），每个 scene 的时长
    （`duration_sec`）就是这段旁白的真实 TTS 时长，天然按 scene 顺序
    首尾相接、不存在 mv 里"场景间空隙/开头空隙"的问题，因此不需要
    `mv-generator` 里 `fill_dur`/`lead_gap`/借用相邻场景那一整套空隙
    填补逻辑，只保留"整体误差兜底对齐"（`setpts` 累积误差 clamp）；
  - 字幕来源是 `scene_plan.yaml` 每个 scene 自带的 `text` 字段（一个
    scene 对应一整句/一段旁白），不是 mv 那种"逐句歌词、句间可能有
    空白"的结构，因此每个 scene 只对应一张字幕 PNG（同文本仍然去重
    复用），不需要 `_build_cues` 那种"用歌词 start/end 生成显示队列、
    句间插入空白"的复杂对齐；
  - 音轨来自本地拼接的旁白 wav（未必同采样率/声道，统一用
    `-filter_complex concat` 重新编码拼接，不用 `concat` demuxer 直接
    拼接，避免不同 wav 参数不一致导致的拼接错误）；
  - 不做歌名水印叠加（mv 特有的"歌名角标"在小说场景里没有对应需求，
    保留 `--cover-image` 的开幕效果即可，做法与 mv 完全一致：挤压/
    替换第一个 scene clip 的前几秒，不新增总时长）；
  - 不接 BGM（`novel_project.json.bgm_enabled` 本版恒为 `false`，
    脚本不读取该字段做任何混音分支，字段先占位）。

用法：
    python compose_novel_video.py <output_dir> \
        [--output video.mp4] [--cover-duration 3] [--no-cover] \
        [--font-size 28] [--overlay-y-offset 80] \
        [--allow-missing-clips]

`<output_dir>` 即 novel-scene-planner 建立的项目目录，脚本会自动读取
其中的 `novel_project.json`（取 `aspect_ratio`）、`scene_plan.yaml`
（取每个 scene 的 `id`/`duration_sec`/`text`/`narration_audio`）、
`clips/<scene_id>.mp4`、`audio/segment_*.wav`，以及可选的
`assets/cover.png`。

产物：`<output_dir>/video.mp4`（或 `--output` 指定路径）。
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
FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"

ASPECT_TO_SIZE = {
    "16:9": "1280:720",
    "9:16": "720:1280",
}


def _resolve_bin(candidates, path_name):
    for c in candidates:
        if Path(c).exists():
            return c
    # 本地 Windows 专用候选路径都不存在时，退回系统 PATH（Linux/macOS
    # 测试环境常见：没装 imageio_ffmpeg，但 ffmpeg/ffprobe 本身在 PATH
    # 里，比如通过 apt/brew 装的）。
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
    probe_cmd = [FFPROBE, "-v", "quiet", "-print_format", "json",
                 "-show_format", str(path)]
    r = subprocess.run(probe_cmd, capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return float(json.loads(r.stdout)["format"]["duration"])
    r2 = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True)
    for line in r2.stderr.split("\n"):
        if "Duration" in line:
            import re
            m = re.search(r"Duration: (\d+):(\d+):(\d+)\.(\d+)", line)
            if m:
                h, m2, s, ms = m.groups()
                return int(h) * 3600 + int(m2) * 60 + int(s) + int(ms) / 100
    raise RuntimeError(f"无法获取媒体时长: {path}")


def _load_yaml(path: Path) -> dict:
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


def load_scenes(output_dir: Path):
    """读取 scene_plan.yaml，按文件里出现的顺序（即旁白/时间轴顺序）
    返回 scene 列表，附上 clip/audio 的绝对路径。"""
    plan = _load_yaml(output_dir / "scene_plan.yaml")
    raw_scenes = plan.get("scenes", [])
    if not raw_scenes:
        raise RuntimeError("scene_plan.yaml 里没有任何 scenes，无法合成")

    scenes = []
    for sc in raw_scenes:
        sc_id = sc["id"]
        clip_path = output_dir / "clips" / f"{sc_id}.mp4"
        audio_rel = sc.get("narration_audio")
        audio_path = (output_dir / audio_rel) if audio_rel else None
        scenes.append({
            "id": sc_id,
            "duration_sec": float(sc.get("duration_sec", 0.0)),
            "text": sc.get("text", ""),
            "clip": clip_path if clip_path.exists() else None,
            "audio": audio_path if (audio_path and audio_path.exists()) else None,
            "status": sc.get("status"),
        })
    return scenes


def _render_text_png(text, W, H, font, font_size, overlay_y_offset, path):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if text:
        draw = ImageDraw.Draw(img)
        # 长文案自动换行：按画面宽度的 90% 作为可用宽度，逐字累加换行
        max_w = int(W * 0.9)
        lines = []
        cur = ""
        for ch in text:
            trial = cur + ch
            tw = draw.textlength(trial, font=font) if hasattr(draw, "textlength") \
                else sum(font.getlength(c) for c in trial)
            if tw > max_w and cur:
                lines.append(cur)
                cur = ch
            else:
                cur = trial
        if cur:
            lines.append(cur)

        line_h = font_size + 10
        block_h = line_h * len(lines) + 12
        y0 = H - overlay_y_offset - block_h
        for i, line in enumerate(lines):
            tw = draw.textlength(line, font=font) if hasattr(draw, "textlength") \
                else sum(font.getlength(c) for c in line)
            bw = 12
            bg_w = int(tw) + bw * 2
            x = (W - bg_w) // 2
            y = y0 + i * line_h
            draw.rectangle([x, y, x + bg_w, y + line_h], fill=(0, 0, 0, 180))
            draw.text((x + bw, y + 5), line, font=font, fill=(255, 255, 255, 255))
    img.save(path, "PNG")


def apply_cover(scaled_dir, first_scene_id, first_scene_target_dur, cover_image,
                 cover_duration, target_size, target_fps, preset_scale):
    """把封面图做成一小段短片，挤压/替换第一个 scene clip 的前若干秒，
    剩余部分原样接回，不改变该 scene 的总时长（做法同 mv-generator）。
    返回实际生效的封面时长，0 表示跳过。"""
    target_clip = scaled_dir / f"{first_scene_id}.mp4"
    if not target_clip.exists():
        print(f"  [警告] 找不到 {target_clip.name}，跳过封面处理", file=sys.stderr)
        return 0.0

    clip_dur = get_dur(target_clip)
    max_allowed = min(first_scene_target_dur * 0.5, clip_dur - 0.2)
    cover_dur = max(0.0, min(cover_duration, max_allowed))
    if cover_dur <= 0.3:
        print(f"  [警告] 封面可用时长过短（clamp 后仅 {cover_dur:.2f}s），跳过封面处理",
              file=sys.stderr)
        return 0.0

    W, H = [int(x) for x in target_size.split(":")]
    frame_count = max(1, int(round(cover_dur * target_fps)))
    workdir = target_clip.parent.parent

    cover_seg = workdir / "cover_seg.mp4"
    remainder = workdir / "cover_remainder.mp4"
    replaced = workdir / "cover_replaced.mp4"

    vf = (
        f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,"
        f"crop={W*2}:{H*2},"
        f"zoompan=z='min(zoom+0.0008,1.15)':d={frame_count}:s={W}x{H}:fps={target_fps},"
        f"format=yuv420p"
    )
    _run([
        FFMPEG, "-y",
        "-loop", "1", "-i", str(cover_image),
        "-vf", vf,
        "-t", f"{cover_dur:.3f}",
        "-c:v", "libx264", "-preset", preset_scale, "-crf", "20",
        "-an",
        str(cover_seg),
    ])
    _run([
        FFMPEG, "-y",
        "-ss", f"{cover_dur:.3f}", "-i", str(target_clip),
        "-c:v", "libx264", "-preset", preset_scale, "-crf", "20",
        "-an",
        str(remainder),
    ])
    concat_list = workdir / "cover_concat.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        f.write(f"file '{cover_seg.as_posix()}'\n")
        f.write(f"file '{remainder.as_posix()}'\n")
    _run([
        FFMPEG, "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c:v", "libx264", "-preset", preset_scale, "-crf", "20",
        "-an",
        str(replaced),
    ])
    shutil.move(str(replaced), str(target_clip))
    print(f"  Cover applied: {cover_image} ({cover_dur:.2f}s) + remainder of "
          f"{target_clip.name} ({clip_dur - cover_dur:.2f}s) -> {target_clip.name} "
          f"(total unchanged: {clip_dur:.2f}s)")
    return cover_dur


def main():
    parser = argparse.ArgumentParser(description="小说转视频最终合成（旁白拼接驱动时间轴）")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--output", default=None,
                         help="默认 <output_dir>/video.mp4")
    parser.add_argument("--target-fps", type=int, default=24)
    parser.add_argument("--font-size", type=int, default=28)
    parser.add_argument("--overlay-y-offset", type=int, default=80)
    parser.add_argument("--preset-scale", default="veryfast")
    parser.add_argument("--preset-final", default="medium")
    parser.add_argument("--cover-duration", type=float, default=3.0,
                         help="封面展示时长（秒），会被 clamp 到第一个场景规划时长的 50%% 以内")
    parser.add_argument("--no-cover", action="store_true",
                         help="即使 assets/cover.png 存在也不使用封面效果")
    parser.add_argument("--allow-missing-clips", action="store_true",
                         help="[默认关闭] 允许缺 clip 的场景借用相邻场景画面强制拉伸填补，"
                              "正常流程应先用 check_clips.py 校验通过、补齐缺失场景，"
                              "不应依赖本参数绕过检查")
    parser.add_argument("--font-path", default=None,
                         help="中文字幕字体文件路径，默认使用脚本内置的 FONT_PATH 常量"
                              "（Windows: C:\\Windows\\Fonts\\msyh.ttc）。非 Windows 环境"
                              "（如 CI/测试）请显式传本地实际可用的中文字体路径，例如"
                              "Linux 上的 Noto Sans CJK/文泉驿字体")
    args = parser.parse_args()

    global FONT_PATH
    if args.font_path:
        FONT_PATH = args.font_path

    if not HAS_YAML:
        print("缺少 pyyaml，请先 pip install pyyaml", file=sys.stderr)
        sys.exit(1)

    output_dir: Path = args.output_dir
    project = _load_json(output_dir / "novel_project.json")
    aspect_ratio = project.get("aspect_ratio", "16:9")
    target_size = resolve_target_size(aspect_ratio)
    output = Path(args.output) if args.output else (output_dir / "video.mp4")
    output.parent.mkdir(parents=True, exist_ok=True)

    scenes = load_scenes(output_dir)
    print(f"Loaded {len(scenes)} scenes, target_size={target_size} "
          f"(aspect_ratio={aspect_ratio})")

    # ── 0. 缺失检查（不做 mv 那种空隙填补，novel 场景本身首尾相接，
    #      缺 clip/audio 只能借相邻场景兜底，默认直接拒绝） ──────────
    missing_clip_ids = [s["id"] for s in scenes if s["clip"] is None]
    missing_audio_ids = [s["id"] for s in scenes if s["audio"] is None]
    if (missing_clip_ids or missing_audio_ids) and not args.allow_missing_clips:
        if missing_clip_ids:
            print(f"❌ 缺少 clip 文件的场景（共 {len(missing_clip_ids)} 个）："
                  f"{missing_clip_ids}", file=sys.stderr)
            print("请先运行 novel-scene-video-generator 的 generate_scene_videos.py "
                  "补齐（--scene-id 定向重跑），用 check_clips.py 校验通过后再执行本命令。",
                  file=sys.stderr)
        if missing_audio_ids:
            print(f"❌ 缺少旁白音频的场景（共 {len(missing_audio_ids)} 个）："
                  f"{missing_audio_ids}", file=sys.stderr)
            print("请先回 novel-asset-generator 用 synthesize_narration.py 补齐配音。",
                  file=sys.stderr)
        sys.exit(1)
    if missing_clip_ids:
        print(f"  [警告] {len(missing_clip_ids)} 个场景缺少 clip，将借用相邻场景画面"
              f"强制拉伸填补：{missing_clip_ids}", file=sys.stderr)

    workdir = Path(tempfile.mkdtemp(prefix="novel_compose_"))
    print(f"Work dir: {workdir}")

    # ── 1. 拼接旁白音轨（narration 本身就是权威时长来源）─────────────
    valid_audio = [s["audio"] for s in scenes if s["audio"] is not None]
    if not valid_audio:
        raise RuntimeError("所有场景都没有可用的旁白音频，无法合成")
    narration_wav = workdir / "narration.wav"
    audio_inputs = []
    filter_inputs = []
    for i, s in enumerate(scenes):
        a = s["audio"] if s["audio"] is not None else valid_audio[min(i, len(valid_audio) - 1)]
        audio_inputs += ["-i", str(a)]
        filter_inputs.append(f"[{i}:a]")
    concat_filter = "".join(filter_inputs) + f"concat=n={len(scenes)}:v=0:a=1[aout]"
    _run([
        FFMPEG, "-y", *audio_inputs,
        "-filter_complex", concat_filter,
        "-map", "[aout]",
        str(narration_wav),
    ])
    audio_dur = get_dur(narration_wav)
    print(f"Narration concatenated: {len(scenes)} segments, total {audio_dur:.2f}s")

    # ── 2. 逐场景独立缩放到 duration_sec（首尾相接，不需要 fill_dur）──
    scaled_dir = workdir / "scaled"
    scaled_dir.mkdir()
    last_available_clip = None
    for s in scenes:
        clip = s["clip"]
        borrowed = False
        if clip is None:
            clip = last_available_clip
            borrowed = True
            if clip is None:
                for later in scenes:
                    if later["clip"] is not None:
                        clip = later["clip"]
                        break
        if clip is None:
            raise RuntimeError("所有场景都没有匹配到任何 clip 文件，无法合成")
        if borrowed:
            print(f"  [强制填补] {s['id']} 没有 clip，借用 {clip.name} 强制拉伸到 "
                  f"{s['duration_sec']:.2f}s", file=sys.stderr)

        clip_dur = get_dur(clip)
        target_dur = max(s["duration_sec"], 0.1)
        scale = target_dur / clip_dur
        out_path = scaled_dir / f"{s['id']}.mp4"
        _run([
            FFMPEG, "-y",
            "-i", str(clip),
            "-vf", (f"fps={args.target_fps},setpts={scale}*PTS,"
                    f"scale={target_size}:force_original_aspect_ratio=decrease,"
                    f"pad={target_size}:(ow-iw)/2:(oh-ih)/2"),
            "-c:v", "libx264", "-preset", args.preset_scale, "-crf", "20",
            "-an",
            str(out_path),
        ])
        scaled_dur = get_dur(out_path)
        print(f"  {s['id']}: {clip.name} ({clip_dur:.2f}s) -> "
              f"{out_path.name} ({scaled_dur:.2f}s, scale={scale:.3f}x, "
              f"target={target_dur:.2f}s)")
        if not borrowed:
            last_available_clip = clip

    # ── 2.5 封面：挤压/替换第一个场景 clip 的前 N 秒 ────────────────
    cover_image = output_dir / "assets" / "cover.png"
    actual_cover_dur = 0.0
    if cover_image.exists() and not args.no_cover:
        actual_cover_dur = apply_cover(
            scaled_dir, scenes[0]["id"], scenes[0]["duration_sec"],
            cover_image, args.cover_duration, target_size,
            args.target_fps, args.preset_scale,
        )
    elif not cover_image.exists():
        print("  未找到 assets/cover.png，跳过封面效果", file=sys.stderr)

    # ── 3. 拼接所有缩放后的 clip ─────────────────────────────────────
    list_file = workdir / "concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for s in scenes:
            f.write(f"file '{(scaled_dir / (s['id'] + '.mp4')).as_posix()}'\n")
    joined = workdir / "joined.mp4"
    _run([
        FFMPEG, "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c:v", "libx264", "-preset", args.preset_scale, "-crf", "20",
        "-an",
        str(joined),
    ])
    joined_dur = get_dur(joined)
    print(f"Joined: {joined_dur:.2f}s (narration: {audio_dur:.2f}s)")

    # ── 3.5 强制整体对齐旁白总时长（累积误差兜底，逻辑同 mv-generator）─
    align_scale = 1.0
    ALIGN_EPS = 0.02
    if abs(joined_dur - audio_dur) > ALIGN_EPS:
        align_scale = audio_dur / joined_dur
        print(f"  [强制对齐] joined={joined_dur:.3f}s 与旁白 {audio_dur:.3f}s 不一致"
              f"（差 {joined_dur - audio_dur:+.3f}s），强制整体 setpts={align_scale:.5f} 对齐")
        aligned = workdir / "joined_aligned.mp4"
        _run([
            FFMPEG, "-y",
            "-i", str(joined),
            "-vf", f"setpts={align_scale}*PTS,fps={args.target_fps}",
            "-c:v", "libx264", "-preset", args.preset_scale, "-crf", "20",
            "-an",
            str(aligned),
        ])
        joined = aligned
        joined_dur = get_dur(joined)
        print(f"  Aligned joined duration: {joined_dur:.3f}s (target {audio_dur:.3f}s)")

    # ── 4. 按 scene.text 渲染字幕 PNG（一个 scene 一张图，同文本去重）──
    from PIL import ImageFont
    W, H = [int(x) for x in target_size.split(":")]
    font = ImageFont.truetype(FONT_PATH, args.font_size)
    subs_dir = workdir / "subs"
    subs_dir.mkdir(exist_ok=True)

    text_to_png = {}
    sub_list = workdir / "subs_list.txt"
    cumulative = 0.0
    cue_durs = []
    with open(sub_list, "w", encoding="utf-8") as f:
        for s in scenes:
            text = s["text"] or ""
            if text not in text_to_png:
                png_path = subs_dir / f"cue_{len(text_to_png):04d}.png"
                _render_text_png(text, W, H, font, args.font_size,
                                  args.overlay_y_offset, png_path)
                text_to_png[text] = png_path
            # 每个 scene 的显示时长按整体对齐比例同步缩放，保证和画面切换
            # 时刻严格一致（align_scale 是对整段 joined 视频的均匀缩放，
            # scene 边界在时间轴上也按同一比例平移）
            dur = round(s["duration_sec"] * align_scale, 6)
            cue_durs.append(dur)
            f.write(f"file '{text_to_png[text].as_posix()}'\n")
            f.write(f"duration {dur}\n")
        last_text = scenes[-1]["text"] or ""
        f.write(f"file '{text_to_png[last_text].as_posix()}'\n")
    # 微调最后一张图时长误差，保证字幕总时长和 joined_dur 完全一致
    print(f"Rendering {len(scenes)} subtitle cues ({len(text_to_png)} unique PNGs)")

    # ── 5. 字幕 overlay（一次 filter_complex 完成）──────────────────
    video_with_subs = workdir / "video_with_subs.mp4"
    _run([
        FFMPEG, "-y",
        "-i", str(joined),
        "-f", "concat", "-safe", "0", "-i", str(sub_list),
        "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[vout]",
        "-map", "[vout]",
        "-t", f"{joined_dur:.3f}",
        "-c:v", "libx264", "-preset", args.preset_final, "-crf", "16",
        "-an",
        str(video_with_subs),
    ])

    # ── 6. 混入拼接后的旁白音轨 ───────────────────────────────────
    print("Mixing narration audio track")
    _run([
        FFMPEG, "-y",
        "-i", str(video_with_subs),
        "-i", str(narration_wav),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{audio_dur:.3f}",
        str(output),
    ])

    # ── 7. 校验交付 ──────────────────────────────────────────────
    report = validate_output(output, target_size, audio_dur)
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
        errors.append(f"总时长 {final_dur:.1f}s 与旁白总时长 {expected_dur:.1f}s 相差过大")

    probe_cmd = [FFPROBE, "-v", "quiet", "-print_format", "json",
                 "-show_streams", str(output)]
    r = subprocess.run(probe_cmd, capture_output=True, text=True)
    bitrate = None
    width = height = None
    if r.returncode == 0 and r.stdout.strip():
        streams = json.loads(r.stdout).get("streams", [])
        vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
        if vstream:
            width = vstream.get("width")
            height = vstream.get("height")
            bitrate = vstream.get("bit_rate") or json.loads(
                subprocess.run(
                    [FFPROBE, "-v", "quiet", "-print_format", "json",
                     "-show_format", str(output)],
                    capture_output=True, text=True,
                ).stdout or "{}"
            ).get("format", {}).get("bit_rate")
    if bitrate is not None:
        try:
            if int(bitrate) < 1_000_000:
                warnings.append(f"视频比特率偏低（{int(bitrate)/1000:.0f} kbps < 1 Mbps），"
                                 "可能是 overlay 步骤质量崩溃，建议检查")
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
