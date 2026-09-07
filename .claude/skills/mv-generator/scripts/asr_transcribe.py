"""本地语音识别脚本，基于 faster-whisper（离线、免费）。

用途：对 mp3/wav 等音频做语音识别，输出带词级/段级时间戳的原始识别结果，
供后续 align_lyrics.py 用标准歌词做校正对齐。

依赖：faster-whisper（可选依赖，不在 requirements.txt 强制安装区，
按需自行安装：`pip install faster-whisper`）。首次运行会自动下载模型
（默认 "small"，中文歌曲建议用 "medium" 或更大以提升识别准确率，
但对齐阶段本来就会用标准歌词校正文字，识别准确率的影响主要体现在
时间戳定位的粗细上，不必追求逐字准确）。

用法（命令行）：
    python asr_transcribe.py song.mp3 --save-path asr_raw.json
    python asr_transcribe.py song.mp3 --model-size medium --language zh --save-path asr_raw.json

用法（作为模块）：
    from asr_transcribe import transcribe
    result = transcribe("song.mp3", model_size="small", language="zh")
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


def _load_whisper_model(model_size: str, device: str, compute_type: str):
    """延迟导入 faster-whisper，未安装时给出清晰的安装提示而不是裸 ImportError。"""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "未检测到 faster-whisper，本 skill 的语音识别步骤依赖它。\n"
            "请先安装：pip install faster-whisper\n"
            "（首次运行还会自动下载识别模型，视网络情况可能需要几分钟）"
        ) from exc
    return WhisperModel(model_size, device=device, compute_type=compute_type)


def transcribe(
    audio_path: str,
    model_size: str = "small",
    language: Optional[str] = "zh",
    device: str = "cpu",
    compute_type: str = "int8",
    word_timestamps: bool = True,
) -> dict:
    """对音频做语音识别，返回段级 + 词级时间戳。

    Args:
        audio_path: 音频文件路径（mp3/wav 等 ffmpeg 支持的格式）。
        model_size: faster-whisper 模型规格，如 tiny/base/small/medium/large-v3。
            越大越准但越慢，歌曲背景音乐较吵时建议至少 small。
        language: 语言代码，中文歌曲传 "zh"；传 None 让模型自动检测。
        device: "cpu" 或 "cuda"（有 GPU 时可显著加速）。
        compute_type: 量化精度，cpu 场景推荐 "int8"（更快更省内存）。
        word_timestamps: 是否输出词级时间戳（对齐阶段更精细，建议保持 True）。

    Returns:
        dict，结构：
        {
            "audio_path": str,
            "language": str,
            "duration": float,
            "segments": [
                {
                    "start": float, "end": float, "text": str,
                    "words": [{"word": str, "start": float, "end": float}, ...]
                },
                ...
            ]
        }
    """
    audio_file = Path(audio_path)
    if not audio_file.exists():
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    model = _load_whisper_model(model_size, device, compute_type)

    segments_iter, info = model.transcribe(
        str(audio_file),
        language=language,
        word_timestamps=word_timestamps,
        vad_filter=True,  # 过滤纯伴奏/静音段，减少幻听文本
    )

    segments = []
    for seg in segments_iter:
        words = []
        if word_timestamps and seg.words:
            for w in seg.words:
                words.append({
                    "word": w.word.strip(),
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                })
        segments.append({
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "words": words,
        })

    return {
        "audio_path": str(audio_file),
        "language": info.language,
        "duration": round(info.duration, 3),
        "segments": segments,
    }


def main():
    parser = argparse.ArgumentParser(description="faster-whisper 本地语音识别（生成歌词时间戳草稿）")
    parser.add_argument("audio_path", help="音频文件路径（mp3/wav 等）")
    parser.add_argument("--model-size", default="small", help="模型规格：tiny/base/small/medium/large-v3，默认 small")
    parser.add_argument("--language", default="zh", help="语言代码，默认 zh；传 auto 让模型自动检测")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"], help="运行设备，默认 cpu")
    parser.add_argument("--compute-type", default="int8", help="量化精度，默认 int8")
    parser.add_argument("--save-path", default=None, help="识别结果保存路径（JSON）")
    args = parser.parse_args()

    language = None if args.language == "auto" else args.language

    try:
        result = transcribe(
            args.audio_path,
            model_size=args.model_size,
            language=language,
            device=args.device,
            compute_type=args.compute_type,
        )
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"识别失败: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.save_path:
        save_path = Path(args.save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已保存识别结果到: {save_path}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
