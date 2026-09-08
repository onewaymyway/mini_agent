#!/usr/bin/env python3
"""基于强制对齐（CTC forced alignment）的歌词打轴脚本 —— 推荐优先使用。

和 align_lyrics_v2.py（思路是"先让 Whisper 自由识别出文本，再拿识别结果
去跟标准歌词做模糊匹配"）不同，本脚本走的是完全不同的技术路线：

    标准歌词文本是已知且保证正确的，根本不需要"猜"这段音频对应哪些字，
    需要知道的只是"这段已知文本在音频的第几秒被唱到" —— 这本来就是
    强制对齐（force alignment）模型的标准任务，而不是自由语音识别任务。

相比 align_lyrics_v2.py 的优势：
  - 不会经过"识别错字 -> 错误文本参与模糊匹配"这个误差放大环节。
  - 天然单调：模型严格按给定文本顺序推进，不可能出现重复段落（副歌/
    主歌重复）被对齐到"另一次重复"的时间点上（这是 align_lyrics_v2.py
    在 v1 版本里踩过的一个真实的坑，v2 靠"单调游标+局部窗口"打了个
    补丁，本质上还是在缓解症状；强制对齐从架构上就不存在这个问题）。

已知局限：
  - 对齐依赖 uroman 音译（把汉字近似转写成拉丁字母）去匹配一个通用多
    语言声学模型，不是真正的拼音/声调建模，个别发音相近的字之间的
    分界可能有 0.1~0.3 秒抖动，但不会出现"跳到别的重复段落"这种量级
    的错误。
  - 纯乐器间奏、气声/尾音拖得很长的转音处，边界会偏向就近的实词。
  - 首次运行需要联网下载模型（见下方"模型"一节）。

强烈建议：先用 separate_vocals.py 分离出人声轨 vocals.wav，把它作为本
脚本的 <audio_path> 输入（而不是原始带伴奏的 mp3）——背景音乐越干净，
对齐边界越准，这一步和"强制对齐 vs ASR+diff"同样重要，两者应该配合使用。

依赖：
    pip install ctc-forced-aligner --break-system-packages
    （会一并装上 librosa / onnxruntime / unidecode 等依赖）

模型：
    首次运行会从 huggingface.co 自动下载一个 ONNX 版 MMS 强制对齐模型
    （数十到上百 MB），默认缓存到 ~/ctc_forced_aligner/model.onnx。
    **这一步需要网络能访问 huggingface.co**；如果当前沙箱网络白名单里
    没有这个域名，会下载失败并给出明确报错，此时可以：
      1) 联系环境管理员把 huggingface.co 加入网络白名单，或
      2) 在有网络的机器上先跑一次（自动下载到
         ~/ctc_forced_aligner/model.onnx），把这个文件拷贝到当前环境
         的相同路径，或者用 --model-path 指定拷贝后的实际路径。

输出格式和 align_lyrics_v2.py 保持一致（同样是
{"audio_path", "duration", "lines": [{"index","text","start","end",
"anchor_coverage","method"}, ...]}），下游 compose_mv.py 等脚本不需要
关心时间戳到底是哪个对齐脚本产出的。

用法：
    python align_lyrics_forced.py <output_dir>/vocals/htdemucs/<歌名>/vocals.wav \
        lyrics.txt --save-path <output_dir>/lyrics_timed.json \
        --save-srt <output_dir>/lyrics.srt
"""
import argparse
import json
import sys
import unicodedata
from pathlib import Path
from typing import List


# ── 复用 align_lyrics_v2.py 同样的"可忽略字符"定义，保持两个脚本行为一致 ──

def _is_ignorable(ch: str) -> bool:
    if ch.strip() == "":
        return True
    if unicodedata.category(ch).startswith("P"):
        return True
    return False


def _load_standard_lines(lyrics_text: str) -> List[str]:
    lines = [ln.strip() for ln in lyrics_text.splitlines()]
    return [ln for ln in lines if ln and not (ln.startswith("[") and ln.endswith("]"))]


