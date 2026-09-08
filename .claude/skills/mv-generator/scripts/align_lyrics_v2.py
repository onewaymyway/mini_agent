#!/usr/bin/env python3
"""歌词对齐校正脚本 v2（精确匹配优先 + 归一化 + 单调窗口约束）。

相比最初版本，这一版修复了一个用真实歌曲数据实测才暴露出来的严重问题：
**重复段落（副歌/主歌重复）会导致时间戳错误地"跳"到另一次重复的位置**。

## 问题复现

以《进化再论》为例，歌词第 17 行"科技的篇章，是进化史上最美的灿烂。"
（第一段副歌最后一句）真实演唱在 ~50-54s；第 18 行"从蒸汽机的轰鸣，到
电灯的明亮，"（第一段 Verse4 第一句）真实演唱紧接着在 ~54.7s。但最初
版本把第 18 行错误对齐到了 ~156s——那其实是**第二遍重复**演唱这句歌词
的时间点。

## 根因

最初版本对"整句锚点覆盖率过低"的行，会退化成"整句级别模糊匹配"：拿
这一行歌词跟**全曲所有** ASR segment 做相似度比较，取全局相似度最高的
一个。问题是歌词包含重复段落（同一句词会在歌里唱两遍），"全局最相似"
不等于"时间上最合理"——如果第二次演唱那句歌词恰好被 ASR 识别得更准
（相似度更高），就会被错误地选中，产生大幅度跳变，并且这个错误会像
多米诺骨牌一样带歪后面所有行的对齐。字符级精确匹配阶段的
`difflib.SequenceMatcher` 虽然本身保证选出的锚点在两个序列里都是单调
递增的，但当标准歌词本身有大段重复文本时，它选出的"全局最长公共子
序列"完全可能把标准歌词第二次出现的字符匹配到 ASR 里第一次出现的时间
上（反之亦然），同样会产生错位。

## 修复：单调游标 + 局部窗口搜索

核心思路很简单：**歌词是按时间顺序唱的，所以对齐结果也必须是时间上
单调不减的**。v2 引入一个随着行号推进单调前移的时间游标 `cursor_time`：
- 优先使用字符级锚点结果，但**只有当锚点覆盖率足够高、且锚点起始时间
  不早于游标（允许极小的回退容差）时才采信**——覆盖率低或者违反单调
  性的锚点，说明很可能是重复文本导致的误匹配，直接放弃，改用下面的
  窗口匹配。
- 窗口匹配：只在 `[cursor_time, cursor_time + window_seconds]`
  （默认 45 秒，找不到会再翻倍找一次）范围内的 ASR segment 里找
  和这一行歌词整体相似度最高的一段（可以跨 1-3 个 segment 拼接），
  而不是在全曲范围内找。这样即使歌词有重复段落，也只会匹配到"这一次"
  唱到的那个时间点，不会跳到未来或过去的重复处。
- 每处理完一行，游标前移到该行结束时间，下一行的搜索永远从游标开始，
  绝不回头。

**已知局限（数据层面，非算法能修复的问题）**：如果实际演唱中出现了
"标准歌词文本里没有对应文字"的重复（比如audio 实际把 Verse5 也完整
重复唱了一遍，但用户提供的 `lyrics.txt` 只写了一遍 Verse5），单调窗口
搜索能保证不出现离谱跳变，但那一段重复演唱期间不会有任何标准歌词行能
命中它，相邻行的时间戳会被拉长/挤压来"跨过"这段无对应文本的音频。
遇到大范围行的 `method` 标注为 `interpolated`（且持续好几行）时，
应提示用户核对 `lyrics.txt` 是否漏掉了某段重复歌词。

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
from typing import List, Optional

# ── 简繁归一化 ──────────────────────────────────────────────────────
try:
    from opencc import OpenCC
    _CC = OpenCC("t2s")

    def _t2s(ch: str) -> str:
        return _CC.convert(ch)
except Exception:
    _CC = None
    _T2S_TABLE = {
        "總": "总", "擔": "担", "證": "证", "陽": "阳", "聲": "声", "裏": "里",
        "裡": "里", "無": "无", "顏": "颜", "話": "话", "腳": "脚", "遲": "迟",
        "類": "类", "現": "现", "學": "学", "會": "会", "權": "权", "輪": "轮",
        "隨": "随", "脈": "脉", "漸": "渐", "從": "从", "領": "领", "袖": "袖",
        "寶": "宝", "歷": "历", "語": "语", "輝": "辉", "煌": "煌", "覺": "觉",
        "醒": "醒", "革": "革", "戲": "戏", "資": "资", "產": "产", "動": "动",
        "經": "经", "濟": "济", "絲": "丝", "綢": "绸", "貨": "货", "幣": "币",
        "換": "换", "積": "积", "纍": "累", "場": "场", "貌": "貌", "術": "术",
        "夢": "梦", "廣": "广", "興": "兴", "業": "业", "轟": "轰", "鳴": "鸣",
        "電": "电", "燈": "灯", "線": "线", "視": "视", "畫": "画", "麵": "面",
        "訊": "讯", "竅": "窍", "門": "门", "溝": "沟", "傳": "传", "醫": "医",
        "藥": "药", "續": "续", "計": "计", "算": "算", "邊": "边", "疆": "疆",
        "推": "推", "極": "极", "聚": "聚", "變": "变", "臟": "脏", "爐": "炉",
        "發": "发", "盡": "尽", "誕": "诞", "渴": "渴", "點": "点", "亮": "亮",
        "希": "希", "耀": "耀", "慧": "慧", "繁": "繁", "勞": "劳", "務": "务",
        "為": "为", "終": "终", "義": "义", "奴": "奴", "隸": "隶", "開": "开",
        "闊": "阔", "靈": "灵", "魂": "魂", "追": "追", "享": "享", "創": "创",
        "樂": "乐", "橋": "桥", "貴": "贵", "賤": "贱", "問": "问", "願": "愿",
        "觸": "触", "寬": "宽", "戰": "战", "號": "号", "礎": "础", "階": "阶",
        "梯": "梯", "並": "并", "麼": "么", "個": "个", "們": "们", "來": "来",
        "後": "后", "時": "时", "間": "间", "與": "与", "當": "当", "還": "还",
        "說": "说", "認": "认", "識": "识", "應": "应", "對": "对", "體": "体",
        "頭": "头", "見": "见", "長": "长", "樣": "样", "軟": "软", "們": "们",
    }

    def _t2s(ch: str) -> str:
        return _T2S_TABLE.get(ch, ch)


def normalize_char(ch: str) -> str:
    if not ch:
        return ""
    ch = unicodedata.normalize("NFKC", ch)
    ch = ch.casefold()
    ch = _t2s(ch)
    return ch


def _is_ignorable(ch: str) -> bool:
    if ch.strip() == "":
        return True
    if unicodedata.category(ch).startswith("P"):
        return True
    return False


def normalize_text(text: str) -> str:
    return "".join(normalize_char(c) for c in text if not _is_ignorable(c))


# ── ASR 展开成逐字符时间戳 ────────────────────────────────────────────

def _flatten_asr_chars(asr_result: dict) -> List[dict]:
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
    return [ln for ln in lines if ln and not (ln.startswith("[") and ln.endswith("]"))]


def _best_window_match(norm_line: str, segments: List[dict], seg_norm: List[str],
                        start_idx: int, end_time: float, max_span: int = 3):
    """在 segments[start_idx:] 中、start_time <= end_time 的范围内，
    找与 norm_line 相似度最高的连续 segment 拼接（跨 1~max_span 个 segment）。

    返回 (best_ratio, (seg_i, seg_j)) 或 (0.0, None)。
    """
    best_ratio, best_span = 0.0, None
    i = start_idx
    n = len(segments)
    while i < n and segments[i]["start"] <= end_time:
        concat = ""
        for span in range(max_span):
            j = i + span
            if j >= n:
                break
            concat += seg_norm[j]
            if not concat:
                continue
            ratio = difflib.SequenceMatcher(a=norm_line, b=concat, autojunk=False).ratio()
            if ratio > best_ratio:
                best_ratio, best_span = ratio, (i, j)
        i += 1
    return best_ratio, best_span


def align(asr_result: dict, lyrics_text: str, line_gap: float = 0.15,
          min_anchor: int = 2, window_seconds: float = 45.0,
          anchor_coverage_threshold: float = 0.4,
          window_match_threshold: float = 0.28) -> dict:
    """对齐标准歌词与 ASR 时间戳（锚点优先 + 单调窗口约束兜底）。"""
    standard_lines = _load_standard_lines(lyrics_text)
    if not standard_lines:
        raise ValueError("标准歌词为空，无法对齐")

    standard_chars, standard_norm, line_char_ranges = [], [], []
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

    # ── 阶段一：归一化精确匹配锚点 ──────────────────────────────────
    matcher = difflib.SequenceMatcher(a=standard_norm, b=asr_norm, autojunk=False)
    matched_time = [None] * len(standard_norm)
    matched_conf = [False] * len(standard_norm)
    for block in matcher.get_matching_blocks():
        if block.size < min_anchor:
            continue
        for k in range(block.size):
            std_pos, asr_pos = block.a + k, block.b + k
            matched_time[std_pos] = asr_chars[asr_pos]["time"]
            matched_conf[std_pos] = True

    if not any(t is not None for t in matched_time):
        raise RuntimeError(
            "ASR 识别结果与标准歌词完全没有匹配上任何字符，无法对齐。"
            "请检查：1) 音频与歌词是否对应；2) ASR 语言/模型设置是否正确；"
            "3) 是否安装了 opencc 以获得更好的简繁转换。"
        )

    raw_lines = []
    for start_i, end_i, idx in line_char_ranges:
        seg_time = [matched_time[p] for p in range(start_i, end_i) if matched_time[p] is not None]
        n_chars = max(end_i - start_i, 1)
        coverage = sum(1 for p in range(start_i, end_i) if matched_conf[p]) / n_chars
        raw_lines.append({
            "index": idx,
            "text": standard_lines[idx],
            "anchor_start": min(seg_time) if seg_time else None,
            "anchor_end": max(seg_time) if seg_time else None,
            "coverage": coverage,
        })

    # ── 阶段二：单调游标 + 局部窗口匹配 ──────────────────────────────
    segments = asr_result.get("segments", [])
    seg_norm = [normalize_text(s.get("text", "")) for s in segments]

    result_lines = []
    cursor_time = 0.0
    search_from_idx = 0
    SLACK = 1.0  # 允许锚点起始时间比游标早最多这么多秒（容忍轻微误差）

    for rl in raw_lines:
        method = None
        start = end = None

        use_anchor = (
            rl["anchor_start"] is not None
            and rl["coverage"] >= anchor_coverage_threshold
            and rl["anchor_start"] >= cursor_time - SLACK
        )
        if use_anchor:
            start, end = rl["anchor_start"], rl["anchor_end"]
            if end is None or end <= start:
                end = start + 0.5
            method = f"anchor(coverage={rl['coverage']:.2f})"
        elif segments:
            norm_line = normalize_text(rl["text"])
            ratio, span = _best_window_match(
                norm_line, segments, seg_norm, search_from_idx,
                end_time=cursor_time + window_seconds,
            )
            if (ratio < window_match_threshold) or span is None:
                # 窗口内没找到，再放宽一次窗口（应对个别行时长规划偏差较大的情况）
                ratio2, span2 = _best_window_match(
                    norm_line, segments, seg_norm, search_from_idx,
                    end_time=cursor_time + window_seconds * 3,
                )
                if ratio2 > ratio:
                    ratio, span = ratio2, span2
            if span is not None and ratio >= window_match_threshold:
                i, j = span
                start, end = segments[i]["start"], segments[j]["end"]
                method = f"window-match(ratio={ratio:.2f})"
                search_from_idx = i  # 允许下一行与本行有轻微重叠（同一 segment 覆盖多句的情况）
            else:
                start = cursor_time + line_gap
                end = start + 1.0
                method = "interpolated(no-window-match)"
        else:
            start = cursor_time + line_gap
            end = start + 1.0
            method = "interpolated(no-segments)"

        if start < cursor_time:
            start = cursor_time
        if end <= start:
            end = start + 0.5

        result_lines.append({
            "index": rl["index"],
            "text": rl["text"],
            "start": round(start, 3),
            "end": round(end, 3),
            "anchor_coverage": round(rl["coverage"], 2),
            "method": method,
        })
        cursor_time = end
        while search_from_idx < len(segments) - 1 and segments[search_from_idx]["end"] < cursor_time:
            search_from_idx += 1

    # ── 最终保险：单调递增 + 最小间隔（正常情况下阶段二已保证，这里兜底）──
    for i in range(1, len(result_lines)):
        prev, cur = result_lines[i - 1], result_lines[i]
        if cur["start"] < prev["end"] + line_gap:
            cur["start"] = round(prev["end"] + line_gap, 3)
        if cur["end"] <= cur["start"]:
            cur["end"] = round(cur["start"] + 0.5, 3)

    low_conf_lines = [
        (l["index"], l["text"], l["anchor_coverage"], l["method"])
        for l in result_lines if l["anchor_coverage"] < 0.34
    ]
    if low_conf_lines:
        print(f"[align_lyrics_v2] {len(low_conf_lines)} 行锚点覆盖率 <34%，已用窗口匹配/插值兜底，"
              f"请人工核对（若连续多行都是 interpolated，可能是 lyrics.txt 漏写了某段重复歌词）：",
              file=sys.stderr)
        for idx, text, cov, method in low_conf_lines:
            print(f"  行{idx}: 覆盖率={cov:.0%} method={method}  文本={text}", file=sys.stderr)

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
    parser = argparse.ArgumentParser(description="用标准歌词校正 ASR 时间戳 v2（归一化精确匹配 + 单调窗口约束）")
    parser.add_argument("asr_json")
    parser.add_argument("lyrics_txt")
    parser.add_argument("--line-gap", type=float, default=0.15)
    parser.add_argument("--min-anchor", type=int, default=2)
    parser.add_argument("--window-seconds", type=float, default=45.0,
                        help="窗口匹配兜底时，向前搜索 ASR segment 的时间窗口大小（秒），默认45")
    parser.add_argument("--anchor-coverage-threshold", type=float, default=0.4,
                        help="锚点覆盖率低于此值时不采信锚点，改用窗口匹配，默认0.4")
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
        result = align(
            asr_result, lyrics_text, line_gap=args.line_gap, min_anchor=args.min_anchor,
            window_seconds=args.window_seconds,
            anchor_coverage_threshold=args.anchor_coverage_threshold,
        )
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