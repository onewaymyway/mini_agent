"""歌词对齐校正脚本。

用途：ASR 识别结果（asr_transcribe.py 的输出）文字通常有错字/漏字/背景音
干扰导致的幻听，但时间戳大体可信；用户提供的"标准歌词"文字准确但没有
时间戳。本脚本把两者对齐，得到"文字准确 + 时间戳可信"的逐句歌词。

算法（不依赖 LLM，纯字符级序列对齐，速度快、可离线运行）：
1. 把标准歌词拆成逐行，再拆成逐字符，形成 standard_chars 序列。
2. 把 ASR 的词级时间戳展开成逐字符序列 asr_chars（每个字符的时间戳用其所属
   词的时间区间线性插值），形成 asr_chars 序列。
3. 用 difflib.SequenceMatcher 在两个字符序列间找最长公共匹配块
   （get_matching_blocks），把 asr 侧的时间戳"传递"给标准歌词侧对应字符。
4. 标准歌词里没有直接匹配上时间戳的字符（错字/漏字导致），在其前后已知
   时间戳之间做线性插值填补。
5. 按标准歌词的换行位置切回逐行，得到每行的 start/end 时间戳。

用法（命令行）：
    python align_lyrics.py asr_raw.json lyrics.txt --save-path lyrics_timed.json

lyrics.txt 是纯文本，每行一句歌词（空行会被忽略）。
"""

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import List, Optional


def _flatten_asr_chars(asr_result: dict) -> List[dict]:
    """把 ASR 结果展开成逐字符时间戳列表：[{"char": str, "time": float}, ...]。

    优先使用词级时间戳（更精细）；若某个词没有词级时间戳（word_timestamps
    未开启或该词识别失败），退化用所在段落的时间区间线性插值。
    """
    chars = []
    for seg in asr_result.get("segments", []):
        words = seg.get("words") or []
        if words:
            for w in words:
                text = w["word"]
                if not text:
                    continue
                start, end = w["start"], w["end"]
                n = len(text)
                for i, ch in enumerate(text):
                    if ch.strip() == "":
                        continue
                    t = start + (end - start) * (i / max(n, 1))
                    chars.append({"char": ch, "time": round(t, 3)})
        else:
            text = seg.get("text", "")
            start, end = seg["start"], seg["end"]
            n = len(text)
            for i, ch in enumerate(text):
                if ch.strip() == "":
                    continue
                t = start + (end - start) * (i / max(n, 1))
                chars.append({"char": ch, "time": round(t, 3)})
    return chars


def _load_standard_lines(lyrics_text: str) -> List[str]:
    lines = [ln.strip() for ln in lyrics_text.splitlines()]
    return [ln for ln in lines if ln]


