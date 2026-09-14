#!/usr/bin/env python3
"""修复歌词时间戳：去除空隙，确保连续显示。

用法：
    python fix_lyrics.py --input lyrics_timed.json --output lyrics_fixed.json --audio song.mp3
    
    # 或只指定input/output，不指定audio（默认不修正总时长）
    python fix_lyrics.py --input lyrics_timed.json --output lyrics_fixed.json

功能：
1. 去除歌词之间的空隙：每条歌词的 end = 下一条歌词的 start
2. 如果指定了 --audio，最后一行的 end 设为 mp3 时长
3. 输出新的 lyrics_timed.json
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from common import resolve_ffprobe

# [BUGFIX] 此前这里是
# `imageio_ffmpeg.get_ffmpeg_exe().replace("ffmpeg.exe", "ffprobe.exe")`——
# 非 Windows 平台 imageio_ffmpeg 返回的文件名没有 `.exe` 后缀，这个
# `.replace()` 根本不会命中，`FFPROBE` 会被错误地设成 ffmpeg 自己的
# 路径；探测失败（比如没装 imageio_ffmpeg）时的 fallback 更是直接写死
# 了某台开发机的用户名路径 `C:\Users\onewa\...`。现在改用
# `common.resolve_ffprobe()`（环境变量 MV_FFPROBE_PATH > imageio_ffmpeg
# 正确猜测 > conda 环境自动探测 > 系统 PATH），找不到时在
# `get_audio_duration()` 实际被调用时才明确报错，而不是在导入模块时就
# 用一条不存在的路径埋下隐患。


def get_audio_duration(audio_path):
    """获取音频文件时长（秒）。"""
    ffprobe = resolve_ffprobe()  # 找不到时抛 RuntimeError，附带详细探测过程
    r = subprocess.run(
        [ffprobe, "-v", "quiet", "-print_format", "json",
         "-show_format", audio_path],
        capture_output=True, text=True
    )
    return float(json.loads(r.stdout)["format"]["duration"])


def fix_lyrics(gaps, audio_path=None):
    """修复歌词时间戳。
    
    Args:
        gaps: input lyrics_timed.json 路径或 dict
        audio_path: mp3 文件路径，用于设置最后一行的 end
    
    Returns:
        dict: 修复后的歌词数据
    """
    # 加载歌词
    if isinstance(gaps, (Path, str)):
        with open(gaps, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = gaps.copy()
    
    lines = data.get("lines", [])
    print(f"原始歌词行数: {len(lines)}")
    print(f"原始总时长: {data.get('duration', 'N/A')}")
    
    if not lines:
        print("警告: 歌词为空")
        return data
    
    # 去除空隙：每条歌词的 end = 下一条的 start
    gaps_removed = 0
    for i in range(len(lines) - 1):
        old_end = lines[i]["end"]
        new_end = lines[i + 1]["start"]
        if abs(old_end - new_end) > 0.01:
            lines[i]["end"] = new_end
            gaps_removed += 1
            print(f"  Line {i}: end {old_end:.2f} -> {new_end:.2f} (gap={old_end-new_end:+.2f}s)")
    
    # 如果指定了音频文件，最后一行的 end = mp3 时长
    if audio_path:
        mp3_dur = get_audio_duration(audio_path)
        old_end = lines[-1]["end"]
        lines[-1]["end"] = mp3_dur
        data["duration"] = mp3_dur
        print(f"\n最后一条歌词: end {old_end:.2f} -> {mp3_dur:.2f} (mp3时长)")
        print(f"修复后总时长: {mp3_dur:.2f}s")
    
    print(f"\n共修复 {gaps_removed} 处空隙")
    return data


def main():
    parser = argparse.ArgumentParser(description="修复歌词时间戳，去除空隙")
    parser.add_argument("--input", required=True, help="输入 lyrics_timed.json 路径")
    parser.add_argument("--output", required=True, help="输出 lyrics_timed_fixed.json 路径")
    parser.add_argument("--audio", default=None, help="mp3 文件路径（用于修正总时长）")
    args = parser.parse_args()
    
    # 执行修复
    result = fix_lyrics(args.input, args.audio)
    
    # 保存结果
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"\n已保存到: {out_path}")


if __name__ == "__main__":
    main()