#!/usr/bin/env python3
"""歌词对齐校正脚本 v2（精确匹配优先 + 归一化 + 锚点插值）。

针对 v1 (`align_lyrics.py`) 暴露出的问题做的改进：

1. **归一化匹配**：ASR 引擎经常输出繁体字（faster-whisper 中文识别常见），
   而标准歌词多为简体；v1 直接按字符相等比较，"总"≠"總" 导致大量本该
   匹配上的字符被判定为不匹配，从而退化成大范围线性插值，产生
   "两边同一时间段字数对不上" 的现象。v2 在匹配阶段对每个字符做
   归一化（简体/繁体统一、大小写统一、全角半角统一），归一化后相等
   即视为精确匹配，但**保存的还是标准歌词原文**，不会改变输出文字。
2. **两阶段对齐**：
   - 阶段一：在归一化后的字符序列上做最长公共子序列匹配
     （`difflib.SequenceMatcher`），只接受长度 >= `--min-anchor` 的匹配块
     作为"锚点"，避免单字符匹配（"的"、"了"这种高频字）引入噪声。
   - 阶段二：锚点之间未匹配上的字符，不再单纯按字符数量线性插值，而是
     结合锚点前后的真实时间差 + 字符数量做插值，并且如果一整句歌词
     完全没有锚点，会退化成"整句级别"的模糊匹配（把该句与临近的 ASR
     segment 文本整体做相似度比较），取相似度最高的 segment 时间区间，
     而不是直接向前一句时间硬套。
3. **覆盖率报告**：输出每一行的锚点覆盖率（有多少字符是精确匹配来的），
   覆盖率过低的行会在 stderr 里给出警告，方便人工核对。

用法：
    python align_lyrics_v2.py asr_raw.json lyrics.txt --save-path lyrics_timed.json --save-srt lyrics.srt

依赖：仅标准库；若安装了 `opencc-python-reimplemented`
（`pip install opencc-python-reimplemented`），简繁转换会更准确全面，
未安装时退化使用脚本内置的高频字对照表（覆盖常见歌词用字，但不完整）。
"""

import argparse
import difflib
import json
import sys
import unicodedata
from pathlib import Path
from typing import List, Optional, Tuple

# ── 简繁归一化 ──────────────────────────────────────────────────────
try:
    from opencc import OpenCC
    _CC = OpenCC("t2s")

    def _t2s(ch: str) -> str:
        return _CC.convert(ch)
except Exception:
    _CC = None
    # 内置高频简繁对照表（不完整，仅覆盖歌词中常见字）。
    # 安装 opencc-python-reimplemented 可获得完整、准确的转换。
    _T2S_TABLE = {
        "總": "总", "擔": "担", "證": "证", "陽": "阳", "聲": "声", "裏": "里",
        "裡": "里", "肩": "肩", "膀": "膀", "無": "无", "重": "重", "翻": "翻",
        "顏": "颜", "情": "情", "話": "话", "腳": "脚", "步": "步", "曾": "曾",
        "停": "停", "歇": "歇", "遲": "迟", "類": "类", "現": "现", "學": "学",
        "會": "会", "焰": "焰", "天": "天", "權": "权", "輪": "轮", "廓": "廓",
        "隨": "随", "會": "会", "脈": "脉", "搏": "搏", "逐": "逐", "漸": "渐",
        "清": "清", "從": "从", "領": "领", "袖": "袖", "君": "君", "寶": "宝",
        "座": "座", "歷": "历", "史": "史", "語": "语", "帝": "帝", "輝": "辉",
        "煌": "煌", "民": "民", "覺": "觉", "醒": "醒", "革": "革", "命": "命",
        "呼": "呼", "聲": "声", "游": "游", "戲": "戏", "試": "试", "金": "金",
        "資": "资", "產": "产", "動": "动", "像": "像", "液": "液", "穿": "穿",
        "梭": "梭", "經": "经", "濟": "济", "管": "管", "絲": "丝", "綢": "绸",
        "貨": "货", "幣": "币", "換": "换", "積": "积", "纍": "累", "場": "场",
        "貌": "貌", "剛": "刚", "術": "术", "夢": "梦", "廣": "广", "興": "兴",
        "業": "业", "轟": "轰", "鳴": "鸣", "電": "电", "燈": "灯", "夜": "夜",
        "黑": "黑", "話": "话", "線": "线", "視": "视", "畫": "画", "麵": "面",
        "訊": "讯", "竅": "窍", "門": "门", "溝": "沟", "傳": "传", "播": "播",
        "醫": "医", "藥": "药", "延": "延", "續": "续", "計": "计", "算": "算",
        "邊": "边", "疆": "疆", "推": "推", "極": "极", "限": "限", "夢": "梦",
        "聚": "聚", "變": "变", "臟": "脏", "爐": "炉", "發": "发", "盡": "尽",
        "誕": "诞", "渴": "渴", "望": "望", "點": "点", "亮": "亮", "希": "希",
        "輝": "辉", "耀": "耀", "慧": "慧", "從": "从", "繁": "繁", "勞": "劳",
        "務": "务", "需": "需", "為": "为", "終": "终", "義": "义", "奴": "奴",
        "隸": "隶", "開": "开", "闊": "阔", "靈": "灵", "魂": "魂", "追": "追",
        "夢": "梦", "享": "享", "創": "创", "樂": "乐", "橋": "桥", "貴": "贵",
        "賤": "贱", "問": "问", "祉": "祉", "願": "愿", "夢": "梦", "觸": "触",
        "寬": "宽", "廣": "广", "戰": "战", "號": "号", "資": "资", "礎": "础",
        "階": "阶", "梯": "梯", "並": "并", "並": "并", "麼": "么", "個": "个",
        "們": "们", "來": "来", "後": "后", "時": "时", "間": "间", "與": "与",
        "當": "当", "還": "还", "說": "说", "認": "认", "識": "识", "應": "应",
        "對": "对", "體": "体", "頭": "头", "見": "见", "長": "长", "樣": "样",
    }

    def _t2s(ch: str) -> str:
        return _T2S_TABLE.get(ch, ch)