def align(asr_result: dict, lyrics_text: str, line_gap: float = 0.15) -> dict:
    """对齐标准歌词与 ASR 时间戳，返回逐句带时间戳的歌词。

    Args:
        asr_result: asr_transcribe.transcribe() 的返回结果（或读取自其 JSON 输出）。
        lyrics_text: 标准歌词纯文本，逐行一句。
        line_gap: 相邻两句之间的最小间隔（秒），避免时间戳完全重叠导致字幕
            显示错乱；当推算出的间隔小于该值时会做轻微收缩/扩张调整。

    Returns:
        dict: {
            "audio_path": str, "duration": float,
            "lines": [{"index": int, "text": str, "start": float, "end": float}, ...]
        }
    """
    standard_lines = _load_standard_lines(lyrics_text)
    if not standard_lines:
        raise ValueError("标准歌词为空，无法对齐")

    # 标准歌词展开成 (行号, 字符) 序列，同时记录每个字符在整体字符串中的位置
    standard_chars = []
    line_char_ranges = []  # 每行覆盖的 [start_idx, end_idx) 区间（左闭右开）
    for idx, line in enumerate(standard_lines):
        start_i = len(standard_chars)
        for ch in line:
            if ch.strip() == "":
                continue
            standard_chars.append(ch)
        line_char_ranges.append((start_i, len(standard_chars), idx))

    asr_chars = _flatten_asr_chars(asr_result)
    asr_char_seq = [c["char"] for c in asr_chars]
    standard_char_seq = standard_chars

    matcher = difflib.SequenceMatcher(a=standard_char_seq, b=asr_char_seq, autojunk=False)
    matched_time = [None] * len(standard_char_seq)  # 标准字符序列里每个位置对应的时间戳
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            std_pos = block.a + k
            asr_pos = block.b + k
            matched_time[std_pos] = asr_chars[asr_pos]["time"]

    # 对未匹配上的位置做线性插值填补；序列开头/结尾未匹配的用最近已知值兜底
    known_positions = [i for i, t in enumerate(matched_time) if t is not None]
    if not known_positions:
        raise RuntimeError("ASR 识别结果与标准歌词完全没有匹配上任何字符，无法对齐，请检查音频/歌词是否对应")

    for i in range(len(matched_time)):
        if matched_time[i] is not None:
            continue
        # 找左右最近的已知点做线性插值
        left = max((p for p in known_positions if p < i), default=None)
        right = min((p for p in known_positions if p > i), default=None)
        if left is None and right is None:
            continue
        if left is None:
            matched_time[i] = matched_time[right]
        elif right is None:
            matched_time[i] = matched_time[left]
        else:
            ratio = (i - left) / (right - left)
            matched_time[i] = matched_time[left] + (matched_time[right] - matched_time[left]) * ratio

    # 按行切回，取每行覆盖字符的首尾时间戳
    result_lines = []
    for start_i, end_i, idx in line_char_ranges:
        seg = [t for t in matched_time[start_i:end_i] if t is not None]
        if not seg:
            # 极端情况：整行都没对齐上，用上一行结束时间做兜底
            prev_end = result_lines[-1]["end"] if result_lines else 0.0
            line_start, line_end = prev_end, prev_end + 1.0
        else:
            line_start, line_end = min(seg), max(seg)
            if line_end <= line_start:
                line_end = line_start + 0.5
        result_lines.append({
            "index": idx,
            "text": standard_lines[idx],
            "start": round(line_start, 3),
            "end": round(line_end, 3),
        })

    # 保证时间戳单调递增，且相邻两行之间留出最小间隔，避免字幕重叠
    for i in range(1, len(result_lines)):
        prev, cur = result_lines[i - 1], result_lines[i]
        if cur["start"] < prev["end"] + line_gap:
            cur["start"] = round(prev["end"] + line_gap, 3)
        if cur["end"] <= cur["start"]:
            cur["end"] = round(cur["start"] + 0.5, 3)

    return {
        "audio_path": asr_result.get("audio_path"),
        "duration": asr_result.get("duration"),
        "lines": result_lines,
    }


def to_srt(lyrics_timed: dict) -> str:
    """把对齐结果转成标准 .srt 字幕文本（供 compose_mv.py 烧录使用）。"""

    def fmt(t: float) -> str:
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    blocks = []
    for i, line in enumerate(lyrics_timed["lines"], start=1):
        blocks.append(f"{i}\n{fmt(line['start'])} --> {fmt(line['end'])}\n{line['text']}\n")
    return "\n".join(blocks)


def main():
    parser = argparse.ArgumentParser(description="用标准歌词校正 ASR 时间戳，输出逐句对齐结果")
    parser.add_argument("asr_json", help="asr_transcribe.py 的输出 JSON 路径")
    parser.add_argument("lyrics_txt", help="标准歌词纯文本路径，逐行一句")
    parser.add_argument("--line-gap", type=float, default=0.15, help="相邻两句最小间隔秒数，默认 0.15")
    parser.add_argument("--save-path", default=None, help="对齐结果保存路径（JSON）")
    parser.add_argument("--save-srt", default=None, help="同时输出 .srt 字幕文件路径（可选）")
    args = parser.parse_args()

    asr_path = Path(args.asr_json)
    lyrics_path = Path(args.lyrics_txt)
    if not asr_path.exists():
        print(f"找不到 ASR 结果文件: {asr_path}", file=sys.stderr)
        sys.exit(1)
    if not lyrics_path.exists():
        print(f"找不到歌词文件: {lyrics_path}", file=sys.stderr)
        sys.exit(1)

    asr_result = json.loads(asr_path.read_text(encoding="utf-8"))
    lyrics_text = lyrics_path.read_text(encoding="utf-8")

    try:
        result = align(asr_result, lyrics_text, line_gap=args.line_gap)
    except (ValueError, RuntimeError) as exc:
        print(f"对齐失败: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.save_path:
        save_path = Path(args.save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已保存对齐结果到: {save_path}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.save_srt:
        srt_path = Path(args.save_srt)
        srt_path.parent.mkdir(parents=True, exist_ok=True)
        srt_path.write_text(to_srt(result), encoding="utf-8")
        print(f"已保存字幕文件到: {srt_path}")


if __name__ == "__main__":
    main()
