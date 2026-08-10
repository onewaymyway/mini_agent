"""
utils/lang_detect.py — 轻量用户常用语言检测。

[next_doc/growth_advisor_diagnostics_and_language_fix_plan.md 方向二]
背景：`profile.py` 生成"Agent 对你的了解"画像时，此前完全依赖 prompt
里"用记忆条目相同的语言输出"这条弱约束——如果上游（session 摘要生成）
本身习惯输出英文，这条约束就没有基准可跟，画像会跟着跑偏成英文。

这里提供一个不依赖任何外部服务/LLM 调用的启发式检测：统计一段文本里
各语言特征 Unicode 区间的字符占比，返回占比最高且过阈值的语言 code；
不追求精确（不区分简繁体、不做地区变体判断），只满足"生成内容该用哪种
语言"这一个用途，可以在任意热路径廉价调用。
"""

from __future__ import annotations

DEFAULT_LANGUAGE = "en"

# 每种语言的特征 Unicode 区间（闭区间，(start, end)）。
# 中文：CJK 统一表意文字主区 + 扩展 A；日文额外看假名区（用于跟纯中文
# 区分开——中文文本几乎不含假名，日文文本通常汉字+假名混排）；韩文看
# 谚文音节区。命中任意区间即计入该语言候选的字符计数。
_LANG_RANGES: dict[str, tuple[tuple[int, int], ...]] = {
    "ja": ((0x3040, 0x309F), (0x30A0, 0x30FF)),  # 平假名 + 片假名
    "ko": ((0xAC00, 0xD7A3),),  # 谚文音节
    "zh": ((0x4E00, 0x9FFF), (0x3400, 0x4DBF)),  # CJK 统一表意文字（+扩展A）
}

# 判定阈值：候选语言的特征字符数占"总有效字符数"（字母/表意文字，
# 不含空白/标点/数字）的比例超过这个值才采信，避免几个零星字符（比如
# 英文里夹的专有名词）就误判成对应语言。
_MIN_RATIO = 0.15


def detect_primary_language(texts: list[str]) -> str:
    """粗粒度语言检测：返回 ISO 639-1 code（zh/ja/ko/en/...）。

    Args:
        texts: 一组待检测的文本（通常是用户的原始输入或新增的记忆摘要，
            越接近用户真实输入越好，避免用已经可能跑偏语言的历史摘要）。

    Returns:
        检测到的语言 code；输入为空、或没有任何语言的字符占比超过阈值时，
        退回 `DEFAULT_LANGUAGE`（"en"）。
    """
    if not texts:
        return DEFAULT_LANGUAGE

    combined = "".join(t for t in texts if t)
    if not combined.strip():
        return DEFAULT_LANGUAGE

    lang_counts: dict[str, int] = {lang: 0 for lang in _LANG_RANGES}
    total_effective = 0

    for ch in combined:
        code = ord(ch)
        matched = False
        for lang, ranges in _LANG_RANGES.items():
            if any(start <= code <= end for start, end in ranges):
                lang_counts[lang] += 1
                matched = True
                break
        if matched:
            total_effective += 1
        elif ch.isalpha():
            # 拉丁字母等未特别识别的字母字符，计入总数但不属于任何候选
            # 语言，用于稀释"少量 CJK 字符夹在大段英文里"的误判。
            total_effective += 1

    if total_effective == 0:
        return DEFAULT_LANGUAGE

    # 中日都命中 CJK 表意文字区间，日文额外靠假名占比区分：只要假名
    # 字符数明显（判定为 ja 的字符数 > 0 且不少于中文表意文字计数的
    # 一小部分），就判定为日文而不是中文。
    zh_count = lang_counts.get("zh", 0)
    ja_count = lang_counts.get("ja", 0)
    ko_count = lang_counts.get("ko", 0)

    if ja_count > 0 and ja_count / total_effective >= _MIN_RATIO * 0.5:
        # 假名阈值放低一些：日文里假名占比天然低于汉字占比，但只要有
        # 明显数量的假名出现，就足以跟纯中文区分开。
        return "ja"
    if ko_count / total_effective >= _MIN_RATIO:
        return "ko"
    if zh_count / total_effective >= _MIN_RATIO:
        return "zh"

    return DEFAULT_LANGUAGE
