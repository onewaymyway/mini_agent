#!/usr/bin/env python3
"""歌词对齐校正脚本 v3（在 v2 基础上加拼音模糊匹配，兜底"ASR+diff"方案的
进一步改进；不需要 ctc-forced-aligner/torch/onnxruntime 这类重依赖，只需要
纯 Python 小包 pypinyin，安装成功率接近 100%，适合 align_lyrics_forced.py
因为环境限制装不上/跑不通时使用）。

## 相对 v2 新增的改进：拼音层面的模糊匹配

v2 的窗口兜底匹配（`_best_window_match`）比较的是**归一化后的汉字**是否
相同。问题是中文 ASR 出错很大一部分是**同音字/近音字替换**（比如把
"科技的窍门"识别成"可惜的窗门"、把"再见"识别成"在见"），这类错误在
字形上完全不匹配，但读音是一样或很接近的——精确比汉字的话直接判定
"整句都不相似"，只能靠插值，而插值不含任何真实演唱节奏信息。

v3 给窗口匹配额外加了一条"拼音相似度"通道：把这一行歌词和候选 ASR
segment 都转成拼音序列（`pypinyin.lazy_pinyin`），再算一次
`difflib.SequenceMatcher` 相似度，跟"汉字相似度"取较大值。同音字/
近音字替换导致的整句识别错误，在拼音层面基本还是高度相似的，能明显
提高窗口匹配命中率，减少退化成"纯插值"的行数。

**只在窗口匹配（整句级）加拼音通道，字符级精确锚点阶段不改**：字符级
锚点是单字符比较，中文单音节同音字极多（"的/地/得/低/滴/敌"等等都读
"de"），如果在锚点阶段也用拼音比较，会引入大量虚假的单字锚点，反而
可能让对齐结果变差。整句级别的拼音序列所需要连续匹配的音节数多得多，
误判概率低得多，所以只在这一层加拼音通道，风险可控。

依赖：仅需 `pypinyin`（纯 Python，无 C 扩展/无需网络下载模型，安装几乎
不会失败）：
    pip install pypinyin --break-system-packages
未安装时会自动退化为只用汉字相似度（等价于 v2 的行为），不会报错。

用法：
    python align_lyrics_v3.py asr_raw.json lyrics.txt --save-path lyrics_timed.json --save-srt lyrics.srt

## 本次优化（在拼音模糊匹配基础上继续加固）

1. **自动忽略歌词首行的歌名**：很多 `lyrics.txt` 习惯性地把歌名写在
   第一行（后面才是真正要唱的 `[Intro]`/正文），这一行从不会被唱出来，
   拿去跟 ASR 比对只会制造一条错误的低置信度行、甚至污染后面的单调
   游标。默认行为是：整份歌词里**第一条非空、且本身不是段落标记的
   行**会被当成歌名自动跳过，不参与对齐、也不出现在输出结果里。如果
   歌词文件本来就没有歌名（第一行直接就是 `[Intro]` 或正文），脚本不会
   误删任何一行——判定逻辑只在"第一行不是段落标记"时才生效。极少数
   歌词第一行本来就是要唱的正文（没有歌名）的情况下，可以加
   `--no-skip-title` 关掉这个行为。
2. **段落标记识别更宽松**：不仅认 `[Intro]`/`[Verse 1]` 这种半角方括号，
   也认 `【副歌】`/`（间奏）`/`(Bridge):` 等中英文括号 + 可选尾部冒号的
   写法，统一在对齐前过滤掉，不会被当成一句要对齐的歌词。
3. **窗口模糊匹配阶段加入"字数对齐"约束**：v2/v3 之前的窗口匹配只看
   相似度 ratio 最高的候选，容易选中"文字很像但长度差很多"的错误
   片段（比如把一句 10 个字的歌词匹配到 ASR 里一个只有 3 个字的碎片
   segment 上）。现在额外算一个**长度惩罚系数**：候选片段的有效字数
   和歌词行字数差得越多，最终得分惩罚越重；差得在 30% 以内基本不惩罚。
   "有效字数"优先按中文字符数计，如果歌词是中文但 ASR 识别失败输出
   了英文/拼音（这种情况下双方"字数"单位根本不是一回事，按字符数硬比
   没有意义），会自动识别这种"中文行 vs 非中文候选"的情况并大幅放宽
   长度惩罚，避免因为单位不可比而错误地否决掉本来正确的候选。
4. **匹配优先级更明确**：窗口匹配时，汉字相似度和拼音相似度不再简单
   取较大值，而是分级："汉字也能对上"的候选（汉字相似度达到较高阈值）
   优先选中；汉字对不上但拼音明显更相似（同音字/近音字替换）的候选
   次优先；两者都很弱时才会退化成最后一档的"插值兜底猜测"。这样保证
   了"中文也都能匹配上的"优先于"只有读音能对上的"，读音能对上的又
   优先于纯插值瞎猜。输出的 `method` 字段里会标注具体命中的是
   `hanzi`/`pinyin` 哪一档，方便复核。

---

以下是 v2 的原始说明（对齐算法主体思路不变，只是给窗口匹配加了拼音通道）：

歌词对齐校正脚本 v2（精确匹配优先 + 归一化 + 单调窗口约束）。

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
    python align_lyrics_v3.py asr_raw.json lyrics.txt --save-path lyrics_timed.json --save-srt lyrics.srt

依赖：仅标准库；若安装了 `opencc-python-reimplemented`
（`pip install opencc-python-reimplemented`），简繁转换会更准确全面，
未安装时退化使用脚本内置的高频字对照表（覆盖常见歌词用字，但不完整）。
"""