def normalize_char(ch: str) -> str:
    """归一化单个字符：全角->半角/兼容分解、大小写统一、繁体->简体。

    返回值仅用于"是否匹配"的比较，不作为最终输出文字。
    """
    if ch is None:
        return ""
    ch = unicodedata.normalize("NFKC", ch)
    ch = ch.casefold()
    ch = _t2s(ch)
    return ch


def _is_ignorable(ch: str) -> bool:
    """匹配时忽略的字符：空白、常见标点符号。"""
    if ch.strip() == "":
        return True
    if unicodedata.category(ch).startswith("P"):
        return True
    return False


# ── ASR 展开成逐字符时间戳 ────────────────────────────────────────────

def _flatten_asr_chars(asr_result: dict) -> List[dict]:
    """把 ASR 结果展开成逐字符时间戳列表：
    [{"char": str, "norm": str, "time": float, "seg_idx": int}, ...]

    优先使用词级时间戳；没有时退化用所在 segment 的时间区间线性插值
    （大多数 faster-whisper 中文识别配置默认不开词级时间戳，这也是
    v1 里"整段一起插值"导致误差偏大的原因之一——v2 在没有词级时间戳
    时会额外记录 seg_idx，方便阶段二做"整句/整段"级别的兜底匹配）。
    """
    chars = []
    for seg_idx, seg in enumerate(asr_result.get("segments", [])):
        words = seg.get("words") or []
        if words:
            for w in words:
                text = w.get("word", "")
                if not text:
                    continue
                start, end = w["start"], w["end"]
                n = len(text)
                for i, ch in enumerate(text):
                    if _is_ignorable(ch):
                        continue
                    t = start + (end - start) * (i / max(n, 1))
                    chars.append({"char": ch, "norm": normalize_char(ch),
                                  "time": round(t, 3), "seg_idx": seg_idx})
        else:
            text = seg.get("text", "")
            start, end = seg["start"], seg["end"]
            n = len(text)
            for i, ch in enumerate(text):
                if _is_ignorable(ch):
                    continue
                t = start + (end - start) * (i / max(n, 1))
                chars.append({"char": ch, "norm": normalize_char(ch),
                              "time": round(t, 3), "seg_idx": seg_idx})
    return chars


def _load_standard_lines(lyrics_text: str) -> List[str]:
    lines = [ln.strip() for ln in lyrics_text.splitlines()]
    # 结构标记行（[Verse 1] 之类）不参与逐字对齐，但保留在输出里可选；
    # 这里直接跳过，避免方括号内容污染字符匹配。
    return [ln for ln in lines if ln and not (ln.startswith("[") and ln.endswith("]"))]


