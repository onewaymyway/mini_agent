"""MV 最终合成脚本：拼接分场景视频片段 + 烧录歌词字幕 + 混入原始音轨。

依赖：系统需要安装 ffmpeg 并在 PATH 中可用。
- Windows: `winget install ffmpeg` 或从 https://ffmpeg.org/download.html 下载后加入 PATH
- 安装完成后建议新开一个终端窗口，确保 PATH 生效

用法（命令行）：
    python compose_mv.py \
        --clips-dir clips \
        --lyrics-timed lyrics_timed.json \
        --audio song.mp3 \
        --output mv.mp4

clips 目录里的片段按文件名排序后依次拼接，命名建议
`scene_01.mp4`、`scene_02.mp4` ... 保证排序即为播放顺序。
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List


def _run(cmd: List[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 命令执行失败:\n命令: {' '.join(cmd)}\n错误输出:\n{proc.stderr[-4000:]}")


# 复用 align_lyrics.py 里的 to_srt()，避免重复实现 SRT 时间码格式化逻辑
sys.path.insert(0, str(Path(__file__).parent))
from align_lyrics import to_srt  # noqa: E402


def check_ffmpeg() -> None:
    """检测 ffmpeg 是否可用，不可用时给出清晰的安装提示而不是让 subprocess 裸报错。"""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "未检测到 ffmpeg，请先安装并确保其在系统 PATH 中：\n"
            "  Windows PowerShell: winget install ffmpeg\n"
            "  或从 https://ffmpeg.org/download.html 下载后手动加入 PATH\n"
            "安装完成后请新开一个终端窗口再重试（PATH 需要重新加载）。"
        )


def collect_clips(clips_dir: Path) -> List[Path]:
    clips = sorted(clips_dir.glob("*.mp4"))
    if not clips:
        raise FileNotFoundError(f"{clips_dir} 下没有找到任何 .mp4 片段")
    return clips


def concat_clips(clips: List[Path], target_size: str, target_fps: int, workdir: Path) -> Path:
    """拼接所有分场景片段为一个视频。

    分场景视频来自不同的 gen_video 调用，分辨率/帧率理论上应该一致（都是
    720P），但为了稳妥，拼接前统一重编码成同一分辨率/帧率，避免因个别片段
    参数不一致导致 concat 失败或画面异常。
    """
    normalized_dir = workdir / "normalized"
    normalized_dir.mkdir(parents=True, exist_ok=True)

    normalized_paths = []
    for i, clip in enumerate(clips):
        out_path = normalized_dir / f"{i:03d}_{clip.stem}.mp4"
        _run([
            "ffmpeg", "-y", "-i", str(clip),
            "-vf", f"scale={target_size}:force_original_aspect_ratio=decrease,"
                   f"pad={target_size}:(ow-iw)/2:(oh-ih)/2,fps={target_fps}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-an",  # 分场景片段的原始音轨（如果有）不需要，最终统一用原曲音轨
            str(out_path),
        ])
        normalized_paths.append(out_path)

    concat_list_path = workdir / "concat_list.txt"
    with concat_list_path.open("w", encoding="utf-8") as f:
        for p in normalized_paths:
            # ffmpeg concat demuxer 要求路径里的反斜杠/单引号做转义，这里统一转成正斜杠更省事
            f.write(f"file '{p.as_posix()}'\n")

    concatenated_path = workdir / "concatenated.mp4"
    _run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list_path),
        "-c", "copy",
        str(concatenated_path),
    ])
    return concatenated_path


def burn_subtitles(video_path: Path, srt_path: Path, workdir: Path,
                    font_size: int = 28, overlay_y_offset: int = 80) -> Path:
    """把歌词字幕烧录进画面（hardsub）。

    使用 PIL 生成半透明字幕 PNG，再通过 ffmpeg overlay 合成到视频上。
    这种方式避免了 drawtext 在 Windows 下对字体路径冒号的解析问题，
    同时支持半透明背景，视觉效果更好。
    """
    from PIL import Image, ImageDraw, ImageFont

    # 解析SRT文件内容提取时间戳和字幕文本
    srt_text = srt_path.read_text(encoding="utf-8")
    import re
    pattern = r'(\d+)\s+(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\n([\s\S]*?)(?=\n\s*\d+\s*\n|\Z)'
    matches = re.findall(pattern, srt_text, re.MULTILINE)
    lines = []
    def time_to_sec(t):
        h, m, s_ms = t.split(':')
        s, ms = s_ms.split(',')
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    for _, start_str, end_str, text in matches:
        lines.append({
            "text": text.strip().replace('\n', ' '),
            "start": time_to_sec(start_str),
            "end": time_to_sec(end_str)
        })

    # 获取视频分辨率
    probe = subprocess.run(
        ["ffmpeg", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0",
         str(video_path)],
        capture_output=True, text=True
    )
    parts = probe.stdout.strip().split(',')
    W, H = int(parts[0]), int(parts[1])

    # 加载字体
    font_paths = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
    ]
    font_path = next((p for p in font_paths if Path(p).exists()), font_paths[0])
    try:
        font = ImageFont.truetype(font_path, font_size)
    except OSError:
        font = ImageFont.load_default()
        print(f"警告: 字体 {font_path} 加载失败，使用默认字体", file=sys.stderr)

    # 生成每个字幕的PNG帧
    subs_dir = workdir / "subs_frames"
    subs_dir.mkdir(parents=True, exist_ok=True)

    prev_end = 0
    seg_info = []
    for i, item in enumerate(lines):
        text = item["text"]
        start = max(item["start"], prev_end + 0.1)
        end = max(item["end"], start + 2.0)
        prev_end = end
        dur = end - start

        # 创建带透明背景的RGBA图
        img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # 计算文字宽度
        if hasattr(draw, 'textlength'):
            tw = draw.textlength(text, font=font)
        else:
            # 兼容旧版PIL
            tw = sum(font.getlength(c) for c in text)
        bw = 12
        bg_w = int(tw) + bw * 2
        bg_h = font_size + 16
        y = H - overlay_y_offset - bg_h
        x = (W - bg_w) // 2
        # 半透明黑底 + 白字
        draw.rectangle([x, y, x + bg_w, y + bg_h], fill=(0, 0, 0, 160))
        draw.text((x + bw, y + 6), text, font=font, fill=(255, 255, 255, 255))
        img.save(subs_dir / f'{i:04d}.png', 'PNG')
        seg_info.append({'file': f'{i:04d}.png', 'start': start, 'end': end, 'dur': dur})

    if not seg_info:
        # 无字幕时直接返回原视频
        return video_path

    # 构建 concat txt
    cat_lines = []
    for si in seg_info:
        cat_lines.append(f"file '{(subs_dir / si['file']).as_posix()}'")
        cat_lines.append(f'duration {si["dur"]:.3f}')
    cat_path = workdir / 'subs_concat.txt'
    cat_path.write_text('\n'.join(cat_lines), encoding='utf-8')

    # 编码字幕视频（rgba格式保留alpha通道）
    sub_vid = workdir / 'subs_video.mp4'
    _run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(cat_path),
        "-vf", "fps=25",
        "-c:v", "png",  # png编码器支持rgba输出
        str(sub_vid)
    ])

    # 叠加字幕到原视频
    out_path = workdir / "with_subtitles.mp4"
    _run([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(sub_vid),
        "-filter_complex", "[0:v][1:v]overlay=0:0[outv]",
        "-map", "[outv]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ])
    return out_path


def mux_audio(video_path: Path, audio_path: Path, output_path: Path) -> None:
    """把原始音轨混入视频。若视频时长与音频时长不一致，以较短者为准截断。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_path),
    ])


