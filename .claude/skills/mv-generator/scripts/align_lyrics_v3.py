#!/usr/bin/env python3
"""歌词对齐校正脚本 v3（拼音模糊匹配 + 单调窗口约束 + 挂起队列区间插值）。

不需要 ctc-forced-aligner/torch/onnxruntime 这类重依赖，只需要纯 Python
小包 pypinyin（可选），安装成功率接近 100%，适合 align_lyrics_forced.py
因为环境限制装不上/跑不通时使用。

用法：
    python align_lyrics_v3.py asr_raw.json lyrics.txt --save-path lyrics_timed.json --save-srt lyrics.srt

依赖：仅标准库；若安装了 `pypinyin`（拼音模糊匹配）和/或
`opencc-python-reimplemented`（简繁转换），效果更好，未安装时自动退化，
不会报错。

## 本次优化（v3.1）：修复"一行没匹配上就带偏整首歌"的级联故障

### 故障复现（用真实歌曲《勇者与恶龙》数据实测发现）

这首歌第 0 个 ASR segment（0~38s，覆盖 Verse1 全部 8 行）的最后一个
"word" 是识别失败输出的占位乱码字符（`�`），但它的时间戳跨度长达
8 秒多（29.76s~38.02s）——也就是说，Verse2 开头两行歌词（"时间流逝，
勇者变成了国王……" / "曾经的誓言，早已被权力吞噬……"）对应的这段真实
演唱音频，ASR 完全没有识别出任何文字，属于**这两行歌词在 ASR 结果里
彻底找不到对应文本**的情况（和纯语气词"la la la"性质一样，只是原因
不同：一个是真的没唱词，一个是 ASR 识别失败）。

旧版本（v2/v3 之前的迭代）处理"这一行完全没对应文本"的策略是**逐行
决策、立即定案**：当前行在窗口内找不到强匹配，就放宽窗口（3倍）再找
一次，只要分数过了一个不算高的阈值（`window_match_threshold=0.28`，
拼音档还打 9 折）就立刻采信、游标立刻跳过去。于是"时间流逝……"这行
被一个仅 0.39 分的拼音模糊匹配错误地"就近拉郎配"到了本该属于后面第
14 行歌词的 ASR segment（57.0~60.4s）上——这个匹配本身是**这一轮里
分数最高的候选**，但它本不该被采信，因为它明显是"矮子里拔将军"：
真正正确的答案是"这行没有对应文本，应该插值"。

一旦这个错误判断被采信，后果是灾难性的：游标 `cursor_time` 和 ASR
搜索指针 `search_from_idx` 被强行推到了 57s 附近，中间 24s~57s 这一
大段本该属于第 9~13 行歌词的真实 ASR segment 全部被跳过——不仅这一行
本身对错了，后面十几行全部被拖成级联插值，从此彻底脱离真实演唱进度。

### 根因

问题不是"阈值定得不够高"（阈值再高也总会有边界情况），而是**决策
时机太早**：逐行、立即、不可撤销地决定"要不要采信当前找到的最佳候选"，
一旦某一行确实没有对应文本，算法却被强迫在"矮子里拔将军"和"这行没
词"之间选一个——旧版本因为没有"这行没词，先放着"这个选项，只能矮子
里拔将军，一步错步步错。

### 修复：挂起队列（pending queue）+ 区间插值

核心思路改成**"不确定就先放着，等后面找到确定的锚点再回头统一处理"**：

1. 逐行尝试找"高置信度"锚点（字符级精确锚点覆盖率达标；或窗口模糊
   匹配汉字/拼音相似度达到**分别设定、拼音档更严格**的确认阈值
   `hanzi_confirm_threshold` / `pinyin_confirm_threshold`）。
2. 找到高置信度锚点：视为"确定点"。如果这个确定点之前有挂起
   （pending）、还没决定时间戳的行，此时才把这一整段挂起的行，按
   **各行文本字数比例**，在"上一个确定点结束时间"到"这个确定点开始
   时间"之间做区间插值分配——不再是逐行退化成"游标+0.15s"的伪时间戳，
   而是合理地把这段真实存在的音频时长（不管是没唱到、还是唱了但
   ASR 没识别出来）按字数比例分给这几行，插值结果仍然落在正确的时间
   区间内，不会把后面行的搜索带偏。
3. 找不到高置信度锚点：这一行进入挂起队列，**不移动游标、不移动
   ASR 搜索指针**，直接处理下一行——把"是否要退化成插值"的决定权
   交给未来，而不是当场用一个勉强及格的弱匹配去赌。
4. 处理完所有行后，如果挂起队列里还有行没被"回收"（比如歌曲结尾的
   人声渐弱段、连续几行语气词一直到曲终），用最后一个确定点到音频
   总时长（`asr_result['duration']`，没有则用最后一个 segment 的
   结束时间）之间的区间做同样的比例插值。

这样"一行没有对应文本"造成的影响，最坏情况下也只是**这一行本身**
（以及紧挨着它、同样没有对应文本的相邻行）的时间戳是插值猜测的，
不会传染给后面任何本来能对上的行——游标和搜索指针只在真正找到高
置信度锚点时才前进，绝不会被一个勉强及格的弱匹配带偏。

### 配套调整：拼音档确认阈值单独收紧

`_best_window_match` 现在会分别返回"汉字最佳候选"和"拼音最佳候选"
（不再合并成单一分数），调用方按候选类型使用不同阈值判断是否够格
"确认"：
- 汉字候选：`hanzi_confirm_threshold`（默认 0.5）。
- 拼音候选：`pinyin_confirm_threshold`（默认 0.6，明显高于汉字档）。
  之所以要更高，是因为纯拼音串用 `difflib` 比较相似度天然存在噪声——
  实测这份数据里，好几个错误的拼音匹配分数都落在 0.35~0.49 这个
  区间，如果沿用旧版的 0.28 门槛（拼音打 9 折后约等于 0.26 就能过），
  这些错误匹配全部会被误判成"确认"，重新引发级联故障；拉到 0.6 之后
  这些噪声匹配基本都会被挡在门外，只有真正"同音字/近音字整句替换"
  这种高置信度的拼音相似情形才能通过。
- 就近窗口内两档都够不到确认阈值时，会再放宽窗口（默认 3 倍）用更
  严格的 `*_confirm_threshold_wide` 门槛试一次；还是够不到就老老实实
  进挂起队列，等后面的确定点来插值兜底，不再"矮子里拔将军"。

以下是历史版本的说明（对齐算法主体思路不变，仅第二阶段的决策方式
从"逐行立即定案"改成了"挂起队列 + 区间插值"，其余部分——归一化、
简繁转换、拼音模糊匹配、字数对齐惩罚、段落标记/歌名过滤——都延续
下来）：

## v2：单调游标 + 局部窗口搜索

歌词是按时间顺序唱的，所以对齐结果也必须是时间上单调不减的。引入
一个随着行号推进单调前移的时间游标 `cursor_time`：优先使用字符级
锚点结果，但只有当锚点覆盖率足够高、且锚点起始时间不早于游标时才
采信；否则改用窗口匹配——只在 `[cursor_time, cursor_time + window]`
范围内的 ASR segment 里找相似度最高的一段，不在全曲范围内找，这样
即使歌词有重复段落，也只会匹配到"这一次"唱到的时间点。

## 拼音模糊匹配（应对同音字/近音字类 ASR 错误）

中文 ASR 出错很大一部分是同音字/近音字替换（比如把"再见"识别成
"在见"），这类错误在字形上完全不匹配、但读音一样或很接近。窗口匹配
阶段把歌词行和候选 ASR segment 都转成拼音序列
（`pypinyin.lazy_pinyin`），再算一次 `difflib.SequenceMatcher`
相似度，作为汉字相似度之外的第二条命中通道。只在整句级窗口匹配加
这条通道，字符级精确锚点阶段不改——单音节同音字太多（"的/地/得"
都读 "de"），字符级加拼音会引入大量虚假锚点。

## 字数对齐惩罚

窗口匹配候选的"有效字数"（中文按汉字数、非中文按字符数）和歌词行
字数差得越多，最终得分惩罚越重，避免"文字很像但长度差很多"的碎片
被误选中；歌词是中文但候选是 ASR 识别失败退化输出的英文/拼音这种
单位不可比的情况，会自动放宽惩罚。

## 歌名 / 段落标记自动过滤

默认跳过歌词首行的歌名（第一条非空、且本身不是段落标记的行）；
`[Intro]`/`[Verse 1]`/`【副歌】`/`(Bridge):` 等半角/全角括号段落标记
统一识别并过滤，不参与对齐。
"""