def align(audio_path: str, lyrics_text: str, model_path: str = None,
          language: str = "chi", batch_size: int = 4, line_gap: float = 0.15) -> dict:
    try:
        from ctc_forced_aligner import (
            load_audio, generate_emissions, preprocess_text,
            get_alignments, get_spans, postprocess_results,
            ensure_onnx_model, MODEL_URL, Tokenizer,
        )
        import onnxruntime
    except ImportError as e:
        raise RuntimeError(
            "缺少依赖 ctc-forced-aligner，请先执行:\n"
            "  pip install ctc-forced-aligner --break-system-packages"
        ) from e

    standard_lines = _load_standard_lines(lyrics_text)
    if not standard_lines:
        raise ValueError("标准歌词为空，无法对齐")

    # 把每行歌词过滤成"内容字符"（去掉标点/空白），记录每个字符属于哪一行，
    # 再把所有行拼接成一条连续字符串整体喂给对齐模型（保证跨行的声学
    # 上下文是连续的，不会因为逐行单独对齐而丢失行与行之间的边界信息）。
    flat_chars: List[str] = []
    line_char_ranges = []  # [(start_i, end_i, line_idx), ...]
    for idx, line in enumerate(standard_lines):
        start_i = len(flat_chars)
        for ch in line:
            if _is_ignorable(ch):
                continue
            flat_chars.append(ch)
        line_char_ranges.append((start_i, len(flat_chars), idx))
    if not flat_chars:
        raise ValueError("歌词过滤标点/空白后没有剩下任何可对齐字符")
    full_text = "".join(flat_chars)

    # 1) 加载/下载 ONNX 强制对齐模型
    model_path = model_path or str(Path.home() / "ctc_forced_aligner" / "model.onnx")
    try:
        ensure_onnx_model(model_path, MODEL_URL)
    except Exception as e:
        raise RuntimeError(
            f"下载强制对齐模型失败（{e}）。大概率是当前网络访问不了 "
            f"huggingface.co。解决办法：1) 把 huggingface.co 加入网络白名单后"
            f"重试；2) 在有网络的机器上预先下载好模型文件，拷贝到 "
            f"{model_path}（或用 --model-path 指定其他已有路径）。"
        ) from e
    session = onnxruntime.InferenceSession(model_path)
    tokenizer = Tokenizer()

    # 2) 音频 -> 逐帧声学后验概率（emissions）
    waveform = load_audio(audio_path)
    emissions, stride = generate_emissions(session, waveform, batch_size=batch_size)

    # 3) 文本预处理（中文按字切分 + uroman 音译）后做强制对齐
    #    language="chi"/"jpn" 会触发 preprocess_text 内部按字符切分，
    #    这正是中文歌词需要的粒度（英文歌词可以传 --language eng 按词切分）。
    tokens_starred, text_starred = preprocess_text(full_text, romanize=True, language=language)
    segments, scores, blank_token = get_alignments(emissions, tokens_starred, tokenizer)
    spans = get_spans(tokens_starred, segments, blank_token)
    char_timestamps = postprocess_results(text_starred, spans, stride, scores)

    if len(char_timestamps) != len(flat_chars):
        raise RuntimeError(
            f"强制对齐返回的字符数（{len(char_timestamps)}）和歌词过滤后的字符数"
            f"（{len(flat_chars)}）对不上，可能是个别字符音译成空字符串导致模型"
            f"内部丢帧。请检查歌词里是否混入了生僻字/emoji/特殊符号/罗马数字等"
            f"uroman 无法处理的内容，必要时手动替换成常见汉字后重试。"
        )

    # 4) 按预先记录的行边界，把逐字时间戳重新组装回逐行时间戳
    result_lines = []
    cursor_time = 0.0
    for start_i, end_i, idx in line_char_ranges:
        seg = char_timestamps[start_i:end_i]
        start, end = seg[0]["start"], seg[-1]["end"]
        if start < cursor_time:
            start = cursor_time
        if end <= start:
            end = start + 0.5
        avg_score = sum(c["score"] for c in seg) / len(seg) if seg else 0.0
        result_lines.append({
            "index": idx,
            "text": standard_lines[idx],
            "start": round(start, 3),
            "end": round(end, 3),
            "anchor_coverage": 1.0,
            "method": f"forced-align(score={avg_score:.2f})",
        })
        cursor_time = end

    # 保险：单调递增 + 最小间隔（强制对齐本身已经保证单调，这里只是兜底，
    # 逻辑和 align_lyrics_v2.py 完全一致，避免两个脚本产物的下游行为不一致）
    for i in range(1, len(result_lines)):
        prev, cur = result_lines[i - 1], result_lines[i]
        if cur["start"] < prev["end"] + line_gap:
            cur["start"] = round(prev["end"] + line_gap, 3)
        if cur["end"] <= cur["start"]:
            cur["end"] = round(cur["start"] + 0.5, 3)

    low_score_lines = [l for l in result_lines if "score=" in l["method"] and
                        float(l["method"].split("score=")[1].rstrip(")")) < -3.0]
    if low_score_lines:
        print(f"[align_lyrics_forced] {len(low_score_lines)} 行对齐置信度偏低，建议人工核对：",
              file=sys.stderr)
        for l in low_score_lines:
            print(f"  行{l['index']}: {l['method']} 文本={l['text']}", file=sys.stderr)

    try:
        import soundfile as sf
        duration = sf.info(audio_path).duration
    except Exception:
        duration = result_lines[-1]["end"] if result_lines else None

    return {
        "audio_path": audio_path,
        "duration": duration,
        "lines": result_lines,
    }


