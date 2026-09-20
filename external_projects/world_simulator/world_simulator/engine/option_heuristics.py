"""world_simulator/engine/option_heuristics.py — 候选选项的辅助
校验/审计提示（阶段三十三第三批，`next_doc/
world_simulator_potential_causal_space_and_decision_engine_plan.md`
4.4 节"可干预性下沉"、4.5 节"现实行动语义"各自提到的"辅助校验"部分）。

这两条差距**最主要的约束**（宏观事件不能直接包装成选项/选项不能是
内部指标调节）本身是纯语义判断，已经作为硬性 prompt 指令写进
`workflows/advance_step.yaml`（阶段三十三第一批），代码层面无法校验
"LLM 是否真的做到了"。这个模块只做方案里明确写出的"辅助的、弱信号
的、不阻断流程"的启发式检测，延续项目里 `uncertain_fields`/
`relation_violations` 这类"仅展示、供人工抽查"的一贯风格：

- `detect_macro_overlap_warnings()`（4.4 节）：选项文本与本步
  `key_drivers`（宏观驱动因素）关键词高度重合，可能是把宏观事件
  直接包装成了选项，没有沿因果链下沉到主体真正可干预的位置；
- `detect_metric_adjustment_warnings()`（4.5 节）：选项文本里出现
  "提升/增加/降低 + 数字/百分比"这类模式，可能是把内部指标调节
  包装成了选项，而不是现实世界的行动/路线语义。
- `detect_similar_option_warnings()`（第五轮方案 5.6 节，`next_doc/
  world_simulator_decision_engine_round2_gap_analysis_plan.md`，
  对应参考文档第十五节"合并重复行动"）：同一批候选选项里两两比较
  `label` 的字符 2-gram jaccard 相似度，超过阈值时提示"可能过于
  相似，考虑合并"——同样是纯文本层面的粗略估计，不做语义去重、
  不自动合并或删除任何选项。

三者都只是简单的字符串/正则/纯文本相似度匹配，不做任何语义理解，
**误报是预期内的**——方案原文明确"这只是辅助人工发现问题的信号，
不是强制校验，避免误伤真正合理的选项"；比如"提高利率 0.25 个
百分点"这种完全合理的现实行动也会被 `detect_metric_adjustment_
warnings` 命中，这是刻意接受的取舍，不是需要修的 bug。检测结果只
落进
`SimState.option_warnings`（见其 docstring），不修改任何选项内容、
不影响推进流程本身。
"""

from __future__ import annotations

import re
from typing import Dict, List, Sequence

from world_simulator.state_model import ChoiceOption

_MIN_DRIVER_LEN_FOR_OVERLAP = 4
"""短于这个长度的 `key_drivers` 短语不参与重合检测——太短的短语
（比如两个字）几乎必然在任意一段选项文本里"重合"，检测出来没有
信息量，只会制造大量无意义的噪音提示。"""

_METRIC_ADJUSTMENT_PATTERNS = [
    # 动词 + 数字/百分号，比如"提升职业竞争力 20%"。
    re.compile(r"(提升|增加|提高|降低|减少|扩大|缩减|下降|翻倍)[^\d%，。！？]{0,8}\d+(\.\d+)?\s*%"),
    # 裸的 `+0.1`/`-15` 这种数值增减记号（参考文档"模型能力 +0.1"这个
    # 反例）；数字后面紧跟常见量词（年/月/天/周/次/岁/个/元/%）时，
    # 大概率是"减少 1 个月"这种描述时间/数量的正常现实行动，不算命中。
    re.compile(r"[+-]\s*\d+(\.\d+)?(?!\s*[年月天周次岁个元%])"),
]


def detect_macro_overlap_warnings(
    options: Sequence[ChoiceOption], key_drivers: Sequence[str]
) -> List[Dict[str, str]]:
    """检测候选选项是否与本步 `key_drivers`（宏观驱动因素）高度重合
    （4.4 节可干预性下沉的辅助校验）。

    `key_drivers` 为空、或者所有短语都短于 `_MIN_DRIVER_LEN_FOR_
    OVERLAP` 时直接返回空列表——没有足够长度的驱动因素可比对，不
    编造警示。
    """
    drivers = [
        str(d).strip()
        for d in (key_drivers or [])
        if isinstance(d, str) and len(str(d).strip()) >= _MIN_DRIVER_LEN_FOR_OVERLAP
    ]
    if not drivers:
        return []
    warnings: List[Dict[str, str]] = []
    for opt in options:
        text = f"{opt.label} {opt.description}"
        for driver in drivers:
            if driver in text:
                warnings.append(
                    {
                        "option_id": opt.id,
                        "kind": "macro_event_overlap",
                        "note": (
                            f"选项内容与本步宏观驱动因素「{driver}」高度重合，"
                            "建议检查是否把宏观事件直接包装成了选项（应沿因果链"
                            "下沉到主体真正可干预的位置）"
                        ),
                    }
                )
                break  # 一个选项只记一条，不按命中的驱动因素数量重复记录
    return warnings


