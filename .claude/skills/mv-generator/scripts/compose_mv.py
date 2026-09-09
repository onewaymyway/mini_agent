#!/usr/bin/env python3
"""MV 最终合成脚本（逐场景独立缩放版 v2：修复标题乱码 + 大幅提速）。

核心逻辑（不变）：
1. 按 scene_plan.yaml 规划时长，每个 scene 独立缩放
   - clip 比规划短时 -> 慢放；比规划长时 -> 快放
2. 字幕按 lyrics_timed.json 的绝对时间显示（与 mp3 同步，不受 clip 缩放影响）
3. 音频使用原始 mp3

本版相对上一版的两个关键修复：
1. **歌名水印乱码/方框**：不再用 ffmpeg `drawtext`（Windows 下字体路径转义
   极易出错，出错时 ffmpeg 会静默退化到内置字体，该字体不含中文字形，
   于是中文全部显示成方框）。改为和歌词字幕一样，用 PIL 渲染成透明 PNG，
   再用 `overlay` 叠加，字体来源与字幕完全一致，不存在转义问题。
2. **合成速度慢**：上一版对每一帧都单独渲染一张 PNG 再逐帧 concat
   （3-4 分钟视频在 24fps 下就是几千张图 + 几千行 concat 列表），
   且中间还多编码了一份从未被使用的 `subs_video.mp4`。新版改为
   **按"歌词分句"渲染**：每一句歌词（含句间空白）只渲染一张 PNG，
   用 ffmpeg concat demuxer 的 `duration` 字段控制每张图的显示时长
   （支持逐张不同时长），相同文本复用同一张图。一首 20-40 句的歌，
   图片数量从"几千张"降到"几十张"，且删除了未使用的中间编码步骤，
   字幕叠加 + 歌名水印合并成同一次 `filter_complex` 一次性完成，
   整体合成步骤从 ~6 次全量编码减少到 ~3 次。

3. 可选：**封面效果**（`--cover-image`/`--cover-duration`）。做法是把封面图
   做成一段轻微推近的短片，"挤压/替换"掉第一个场景 clip 的前 N 秒，剩余
   部分原样接回——不插入新片段、不改变视频总时长，因此不影响任何后续
   场景的时间轴/字幕对齐。时长会被 clamp 到第一个场景规划时长的 50% 以内。

用法：
    python compose_mv.py \
        --clips-dir clips \
        --scene-plan scene_plan.yaml \
        --lyrics-timed lyrics_timed.json \
        --audio song.mp3 \
        --output mv.mp4 \
        --title "歌名" \
        --cover-image assets/cover.png --cover-duration 3
"""

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
    # imageio_ffmpeg returns a named binary like ffmpeg-win-x86_64-v7.1.exe
    # so replace 'ffmpeg' with 'ffprobe' in the basename, not just '.exe'
    _imgio_dir = Path(_IMGIO_FFMPEG).parent
    _imgio_name = Path(_IMGIO_FFMPEG).name.replace('ffmpeg', 'ffprobe')
    _FFPROBE_CANDIDATES.insert(0, str(_imgio_dir / _imgio_name))
except ImportError:
    pass
FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"


def _resolve_bin(candidates):
    for c in candidates:
        if Path(c).exists():
            return c
    return candidates[-1]


FFMPEG = _resolve_bin(_FFMPEG_CANDIDATES)
FFPROBE = _resolve_bin(_FFPROBE_CANDIDATES)


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"命令失败:\n{' '.join(cmd)}\nstderr:\n{result.stderr[-3000:]}")
    return result


def get_dur(path):
    # 优先使用 ffprobe，若不存在则用 ffmpeg 解析 stderr 获取时长
    probe_cmd = [FFPROBE, "-v", "quiet", "-print_format", "json",
                 "-show_format", str(path)]
    r = subprocess.run(probe_cmd, capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return float(json.loads(r.stdout)["format"]["duration"])
    # fallback: 使用 ffmpeg 获取时长
    r2 = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True)
    for line in r2.stderr.split('\n'):
        if 'Duration' in line:
            # 格式: Duration: 00:04:06.14, start: ..., bitrate: ...
            import re
            m = re.search(r'Duration: (\d+):(\d+):(\d+)\.(\d+)', line)
            if m:
                h, m2, s, ms = m.groups()
                return int(h)*3600 + int(m2)*60 + int(s) + int(ms)/100
    raise RuntimeError("无法获取音频时长")


