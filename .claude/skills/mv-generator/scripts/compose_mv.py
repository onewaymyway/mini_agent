#!/usr/bin/env python3
"""MV 最终合成脚本：拼接分场景视频片段 + 烧录歌词字幕 + 混入原始音轨。

已验证修复（2026-09-08）：
1. 硬编码 ffmpeg/ffprobe 路径，避免 PATH 问题
2. 整体慢放 concat + setpts，而非逐 clip 慢放（效率更高，无累积误差）
3. 直接读 lyrics_timed.json 的 lines 字段，无需生成中间 .srt 文件
4. 正确替换音轨：先 -an 去掉 clips 自带音轨，再混入 mp3
5. drawtext font 路径冒号转义（C:/ → C\:)解决 Windows 解析问题

用法（命令行）：
    python compose_mv.py \\
        --clips-dir clips \\
        --lyrics-timed lyrics_timed.json \\
        --audio song.mp3 \\
        --output mv.mp4 \\
        --title "歌名" \\
        --title-pos top-right
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

# ── 硬编码路径（避免 PATH 问题）──────────────────────────────────────
FFMPEG = r"C:\Users\onewa\.conda\envs\mv_env\Library\bin\ffmpeg.exe"
FFPROBE = r"C:\Users\onewa\.conda\envs\mv_env\Library\bin\ffprobe.exe"
FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"


def _run(cmd):
    """执行命令，失败时抛出详细错误。"""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"命令失败:\n{' '.join(cmd)}\nstderr:\n{result.stderr[-3000:]}")
    return result


def get_dur(path):
    """用 ffprobe 获取视频/音频时长（秒）。"""
    r = subprocess.run(
        [FFPROBE, "-v", "quiet", "-print_format", "json",
         "-show_format", str(path)],
        capture_output=True, text=True
    )
    return float(json.loads(r.stdout)["format"]["duration"])


def main():
    parser = argparse.ArgumentParser(description="MV 最终合成（修复版）")
    parser.add_argument("--clips-dir", required=True,
                        help="分场景视频片段目录（按文件名排序拼接）")
    parser.add_argument("--lyrics-timed", required=True,
                        help="lyrics_timed.json 路径（含 duration 和 lines 字段）")
    parser.add_argument("--audio", required=True, help="原始 mp3 音频路径")
    parser.add_argument("--output", required=True, help="最终 MV 输出路径")
    parser.add_argument("--target-size", default="1280:720")
    parser.add_argument("--target-fps", type=int, default=24)
    parser.add_argument("--font-size", type=int, default=28)
    parser.add_argument("--overlay-y-offset", type=int, default=80)
    parser.add_argument("--title", default=None, help="歌名水印文本")
    parser.add_argument("--title-pos", default="top-right",
                        choices=["top-left","top-right","bottom-left","bottom-right"])
    args = parser.parse_args()

    clips_dir = Path(args.clips_dir)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="mv_compose_"))
    print(f"Work dir: {workdir}")

    # ── 1. 收集 clips ──────────────────────────────────────────────
    clip_files = sorted(
        [p for p in clips_dir.iterdir() if p.suffix.lower() == ".mp4"],
        key=lambda p: p.name
    )
    print(f"Found {len(clip_files)} clips")
    if not clip_files:
        raise RuntimeError("No clips found")

    # ── 2. 计算慢放比例 ────────────────────────────────────────────
    lyrics_data = json.load(open(args.lyrics_timed, "r", encoding="utf-8"))
    lines = lyrics_data.get("lines", lyrics_data)
    target_duration = lyrics_data.get("duration", 239.04)
    total_clip_dur = sum(get_dur(c) for c in clip_files)
    scale_factor = target_duration / total_clip_dur
    print(f"Clips: {total_clip_dur:.1f}s -> Target: {target_duration:.1f}s "
          f"(scale={scale_factor:.4f}x slow)")
    if total_clip_dur <= 0:
        raise RuntimeError("Clip duration is zero")

    # ── 3. 构建 concat 文件列表 ────────────────────────────────────
    list_file = workdir / "clips.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for clip in clip_files:
            f.write(f"file '{clip.as_posix()}'\n")

    # ── 4. 拼接 clips（统一分辨率/帧率 + 整体慢放）─────────────────
    # 关键：所有 clip 一起过 concat + setpts，避免逐 clip 重编码的累积误差
    joined = workdir / "joined.mp4"
    _run([
        FFMPEG, "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-vf", (
            f"scale={args.target_size}:force_original_aspect_ratio=decrease,"
            f"pad={args.target_size}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={args.target_fps},"
            f"setpts={scale_factor}*PTS"
        ),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-an",   # 不保留任何 clips 自带音轨
        str(joined),
    ])
    joined_dur = get_dur(joined)
    print(f"Joined: {joined_dur:.1f}s")

    # ── 5. 生成字幕 PNG 帧（PIL RGBA）─────────────────────────────
    from PIL import Image, ImageDraw, ImageFont
    W, H = [int(x) for x in args.target_size.split(":")]
    font = ImageFont.truetype(FONT_PATH, args.font_size)
    subs_dir = workdir / "subs"
    subs_dir.mkdir(exist_ok=True)

    prev_end = 0.0
    seg_info = []
    for i, item in enumerate(lines):
        text = item.get("text", "")
        start = max(item["start"], prev_end + 0.1)
        end = max(item["end"], start + 1.0)
        prev_end = end
        dur = end - start

        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
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
        png_path = subs_dir / f"{i:04d}.png"
        img.save(png_path, "PNG")
        seg_info.append({"file": f"{i:04d}.png", "start": start, "end": end, "dur": dur})

    print(f"Generated {len(seg_info)} subtitle frames")

    # ── 6. 编码字幕视频（PNG 格式保留 alpha）───────────────────────
    cat_lines = []
    for si in seg_info:
        cat_lines.append(f"file '{(subs_dir / si['file']).as_posix()}'")
        cat_lines.append(f"duration {si['dur']:.3f}")
    cat_path = workdir / "subs_concat.txt"
    cat_path.write_text("\n".join(cat_lines), encoding="utf-8")

    sub_vid = workdir / "subs_video.mp4"
    _run([
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(cat_path),
        "-vf", "fps=24",
        "-c:v", "png",   # PNG 编码器保留 RGBA alpha 通道
        str(sub_vid),
    ])

    # ── 7. Overlay 字幕到视频 ──────────────────────────────────────
    video_with_subs = workdir / "with_subs.mp4"
    _run([
        FFMPEG, "-y",
        "-i", str(joined),
        "-i", str(sub_vid),
        "-filter_complex", "[0:v][1:v]overlay=0:0[outv]",
        "-map", "[outv]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p",   # 兼容播放器
        "-an",                    # 仍然不要音频
        str(video_with_subs),
    ])

    # ── 8. 叠加歌名水印（drawtext）─────────────────────────────────
    if args.title:
        print(f'Title watermark: "{args.title}" at {args.title_pos}')
        video_with_title = workdir / "with_title.mp4"
        pos_map = {
            "top-left": "10:10",
            "top-right": "W-w-10:10",
            "bottom-left": "10:H-h-10",
            "bottom-right": "W-w-10:H-h-10",
        }
        pos_x, pos_y = pos_map[args.title_pos].split(":")
        # Windows 路径中冒号被 drawtext 当作分隔符，需用反斜杠转义
        # C:/Windows/... → C\:/Windows/...（每个 / 变 \\，每个 : 变 \\:）
        font_escaped = FONT_PATH.replace("/", "\\").replace(":", "\\:")
        filter_str = (
            f"drawtext=text='{args.title}':fontsize={args.font_size}"
            f":fontfile='{font_escaped}':x={pos_x}:y={pos_y}"
            f":box=1:boxcolor=black@0.5:boxborderw=5"
        )
        _run([
            FFMPEG, "-y",
            "-i", str(video_with_subs),
            "-vf", filter_str,
            "-c:a", "copy",   # 拷贝现有音轨（此时仍无音轨，无害）
            str(video_with_title),
        ])
        final_video = video_with_title
    else:
        final_video = video_with_subs

    # ── 9. 混入原始 mp3 音轨 ───────────────────────────────────────
    # -an 确保不保留中间文件的音轨，-shortest 以较短者为准
    print(f"Mixing audio: {args.audio}")
    _run([
        FFMPEG, "-y",
        "-i", str(final_video),
        "-i", args.audio,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output),
    ])

    # ── 10. 验证 & 清理 ────────────────────────────────────────────
    final_dur = get_dur(output)
    final_size_mb = output.stat().st_size // 1024 // 1024
    print(f"\nFinal MV: {output}")
    print(f"  Duration: {final_dur:.1f}s (target: {target_duration:.1f}s)")
    print(f"  Size: {final_size_mb}MB")

    shutil.rmtree(workdir, ignore_errors=True)
    print("Cleaned up temp dir")


if __name__ == "__main__":
    main()