import argparse
import difflib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import List, Optional, Tuple

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


# 汉字相似度达到这个阈值就认为"中文也对上了"，优先采信，不再看拼音档。
_HANZI_GOOD_THRESHOLD = 0.55
# 拼音档命中时打的折扣：同样是候选，"读音对上但字对不上"的可信度天然
# 低于"字也对上"，给个折扣，避免拼音层面偶然的高相似度盖过更靠谱的
# 汉字候选（两者会在不同 span 上分别比较，折扣后再统一取最大值）。
_PINYIN_DISCOUNT = 0.92


def _best_window_match(norm_line: str, segments: List[dict], seg_norm: List[str],
                        start_idx: int, end_time: float, max_span: int = 3,
                        pinyin_line: str = "", seg_pinyin: Optional[List[str]] = None):
    """在 segments[start_idx:] 中、start_time <= end_time 的范围内，
    找与 norm_line 最匹配的连续 segment 拼接（跨 1~max_span 个 segment）。

    匹配优先级分三档（本次优化明确化）：
    1. **汉字也对上**：汉字层面相似度达到 `_HANZI_GOOD_THRESHOLD`，
       直接按汉字相似度打分，这一档最可信。
    2. **拼音能对上**：汉字对不上（同音字/近音字替换导致），但拼音层面
       相似度更高——这类候选打个折扣（`_PINYIN_DISCOUNT`）后再参与
       比较，比"汉字也对上"的候选低一档，但仍然明显优于瞎猜插值。
    3. 两档都拿不到足够高的分，调用方会用 `window_match_threshold`
       过滤掉，最终退化成插值兜底（"最后猜测其他的匹配"）。

    另外引入**字数对齐**：候选片段的有效字数和歌词行字数差得越多，
    最终得分会被 `_length_penalty` 按比例打折——避免"文字很像但明显
    长度对不上"的碎片被误选中；如果歌词是中文而候选是 ASR 识别失败
    输出的非中文文本，两边字数单位不可比，会自动放宽这个惩罚（见
    `_length_penalty` 里的说明）。

    若未安装 pypinyin（`pinyin_line`/`seg_pinyin` 为空），拼音档不生效，
    行为退化为只用"汉字相似度 * 长度惩罚"。

    返回 (best_score, (seg_i, seg_j), match_kind) 或 (0.0, None, None)。
    """
    best_score, best_span, best_kind = 0.0, None, None
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

            pinyin_ratio = 0.0
            if pinyin_line and py_concat:
                pinyin_ratio = difflib.SequenceMatcher(
                    a=pinyin_line, b=py_concat, autojunk=False).ratio()

            if hanzi_ratio >= _HANZI_GOOD_THRESHOLD or hanzi_ratio >= pinyin_ratio:
                score, kind = hanzi_ratio * length_penalty, "hanzi"
            else:
                score, kind = pinyin_ratio * _PINYIN_DISCOUNT * length_penalty, "pinyin"

            if score > best_score:
                best_score, best_span, best_kind = score, (i, j), kind
        i += 1
    return best_score, best_span, best_kind


