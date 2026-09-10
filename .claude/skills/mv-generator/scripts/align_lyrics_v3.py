#!/usr/bin/env python3
"""歌词对齐校正脚本 v3（局部窗口字符级精确匹配 + 词组加权 + 拼音兜底 + 挂起队列区间插值）。

不需要 ctc-forced-aligner/torch/onnxruntime 这类重依赖，只需要纯 Python
小包 pypinyin（可选），安装成功率接近 100%，适合 align_lyrics_forced.py
因为环境限制装不上/跑不通时使用。

用法：
    python align_lyrics_v3.py asr_raw.json lyrics.txt --save-path lyrics_timed.json --save-srt lyrics.srt

依赖：仅标准库；若安装了 `pypinyin`（拼音模糊匹配）和/或
`opencc-python-reimplemented`（简繁转换），效果更好，未安装时自动退化，
不会报错。

## 本次优化（v3.2）：废弃"全局字符锚点"阶段，改成"逐行局部窗口"统一匹配

### 故障复现（用真实歌曲《最后一只渡渡鸟死于1681年》数据实测发现）

这首歌大量段落是副歌重复（同一段"最后一只渡渡鸟 死于一六八一年……"
唱了 4 遍）、以及后半段每句都在化用前面出现过的字词（"曾经/如今/我看见/
我听见/它不会飞翔"之类的短语在全曲反复出现）。旧版本（v3.1 及更早）
第一阶段用**一次性的全局** `difflib.SequenceMatcher(standard_norm,
asr_norm)` 在"整首歌词的字符串"和"整份 ASR 转写的字符串"之间求最长
公共子序列锚点——这在歌词高度重复时是错的：`difflib` 只会给出**某一种**
全局最优的公共子序列匹配，完全不管歌词实际的演唱顺序，于是"死于一六
八一年"这几个字可能被匹配到第 1 遍副歌，也可能被匹配到第 3 遍副歌，
和这一行歌词自己实际在第几分钟唱毫无关系；一旦重复段落一多，越往后的
重复段落越容易被前面已经"抢先"用掉的锚点位置带偏，或者干脆抢不到锚点、
覆盖率归零。

实测复现：全曲 58 行标准歌词里，从第 22 行（"利爪撕裂 它的羽毛……"）
开始到第 57 行（倒数第二行）之间，**32 行**锚点覆盖率 <34%，绝大多数
直接进了挂起队列，最后被一次性摊在"最后一个确定点"到"第 55 行才找到
的一个窗口匹配"之间做区间插值——中间横跨了全曲过半的真实演唱时长
（覆盖了 3 段副歌 + 2 段主歌），插值结果和真实演唱时间点基本没有关系，
虽然算法本身没有崩，但对齐质量完全不可用。

### 根因

全局锚点阶段和后面"逐行窗口匹配"阶段用的是两套完全不同的搜索范围
（一个不受游标约束搜全曲，一个受游标约束只搜局部窗口），这本身就是
自相矛盾的：**锚点阶段恰恰是最需要"只在游标附近找"的地方**——它面对
的正是歌词里最容易重复、最需要靠"当前唱到哪儿了"来消歧的那部分文字
（单字锚点，比如"我"、"它"，一首歌里能出现几十次）。全局搜索完全没有
时间顺序约束，越到歌曲后半段、重复的历史越长，越容易被带偏。

### 修复：锚点阶段并入窗口匹配，全程只在游标局部窗口内做字符级精确匹配

新版本删除了独立的"全局字符锚点"阶段，把**字符级精确匹配**下沉到
`_best_window_match` 内部，和窗口匹配阶段合并成同一次搜索：

1. 对 `[search_from_idx, cursor_time + window]` 窗口内的每个候选
   ASR segment 拼接跨度（1~`max_span` 个 segment），不再只算"整句
   相似度"，而是对**这一小段候选文本**和**当前歌词行**做逐字符
   `difflib.SequenceMatcher`，取出它的 `matching_blocks`——这些块
   就是双方"完全匹配上的单字和词组"：块越长（词组），匹配置信度
   越高；散落的单字块，置信度贡献按比例打折（见下方"词组加权"）。
2. 因为这一步的搜索范围天生就被 `cursor_time`/`search_from_idx`
   约束在"当前游标往后一小段窗口"内，重复歌词段落只会匹配到**这一次**
   实际唱到的那个窗口，不会被拉到几分钟后/前的另一次重复。
3. 匹配到的字符块自带精确时间戳（来自 ASR word-level 时间戳或
   segment 内插值），可以直接取"匹配上的字符里最早/最晚的时间"作为
   这一行的 `start`/`end`——比旧版"整个 segment 的起止时间"更精确
   （旧版即使窗口匹配对了，给出的时间戳也是整个 segment 的边界，可能
   包含这个 segment 里不属于这一行的部分）。
4. 找不到任何精确字符匹配、或精确匹配质量不够时，退化到**拼音层面**
   的整句模糊相似度作为第二命中通道（应对同音字 ASR 错误），这条通道
   仍然只用 segment 边界时间（没有字符级精度可言）。
5. 决策方式仍然沿用"挂起队列 + 区间插值"（见下方历史说明）：某一行
   在窗口内两个通道都够不到确认阈值时，不立即采信弱匹配、不移动
   游标，而是放进挂起队列，等后面找到的下一个确定点，再按字数比例
   对这一段做区间插值。

### 词组加权（对应"更好地利用完全匹配上的单字和词组"）

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
惩罚系数（候选片段字符数和歌词行字符数差太多则打折，逻辑同旧版
`_length_penalty`，现在直接作用在"参与比较的归一化字符数"上，比旧版
基于原始文本长度更准确，不受标点/空白干扰）。

以下是历史版本的说明（对齐算法主体思路不变，仅"字符级精确匹配"这一步
的搜索范围从"全曲不限"改成了"游标局部窗口"，且和窗口模糊匹配合并成
了同一次搜索；简繁转换、拼音模糊匹配、字数对齐惩罚、段落标记/歌名
过滤、挂起队列区间插值，都延续下来）：

## 挂起队列（pending queue）+ 区间插值

核心思路是**"不确定就先放着，等后面找到确定的锚点再回头统一处理"**：

1. 逐行尝试找"高置信度"确认（字符级精确匹配的汉字得分达到
   `hanzi_confirm_threshold`；或拼音相似度达到`pinyin_confirm_threshold`，
   拼音档门槛明显更高，因为纯拼音串比较噪声更大）。
2. 找到高置信度确认：视为"确定点"。如果这个确定点之前有挂起
   （pending）、还没决定时间戳的行，此时才把这一整段挂起的行，按
   **各行文本字数比例**，在"上一个确定点结束时间"到"这个确定点开始
   时间"之间做区间插值分配。
3. 找不到高置信度确认：这一行进入挂起队列，**不移动游标、不移动
   ASR 搜索指针**，直接处理下一行——把"是否要退化成插值"的决定权
   交给未来，而不是当场用一个勉强及格的弱匹配去赌。
4. 处理完所有行后，如果挂起队列里还有行没被"回收"（比如歌曲结尾的
   人声渐弱段、连续几行语气词一直到曲终），用最后一个确定点到音频
   总时长之间的区间做同样的比例插值。

## 单调游标 + 局部窗口搜索

歌词是按时间顺序唱的，所以对齐结果也必须是时间上单调不减的。引入
一个随着行号推进单调前移的时间游标 `cursor_time`：只在
`[search_from_idx, cursor_time + window]` 范围内的 ASR segment 里
找相似度最高的一段，不在全曲范围内找，这样即使歌词有重复段落，也
只会匹配到"这一次"唱到的时间点。就近窗口找不到确认，会放宽到 3 倍
窗口再试一次（门槛相应收紧），还是不行就进挂起队列。

## 拼音模糊匹配（应对同音字/近音字类 ASR 错误）

中文 ASR 出错很大一部分是同音字/近音字替换（比如把"再见"识别成
"在见"），这类错误在字形上完全不匹配、但读音一样或很接近。窗口内
每个候选片段都转成拼音序列（`pypinyin.lazy_pinyin`），再算一次
`difflib.SequenceMatcher` 相似度，作为字符精确匹配之外的第二条命中
通道，只有候选片段本身是中文、且和歌词行的汉字相似度不算太离谱时
才信任这条通道的分数（避免"风马牛不相及但拼音偶然相似"的噪声匹配）。

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

    与旧版"全局展开成一条扁平列表只用于全局锚点匹配"不同：这里按
    segment 分开保留，供窗口匹配阶段按需拼接任意 `[i, j]` 跨度，同时
    保留每个字符的精确时间戳，用来在"确认"之后给出字符级精度的行首/
    行尾时间，而不是整个 segment 的边界时间。

    识别失败产生的占位乱码字符（`�`）会被跳过——这类字符不仅本身没有
    文本信息、无法参与匹配，它的时间跨度往往异常巨大（实测复现过一个
    占位字符独占 8 秒多的真实演唱时间），留着没有任何用处。
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
# 汉字相似度不算太离谱（>= 此值）时，才信任拼音层面的相似度。"大数据
# 比你妈还懂你" vs "他们拿他慢慢来的鬼话" 这种毫不相关的两句歌词，
# 汉字相似度只有 0.05，拼音相似度却能到 0.51——如果候选片段是中文，
# 必须先过这道汉字相似度下限，才允许信任拼音分数。
_PINYIN_HANZI_FLOOR = 0.15

# "就近优先"早停阈值：窗口扫描中一旦某个候选（离游标更近）已经达到
# 这个分数，立即采信、不再继续往窗口更远处找可能分数更高但其实属于
# 下一次重复段落的候选。明显高于普通确认阈值，只用来短路"明显够好"
# 的情形，不影响普通确认阈值本身的把关。
_EARLY_ACCEPT_HANZI = 0.72
_EARLY_ACCEPT_PINYIN = 0.8


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


def _best_window_match(
    line_norm: str, segments: List[dict], seg_chars: List[List[dict]],
    start_idx: int, end_time: float, max_span: int = 4,
    pinyin_line: str = "", seg_pinyin: Optional[List[str]] = None,
    seg_norm_text: Optional[List[str]] = None,
) -> Dict[str, tuple]:
    """在 `segments[start_idx:]` 中、`segment.start <= end_time` 的
    范围内，找与 `line_norm` 最匹配的连续 segment 拼接（跨 1~max_span
    个 segment）。

    两个独立通道，各自返回各自的最佳候选，交给调用方按不同的"确认
    阈值"分别判断是否够格采信：

    - `"hanzi"`：字符级精确匹配通道（见 `_char_match_score`），返回
      `(score, span, anchor_start, anchor_end)`——`anchor_start/end`
      是**命中字符本身**的精确时间，不是整个候选片段的边界时间。
    - `"pinyin"`：拼音整句模糊相似度通道（应对同音字 ASR 错误），
      返回 `(score, span, None, None)`——没有字符级精度，调用方需要
      自己用 `span` 对应的 segment 边界时间。

    若未安装 pypinyin（`pinyin_line`/`seg_pinyin` 为空），拼音档始终
    是 `(0.0, None, None, None)`。
    """
    best_hanzi: tuple = (0.0, None, None, None)
    best_pinyin: tuple = (0.0, None, None, None)
    i = start_idx
    n = len(segments)
    while i < n and segments[i]["start"] <= end_time:
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
            if hanzi_score > best_hanzi[0]:
                best_hanzi = (hanzi_score, (i, j), a_start, a_end)

            if pinyin_line and py_concat and seg_norm_text is not None:
                concat_text = "".join(seg_norm_text[i:j + 1])
                cjk_count = sum(1 for ch in concat_text if _is_cjk_char(ch))
                cand_is_cjk = cjk_count > 0
                # 候选是中文但汉字相似度低于下限：两句大概率毫不相关，
                # 拼音层面的相似度就算数值不低也当噪声丢弃。
                hanzi_ratio = matched_chars / max(len(line_norm), 1)
                if (not cand_is_cjk) or hanzi_ratio >= _PINYIN_HANZI_FLOOR:
                    pinyin_ratio = difflib.SequenceMatcher(
                        a=pinyin_line, b=py_concat, autojunk=False).ratio()
                    pinyin_score = pinyin_ratio * length_penalty
                    if pinyin_score > best_pinyin[0]:
                        best_pinyin = (pinyin_score, (i, j), None, None)

        # 就近优先：一旦在当前 `i`（离游标更近）就已经找到"足够强"的
        # 精确匹配，立即停止继续往后扫描窗口——歌词重复段落一多，窗口
        # 里越往后的位置越容易出现"文字更清晰但其实是下一次重复"的
        # 候选片段，得分可能反而更高；如果一路扫到底才取全窗口最大值，
        # 就会舍近求远、误把下一次重复的音频当成这一行的时间戳（详见
        # 文件头部"故障复现"）。这里改成"够强就地采信，不再等更好的"，
        # 天然优先离游标最近、按时间顺序正确的那次命中。
        if best_hanzi[0] >= _EARLY_ACCEPT_HANZI or best_pinyin[0] >= _EARLY_ACCEPT_PINYIN:
            break
        i += 1
    return {"hanzi": best_hanzi, "pinyin": best_pinyin}


def _find_confirmed_window_match(
    line_norm: str, segments: List[dict], seg_chars: List[List[dict]],
    search_from_idx: int, cursor_time: float, window_seconds: float,
    pinyin_line: str, seg_pinyin: Optional[List[str]], seg_norm_text: Optional[List[str]],
    hanzi_confirm_threshold: float, pinyin_confirm_threshold: float,
    hanzi_confirm_threshold_wide: float, pinyin_confirm_threshold_wide: float,
    max_span: int,
) -> Optional[Tuple[Tuple[int, int], str, float, Optional[float], Optional[float]]]:
    """就近窗口 → （不够格再）放宽窗口，两档都要用各自的"确认阈值"
    单独把关；两次都够不到就返回 None（调用方应把这一行放进挂起队列，
    不要采信一个"矮子里拔将军"的弱匹配——弱匹配一旦被采信，游标和
    搜索指针会被带偏，后面一长串本该能对上的行会被连带拖成插值，
    详见文件头部"故障复现"说明）。

    返回 `(span, kind, score, anchor_start, anchor_end)` 或 `None`；
    `kind="pinyin"` 时 `anchor_start/end` 为 `None`（调用方用 span
    对应的 segment 边界时间）。
    """
    for widen, hanzi_th, pinyin_th in (
        (1.0, hanzi_confirm_threshold, pinyin_confirm_threshold),
        (3.0, hanzi_confirm_threshold_wide, pinyin_confirm_threshold_wide),
    ):
        cand = _best_window_match(
            line_norm, segments, seg_chars, search_from_idx,
            end_time=cursor_time + window_seconds * widen, max_span=max_span,
            pinyin_line=pinyin_line, seg_pinyin=seg_pinyin, seg_norm_text=seg_norm_text,
        )
        hanzi_score, hanzi_span, a_start, a_end = cand["hanzi"]
        pinyin_score, pinyin_span, _, _ = cand["pinyin"]
        if hanzi_span is not None and hanzi_score >= hanzi_th:
            return hanzi_span, "hanzi", hanzi_score, a_start, a_end
        if pinyin_span is not None and pinyin_score >= pinyin_th:
            return pinyin_span, "pinyin", pinyin_score, None, None
    return None


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
          window_seconds: float = 30.0,
          hanzi_confirm_threshold: float = 0.42,
          pinyin_confirm_threshold: float = 0.6,
          hanzi_confirm_threshold_wide: float = 0.6,
          pinyin_confirm_threshold_wide: float = 0.75,
          max_span: int = 4,
          skip_title: bool = True) -> dict:
    """对齐标准歌词与 ASR 时间戳（逐行局部窗口字符级精确匹配 + 词组加权
    + 拼音兜底 + 挂起队列区间插值）。

    `skip_title=True`（默认）时会自动丢弃歌词首行的歌名和所有段落标记
    行（`[Intro]`/`[Verse 1]`/`【副歌】` 等），见 `_load_standard_lines`。

    核心流程：
    1. 逐行在 `[search_from_idx, cursor_time + window]` 这个随游标
       单调前移的局部窗口内，用字符级精确匹配（单字+词组加权）找
       候选片段；找不到够格候选时，用拼音整句模糊相似度兜底一次
       （应对同音字 ASR 错误）。两个通道分别有独立的"确认阈值"，就近
       窗口不够格会放宽窗口（默认 3 倍）再试一次（门槛相应收紧）。
    2. 够格才算"确认"，才会真正推进时间游标和 ASR 搜索指针。够不到
       确认阈值的行，不再矮子里拔将军地立即采信一个弱匹配，而是放进
       挂起队列，交给后面找到的下一个确认点来统一做区间插值
       （`_interpolate_block`）——避免一次误判带偏游标、级联拖垮后面
       一长串本该能对上的行。**因为窗口搜索全程被游标约束在局部范围
       内，重复的副歌/化用段落只会匹配到"这一次"唱到的时间点，不会
       被拉到全曲任意一次同样的重复上**（详见文件头部"故障复现"/
       "修复"说明）。
    3. 处理完所有行后，挂起队列里剩下的（比如结尾渐弱的语气词/和声）
       用最后一个确认点到音频总时长之间的区间统一插值。
    """
    standard_lines = _load_standard_lines(lyrics_text, skip_title=skip_title)
    if not standard_lines:
        raise ValueError("标准歌词为空，无法对齐")

    if not _PINYIN_AVAILABLE:
        print("[align_lyrics_v3] 未安装 pypinyin，拼音模糊匹配已禁用，仅使用汉字精确匹配"
              "（pip install pypinyin --break-system-packages 可开启）", file=sys.stderr)

    line_norms = [normalize_text(line) for line in standard_lines]

    segments = asr_result.get("segments", [])
    seg_chars = _segment_char_lists(asr_result)
    seg_norm_text = [normalize_text(s.get("text", "")) for s in segments]
    seg_pinyin = [to_pinyin(s.get("text", "")) for s in segments] if _PINYIN_AVAILABLE else None

    duration = asr_result.get("duration")
    last_seg_end = segments[-1]["end"] if segments else 0.0
    audio_end = duration if duration else last_seg_end

    result_lines: List[Optional[dict]] = [None] * len(standard_lines)
    cursor_time = 0.0
    search_from_idx = 0
    pending: List[dict] = []  # 挂起、尚未确定时间戳的行（result_lines 里对应位置的 dict）
    any_confirmed = False

    def _advance_search_idx():
        nonlocal search_from_idx
        while search_from_idx < len(segments) - 1 and segments[search_from_idx]["end"] < cursor_time:
            search_from_idx += 1

    for pos, (text, norm_line) in enumerate(zip(standard_lines, line_norms)):
        stub = {"index": pos, "text": text}
        result_lines[pos] = stub

        confirmed = False
        start = end = None
        method = None
        confirmed_span = None

        if segments and norm_line:
            pinyin_line = to_pinyin(text) if _PINYIN_AVAILABLE else ""
            found = _find_confirmed_window_match(
                norm_line, segments, seg_chars, search_from_idx, cursor_time,
                window_seconds, pinyin_line, seg_pinyin, seg_norm_text,
                hanzi_confirm_threshold, pinyin_confirm_threshold,
                hanzi_confirm_threshold_wide, pinyin_confirm_threshold_wide,
                max_span,
            )
            if found is not None:
                span, kind, score, a_start, a_end = found
                i, j = span
                if kind == "hanzi" and a_start is not None:
                    start, end = a_start, a_end
                else:
                    start, end = segments[i]["start"], segments[j]["end"]
                if end is None or end <= start:
                    end = (start or 0.0) + 0.5
                method = f"window-match(kind={kind},score={score:.2f})"
                confirmed = True
                confirmed_span = span

        if not confirmed:
            # 不确定：先挂起，不动游标、不动搜索指针，交给后面的确定点
            # 回头统一插值（见 `_interpolate_block`）。
            pending.append(stub)
            continue

        any_confirmed = True
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
        search_from_idx = confirmed_span[0]  # 允许下一行与本行有轻微重叠
        _advance_search_idx()

    # 曲终仍未被回收的挂起行（比如结尾渐弱的语气词/和声）：用最后一个
    # 确定点到音频总时长之间的区间统一插值。
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

    # ── 最终保险：单调递增 + 最小间隔（正常情况下阶段二已保证，这里兜底）──
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
    parser = argparse.ArgumentParser(description="用标准歌词校正 ASR 时间戳 v3（局部窗口字符级精确匹配 + 词组加权 + 拼音模糊兜底 + 挂起队列区间插值）")
    parser.add_argument("asr_json")
    parser.add_argument("lyrics_txt")
    parser.add_argument("--line-gap", type=float, default=0.15)
    parser.add_argument("--window-seconds", type=float, default=30.0,
                        help="局部窗口匹配时，向前搜索 ASR segment 的时间窗口大小（秒），默认30；"
                             "调大会更容易找到远处的匹配，但歌词重复段落密集的歌曲里也更容易"
                             "舍近求远错配到下一次重复，一般不建议调大于两次重复间隔的时长")
    parser.add_argument("--max-span", type=int, default=4,
                        help="窗口匹配时最多拼接几个连续 ASR segment 作为候选片段，默认4")
    parser.add_argument("--hanzi-confirm-threshold", type=float, default=0.42,
                        help="字符级精确匹配（单字+词组加权）得分达到此值才算\"确认\"，默认0.42")
    parser.add_argument("--pinyin-confirm-threshold", type=float, default=0.6,
                        help="拼音整句模糊相似度达到此值才算\"确认\"（明显高于汉字档，"
                             "因为纯拼音串比较天然噪声更大），默认0.6")
    parser.add_argument("--hanzi-confirm-threshold-wide", type=float, default=0.6,
                        help="就近窗口内两档都不够格确认时，会放宽到3倍窗口再试一次，"
                             "这是放宽后汉字档的确认门槛（比就近窗口更严格，避免放宽窗口后"
                             "误把下一次重复段落当成这一行），默认0.6")
    parser.add_argument("--pinyin-confirm-threshold-wide", type=float, default=0.75,
                        help="放宽窗口后拼音档的确认门槛，默认0.75")
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
            asr_result, lyrics_text, line_gap=args.line_gap,
            window_seconds=args.window_seconds,
            max_span=args.max_span,
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