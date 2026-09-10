#!/usr/bin/env python3
"""歌词对齐校正脚本 v3.3（全局候选生成 + 单调 DP 最优组合，取代逐行贪心挂起队列）。

不需要 ctc-forced-aligner/torch/onnxruntime 这类重依赖，只需要纯 Python
小包 pypinyin（可选），安装成功率接近 100%，适合 align_lyrics_forced.py
因为环境限制装不上/跑不通时使用。

用法：
    python align_lyrics_v3.py asr_raw.json lyrics.txt --save-path lyrics_timed.json --save-srt lyrics.srt

依赖：仅标准库；若安装了 `pypinyin`（拼音模糊匹配）和/或
`opencc-python-reimplemented`（简繁转换），效果更好，未安装时自动退化，
不会报错。

## v3.3 本次优化：从"逐行贪心 + 挂起队列"改成"全局候选生成 + 单调 DP 最优组合"

### 故障复现（用真实歌曲《最后一只渡渡鸟死于1681年》数据实测发现）

v3.2 虽然把字符级精确匹配下沉到了局部窗口内（不再用全局 `difflib` 求
锚点），但决策方式仍然是**逐行贪心**：每一行只要窗口内最佳候选的得分
没过"确认阈值"（默认 0.42），就整行放弃、扔进挂起队列，靠前后已确认
的行做区间插值兜底。这在下面这行上出了问题：

```
标准歌词：最后一只渡渡鸟  死于一六八一年  无人问  无人怜   （20字）
ASR原文：最后一只 独独鸟 四月一六八一年                    （14字，
          "死于"被识别成"四月"，"渡渡"被识别成"独独"）
```

字符级精确匹配能找到的完全匹配块是"最后一只"（4字）+"鸟"（1字）+
"一六八一年"（5字）＝10字，覆盖率 0.5，词组得分（超线性加权后）
约 0.23，综合得分约 0.35——**已经是全曲能找到的最佳候选，且明显是
对的（人工核对音频可确认）**，但恰好卡在 0.42 的硬阈值之下，被判定
为"不够格"，于是整行改用插值，得到一个和真实演唱时间点几乎无关的
时间戳。类似情况在全曲重复较多、ASR 识别有噪声的段落里反复出现：
"及格线"这种一刀切的硬阈值，天然没法区分"这就是全曲能找到的最佳
匹配、应该采信"和"这是噪声、宁可插值"这两种情况——因为**它只看
单独这一行、单独这一次候选的绝对分数，不看"这个分配和其他所有行的
分配放在一起是不是全局最优"**。

### 根因

逐行贪心决策 + 硬阈值本质上是在做"局部最优"：处理第 N 行的时候，
完全不知道第 N+1、N+2... 行会花落谁家、以及它们分别能打多少分。
一行 0.35 分的候选，如果它是"这个位置目前能找到的最强证据、且跟前后
行的时间顺序完全自洽"，理应比一段和真实时间毫无关系的插值更可信；
但只用固定阈值卡这一行，没法引入"和整体分配放在一起看"这个信息。

### 修复：两阶段"候选生成 + 全局单调 DP"

新版本彻底放弃"逐行贪心 + 挂起队列 + 硬确认阈值"，改成：

**阶段一（候选生成，允许一处 ASR 匹配多处歌词）**：对每一行歌词，在
**全曲**范围内（不再受游标/局部窗口限制）扫描所有 `[i, i+span]`
（`span` 从 0 到 `max_span-1`）的连续 ASR segment 拼接，用字符级精确
匹配（单字+词组加权，见 `_char_match_score`，逻辑不变）和拼音模糊
匹配（应对同音字）分别打分，取两者较高值作为这个候选窗口的得分。
只要净收益（得分减去一个很小的"启用成本" `min_net_score`，默认
0.05，过滤掉纯偶然的单字巧合）为正就保留为候选——**不做任何"这一行
是不是该行"的判断，同一个 ASR 位置完全可以，也应该，同时成为副歌
第 1/2/3/4 遍等多处重复歌词行的候选**，把"消歧"完全留给阶段二。

**阶段二（全局单调 DP，选出让整体最合"字数关系"的组合）**：把"给每行
歌词挑一个候选窗口（或跳过不挑）"建模成一个**带权最长单调子序列**
问题——用一个随行号推进、按"已用到的最靠后 ASR segment 下标"做状态
的动态规划，一次性求出**整首歌**"每行选哪个候选（或跳过）"的组合，
使得（a）候选之间的 ASR 位置随歌词行号单调不减（复现"歌词是按时间
顺序唱的"这个约束，重复段落只会被分配到实际唱到的那一次，不会被
拉到别处），且（b）所有被选中候选的得分之和最大。这正是题目里说的
"不同的匹配方式，通过计算，找到让最终模糊匹配效果最好的那种组合"：
不再是"这一行分数够不够格"的独立判断，而是"这个全局组合方案是不是
比其他任何组合方案的总分都高"。DP 的状态转移天然允许"允许 ASR 一个
地方和歌词的多个地方匹配"（阶段一各行的候选池本来就可能重叠/共享
同一段 ASR 位置），DP 只会在真正符合时间顺序、且整体收益最大的情况
下才把某处 ASR 位置分给某一行，重复的其它几处会在各自应该匹配的行
位置上分别被分配到（因为每一次重复在候选池里都以自己的时间位置重复
出现一份）。

最终没有被 DP 选中候选的行（DP 判断"跳过更优"，比如全曲确实没唱到、
或者证据太弱以至于插值更靠谱），沿用旧版的**挂起区间插值**：在两个
相邻的"确定点"之间，按各行字数比例分配时间（`_interpolate_block`，
逻辑不变）。

这个改法在算法上等价于把"歌词行序列"和"ASR segment 序列"做一次
**加权、允许跳过、允许候选窗口任意长度**的序列对齐（类似 DTW /
Needleman-Wunsch 的思路，但状态空间是"行号 × 已用到的 segment 下标"，
而不是"字符 × 字符"，规模是可控的：典型一首歌 `n_lines × n_segments`
在几千到几万量级，纯 Python 里毫秒级即可算完）。

以下小节延续 v3.2 及更早版本的说明（简繁转换、拼音模糊匹配、字数
对齐惩罚、段落标记/歌名过滤、挂起区间插值这些子模块本身逻辑不变，
只是不再有独立的"确认阈值"/"局部窗口"/"游标"概念——统一交给全局 DP
判断要不要用某个候选）：

## 词组加权（对应"更好地利用完全匹配上的单字和词组"）

一行歌词命中的证据强度不应该只看"匹配了多少个字"（覆盖率），同样
匹配 6 个字，"一次性连续匹配一个 6 字词组"比"6 个字分散命中、彼此
不连续"证据力强得多——后者更可能是偶然的字符重合（尤其中文单字
同音同形字很多）。评分同时使用两个信号并加权：

- **覆盖率**（`matched_chars / line_chars`）：对应"字数关系"，衡量
  匹配的字符数量在这一行歌词里占比多少。
- **词组得分**（`sum(block.size ** 1.4) / line_chars ** 1.4`，封顶
  1.0）：对长度做超线性加权，一个 6 字连续块贡献远大于 6 个孤立
  1 字块（`6**1.4 ≈ 11.4`，而 `6 * 1**1.4 = 6`），从而让"完全匹配
  上的词组"在最终得分里占主导。

最终汉字得分 = `0.45 * 覆盖率 + 0.55 * 词组得分`，再乘以字数比例
惩罚系数（候选片段字符数和歌词行字符数差太多则打折）。

## 拼音模糊匹配（应对同音字/近音字类 ASR 错误）

中文 ASR 出错很大一部分是同音字/近音字替换（比如把"再见"识别成
"在见"），这类错误在字形上完全不匹配、但读音一样或很接近。每个候选
片段都转成拼音序列（`pypinyin.lazy_pinyin`），再算一次
`difflib.SequenceMatcher` 相似度，作为字符精确匹配之外的第二条命中
通道；只有候选片段本身是中文、且和歌词行的汉字相似度不算太离谱时
才信任这条通道的分数（避免"风马牛不相及但拼音偶然相似"的噪声匹配），
且额外乘一个折扣系数（`--pinyin-discount`，默认0.85）——拼音通道
天然噪声更大、又没有字符级时间精度，整体上应该比汉字通道更保守。

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


# ── ASR 按 segment 展开成逐字符时间戳（每个 segment 一个列表）────────

def _segment_char_lists(asr_result: dict) -> List[List[dict]]:
    """把每个 ASR segment 分别展开成逐字符时间戳列表（不跨 segment 合并）。

    按 segment 分开保留，供候选生成阶段按需拼接任意 `[i, j]` 跨度，同时
    保留每个字符的精确时间戳，用来在"选中"之后给出字符级精度的行首/
    行尾时间，而不是整个 segment 的边界时间——即使一个 segment 里混杂
    了两行歌词的文字（ASR 断句和歌词分行经常对不齐），也能只取出真正
    命中这一行的那些字符各自的时间。

    识别失败产生的占位乱码字符（`�`）会被跳过——这类字符不仅本身没有
    文本信息、无法参与匹配，它的时间跨度往往异常巨大，留着没有任何
    用处。
    """
    result = []
    for seg in asr_result.get("segments", []):
        chars = []
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
                    norm = normalize_char(ch)
                    if not norm:
                        continue
                    t = start + (end - start) * (i / max(n, 1))
                    chars.append({"char": ch, "norm": norm, "time": round(t, 3)})
        else:
            text = seg.get("text", "")
            start, end = seg["start"], seg["end"]
            n = len(text)
            for i, ch in enumerate(text):
                if _is_ignorable(ch):
                    continue
                norm = normalize_char(ch)
                if not norm:
                    continue
                t = start + (end - start) * (i / max(n, 1))
                chars.append({"char": ch, "norm": norm, "time": round(t, 3)})
        result.append(chars)
    return result


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


# ── 字数对齐辅助：字数比例差得越多，得分惩罚越重 ─────────────────────

def _length_penalty(line_len: int, cand_len: int) -> float:
    """字数对齐惩罚系数，范围 [0.35, 1.0]。

    双方都是"归一化后参与比较的字符数"（已剔除标点/空白），差距在
    30% 以内不惩罚，差距越大惩罚越重，最低封顶到 0.35（不直接判死刑，
    因为相似度本身已经包含了大量信息，长度只是辅助信号）。
    """
    if line_len == 0 or cand_len == 0:
        return 0.5
    ratio = min(line_len, cand_len) / max(line_len, cand_len)
    if ratio >= 0.7:
        return 1.0
    # 从 ratio=0.7 处的 1.0 线性衰减到 ratio=0 处的 0.35
    return 0.35 + 0.65 * (ratio / 0.7)


# 拼音档生效的汉字相似度下限：只有当候选片段本身是中文、且和歌词行的
# 汉字相似度不算太离谱（>= 此值）时，才信任拼音层面的相似度。
_PINYIN_HANZI_FLOOR = 0.15


def _is_cjk_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF or  # CJK统一表意文字
        0x3400 <= cp <= 0x4DBF or  # 扩展A
        0xF900 <= cp <= 0xFAFF     # 兼容表意文字
    )


def _char_match_score(line_norm: str, cand_chars: List[dict]) -> Tuple[float, Optional[float], Optional[float], int]:
    """歌词行 vs 候选片段的字符级精确匹配打分（核心：单字/词组加权）。

    用 `difflib.SequenceMatcher` 在"歌词行归一化字符串"和"候选片段
    归一化字符串"之间求 `matching_blocks`——这些块就是双方**完全匹配
    上的单字和词组**（长度 1 的块是孤立单字命中，长度 >1 的块是连续
    词组命中）。

    打分同时用两个信号：
    - 覆盖率 = 命中字符总数 / 歌词行字符数（"字数关系"）。
    - 词组得分 = `sum(block.size**1.4) / line_len**1.4`（封顶 1.0）：
      对块长度做超线性加权，让"一次性连续命中一个长词组"比"命中相同
      总字数但打散成很多孤立单字"的证据强度高得多，避免被中文里大量
      存在的单字同音/同形巧合带偏。

    返回 `(combined_score, anchor_start, anchor_end, matched_chars)`；
    完全没有命中任何字符时 `combined_score=0.0`，`anchor_start/end`
    为 `None`。
    """
    cand_norm = "".join(c["norm"] for c in cand_chars)
    line_len = len(line_norm)
    if line_len == 0 or not cand_norm:
        return 0.0, None, None, 0

    sm = difflib.SequenceMatcher(a=line_norm, b=cand_norm, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size > 0]
    if not blocks:
        return 0.0, None, None, 0

    matched_chars = sum(b.size for b in blocks)
    coverage = matched_chars / line_len
    phrase_score = sum(b.size ** 1.4 for b in blocks)
    phrase_ratio = min(phrase_score / (line_len ** 1.4), 1.0)
    combined = 0.45 * coverage + 0.55 * phrase_ratio

    times = [cand_chars[b.b + k]["time"] for b in blocks for k in range(b.size)]
    anchor_start, anchor_end = min(times), max(times)
    return combined, anchor_start, anchor_end, matched_chars


# ══════════════════════════════════════════════════════════════════
# 阶段一：候选生成（全曲扫描，允许一处 ASR 匹配多处歌词）
# ══════════════════════════════════════════════════════════════════

# 每个候选的结构：(seg_start_idx, seg_end_idx, net_score, raw_score, kind,
#                   anchor_start, anchor_end)
Candidate = Tuple[int, int, float, float, str, Optional[float], Optional[float]]


def _line_candidates(
    line_norm: str, pinyin_line: str,
    segments: List[dict], seg_chars: List[List[dict]],
    seg_pinyin: Optional[List[str]], seg_norm_text: Optional[List[str]],
    max_span: int, min_net_score: float, pinyin_discount: float,
    max_candidates: int = 60,
) -> List[Candidate]:
    """在**全曲所有** ASR segment 上（不受游标/窗口限制）为一行歌词生成
    候选窗口列表。

    对每个起点 `i`、每个跨度 `span in [0, max_span)`（即拼接
    `segments[i..i+span]`）都独立打分：字符级精确匹配（`_char_match_score`）
    与拼音模糊匹配各算一次，取较高值作为这个候选窗口的原始得分
    `raw_score`，再减去一个很小的"启用成本" `min_net_score`得到
    `net_score`——`net_score<=0` 的候选直接丢弃（不带来净收益，DP 里
    等价于必然不如"跳过这一行"，留着只会浪费计算）。

    这一步**故意不做任何"消歧"**：同一段 ASR 音频完全可以，也应该，
    同时是好几个重复歌词行（比如副歌反复出现的"最后一只渡渡鸟……"）
    各自候选池里的高分候选——把"这一次到底该分给哪一行"完全交给
    阶段二的全局 DP 通过时间顺序约束和总分最大化来决定。
    """
    candidates: List[Candidate] = []
    n = len(segments)
    for i in range(n):
        cand_chars: List[dict] = []
        py_concat = ""
        for span in range(max_span):
            j = i + span
            if j >= n:
                break
            cand_chars = cand_chars + seg_chars[j]
            if seg_pinyin is not None:
                py_concat = (py_concat + " " + seg_pinyin[j]).strip()
            if not cand_chars:
                continue

            length_penalty = _length_penalty(len(line_norm), len(cand_chars))
            hanzi_score, a_start, a_end, matched_chars = _char_match_score(line_norm, cand_chars)
            hanzi_score *= length_penalty

            pinyin_score = 0.0
            if pinyin_line and py_concat and seg_norm_text is not None:
                concat_text = "".join(seg_norm_text[i:j + 1])
                cjk_count = sum(1 for ch in concat_text if _is_cjk_char(ch))
                cand_is_cjk = cjk_count > 0
                hanzi_ratio = matched_chars / max(len(line_norm), 1)
                # 候选是中文但汉字相似度低于下限：两句大概率毫不相关，
                # 拼音层面的相似度就算数值不低也当噪声丢弃。
                if (not cand_is_cjk) or hanzi_ratio >= _PINYIN_HANZI_FLOOR:
                    pinyin_ratio = difflib.SequenceMatcher(
                        a=pinyin_line, b=py_concat, autojunk=False).ratio()
                    pinyin_score = pinyin_ratio * length_penalty * pinyin_discount

            if hanzi_score >= pinyin_score:
                raw_score, kind, anchor_s, anchor_e = hanzi_score, "hanzi", a_start, a_end
            else:
                raw_score, kind, anchor_s, anchor_e = pinyin_score, "pinyin", None, None

            net = raw_score - min_net_score
            if net > 0:
                candidates.append((i, j, net, raw_score, kind, anchor_s, anchor_e))

    if len(candidates) > max_candidates:
        candidates.sort(key=lambda c: c[2], reverse=True)
        candidates = candidates[:max_candidates]
    return candidates


# ══════════════════════════════════════════════════════════════════
# 阶段二：全局单调 DP（在所有行×所有候选里，选出总分最高、且 ASR 位置
# 随行号单调不减的组合方案）
# ══════════════════════════════════════════════════════════════════

def _global_align(n_lines: int, n_segments: int,
                   candidates_per_line: List[List[Candidate]]) -> List[Optional[Candidate]]:
    """带权最长单调子序列 DP：
    `dp[k]` = 只用"结束位置 <= k-1"的候选，处理完当前行为止能拿到的
    最大累计净得分（`dp[0]=0.0` 表示"一个候选都没用"这个基线，任何
    时候都可行）。

    对每一行，允许"跳过"（不消耗任何 segment、`dp` 数组原样传递）或者
    "使用某个候选 `(a, b, net, ...)`"（要求此前所有已使用候选的结束
    位置 `<= a`，即允许候选起点和上一次用到的结束点重叠 1 个 segment
    ——同一个 ASR segment 里混杂了两行歌词文字时很常见）。

    行与行之间独立地在自己的候选池里挑选，互不冲突时（挑到的 segment
    区间不违反单调顺序）就都能生效——这正是"全局最优组合"：不是谁先
    处理谁先占坑，而是一次性算出让**总分最高**的整体分配方案。

    返回 `assign`：长度 `n_lines`，`assign[i]` 是被选中的候选元组，或
    `None`（DP 判断这一行跳过、留给插值兜底更划算）。
    """
    m = n_segments
    dp = [0.0] * (m + 1)  # dp[k]: 最大累计净得分，约束"已用结束位置 <= k-1"
    chosen_history: List[List[Optional[Candidate]]] = []

    for cands in candidates_per_line:
        new_dp = dp[:]  # 基线：这一行跳过，分数不变
        cand_for_slot: Dict[int, Candidate] = {}
        for (a, b, net, raw_score, kind, anchor_s, anchor_e) in cands:
            base = dp[a + 1]  # 允许起点 a 和上一个候选的结束位置重叠
            total = base + net
            slot = b + 1
            if total > new_dp[slot]:
                new_dp[slot] = total
                cand_for_slot[slot] = (a, b, net, raw_score, kind, anchor_s, anchor_e)

        # 前缀最大化：new_dp 本身不保证单调（某个 slot 被这一行的候选
        # 顶高了，右边更大的 slot 未必也跟着变大），要转成"截止到 k 的
        # 历史最大值"才能作为下一行的 `dp[a+1]` 查询基线使用。
        prefixed = [0.0] * (m + 1)
        chosen = [None] * (m + 1)
        best_val = new_dp[0]
        best_cand = cand_for_slot.get(0)
        prefixed[0] = best_val
        chosen[0] = best_cand
        for k in range(1, m + 1):
            if new_dp[k] > best_val:
                best_val = new_dp[k]
                best_cand = cand_for_slot.get(k)
            prefixed[k] = best_val
            chosen[k] = best_cand

        dp = prefixed
        chosen_history.append(chosen)

    # 回溯：从终点往回走，chosen_history[i][k] 直接记录了"在这条最优
    # 路径上，第 i 行是否使用了某个候选"（None 表示这一行在最优路径里
    # 被跳过），不需要额外比较 dp 数组。
    n_lines = len(candidates_per_line)
    assign: List[Optional[Candidate]] = [None] * n_lines
    k = m
    for i in range(n_lines - 1, -1, -1):
        cand = chosen_history[i][k]
        if cand is not None:
            assign[i] = cand
            a = cand[0]
            k = a + 1
        # 否则这一行在最优路径里被跳过，k 不变，继续回溯上一行
    return assign


def _interpolate_block(pending: List[dict], block_start: float, block_end: float,
                        line_gap: float) -> None:
    """把挂起队列里的若干行，按各自文本字数比例，均分插值到
    `[block_start, block_end]` 这个真实存在的时间区间内。

    知道"下一个确定点在哪"，所以能把这段真实存在的时长合理地分给挂起
    的这几行（字数越多分到的时长越长），插值结果落在正确的时间区间
    内，不会制造出脱离实际演唱进度的时间戳。

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
          max_span: int = 4,
          min_net_score: float = 0.05,
          pinyin_discount: float = 0.85,
          max_candidates_per_line: int = 60,
          skip_title: bool = True,
          # 以下参数为兼容旧版 v3.2 CLI 保留，新算法不再使用固定阈值/
          # 局部窗口做逐行确认判断（全部交给全局 DP），传入会被忽略。
          window_seconds: Optional[float] = None,
          hanzi_confirm_threshold: Optional[float] = None,
          pinyin_confirm_threshold: Optional[float] = None,
          hanzi_confirm_threshold_wide: Optional[float] = None,
          pinyin_confirm_threshold_wide: Optional[float] = None) -> dict:
    """对齐标准歌词与 ASR 时间戳（全局候选生成 + 单调 DP 最优组合 +
    挂起区间插值兜底）。

    `skip_title=True`（默认）时会自动丢弃歌词首行的歌名和所有段落标记
    行（`[Intro]`/`[Verse 1]`/`【副歌】` 等），见 `_load_standard_lines`。

    核心流程：
    1. **候选生成**：每一行歌词独立在全曲所有 ASR segment 上扫描
       （不受游标/窗口限制），用字符级精确匹配 + 拼音模糊匹配算出
       每个候选窗口 `[i, i+span]` 的得分，净收益为正的都保留——同一段
       ASR 音频可以同时是好几个重复歌词行的候选，消歧留给下一步。
    2. **全局单调 DP**：一次性求出"每行选哪个候选（或跳过）"的组合，
       使候选 ASR 位置随歌词行号单调不减、且所有被选中候选得分之和
       最大。这是全局最优解，不是逐行贪心。
    3. DP 判断"跳过更优"的行，用相邻两个确定点之间的时间区间，按
       字数比例插值填充（`_interpolate_block`，逻辑不变）。
    """
    standard_lines = _load_standard_lines(lyrics_text, skip_title=skip_title)
    if not standard_lines:
        raise ValueError("标准歌词为空，无法对齐")

    if not _PINYIN_AVAILABLE:
        print("[align_lyrics_v3] 未安装 pypinyin，拼音模糊匹配已禁用，仅使用汉字精确匹配"
              "（pip install pypinyin --break-system-packages 可开启）", file=sys.stderr)

    line_norms = [normalize_text(line) for line in standard_lines]
    line_pinyins = [to_pinyin(line) if _PINYIN_AVAILABLE else "" for line in standard_lines]

    segments = asr_result.get("segments", [])
    seg_chars = _segment_char_lists(asr_result)
    seg_norm_text = [normalize_text(s.get("text", "")) for s in segments]
    seg_pinyin = [to_pinyin(s.get("text", "")) for s in segments] if _PINYIN_AVAILABLE else None

    duration = asr_result.get("duration")
    last_seg_end = segments[-1]["end"] if segments else 0.0
    audio_end = duration if duration else last_seg_end

    result_lines: List[Optional[dict]] = [None] * len(standard_lines)
    for pos, text in enumerate(standard_lines):
        result_lines[pos] = {"index": pos, "text": text}

    assign: List[Optional[Candidate]] = [None] * len(standard_lines)
    if segments:
        # 阶段一：候选生成（每行独立、全曲范围）
        candidates_per_line = [
            _line_candidates(
                line_norm, pinyin_line, segments, seg_chars, seg_pinyin, seg_norm_text,
                max_span, min_net_score, pinyin_discount, max_candidates_per_line,
            ) if line_norm else []
            for line_norm, pinyin_line in zip(line_norms, line_pinyins)
        ]
        # 阶段二：全局单调 DP，求总分最高的组合方案
        assign = _global_align(len(standard_lines), len(segments), candidates_per_line)

    cursor_time = 0.0
    pending: List[dict] = []
    any_confirmed = False

    for pos, cand in enumerate(assign):
        stub = result_lines[pos]
        if cand is None:
            pending.append(stub)
            continue

        a, b, net, raw_score, kind, anchor_s, anchor_e = cand
        if kind == "hanzi" and anchor_s is not None:
            start, end = anchor_s, anchor_e
        else:
            start, end = segments[a]["start"], segments[b]["end"]
        if end is None or end <= start:
            end = (start or 0.0) + 0.5

        any_confirmed = True
        if pending:
            _interpolate_block(pending, cursor_time, start, line_gap)
            pending = []

        if start < cursor_time:
            start = cursor_time
        if end <= start:
            end = start + 0.5

        stub["start"] = start
        stub["end"] = end
        stub["method"] = f"global-align(kind={kind},score={raw_score:.2f})"
        cursor_time = end

    if pending:
        _interpolate_block(pending, cursor_time, max(audio_end, cursor_time), line_gap)

    if not any_confirmed:
        raise RuntimeError(
            "ASR 识别结果与标准歌词完全没有匹配上任何字符/词组，无法对齐。"
            "请检查：1) 音频与歌词是否对应；2) ASR 语言/模型设置是否正确；"
            "3) 是否安装了 opencc 以获得更好的简繁转换。"
        )

    for l in result_lines:
        l["start"] = round(l["start"], 3)
        l["end"] = round(l["end"], 3)

    # ── 最终保险：单调递增 + 最小间隔（正常情况下 DP 已保证 ASR 位置
    # 单调，这里对时间戳兜底，防止字符级锚点时间出现极小的乱序抖动）──
    for i in range(1, len(result_lines)):
        prev, cur = result_lines[i - 1], result_lines[i]
        if cur["start"] < prev["end"] + line_gap:
            cur["start"] = round(prev["end"] + line_gap, 3)
        if cur["end"] <= cur["start"]:
            cur["end"] = round(cur["start"] + 0.5, 3)

    low_conf_lines = [
        (l["index"], l["text"], l["method"])
        for l in result_lines if l["method"] == "interpolated(pending-block)"
    ]
    if low_conf_lines:
        print(f"[align_lyrics_v3] {len(low_conf_lines)} 行没有找到够格的字符/词组匹配，已用区间插值兜底，"
              f"请人工核对（若连续多行都是 interpolated，可能是这段音频 ASR 没识别出文本，"
              f"或 lyrics.txt 漏写了某段重复歌词）：",
              file=sys.stderr)
        for idx, text, method in low_conf_lines:
            print(f"  行{idx}: method={method}  文本={text}", file=sys.stderr)

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
    parser = argparse.ArgumentParser(description="用标准歌词校正 ASR 时间戳 v3.3（全局候选生成 + 单调 DP 最优组合 + 拼音模糊兜底 + 挂起队列区间插值）")
    parser.add_argument("asr_json")
    parser.add_argument("lyrics_txt")
    parser.add_argument("--line-gap", type=float, default=0.15)
    parser.add_argument("--max-span", type=int, default=4,
                        help="候选窗口最多拼接几个连续 ASR segment，默认4")
    parser.add_argument("--min-net-score", type=float, default=0.05,
                        help="候选窗口的启用成本：原始得分需要超过此值才会被当作候选参与"
                             "全局 DP（防止偶然的单字巧合被采信），默认0.05；"
                             "调低会让更多弱证据参与 DP 竞争（可能救回更多行，也可能引入"
                             "噪声候选），调高更保守")
    parser.add_argument("--pinyin-discount", type=float, default=0.85,
                        help="拼音模糊匹配通道的折扣系数（拼音通道没有字符级时间精度、"
                             "噪声也更大，整体上应比汉字通道更保守），默认0.85")
    parser.add_argument("--max-candidates-per-line", type=int, default=60,
                        help="每行歌词最多保留的候选窗口数（按得分取前N个），用于控制"
                             "全局 DP 的计算量，默认60，一般不需要调")
    parser.add_argument("--no-skip-title", dest="skip_title", action="store_false",
                        help="默认会自动跳过歌词首行的歌名，加此参数关闭该行为"
                             "（歌词第一行本来就是要唱的正文时使用）")
    parser.set_defaults(skip_title=True)
    # 以下参数为兼容旧版 v3.2 调用方式保留（接受但忽略，新算法用全局 DP
    # 取代了逐行"局部窗口+固定确认阈值"的判断方式，不再需要这些参数）。
    parser.add_argument("--window-seconds", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--hanzi-confirm-threshold", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--pinyin-confirm-threshold", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--hanzi-confirm-threshold-wide", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--pinyin-confirm-threshold-wide", type=float, default=None, help=argparse.SUPPRESS)
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

    for legacy_flag in ("window_seconds", "hanzi_confirm_threshold", "pinyin_confirm_threshold",
                        "hanzi_confirm_threshold_wide", "pinyin_confirm_threshold_wide"):
        if getattr(args, legacy_flag) is not None:
            print(f"[align_lyrics_v3] 提示：--{legacy_flag.replace('_', '-')} 已在 v3.3 中废弃"
                  f"（全局 DP 取代了局部窗口+固定阈值），本次调用会忽略该参数。", file=sys.stderr)

    try:
        result = align(
            asr_result, lyrics_text, line_gap=args.line_gap,
            max_span=args.max_span,
            min_net_score=args.min_net_score,
            pinyin_discount=args.pinyin_discount,
            max_candidates_per_line=args.max_candidates_per_line,
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