def compose(
    clips_dir: str,
    lyrics_timed_path: str,
    audio_path: str,
    output_path: str,
    target_size: str = "1280:720",
    target_fps: int = 24,
    font_size: int = 28,
    overlay_y_offset: int = 80,
) -> Path:
    check_ffmpeg()

    clips_dir_p = Path(clips_dir)
    audio_p = Path(audio_path)
    output_p = Path(output_path)

    if not audio_p.exists():
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    lyrics_timed = json.loads(Path(lyrics_timed_path).read_text(encoding="utf-8"))
    srt_text = to_srt(lyrics_timed)

    clips = collect_clips(clips_dir_p)

    with tempfile.TemporaryDirectory(prefix="mv_compose_") as tmp:
        workdir = Path(tmp)

        srt_path = workdir / "lyrics.srt"
        srt_path.write_text(srt_text, encoding="utf-8")

        concatenated = concat_clips(clips, target_size, target_fps, workdir)
        with_subs = burn_subtitles(
            concatenated, srt_path, workdir,
            font_size=font_size,
            overlay_y_offset=overlay_y_offset
        )
        mux_audio(with_subs, audio_p, output_p)

    return output_p


def main():
    parser = argparse.ArgumentParser(description="拼接分场景视频 + 烧录字幕 + 混入原始音轨，输出最终 MV")
    parser.add_argument("--clips-dir", required=True, help="分场景视频片段所在目录（按文件名排序拼接）")
    parser.add_argument("--lyrics-timed", required=True, help="align_lyrics.py 输出的逐句时间戳 JSON")
    parser.add_argument("--audio", required=True, help="原始 mp3 音频路径")
    parser.add_argument("--output", required=True, help="最终 MV 输出路径")
    parser.add_argument("--target-size", default="1280:720", help="统一分辨率，默认 1280:720")
    parser.add_argument("--target-fps", type=int, default=24, help="统一帧率，默认 24")
    parser.add_argument("--font-size", type=int, default=28, help="字幕字号，默认 28")
    parser.add_argument("--overlay-y-offset", type=int, default=80, help="字幕距底部偏移像素，默认80")
    args = parser.parse_args()

    try:
        output = compose(
            clips_dir=args.clips_dir,
            lyrics_timed_path=args.lyrics_timed,
            audio_path=args.audio,
            output_path=args.output,
            target_size=args.target_size,
            target_fps=args.target_fps,
            font_size=args.font_size,
            overlay_y_offset=args.overlay_y_offset,
        )
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"合成失败: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"MV 已生成: {output}")


if __name__ == "__main__":
    main()