def to_srt(lyrics_timed: dict) -> str:
    def fmt(t: float) -> str:
        h = int(t // 3600)
        m = int(t % 3600 // 60)
        s = t % 60
        return f"{h:02}:{m:02}:{s:06.3f}".replace(".", ",")

    blocks = []
    for i, line in enumerate(lyrics_timed["lines"], start=1):
        blocks.append(f"{i}\n{fmt(line['start'])} --> {fmt(line['end'])}\n{line['text']}\n")
    return "\n".join(blocks)


def main():
    parser = argparse.ArgumentParser(description="用 CTC 强制对齐模型给标准歌词打时间戳（推荐优先使用）")
    parser.add_argument("audio_path", help="音频文件路径（强烈建议传入 separate_vocals.py 分离出的人声轨 vocals.wav）")
    parser.add_argument("lyrics_txt", help="标准歌词文本文件路径")
    parser.add_argument("--model-path", default=None,
                         help="ONNX 模型本地路径，默认 ~/ctc_forced_aligner/model.onnx，不存在时会自动从 huggingface.co 下载")
    parser.add_argument("--language", default="chi",
                         help="语言代码，中文/日文歌词固定用 chi（触发按字切分），英文歌词可用 eng（按词切分）")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--line-gap", type=float, default=0.15)
    parser.add_argument("--save-path", default=None)
    parser.add_argument("--save-srt", default=None)
    args = parser.parse_args()

    lyrics_text = Path(args.lyrics_txt).read_text(encoding="utf-8")
    result = align(args.audio_path, lyrics_text, model_path=args.model_path,
                    language=args.language, batch_size=args.batch_size,
                    line_gap=args.line_gap)

    if args.save_path:
        Path(args.save_path).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[align_lyrics_forced] 已保存到 {args.save_path}", file=sys.stderr)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.save_srt:
        Path(args.save_srt).write_text(to_srt(result), encoding="utf-8")
        print(f"[align_lyrics_forced] 已保存 SRT 到 {args.save_srt}", file=sys.stderr)


if __name__ == "__main__":
    main()
