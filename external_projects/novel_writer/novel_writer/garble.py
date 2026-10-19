"""novel_writer/garble.py — 正文乱码检测（纯规则，零 LLM 成本）。

两类乱码都会被抓到：

1. 字符级损坏：U+FFFD 替换符、私有使用区字符、控制字符。
2. 编码错乱（典型：UTF-8 字节被按 GBK 解码后再存成 UTF-8）：结果是大量生僻汉字，
   以及因字节丢失而残留的字面 `?`（夹在汉字中间）。这类文本无法靠转码还原，只能重新生成。

判定依据是三个比例 / 计数，均可在 `GarbleThresholds` 中调整：
  * 替换符 / 私有区 / 控制字符：出现即为硬信号；
  * 汉字间夹杂的半角 `?`：正常中文应使用全角 `？`，出现 >= 阈值即为信号；
  * 生僻字比例：汉字中不在 GB2312 一级字表（3755 字）内的比例，超过阈值即为信号。

注意：阈值只能用少量样本初步校准。正常小说里偶有生僻字（人名、古字），所以生僻字比例
单独不足以判乱码，需要与其它信号组合（见 `is_garbled`）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List

_CJK = re.compile(r"[\u4e00-\u9fff]")
_HARD_CHARS = re.compile(r"[\ufffd\ue000-\uf8ff\x00-\x08\x0b\x0c\x0e-\x1f]")
_QMARK_IN_CJK = re.compile(r"[\u4e00-\u9fff]\?|\?[\u4e00-\u9fff]")


@dataclass(frozen=True)
class GarbleThresholds:
    max_hard_chars: int = 0          # 替换符/私有区/控制字符允许的数量
    max_qmark_in_cjk: int = 2        # 汉字旁的半角 ? 允许的数量
    max_rare_ratio: float = 0.08     # 生僻字占汉字的比例：超过且有其它信号时判乱码
    severe_rare_ratio: float = 0.30  # 生僻字占汉字的比例：超过即单独判乱码
    min_cjk_for_ratio: int = 200     # 汉字太少时生僻字比例不可靠，不参与判定


DEFAULT_THRESHOLDS = GarbleThresholds()


@lru_cache(maxsize=1)
def gb2312_level1() -> frozenset:
    """GB2312 一级常用字（区位 16–55 行，即 0xB0A1–0xD7F9）。"""
    chars = set()
    for hi in range(0xB0, 0xD8):
        for lo in range(0xA1, 0xFF):
            try:
                chars.add(bytes([hi, lo]).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    return frozenset(c for c in chars if _CJK.fullmatch(c))


@dataclass
class GarbleReport:
    cjk: int = 0
    hard_chars: int = 0
    qmark_in_cjk: int = 0
    rare_cjk: int = 0
    rare_ratio: float = 0.0
    reasons: List[str] = field(default_factory=list)

    @property
    def garbled(self) -> bool:
        return bool(self.reasons)

    def to_dict(self) -> Dict[str, object]:
        return {
            "cjk": self.cjk,
            "hard_chars": self.hard_chars,
            "qmark_in_cjk": self.qmark_in_cjk,
            "rare_cjk": self.rare_cjk,
            "rare_ratio": round(self.rare_ratio, 4),
            "garbled": self.garbled,
            "reasons": list(self.reasons),
        }


def analyze(text: str, thresholds: GarbleThresholds = DEFAULT_THRESHOLDS) -> GarbleReport:
    """计算各项指标（不下结论）。"""
    report = GarbleReport()
    report.hard_chars = len(_HARD_CHARS.findall(text))
    report.qmark_in_cjk = len(_QMARK_IN_CJK.findall(text))
    cjk_chars = _CJK.findall(text)
    report.cjk = len(cjk_chars)
    common = gb2312_level1()
    report.rare_cjk = sum(1 for c in cjk_chars if c not in common)
    report.rare_ratio = report.rare_cjk / report.cjk if report.cjk else 0.0
    return report


def is_garbled(text: str, thresholds: GarbleThresholds = DEFAULT_THRESHOLDS) -> GarbleReport:
    """给出判定，并在 report.reasons 中记录每条命中的规则。

    判定规则：
      * 替换符 / 私有区 / 控制字符超过阈值 → 乱码（单条即可）；
      * 汉字旁的半角 ? 超过阈值 → 乱码（单条即可）；
      * 生僻字比例超过 severe_rare_ratio → 乱码（单独即可）。正常小说的生僻字比例通常
        在 1% 以下，校准样本中正常段落为 0.4%，编码错乱样本为 90%；
      * 生僻字比例超过 max_rare_ratio（但未到 severe）→ 仅当同时存在上面信号时才计入，
        避免正常文本中偶有生僻字被误判。
    汉字数量少于 min_cjk_for_ratio 时不做生僻字判定。
    """
    report = analyze(text, thresholds)
    if report.hard_chars > thresholds.max_hard_chars:
        report.reasons.append(f"出现 {report.hard_chars} 个替换符/私有区/控制字符")
    if report.qmark_in_cjk > thresholds.max_qmark_in_cjk:
        report.reasons.append(f"汉字旁出现 {report.qmark_in_cjk} 处半角 ?（应为全角 ？）")
    if report.cjk >= thresholds.min_cjk_for_ratio:
        if report.rare_ratio > thresholds.severe_rare_ratio:
            report.reasons.append(f"生僻字占汉字 {report.rare_ratio:.1%}（超过 {thresholds.severe_rare_ratio:.0%}）")
        elif report.rare_ratio > thresholds.max_rare_ratio and report.reasons:
            report.reasons.append(f"生僻字占汉字 {report.rare_ratio:.1%}（超过 {thresholds.max_rare_ratio:.0%}）")
    return report