def align(asr_result: dict, lyrics_text: str, line_gap: float = 0.15,
          min_anchor: int = 2, window_seconds: float = 45.0,
          anchor_coverage_threshold: float = 0.4,
          window_match_threshold: float = 0.28,
          skip_title: bool = True) -> dict:
    """对齐标准歌词与 ASR 时间戳（锚点优先 + 单调窗口约束兜底）。

    `skip_title=True`（默认）时会自动丢弃歌词首行的歌名和所有段落标记
    行（`[Intro]`/`[Verse 1]`/`【副歌】` 等），见 `_load_standard_lines`。
    """
    standard_lines = _load_standard_lines(lyrics_text, skip_title=skip_title)
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
    seg_pinyin = [to_pinyin(s.get("text", "")) for s in segments] if _PINYIN_AVAILABLE else None
    if not _PINYIN_AVAILABLE:
        print("[align_lyrics_v3] 未安装 pypinyin，拼音模糊匹配已禁用，退化为 v2 行为"
              "（pip install pypinyin --break-system-packages 可开启）", file=sys.stderr)

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
            pinyin_line = to_pinyin(rl["text"]) if _PINYIN_AVAILABLE else ""
            ratio, span, kind = _best_window_match(
                norm_line, segments, seg_norm, search_from_idx,
                end_time=cursor_time + window_seconds,
                pinyin_line=pinyin_line, seg_pinyin=seg_pinyin,
            )
            if (ratio < window_match_threshold) or span is None:
                # 窗口内没找到，再放宽一次窗口（应对个别行时长规划偏差较大的情况）
                ratio2, span2, kind2 = _best_window_match(
                    norm_line, segments, seg_norm, search_from_idx,
                    end_time=cursor_time + window_seconds * 3,
                    pinyin_line=pinyin_line, seg_pinyin=seg_pinyin,
                )
                if ratio2 > ratio:
                    ratio, span, kind = ratio2, span2, kind2
            if span is not None and ratio >= window_match_threshold:
                i, j = span
                start, end = segments[i]["start"], segments[j]["end"]
                method = f"window-match(kind={kind},score={ratio:.2f})"
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
        print(f"[align_lyrics_v3] {len(low_conf_lines)} 行锚点覆盖率 <34%，已用窗口匹配/插值兜底，"
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
    parser = argparse.ArgumentParser(description="用标准歌词校正 ASR 时间戳 v3（归一化精确匹配 + 单调窗口约束 + 拼音模糊匹配）")
    parser.add_argument("asr_json")
    parser.add_argument("lyrics_txt")
    parser.add_argument("--line-gap", type=float, default=0.15)
    parser.add_argument("--min-anchor", type=int, default=2)
    parser.add_argument("--window-seconds", type=float, default=45.0,
                        help="窗口匹配兜底时，向前搜索 ASR segment 的时间窗口大小（秒），默认45")
    parser.add_argument("--anchor-coverage-threshold", type=float, default=0.4,
                        help="锚点覆盖率低于此值时不采信锚点，改用窗口匹配，默认0.4")
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