def detect_metric_adjustment_warnings(
    options: Sequence[ChoiceOption],
) -> List[Dict[str, str]]:
    """检测候选选项是否疑似"内部指标调节"而非现实行动语义
    （4.5 节的辅助校验）。"""
    warnings: List[Dict[str, str]] = []
    for opt in options:
        text = f"{opt.label} {opt.description}"
        if any(pattern.search(text) for pattern in _METRIC_ADJUSTMENT_PATTERNS):
            warnings.append(
                {
                    "option_id": opt.id,
                    "kind": "metric_adjustment_pattern",
                    "note": (
                        "选项文本里出现类似「提升/增加/降低 + 数字」的指标"
                        "调节语言，建议检查是否应该改写成现实世界的行动/"
                        "路线语义"
                    ),
                }
            )
    return warnings


_SIMILAR_LABEL_JACCARD_THRESHOLD = 0.6
"""两个选项 `label` 的字符 2-gram jaccard 相似度超过这个阈值时，
认为"可能过于相似，考虑合并"（第五轮方案 5.6 节）。取 0.6 是刻意
偏保守的阈值——宁可漏报（语义相似但用词完全不同时无法检出），也
不要在用词略有交集的正常选项之间制造大量误报噪音；具体数值没有
经过大规模数据调优，只是"看起来足够高、不会随手命中"的经验值，
后续如果实测发现漏报/误报比例明显失衡，可以再调整。"""

_MIN_LABEL_LEN_FOR_SIMILARITY = 4
"""短于这个字符数的 `label` 不参与相似度比较——太短的标题（比如
两三个字）任何两个之间的 2-gram jaccard 都容易虚高，没有实际比较
意义。"""


def _char_bigrams(text: str) -> set:
    chars = [c for c in text if not c.isspace()]
    if len(chars) < 2:
        return {"".join(chars)} if chars else set()
    return {"".join(chars[i : i + 2]) for i in range(len(chars) - 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def detect_similar_option_warnings(
    options: Sequence[ChoiceOption],
) -> List[Dict[str, str]]:
    """检测同一批候选选项里是否存在高度相似、可能应该合并的选项
    （第五轮方案 `next_doc/world_simulator_decision_engine_round2_
    gap_analysis_plan.md` 5.6 节，对应参考文档第十五节"过滤不可行
    行动/合并重复行动"）。

    只比较 `label`（选项标题），用字符 2-gram 的 jaccard 相似度做
    纯文本层面的粗略估计，**不做任何语义理解**——这类启发式注定
    会有漏报（语义相似但用词完全不同，比如"寻找新工作"和"考虑
    转行"）和误报（用词偶然重叠但语义无关），这是刻意接受的精度
    取舍，延续本模块其它两个检测函数"宁可漏报、不做过度推断"的
    一贯风格。检测结果只汇入 `SimState.option_warnings`（供人工
    抽查参考），不修改、不合并、不删除任何选项，也不阻断推进流程。
    """
    candidates = [
        opt for opt in options if len(str(opt.label or "").strip()) >= _MIN_LABEL_LEN_FOR_SIMILARITY
    ]
    warnings: List[Dict[str, str]] = []
    seen_pairs: set = set()
    for i, opt_a in enumerate(candidates):
        bigrams_a = _char_bigrams(opt_a.label)
        for opt_b in candidates[i + 1 :]:
            pair_key = tuple(sorted((opt_a.id, opt_b.id)))
            if pair_key in seen_pairs:
                continue
            score = _jaccard(bigrams_a, _char_bigrams(opt_b.label))
            if score >= _SIMILAR_LABEL_JACCARD_THRESHOLD:
                seen_pairs.add(pair_key)
                warnings.append(
                    {
                        "option_id": opt_a.id,
                        "kind": "similar_option",
                        "note": (
                            f"选项「{opt_a.label}」与选项「{opt_b.label}」"
                            "标题高度相似，建议检查是否应该合并成一个选项"
                            "（纯文本相似度提示，不代表语义一定重复，仅供参考）"
                        ),
                    }
                )
    return warnings


def compute_option_warnings(
    options: Sequence[ChoiceOption], key_drivers: Sequence[str]
) -> List[Dict[str, str]]:
    """汇总以上三类辅助校验的结果，供 `engine/advance.py` 直接调用。"""
    return (
        detect_macro_overlap_warnings(options, key_drivers)
        + detect_metric_adjustment_warnings(options)
        + detect_similar_option_warnings(options)
    )