def align(asr_result: dict, lyrics_text: str, line_gap: float = 0.15,
          min_anchor: int = 2) -> dict:
    """对齐标准歌词与 ASR 时间戳，返回逐句带时间戳的歌词 + 覆盖率信息。"""
    standard_lines = _load_standard_lines(lyrics_text)
    if not standard_lines:
        raise ValueError("标准歌词为空，无法对齐")

    standard_chars = []      # 原始字符（用于最终输出文本）
    standard_norm = []       # 归一化字符（用于匹配）
    line_char_ranges = []
    for idx, line in enumerate(standard_lines):
        start_i = len(standard_chars)
        for ch in line:
            if _is_ignorable(ch):
                continue
            standard_chars.append(ch)
            standard_norm.append(normalize_char(ch))
        line_char_ranges.append((start_i, len(standard_chars), idx))

    asr_chars = _flatten_asr_chars(asr_result)
    asr_norm = [c["norm"] for c in asr_chars]

    # ── 阶段一：归一化后的精确匹配（锚点） ─────────────────────────
    matcher = difflib.SequenceMatcher(a=standard_norm, b=asr_norm, autojunk=False)
    matched_time = [None] * len(standard_norm)
    matched_conf = [False] * len(standard_norm)  # 是否来自"锚点"（高置信度）
    for block in matcher.get_matching_blocks():
        if block.size < min_anchor:
            continue
        for k in range(block.size):
            std_pos = block.a + k
            asr_pos = block.b + k
            matched_time[std_pos] = asr_chars[asr_pos]["time"]
            matched_conf[std_pos] = True

    known_positions = [i for i, t in enumerate(matched_time) if t is not None]
    if not known_positions:
        raise RuntimeError(
            "ASR 识别结果与标准歌词完全没有匹配上任何字符，无法对齐。"
            "请检查：1) 音频与歌词是否对应；2) ASR 语言/模型设置是否正确；"
            "3) 是否安装了 opencc 以获得更好的简繁转换。"
        )

    # ── 阶段二：锚点之间的空隙用线性插值兜底 ────────────────────────
    for i in range(len(matched_time)):
        if matched_time[i] is not None:
            continue
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

    # ── 按行切回，同时计算每行的锚点覆盖率 ──────────────────────────
    result_lines = []
    low_conf_lines = []
    for start_i, end_i, idx in line_char_ranges:
        seg_time = [t for t in matched_time[start_i:end_i] if t is not None]
        seg_conf = matched_conf[start_i:end_i]
        n_chars = max(end_i - start_i, 1)
        coverage = sum(1 for c in seg_conf if c) / n_chars

        if not seg_time:
            prev_end = result_lines[-1]["end"] if result_lines else 0.0
            line_start, line_end = prev_end, prev_end + 1.0
        else:
            line_start, line_end = min(seg_time), max(seg_time)
            if line_end <= line_start:
                line_end = line_start + 0.5

        if coverage < 0.34:
            low_conf_lines.append((idx, standard_lines[idx], coverage))

        result_lines.append({
            "index": idx,
            "text": standard_lines[idx],
            "start": round(line_start, 3),
            "end": round(line_end, 3),
            "anchor_coverage": round(coverage, 2),
        })

    # ── 阶段三：整句级别兜底 —— 覆盖率过低的行，改用整句相似度匹配 ──
    asr_segments = asr_result.get("segments", [])
    for line in result_lines:
        if line["anchor_coverage"] >= 0.34 or not asr_segments:
            continue
        norm_line = "".join(normalize_char(c) for c in line["text"] if not _is_ignorable(c))
        best_ratio, best_seg = 0.0, None
        for seg in asr_segments:
            norm_seg = "".join(
                normalize_char(c) for c in seg.get("text", "") if not _is_ignorable(c)
            )
            if not norm_seg:
                continue
            ratio = difflib.SequenceMatcher(a=norm_line, b=norm_seg, autojunk=False).ratio()
            if ratio > best_ratio:
                best_ratio, best_seg = ratio, seg
        if best_seg is not None and best_ratio > 0.4:
            line["start"] = round(best_seg["start"], 3)
            line["end"] = round(best_seg["end"], 3)
            line["fallback"] = f"whole-line match (ratio={best_ratio:.2f})"

    # ── 保证时间戳单调递增 + 最小间隔 ───────────────────────────────
    for i in range(1, len(result_lines)):
        prev, cur = result_lines[i - 1], result_lines[i]
        if cur["start"] < prev["end"] + line_gap:
            cur["start"] = round(prev["end"] + line_gap, 3)
        if cur["end"] <= cur["start"]:
            cur["end"] = round(cur["start"] + 0.5, 3)

    if low_conf_lines:
        print(f"[align_lyrics_v2] 警告：{len(low_conf_lines)} 行锚点覆盖率过低（<34%），"
              f"已尝试整句兜底匹配，请人工核对：", file=sys.stderr)
        for idx, text, cov in low_conf_lines:
            print(f"  行{idx}: 覆盖率={cov:.0%}  文本={text}", file=sys.stderr)

    return {
        "audio_path": asr_result.get("audio_path"),
        "duration": asr_result.get("duration"),
        "lines": result_lines,
    }


def to_srt(lyrics_timed: dict) -> str:
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
    parser = argparse.ArgumentParser(description="用标准歌词校正 ASR 时间戳 v2（归一化精确匹配优先）")
    parser.add_argument("asr_json")
    parser.add_argument("lyrics_txt")
    parser.add_argument("--line-gap", type=float, default=0.15)
    parser.add_argument("--min-anchor", type=int, default=2,
                        help="被视为锚点的最小连续匹配字符数，默认2（避免单字高频字噪声）")
    parser.add_argument("--save-path", default=None)
    parser.add_argument("--save-srt", default=None)
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
        result = align(asr_result, lyrics_text, line_gap=args.line_gap, min_anchor=args.min_anchor)
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
