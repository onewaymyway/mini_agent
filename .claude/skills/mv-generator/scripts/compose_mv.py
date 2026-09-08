#!/usr/bin/env python3
"""MV 最终合成脚本（逐场景独立缩放版）。

核心逻辑：
1. 按 scene_plan.yaml 规划时长，每个 scene 独立缩放
   - clip 比规划短时 -> 慢放
   - clip 比规划长时 -> 快放
2. 字幕按 lyrics_timed.json 绝对时间显示（与 mp3 同步）
3. 音频使用原始 mp3

用法：
    python compose_mv.py \
        --clips-dir clips \
        --scene-plan scene_plan.yaml \
        --lyrics-timed lyrics_timed.json \
        --audio song.mp3 \
        --output mv.mp4 \
        --title "歌名"
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

FFMPEG = r"C:\Users\onewa\.conda\envs\mv_env\Library\bin\ffmpeg.exe"
FFPROBE = r"C:\Users\onewa\.conda\envs\mv_env\Library\bin\ffprobe.exe"
FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"命令失败:\n{' '.join(cmd)}\nstderr:\n{result.stderr[-3000:]}")
    return result


def get_dur(path):
    r = subprocess.run(
        [FFPROBE, "-v", "quiet", "-print_format", "json",
         "-show_format", str(path)],
        capture_output=True, text=True
    )
    return float(json.loads(r.stdout)["format"]["duration"])


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
        # 匹配该 scene 的所有 clips（如 scene_01, scene_07a, scene_07b）
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


def main():
    parser = argparse.ArgumentParser(description="MV 最终合成（逐场景独立缩放）")
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
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="mv_compose_"))
    print(f"Work dir: {workdir}")

    # ── 1. 解析 scene_plan ─────────────────────────────────────────
    scenes = parse_scene_plan(args.scene_plan, args.clips_dir)
    print(f"Loaded {len(scenes)} scenes from scene_plan.yaml")
    for s in scenes:
        print(f"  {s['id']}: {s['start']:.1f}s-{s['end']:.1f}s (target={s['target_dur']:.1f}s), "
              f"clips={[c.name for c in s['clips']]}")

    # ── 2. 逐场景独立缩放 ──────────────────────────────────────────
    scaled_dir = workdir / "scaled"
    scaled_dir.mkdir()
    total_scaled_dur = 0.0

    for scene in scenes:
        for i, clip in enumerate(scene["clips"]):
            clip_dur = get_dur(clip)
            per_clip_target = scene["target_dur"] / len(scene["clips"])
            scale = per_clip_target / clip_dur
            out_name = f"{scene['id']}_c{i+1:02d}.mp4"
            out_path = scaled_dir / out_name
            _run([
                FFMPEG, "-y",
                "-i", str(clip),
                "-vf", f"fps={args.target_fps},setpts={scale}*PTS",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-an",
                str(out_path),
            ])
            scaled_dur = get_dur(out_path)
            total_scaled_dur += scaled_dur
            print(f"  {clip.name} ({clip_dur:.2f}s) -> {out_name} ({scaled_dur:.2f}s, scale={scale:.3f}x)")

    print(f"Total scaled duration: {total_scaled_dur:.1f}s")

    # ── 3. 拼接所有缩放后的 clip ───────────────────────────────────
    list_file = workdir / "concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for p in sorted(scaled_dir.glob("*.mp4"), key=lambda x: x.name):
            f.write(f"file '{p.as_posix()}'\n")

    joined = workdir / "joined.mp4"
    _run([
        FFMPEG, "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-vf", f"scale={args.target_size}:force_original_aspect_ratio=decrease,pad={args.target_size}:(ow-iw)/2:(oh-ih)/2,fps={args.target_fps}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-an",
        str(joined),
    ])
    joined_dur = get_dur(joined)
    print(f"Joined: {joined_dur:.1f}s")

    # ── 4. 生成字幕（按 lyrics_timed.json 绝对时间，逐帧渲染）────────
    from PIL import Image, ImageDraw, ImageFont
    W, H = [int(x) for x in args.target_size.split(":")]
    font = ImageFont.truetype(FONT_PATH, args.font_size)
    subs_dir = workdir / "subs"
    subs_dir.mkdir(exist_ok=True)

    lyrics_data = json.load(open(args.lyrics_timed, "r", encoding="utf-8"))
    lines = lyrics_data.get("lines", [])
    total_frames = int(joined_dur * args.target_fps)
    print(f"Generating {total_frames} subtitle frames ({len(lines)} lyric lines)")

    # 计算每行歌词的显示时长（对齐到帧）
    frame_dur = 1.0 / args.target_fps
    for i, item in enumerate(lines):
        item["start_frame"] = int(item["start"] / frame_dur)
        item["end_frame"] = int(item["end"] / frame_dur)

    # 逐帧渲染
    rendered = 0
    for frame_idx in range(total_frames):
        t = frame_idx * frame_dur
        # 找到当前时间对应的歌词行
        active_text = ""
        for item in lines:
            if item["start_frame"] <= frame_idx < item["end_frame"]:
                active_text = item.get("text", "")
                break

        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        if active_text:
            if hasattr(draw, "textlength"):
                tw = draw.textlength(active_text, font=font)
            else:
                tw = sum(font.getlength(c) for c in active_text)
            bw = 12
            bg_w = int(tw) + bw * 2
            bg_h = args.font_size + 16
            y = H - args.overlay_y_offset - bg_h
            x = (W - bg_w) // 2
            draw.rectangle([x, y, x + bg_w, y + bg_h], fill=(0, 0, 0, 180))
            draw.text((x + bw, y + 6), active_text, font=font, fill=(255, 255, 255, 255))
        png_path = subs_dir / f"{frame_idx:05d}.png"
        img.save(png_path, "PNG")
        rendered += 1
        if rendered % 500 == 0:
            print(f"  Rendered {rendered}/{total_frames} frames...")

    # concat 字幕 PNG 为视频
    sub_list = workdir / "subs_list.txt"
    with open(sub_list, "w", encoding="utf-8") as f:
        for idx in range(total_frames):
            f.write(f"file '{(subs_dir / f'{idx:05d}.png').as_posix()}'\n")
            f.write(f"duration {frame_dur}\n")

    subs_video = workdir / "subs_video.mp4"
    _run([
        FFMPEG, "-y",
        "-f", "concat", "-safe", "0", "-i", str(sub_list),
        "-pix_fmt", "rgba", "-r", str(args.target_fps),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        str(subs_video),
    ])

    # overlay 字幕：直接用 PNG 帧序列叠加到主视频（避免二次编码损失）
    video_with_subs = workdir / "video_with_subs.mp4"
    png_pattern = (subs_dir / "%05d.png").as_posix()
    _run([
        FFMPEG, "-y",
        "-i", str(joined),
        "-i", png_pattern,
        "-filter_complex", "[0:v][1:v]overlay=0:0[out]", "-map", "[out]",
        "-c:v", "libx264", "-preset", "slow", "-crf", "14",
        "-an",
        str(video_with_subs),
    ])

    # ── 5. 添加歌名水印（PIL 方式，避免 drawtext 路径问题） ──
    if args.title:
        from PIL import Image, ImageDraw, ImageFont
        W, H = [int(x) for x in args.target_size.split(":")]
        wm_font = ImageFont.truetype(FONT_PATH, args.font_size)
        
        # 提取主视频帧，叠加水印，重新编码
        wm_input = video_with_subs
        watermarked = workdir / "watermarked.mp4"
        
        # 用 ffmpeg 提取帧序列
        extract_cmd = [
            FFMPEG, "-y",
            "-i", str(wm_input),
            "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2",
            "-q:v", "2",
            str(workdir / "frames\%05d.jpg")
        ]
        _run(extract_cmd)
        
        # PIL 添加水印并保存
        wm_dir = workdir / "wm_output_frames"
        wm_dir.mkdir()
        frame_files = sorted((workdir / "frames").glob("*.jpg"))
        print(f"Processing {len(frame_files)} frames for watermark...")
        
        for idx, frame_file in enumerate(frame_files):
            img = Image.open(frame_file).convert("RGB")
            draw = ImageDraw.Draw(img)
            text = args.title
            bbox = draw.textbbox((0, 0), text, font=wm_font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x = W - tw - 10
            y = 10
            draw.rectangle([x-5, y-5, x+tw+5, y+th+5], fill=(0, 0, 0, 150))
            draw.text((x, y), text, font=wm_font, fill=(255, 255, 255))
            img.save(wm_dir / frame_file.name, quality=95)
            if (idx + 1) % 500 == 0:
                print(f"  Watermarked {idx+1}/{len(frame_files)} frames...")
        
        # 重新编码带水印的视频
        wm_list = workdir / "wm_list.txt"
        with open(wm_list, "w") as f:
            for p in sorted(wm_dir.glob("*.jpg")):
                f.write(f"file '{p.as_posix()}'\n")
                f.write(f"duration {1.0/args.target_fps}\n")
        
        _run([
            FFMPEG, "-y",
            "-f", "concat", "-safe", "0", "-i", str(wm_list),
            "-r", str(args.target_fps),
            "-c:v", "libx264", "-preset", "slow", "-crf", "14",
            str(watermarked),
        ])
        video_with_subs = watermarked
        print(f"Title watermark added: '{args.title}' at top-right")

    # ── 6. 混入原始 mp3 音轨 ──────────────────────────────────────
    print(f"Mixing audio: {args.audio}")
    _run([
        FFMPEG, "-y",
        "-i", str(video_with_subs),
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