def parse_scene_plan(plan_path, clips_dir):
    """解析 scene_plan.yaml，返回 scene 列表（含规划时长和匹配到的 clips）。"""
    with open(plan_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    scenes = []
    for s in data.get("scenes", []):
        scene_id = s["id"]
        start = s["start"]
        end = s["end"]
        target_dur = end - start
        matched = sorted(list(Path(clips_dir).glob(scene_id + "*.mp4")),
                        key=lambda p: p.name)
        scenes.append({
            "id": scene_id,
            "start": start,
            "end": end,
            "target_dur": target_dur,
            "clips": matched,
            "lyric_lines": s.get("lyric_lines", []),
        })
    return scenes


def _build_cues(lines, total_dur, frame_dur):
    """把逐句歌词转换成"按帧对齐、首尾相接、覆盖 [0, total_dur)"的显示队列。

    返回 [{"text": str, "start": float, "end": float}, ...]，其中
    text 为空串代表这一段没有歌词显示（黑场之间的间隙）。
    """
    def snap(t):
        return round(round(t / frame_dur) * frame_dur, 6)

    cues = []
    cursor = 0.0
    for item in lines:
        start = snap(max(item["start"], cursor))
        end = snap(max(item["end"], start + frame_dur))
        if start > cursor:
            cues.append({"text": "", "start": cursor, "end": start})
        cues.append({"text": item.get("text", ""), "start": start, "end": end})
        cursor = end
    total_dur = snap(total_dur)
    if cursor < total_dur:
        cues.append({"text": "", "start": cursor, "end": total_dur})
    return [c for c in cues if c["end"] > c["start"]]


def _render_text_png(text, W, H, font, args, path):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if text:
        draw = ImageDraw.Draw(img)
        if hasattr(draw, "textlength"):
            tw = draw.textlength(text, font=font)
        else:
            tw = sum(font.getlength(c) for c in text)
        bw = 12
        bg_w = int(tw) + bw * 2
        bg_h = args.font_size + 16
        y = H - args.overlay_y_offset - bg_h
        x = (W - bg_w) // 2
        draw.rectangle([x, y, x + bg_w, y + bg_h], fill=(0, 0, 0, 180))
        draw.text((x + bw, y + 6), text, font=font, fill=(255, 255, 255, 255))
    img.save(path, "PNG")


def _render_title_png(title, W, H, font, pos, path):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if hasattr(draw, "textlength"):
        tw = draw.textlength(title, font=font)
    else:
        tw = sum(font.getlength(c) for c in title)
    pad = 10
    bw = 8
    bg_w = int(tw) + bw * 2
    bg_h = font.size + 12
    if pos == "top-left":
        x, y = pad, pad
    elif pos == "top-right":
        x, y = W - pad - bg_w, pad
    elif pos == "bottom-left":
        x, y = pad, H - pad - bg_h
    else:
        x, y = W - pad - bg_w, H - pad - bg_h
    draw.rectangle([x, y, x + bg_w, y + bg_h], fill=(0, 0, 0, 140))
    draw.text((x + bw, y + 6), title, font=font, fill=(255, 255, 255, 255))
    img.save(path, "PNG")


def apply_cover(scaled_dir, first_scene, cover_image, cover_duration, target_size,
                 target_fps, preset_scale):
    """把封面图做成一小段"呼吸感"短片，替换掉 first_scene 的第一个 clip 的
    前若干秒，剩余部分原样保留、无缝接回。不新增总时长，只是"挤压/替换"，
    所以不会影响任何后续场景的时间轴、字幕对齐。

    返回实际使用的封面时长（clamp 后的值），失败/跳过时返回 0。
    """
    if not first_scene["clips"]:
        print("  [警告] 第一个场景没有匹配到 clip，跳过封面处理", file=sys.stderr)
        return 0.0

    target_clip = scaled_dir / f"{first_scene['id']}_c01.mp4"
    if not target_clip.exists():
        print(f"  [警告] 找不到 {target_clip.name}，跳过封面处理", file=sys.stderr)
        return 0.0

    clip_dur = get_dur(target_clip)
    # clamp：不超过场景规划时长的 50%，也不能吃光整个 clip 的实际时长
    max_allowed = min(first_scene["target_dur"] * 0.5, clip_dur - 0.2)
    cover_dur = max(0.0, min(cover_duration, max_allowed))
    if cover_dur <= 0.3:
        print(f"  [警告] 封面可用时长过短（clamp 后仅 {cover_dur:.2f}s），跳过封面处理", file=sys.stderr)
        return 0.0

    W, H = [int(x) for x in target_size.split(":")]
    frame_count = max(1, int(round(cover_dur * target_fps)))

    workdir = target_clip.parent.parent  # scaled_dir 的上一级临时目录
    cover_seg = workdir / "cover_seg.mp4"
    remainder = workdir / "cover_remainder.mp4"
    replaced = workdir / "cover_replaced.mp4"

    # 封面短片：轻微缓慢推近（zoompan），避免"死板静帧"的观感
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

    # 原 clip 的剩余部分（cover_dur 秒之后到结尾），无缝接回
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

    # 用拼好的结果原地替换第一个场景的第一个 clip 文件，
    # 这样后续第 3 步的拼接逻辑完全不用改，天然会用上封面
    shutil.move(str(replaced), str(target_clip))
    print(f"  Cover applied: {cover_image} ({cover_dur:.2f}s) + remainder of "
          f"{target_clip.name} ({clip_dur - cover_dur:.2f}s) -> {target_clip.name} "
          f"(total unchanged: {clip_dur:.2f}s)")
    return cover_dur


def main():
    parser = argparse.ArgumentParser(description="MV 最终合成（逐场景独立缩放，v2 提速+修复标题乱码）")
    parser.add_argument("--clips-dir", required=True)
    parser.add_argument("--scene-plan", required=True)
    parser.add_argument("--lyrics-timed", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--target-size", default="1280:720")
    parser.add_argument("--target-fps", type=int, default=24)
    parser.add_argument("--font-size", type=int, default=28)
    parser.add_argument("--overlay-y-offset", type=int, default=80)
    parser.add_argument("--title", default=None)
    parser.add_argument("--title-pos", default="top-right",
                        choices=["top-left", "top-right", "bottom-left", "bottom-right"])
    parser.add_argument("--preset-scale", default="veryfast",
                        help="逐 clip 缩放阶段的 ffmpeg preset，追求速度用 veryfast/ultrafast")
    parser.add_argument("--preset-final", default="medium",
                        help="最终叠加/输出阶段的 ffmpeg preset")
    parser.add_argument("--cover-image", default=None,
                        help="封面图路径（scene_plan.yaml 里 cover.asset_path）。"
                             "提供后会用它替换第一个场景 clip 的前若干秒，"
                             "做成'MV 前几秒是封面'的效果，不改变视频总时长")
    parser.add_argument("--cover-duration", type=float, default=3.0,
                        help="封面展示时长（秒），会被 clamp 到第一个场景规划时长的 50%% 以内")
    args = parser.parse_args()

    if not HAS_YAML:
        print("缺少 pyyaml，请先 pip install pyyaml", file=sys.stderr)
        sys.exit(1)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="mv_compose_"))
    print(f"Work dir: {workdir}")

    # ── 0.5. 修复歌词时间戳（去除空隙）─────────────────────────────
    lyrics_data = json.load(open(args.lyrics_timed, "r", encoding="utf-8"))
    lines = lyrics_data.get("lines", [])

    audio_dur = get_dur(Path(args.audio))

    for i in range(len(lines) - 1):
        lines[i]["end"] = lines[i + 1]["start"]
    if lines:
        lines[-1]["end"] = audio_dur
    lyrics_data["duration"] = audio_dur

    print(f"Lyrics fixed: {len(lines)} lines, total duration: {audio_dur:.2f}s")
    with open(args.lyrics_timed, "w", encoding="utf-8") as f:
        json.dump(lyrics_data, f, ensure_ascii=False, indent=2)
    print(f"  (Overwrote {args.lyrics_timed})")

    # ── 1. 解析 scene plan ─────────────────────────────────────────
    scenes = parse_scene_plan(args.scene_plan, args.clips_dir)
    print(f"Loaded {len(scenes)} scenes from scene_plan.yaml")
    for s in scenes:
        print(f"  {s['id']}: {s['start']:.1f}s-{s['end']:.1f}s (target={s['target_dur']:.1f}s), "
              f"clips={[c.name for c in s['clips']]}")

    # ── 2. 逐场景独立缩放（画面节奏严格按 scene_plan.yaml）──────────
    scaled_dir = workdir / "scaled"
    scaled_dir.mkdir()
    total_scaled_dur = 0.0

    for scene in scenes:
        if not scene["clips"]:
            print(f"  [警告] {scene['id']} 没有匹配到任何 clip 文件，已跳过", file=sys.stderr)
            continue
        for i, clip in enumerate(scene["clips"]):
            clip_dur = get_dur(clip)
            per_clip_target = scene["target_dur"] / len(scene["clips"])
            scale = per_clip_target / clip_dur
            out_name = f"{scene['id']}_c{i+1:02d}.mp4"
            out_path = scaled_dir / out_name
            _run([
                FFMPEG, "-y",
                "-i", str(clip),
                "-vf", (f"fps={args.target_fps},setpts={scale}*PTS,"
                        f"scale={args.target_size}:force_original_aspect_ratio=decrease,"
                        f"pad={args.target_size}:(ow-iw)/2:(oh-ih)/2"),
                "-c:v", "libx264", "-preset", args.preset_scale, "-crf", "20",
                "-an",
                str(out_path),
            ])
            scaled_dur = get_dur(out_path)
            total_scaled_dur += scaled_dur
            print(f"  {clip.name} ({clip_dur:.2f}s) -> {out_name} ({scaled_dur:.2f}s, scale={scale:.3f}x)")

    print(f"Total scaled duration: {total_scaled_dur:.1f}s")

    # ── 2.5. 封面（若提供 --cover-image）：挤压/替换第一个场景的前 N 秒 ──
    if args.cover_image:
        if not Path(args.cover_image).exists():
            print(f"  [警告] --cover-image 指定的文件不存在: {args.cover_image}，跳过封面处理", file=sys.stderr)
        else:
            first_scene = min(scenes, key=lambda s: s["start"])
            apply_cover(
                scaled_dir, first_scene, args.cover_image, args.cover_duration,
                args.target_size, args.target_fps, args.preset_scale,
            )

    # ── 3. 拼接所有缩放后的 clip（分辨率已在上一步统一，这里纯拼接不再重算 scale）──
    list_file = workdir / "concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for p in sorted(scaled_dir.glob("*.mp4"), key=lambda x: x.name):
            f.write(f"file '{p.as_posix()}'\n")

    joined = workdir / "joined.mp4"
    _run([
        FFMPEG, "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c:v", "libx264", "-preset", args.preset_scale, "-crf", "20",
        "-an",
        str(joined),
    ])
    joined_dur = get_dur(joined)
    print(f"Joined: {joined_dur:.1f}s")

    # ── 4. 按歌词分句渲染字幕 PNG（每句一张图，而非每帧一张）────────
    from PIL import ImageFont
    W, H = [int(x) for x in args.target_size.split(":")]
    font = ImageFont.truetype(FONT_PATH, args.font_size)
    subs_dir = workdir / "subs"
    subs_dir.mkdir(exist_ok=True)

    lyrics_data = json.load(open(args.lyrics_timed, "r", encoding="utf-8"))
    lines = lyrics_data.get("lines", [])
    frame_dur = 1.0 / args.target_fps
    cues = _build_cues(lines, joined_dur, frame_dur)
    print(f"Rendering {len(cues)} subtitle cues (was: one PNG per frame in v1)")

    text_to_png = {}
    sub_list = workdir / "subs_list.txt"
    with open(sub_list, "w", encoding="utf-8") as f:
        for i, cue in enumerate(cues):
            text = cue["text"]
            if text not in text_to_png:
                png_path = subs_dir / f"cue_{len(text_to_png):04d}.png"
                _render_text_png(text, W, H, font, args, png_path)
                text_to_png[text] = png_path
            dur = round(cue["end"] - cue["start"], 6)
            f.write(f"file '{text_to_png[text].as_posix()}'\n")
            f.write(f"duration {dur}\n")
        f.write(f"file '{text_to_png[cues[-1]['text']].as_posix()}'\n")
    print(f"  {len(text_to_png)} unique PNGs written (dedup by text)")

    # ── 5. 歌名水印也渲染成 PNG（不用 drawtext，避免 Windows 路径转义问题）──
    title_input_args = []
    filter_chain = "[0:v][1:v]overlay=0:0:shortest=1[v1]"
    map_out = "[v1]"
    if args.title:
        title_png = workdir / "title.png"
        _render_title_png(args.title, W, H, font, args.title_pos, title_png)
        title_input_args = ["-loop", "1", "-i", str(title_png)]
        filter_chain += ";[v1][2:v]overlay=0:0:shortest=1[v2]"
        map_out = "[v2]"
        print(f"Title watermark PNG rendered: '{args.title}' at {args.title_pos}")

    # ── 6. 一次性完成：字幕叠加 + 歌名水印叠加（合并成单次 filter_complex）──
    video_with_overlays = workdir / "video_with_overlays.mp4"
    cmd = [
        FFMPEG, "-y",
        "-i", str(joined),
        "-f", "concat", "-safe", "0", "-i", str(sub_list),
        *title_input_args,
        "-filter_complex", filter_chain,
        "-map", map_out,
        "-t", f"{joined_dur:.3f}",
        "-c:v", "libx264", "-preset", args.preset_final, "-crf", "16",
        "-an",
        str(video_with_overlays),
    ]
    _run(cmd)

    # ── 7. 混入原始 mp3 音轨 ──────────────────────────────────────
    print(f"Mixing audio: {args.audio}")
    _run([
        FFMPEG, "-y",
        "-i", str(video_with_overlays),
        "-i", args.audio,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output),
    ])

    final_dur = get_dur(output)
    final_size_mb = output.stat().st_size // 1024 // 1024
    print(f"\nFinal MV: {output}")
    print(f"  Duration: {final_dur:.1f}s")
    print(f"  Size: {final_size_mb}MB")
    shutil.rmtree(workdir, ignore_errors=True)
    print("Cleaned up temp dir")


if __name__ == "__main__":
    main()