import argparse
import difflib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── 拼音（可选依赖，装不上就退化成只用汉字相似度，不报错）────────────
try:
    from pypinyin import lazy_pinyin

    def to_pinyin(text: str) -> str:
        """转成不带声调的拼音串，音节间用空格分隔，用于模糊比较。"""
        if not text:
            return ""
        return " ".join(lazy_pinyin(text))

    _PINYIN_AVAILABLE = True
except Exception:
    def to_pinyin(text: str) -> str:
        return ""

    _PINYIN_AVAILABLE = False

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
        "頭": "头", "見": "见", "長": "长", "樣": "样", "軟": "软",
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


# ASR 常见的识别失败占位/替换字符，不携带任何文本信息，不应参与匹配，
# 也不该被当成有意义的字符时间点。
_GARBAGE_CHARS = {"\ufffd", "�"}


def _is_ignorable(ch: str) -> bool:
    if ch.strip() == "":
        return True
    if ch in _GARBAGE_CHARS:
        return True
    if unicodedata.category(ch).startswith("P"):
        return True
    return False


def normalize_text(text: str) -> str:
    return "".join(normalize_char(c) for c in text if not _is_ignorable(c))


# ── ASR 展开成逐字符时间戳 ────────────────────────────────────────────

def _flatten_asr_chars(asr_result: dict) -> List[dict]:
    """把 ASR segments 展开成逐字符时间戳列表。

    识别失败产生的占位乱码字符（`�`）会被跳过——这类字符不仅本身没有
    文本信息、无法参与匹配，它的时间跨度往往异常巨大（实测复现过一个
    占位字符独占 8 秒多的真实演唱时间），如果不过滤，虽然不会被直接
    匹配上（不影响锚点正确性），但留着没有任何用处，干脆滤掉更干净。
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


# 段落标记：半角/全角方括号、圆括号、中文书名号风格括号都认，允许
# 尾部有个冒号（比如 "[Verse 1]:"），内容长度限制在 40 字符内，避免
# 把正常带括号的歌词句子（比如 "(我爱你)这句话"）误判成段落标记——
# 正常歌词括号后通常还跟着别的文字，段落标记则是整行只有括号本身。
_SECTION_TAG_RE = re.compile(r"^[\[\(【（][^\]\)】）]{0,40}[\]\)】）]:?\s*$")


def _is_section_tag(line: str) -> bool:
    return bool(_SECTION_TAG_RE.match(line))


def _load_standard_lines(lyrics_text: str, skip_title: bool = True) -> List[str]:
    """解析出真正需要参与对齐的歌词行。

    - 空行直接丢弃。
    - 形如 `[Intro]`/`[Verse 1]`/`【副歌】`/`(Bridge):` 的段落标记行丢弃，
      这些从来不会被唱出来，拿去跟 ASR 比对只会产生错误锚点。
    - 默认额外丢弃"第一条非空且本身不是段落标记的行"，因为很多
      `lyrics.txt` 习惯把歌名写在第一行。只有当第一行本身就是段落标记
      （说明歌词本来就没写歌名，直接从 `[Intro]` 开始）时才不生效，
      避免误删真正的第一句歌词。`skip_title=False` 可以整体关闭这个
      行为。
    """
    raw_lines = [ln.strip() for ln in lyrics_text.splitlines()]
    non_blank = [ln for ln in raw_lines if ln]
    if not non_blank:
        return []

    start_idx = 0
    if skip_title and not _is_section_tag(non_blank[0]):
        start_idx = 1  # 首行是歌名，跳过

    return [ln for ln in non_blank[start_idx:] if not _is_section_tag(ln)]


# ── 字数对齐辅助：判断"有效字数"，中文按汉字数，非中文按字符数 ────────

def _is_cjk_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF or  # CJK统一表意文字
        0x3400 <= cp <= 0x4DBF or  # 扩展A
        0xF900 <= cp <= 0xFAFF     # 兼容表意文字
    )


def _effective_length(text: str) -> Tuple[int, bool]:
    """返回 (有效字数, 是否以中文字符为主)。

    中文行按汉字数计（更贴近"字数"的直觉，且不受夹杂的英文单词、数字
    干扰）；如果一段文本里几乎没有中文字符（比如 ASR 把中文识别成了
    英文/拼音），则退化成按字符总数计，并标记为"非中文为主"，供调用方
    识别"中文行 vs 非中文候选"这种单位不可比的特殊情况。
    """
    cjk_count = sum(1 for ch in text if _is_cjk_char(ch))
    if cjk_count > 0:
        return cjk_count, True
    return len(text), False


def _length_penalty(norm_line: str, candidate: str) -> float:
    """字数对齐惩罚系数，范围 (0, 1]，1 表示字数完全不惩罚。

    - 双方都是中文（或都不是中文）时：按有效字数比值算惩罚，差距在
      30% 以内不惩罚，差距越大惩罚越重，最低封顶到 0.35（不会直接
      判死刑，因为相似度本身已经包含了大量信息，长度只是辅助信号）。
    - 歌词行是中文、但候选片段几乎不含中文字符（典型场景：ASR 把这句
      中文识别失败、输出了一堆英文/拼音）：双方"字数"根本不是同一个
      计量单位，硬比字数没有意义，这里只给一个很轻的固定折扣，不做
      比例惩罚，避免把本来该选中的候选错误地压低。
    """
    line_len, line_is_cjk = _effective_length(norm_line)
    cand_len, cand_is_cjk = _effective_length(candidate)

    if line_len == 0 or cand_len == 0:
        return 0.5

    if line_is_cjk and not cand_is_cjk:
        # 中文行对上了非中文候选（ASR 识别失败退化成英文/拼音等）：
        # 单位不可比，只做轻微固定折扣，不按比例惩罚。
        return 0.85

    ratio = min(line_len, cand_len) / max(line_len, cand_len)
    if ratio >= 0.7:
        return 1.0
    # 从 ratio=0.7 处的 1.0 线性衰减到 ratio=0 处的 0.35
    return 0.35 + 0.65 * (ratio / 0.7)


# 拼音档生效的汉字相似度下限：只有当候选片段本身是中文、且和歌词行的
# 汉字相似度不算太离谱（>= 此值）时，才信任拼音层面的相似度。见文件
# 头部说明："大数据比你妈还懂你" vs "他们拿他慢慢来的鬼话" 这种毫不
# 相关的两句歌词，汉字相似度只有 0.05，拼音相似度却能到 0.51——如果
# 候选片段是中文，必须先过这道汉字相似度下限，才允许信任拼音分数。
_PINYIN_HANZI_FLOOR = 0.15


def _best_window_match(norm_line: str, segments: List[dict], seg_norm: List[str],
                        start_idx: int, end_time: float, max_span: int = 3,
                        pinyin_line: str = "", seg_pinyin: Optional[List[str]] = None
                        ) -> Dict[str, Tuple[float, Optional[Tuple[int, int]]]]:
    """在 segments[start_idx:] 中、start_time <= end_time 的范围内，
    找与 norm_line 最匹配的连续 segment 拼接（跨 1~max_span 个 segment）。

    **与旧版本的关键区别**：不再把"汉字最佳候选"和"拼音最佳候选"合并
    成一个分数就返回，而是分别独立返回两档里各自的最佳候选，交给调用
    方按不同的"确认阈值"分别判断是否够格采信——拼音档天然比汉字档
    噪声更大（见 `_PINYIN_HANZI_FLOOR` 说明），如果合并成一个分数，
    调用方就没法对两档区别对待，容易被拼音层面偶然凑出的相似度带偏。

    引入了**字数对齐**：候选片段的有效字数和歌词行字数差得越多，最终
    得分会被 `_length_penalty` 按比例打折——避免"文字很像但明显长度
    对不上"的碎片被误选中；如果歌词是中文而候选是 ASR 识别失败输出的
    非中文文本，两边字数单位不可比，会自动放宽这个惩罚。

    若未安装 pypinyin（`pinyin_line`/`seg_pinyin` 为空），拼音档始终
    是 (0.0, None)，调用方自然会跳过它，等价于只用汉字相似度。

    返回 {"hanzi": (score, (seg_i, seg_j) | None), "pinyin": (score, span | None)}。
    """
    best_hanzi = (0.0, None)
    best_pinyin = (0.0, None)
    i = start_idx
    n = len(segments)
    while i < n and segments[i]["start"] <= end_time:
        concat = ""
        py_concat = ""
        for span in range(max_span):
            j = i + span
            if j >= n:
                break
            concat += seg_norm[j]
            if seg_pinyin is not None:
                py_concat = (py_concat + " " + seg_pinyin[j]).strip()
            if not concat:
                continue

            length_penalty = _length_penalty(norm_line, concat)
            hanzi_ratio = difflib.SequenceMatcher(a=norm_line, b=concat, autojunk=False).ratio()
            hanzi_score = hanzi_ratio * length_penalty
            if hanzi_score > best_hanzi[0]:
                best_hanzi = (hanzi_score, (i, j))

            if pinyin_line and py_concat:
                _, cand_is_cjk = _effective_length(concat)
                if (not cand_is_cjk) or hanzi_ratio >= _PINYIN_HANZI_FLOOR:
                    pinyin_ratio = difflib.SequenceMatcher(
                        a=pinyin_line, b=py_concat, autojunk=False).ratio()
                    pinyin_score = pinyin_ratio * length_penalty
                    if pinyin_score > best_pinyin[0]:
                        best_pinyin = (pinyin_score, (i, j))
                # 候选是中文但汉字相似度低于下限：两句大概率毫不相关，
                # 拼音层面的相似度就算数值不低也当噪声丢弃，不参与打分。
        i += 1
    return {"hanzi": best_hanzi, "pinyin": best_pinyin}


def _find_confirmed_window_match(
    norm_line: str, segments: List[dict], seg_norm: List[str],
    search_from_idx: int, cursor_time: float, window_seconds: float,
    pinyin_line: str, seg_pinyin: Optional[List[str]],
    hanzi_confirm_threshold: float, pinyin_confirm_threshold: float,
    hanzi_confirm_threshold_wide: float, pinyin_confirm_threshold_wide: float,
) -> Optional[Tuple[Tuple[int, int], str, float]]:
    """就近窗口 → （不够格再）放宽窗口，两档都要用各自的"确认阈值"
    单独把关；两次都够不到就返回 None（调用方应把这一行放进挂起队列，
    不要采信一个"矮子里拔将军"的弱匹配——弱匹配一旦被采信，游标和
    搜索指针会被带偏，后面一长串本该能对上的行会被连带拖成插值，
    详见文件头部"故障复现"说明）。

    返回 (span, kind, score) 或 None。
    """
    for widen, hanzi_th, pinyin_th in (
        (1.0, hanzi_confirm_threshold, pinyin_confirm_threshold),
        (3.0, hanzi_confirm_threshold_wide, pinyin_confirm_threshold_wide),
    ):
        cand = _best_window_match(
            norm_line, segments, seg_norm, search_from_idx,
            end_time=cursor_time + window_seconds * widen,
            pinyin_line=pinyin_line, seg_pinyin=seg_pinyin,
        )
        hanzi_score, hanzi_span = cand["hanzi"]
        pinyin_score, pinyin_span = cand["pinyin"]
        if hanzi_span is not None and hanzi_score >= hanzi_th:
            return hanzi_span, "hanzi", hanzi_score
        if pinyin_span is not None and pinyin_score >= pinyin_th:
            return pinyin_span, "pinyin", pinyin_score
    return None


def _interpolate_block(pending: List[dict], block_start: float, block_end: float,
                        line_gap: float) -> None:
    """把挂起队列里的若干行，按各自文本字数比例，均分插值到
    `[block_start, block_end]` 这个真实存在的时间区间内。

    这是相对旧版"逐行退化成 cursor+line_gap"的核心改进：旧版每插值
    一行就立即让游标前移一点点（相当于假设这一行只占极短时间），
    完全不管这一行和下一个确定锚点之间实际上还有多长的真实音频
    时长；新版知道"下一个确定点在哪"，所以能把这段真实存在的时长
    合理地分给挂起的这几行（字数越多分到的时长越长），插值结果落在
    正确的时间区间内，不会制造出脱离实际演唱进度的时间戳。

    直接原地修改 `pending` 里每个 dict 的 `start`/`end` 字段。
    """
    n = len(pending)
    if n == 0:
        return
    if block_end <= block_start:
        block_end = block_start + 0.3 * n
    avail = max(block_end - block_start - line_gap * n, 0.3 * n)
    lengths = [max(len(p["text"]), 1) for p in pending]
    total = sum(lengths)
    t = block_start
    for p, ln in zip(pending, lengths):
        dur = avail * (ln / total) if total else avail / n
        dur = max(dur, 0.3)
        p["start"] = t
        p["end"] = t + dur
        p["method"] = "interpolated(pending-block)"
        t = p["end"] + line_gap


def align(asr_result: dict, lyrics_text: str, line_gap: float = 0.15,
          min_anchor: int = 2, window_seconds: float = 45.0,
          anchor_coverage_threshold: float = 0.4,
          hanzi_confirm_threshold: float = 0.5,
          pinyin_confirm_threshold: float = 0.6,
          hanzi_confirm_threshold_wide: float = 0.62,
          pinyin_confirm_threshold_wide: float = 0.72,
          skip_title: bool = True) -> dict:
    """对齐标准歌词与 ASR 时间戳（锚点优先 + 单调窗口约束 + 挂起队列区间插值）。

    `skip_title=True`（默认）时会自动丢弃歌词首行的歌名和所有段落标记
    行（`[Intro]`/`[Verse 1]`/`【副歌】` 等），见 `_load_standard_lines`。

    核心流程：
    1. 字符级归一化精确匹配，得到每行的锚点覆盖率。
    2. 逐行判定："锚点覆盖率够高" 或 "窗口模糊匹配（汉字/拼音分别用
       不同阈值）够格" 才算"确认"；确认了才会真正推进时间游标和 ASR
       搜索指针。够不到确认阈值的行，不再像旧版那样矮子里拔将军地
       立即采信一个弱匹配，而是放进挂起队列，交给后面找到的下一个
       确认点来统一做区间插值（`_interpolate_block`）——避免一次
       误判带偏游标、级联拖垮后面一长串本该能对上的行。详见文件头部
       "故障复现"/"修复"说明。
    3. 处理完所有行后，挂起队列里剩下的（比如结尾渐弱的语气词/和声）
       用最后一个确认点到音频总时长之间的区间统一插值。
    """
    standard_lines = _load_standard_lines(lyrics_text, skip_title=skip_title)
    if not standard_lines:
        raise ValueError("标准歌词为空，无法对齐")

    if not _PINYIN_AVAILABLE:
        print("[align_lyrics_v3] 未安装 pypinyin，拼音模糊匹配已禁用，仅使用汉字相似度"
              "（pip install pypinyin --break-system-packages 可开启）", file=sys.stderr)

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

    # ── 阶段二：单调游标 + 窗口匹配（分级确认）+ 挂起队列区间插值 ──────
    segments = asr_result.get("segments", [])
    seg_norm = [normalize_text(s.get("text", "")) for s in segments]
    seg_pinyin = [to_pinyin(s.get("text", "")) for s in segments] if _PINYIN_AVAILABLE else None

    duration = asr_result.get("duration")
    last_seg_end = segments[-1]["end"] if segments else 0.0
    audio_end = duration if duration else last_seg_end

    result_lines: List[Optional[dict]] = [None] * len(raw_lines)
    cursor_time = 0.0
    search_from_idx = 0
    SLACK = 1.0  # 允许锚点起始时间比游标早最多这么多秒（容忍轻微误差）
    pending: List[dict] = []  # 挂起、尚未确定时间戳的行（result_lines 里对应位置的 dict）

    def _advance_search_idx():
        nonlocal search_from_idx
        while search_from_idx < len(segments) - 1 and segments[search_from_idx]["end"] < cursor_time:
            search_from_idx += 1

    for pos, rl in enumerate(raw_lines):
        stub = {
            "index": rl["index"],
            "text": rl["text"],
            "anchor_coverage": round(rl["coverage"], 2),
        }
        result_lines[pos] = stub

        use_anchor = (
            rl["anchor_start"] is not None
            and rl["coverage"] >= anchor_coverage_threshold
            and rl["anchor_start"] >= cursor_time - SLACK
        )
        confirmed_span = None
        if use_anchor:
            start, end = rl["anchor_start"], rl["anchor_end"]
            if end is None or end <= start:
                end = start + 0.5
            method = f"anchor(coverage={rl['coverage']:.2f})"
            confirmed = True
        elif segments:
            norm_line = normalize_text(rl["text"])
            pinyin_line = to_pinyin(rl["text"]) if _PINYIN_AVAILABLE else ""
            found = _find_confirmed_window_match(
                norm_line, segments, seg_norm, search_from_idx, cursor_time,
                window_seconds, pinyin_line, seg_pinyin,
                hanzi_confirm_threshold, pinyin_confirm_threshold,
                hanzi_confirm_threshold_wide, pinyin_confirm_threshold_wide,
            )
            if found is not None:
                span, kind, score = found
                i, j = span
                start, end = segments[i]["start"], segments[j]["end"]
                method = f"window-match(kind={kind},score={score:.2f})"
                confirmed = True
                confirmed_span = span
            else:
                confirmed = False
        else:
            confirmed = False

        if not confirmed:
            # 不确定：先挂起，不动游标、不动搜索指针，交给后面的确定点
            # 回头统一插值（见 `_interpolate_block`）。
            pending.append(stub)
            continue

        # 找到确定点：先把挂起队列里积压的行，用 [cursor_time, start]
        # 这段真实存在的时间区间做区间插值。
        if pending:
            _interpolate_block(pending, cursor_time, start, line_gap)
            pending = []

        if start < cursor_time:
            start = cursor_time
        if end <= start:
            end = start + 0.5

        stub["start"] = start
        stub["end"] = end
        stub["method"] = method

        cursor_time = end
        if confirmed_span is not None:
            search_from_idx = confirmed_span[0]  # 允许下一行与本行有轻微重叠
        _advance_search_idx()

    # 曲终仍未被回收的挂起行（比如结尾渐弱的语气词/和声）：用最后一个
    # 确定点到音频总时长之间的区间统一插值。
    if pending:
        _interpolate_block(pending, cursor_time, max(audio_end, cursor_time), line_gap)

    for l in result_lines:
        l["start"] = round(l["start"], 3)
        l["end"] = round(l["end"], 3)

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
        print(f"[align_lyrics_v3] {len(low_conf_lines)} 行锚点覆盖率 <34%，已用窗口匹配/区间插值兜底，"
              f"请人工核对（若连续多行都是 interpolated，可能是这段音频 ASR 没识别出文本，"
              f"或 lyrics.txt 漏写了某段重复歌词）：",
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
    parser = argparse.ArgumentParser(description="用标准歌词校正 ASR 时间戳 v3（归一化精确匹配 + 单调窗口约束 + 拼音模糊匹配 + 挂起队列区间插值）")
    parser.add_argument("asr_json")
    parser.add_argument("lyrics_txt")
    parser.add_argument("--line-gap", type=float, default=0.15)
    parser.add_argument("--min-anchor", type=int, default=2)
    parser.add_argument("--window-seconds", type=float, default=45.0,
                        help="窗口匹配兜底时，向前搜索 ASR segment 的时间窗口大小（秒），默认45")
    parser.add_argument("--anchor-coverage-threshold", type=float, default=0.4,
                        help="锚点覆盖率低于此值时不采信锚点，改用窗口匹配，默认0.4")
    parser.add_argument("--hanzi-confirm-threshold", type=float, default=0.5,
                        help="窗口模糊匹配中，汉字相似度达到此值才算\"确认\"，默认0.5")
    parser.add_argument("--pinyin-confirm-threshold", type=float, default=0.6,
                        help="窗口模糊匹配中，拼音相似度达到此值才算\"确认\"（明显高于汉字档，"
                             "因为纯拼音串比较天然噪声更大），默认0.6")
    parser.add_argument("--hanzi-confirm-threshold-wide", type=float, default=0.62,
                        help="就近窗口内两档都不够格确认时，会放宽到3倍窗口再试一次，"
                             "这是放宽后汉字档的确认门槛（比就近窗口更严格），默认0.62")
    parser.add_argument("--pinyin-confirm-threshold-wide", type=float, default=0.72,
                        help="放宽窗口后拼音档的确认门槛，默认0.72")
    parser.add_argument("--no-skip-title", dest="skip_title", action="store_false",
                        help="默认会自动跳过歌词首行的歌名，加此参数关闭该行为"
                             "（歌词第一行本来就是要唱的正文时使用）")
    parser.set_defaults(skip_title=True)
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
            hanzi_confirm_threshold=args.hanzi_confirm_threshold,
            pinyin_confirm_threshold=args.pinyin_confirm_threshold,
            hanzi_confirm_threshold_wide=args.hanzi_confirm_threshold_wide,
            pinyin_confirm_threshold_wide=args.pinyin_confirm_threshold_wide,
            skip_title=args.skip_title,
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
