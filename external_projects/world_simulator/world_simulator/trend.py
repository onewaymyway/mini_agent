"""world_simulator/trend.py — "顺势/逆势/改变趋势"判断（阶段三十一）

设计依据：`next_doc/world_simulator_universal_simulator_gap_analysis_
and_roadmap_v2_plan.md` 4.23 节。

背景：`attribution.summarize_contributions()`（4.21 节）能回答"这个
结果主要是哪条因果线贡献的"，但回答不了"用户是在顺应一个既有趋势、
对抗一个既有趋势，还是自己的行动正在改变这个趋势"。本模块基于归因
结果做一个轻量的启发式分类，**不追求精确**（原方案本身就把这个方向
标注为"十个方向里价值相对不确定的一个"，建议先收集反馈再评估——
现在按用户要求实现，但保留同样克制的实现方式）：

- 如果目标字段的主要贡献来源集中在"自己所在因果线"之外的外部线，
  说明结果主要由外部趋势决定——**但 `causal_links` 不记录数值变化
  方向**，系统没有足够信息自动判断这是"顺应了外部趋势"还是"与外部
  趋势相反"，因此这里没有强行拆成"顺势"/"逆势"两个判定，而是给出
  一个更谨慎的 `with_trend` 判断 + 具体因果链明细，把"顺势还是逆势"
  的最终判断留给用户自己看着因果链内容判断。
- 如果历史因果链里出现"自己所在因果线 → 反向影响了某条外部线"的
  记录（`causal_links` 里 `source_line_id` 是自己这条线、`line_id`
  是另一条线），判定为"正在改变趋势"——这个信号比"顺势/逆势"更明确，
  优先判断。
- 两种信号都没有出现时，返回 `inconclusive`（无法判断），不强行给出
  一个没有依据的结论。

每次判断都附带"依据是哪几条因果链"，供用户自己判断认不认同——同
`attribution.py`"不做伪精确"的一贯风格。纯函数、不缓存、不落盘、
不发起任何新的 LLM 调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from world_simulator.attribution import _UNASSIGNED, _get_attr_or_key, summarize_contributions

_VERDICT_LABELS = {
    "with_trend": "顺势/逆势（结果主要由外部趋势决定，具体方向需你结合因果链自行判断）",
    "changing_trend": "正在改变趋势（你所在的因果线反向影响了外部因果线）",
    "inconclusive": "无法判断（现有因果链信息不足以支持这类判断）",
}


@dataclass
class TrendJudgement:
    target_field: str
    self_line_id: str
    verdict: str  # "with_trend" | "changing_trend" | "inconclusive"
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    caveat: Optional[str] = None

    @property
    def verdict_label(self) -> str:
        return _VERDICT_LABELS.get(self.verdict, self.verdict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_field": self.target_field,
            "self_line_id": self.self_line_id,
            "verdict": self.verdict,
            "verdict_label": self.verdict_label,
            "evidence": list(self.evidence),
            "caveat": self.caveat,
        }


def classify_trend(
    history: Sequence[Any], target_field: str, self_line_id: str
) -> TrendJudgement:
    """对目标字段做一次"顺势/逆势/改变趋势"的启发式判断。

    Args:
        history: 同 `attribution.summarize_contributions()`。
        target_field: 要判断的目标字段路径（通常来自
            `manifest.settings.objectives` 里声明过 `field` 的目标）。
        self_line_id: 用户指定的"自己/所在实体"对应的因果线 id
            （`manifest.settings.causal_lines` 里的某个 `id`）——这个
            判断依赖"哪条线代表'我'"这个前提，系统无法自动识别，
            必须由用户在界面上选择。
    """
    target_field = str(target_field or "").strip()
    self_line_id = str(self_line_id or "").strip()

    changing_evidence: List[Dict[str, Any]] = []
    if self_line_id:
        for state in history:
            links = _get_attr_or_key(state, "causal_links") or []
            for link in links:
                if not isinstance(link, dict):
                    continue
                source = str(link.get("source_line_id") or "").strip()
                target_line = str(link.get("line_id") or "").strip()
                if source == self_line_id and target_line and target_line != self_line_id:
                    changing_evidence.append(
                        {
                            "step": _get_attr_or_key(state, "step"),
                            "driver": link.get("driver"),
                            "effect": link.get("effect"),
                            "from_line": source,
                            "to_line": target_line,
                        }
                    )

    if changing_evidence:
        return TrendJudgement(
            target_field=target_field,
            self_line_id=self_line_id,
            verdict="changing_trend",
            evidence=changing_evidence[:5],
            caveat="这是基于既有因果链的启发式判断，不追求精确，仅供参考。",
        )

    report = summarize_contributions(history, target_field) if target_field else None
    if report is not None and report.sources:
        external_sources = [
            s for s in report.sources if s.source_line not in (self_line_id, _UNASSIGNED)
        ]
        if external_sources and external_sources[0].level == "high":
            top = external_sources[0]
            return TrendJudgement(
                target_field=target_field,
                self_line_id=self_line_id,
                verdict="with_trend",
                evidence=list(top.examples),
                caveat=(
                    f"主要贡献来源是外部因果线「{top.source_line}」（{top.level_label}），"
                    "但系统无法自动判断这是顺应了该线的既有走向还是与之相反，"
                    "需要你结合具体因果链内容自行判断顺势/逆势。"
                ),
            )

    return TrendJudgement(
        target_field=target_field,
        self_line_id=self_line_id,
        verdict="inconclusive",
        evidence=[],
        caveat=(
            "贡献来源没有明显集中在外部因果线，也没有发现你所在的因果线"
            "反向影响外部线的记录，暂不满足顺势/逆势/改变趋势判断的条件。"
        ),
    )
