"""world_simulator/attribution.py — 归因/贡献拆解报告（阶段二十九）

设计依据：`next_doc/world_simulator_universal_simulator_gap_analysis_
and_roadmap_v2_plan.md` 4.21 节。

背景：`SimState.key_drivers` 只是一份"划重点"短语列表，`analysis.
aggregate_field_stats()` 给的是跨分支的统计摘要，两者都回答不了
"这个具体结果字段最终是被哪条因果线／哪些因果链条决定的"这个问题。
本模块从历史 `causal_links`（阶段二十七起支持的 `relation_type`/
`source_line_id`，见 `causal_graph.py`）里筛出真正提到过某个目标
字段的因果链，按"来源线"分组统计，输出一份"贡献来源清单"。

刻意延续项目一贯的"不做伪精确"取舍：
- 不做回归系数/敏感性打分之类的量化归因（那需要能反复重跑模拟做
  对照实验，超出"读历史、做统计聚合"这个模块的职责范围，真要做
  应该是反事实矩阵——4.22 节——之上的进阶功能）。
- 不构造反事实来验证归因（同上，属于 4.22 的范畴）。
- 相关程度只分"高/中/低相关"三档（基于该来源线在所有匹配因果链
  里的出现占比，不是什么统计显著性检验），和 `confidence: high/
  medium/low`、`likelihood: high/medium/low` 一以贯之。

和 `causal_graph.py` 一样，本模块是纯函数、不缓存、不落盘：调用方
（`app.py`）需要看的时候现算，输入是 `SimStore.load_history()` 的
返回值（或任意带 `causal_links`/`uncertain_fields` 属性/键的对象
列表），不发起任何新的 LLM 调用。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

_UNASSIGNED = "(未归属)"

_LEVEL_LABELS = {
    "high": "高相关",
    "medium": "中相关",
    "low": "低相关",
}


def _get_attr_or_key(state: Any, name: str) -> Any:
    """兼容 `SimState` 实例（属性）和纯 dict（如测试里直接构造的假
    历史数据）两种输入形态，取不到时返回 `None`。"""
    value = getattr(state, name, None)
    if value is None and isinstance(state, dict):
        value = state.get(name)
    return value


def _last_segment(path: str) -> str:
    return path.rsplit(".", 1)[-1]


def _field_matches(affected: Any, target_field: str) -> bool:
    """`causal_links[i].affected_fields` 是自由文本字段名列表（不要求
    是可执行的读写路径，见 `state_model.py::SimState.causal_links`
    docstring），这里用两种方式判断是否命中目标字段：完全相同，或者
    去掉嵌套前缀后的末段相同（比如 `resources.cash` 和 `cash`）。"""
    aff = str(affected or "").strip()
    target = str(target_field or "").strip()
    if not aff or not target:
        return False
    if aff == target:
        return True
    return _last_segment(aff) == _last_segment(target)


def _classify_level(count: int, total: int) -> str:
    """按该来源线在所有匹配因果链里的出现占比分档，不是统计显著性
    检验——延续项目"不做伪精确"的一贯风格。"""
    if total <= 0:
        return "low"
    ratio = count / total
    if ratio >= 0.5:
        return "high"
    if ratio >= 0.2:
        return "medium"
    return "low"


@dataclass
class ContributionSource:
    """某个来源线（因果线 `id`，或 `(未归属)` 表示没有 `source_line_id`/
    `line_id` 标注）对目标字段的贡献汇总。"""

    source_line: str
    count: int
    level: str  # "high" | "medium" | "low"
    relation_counts: Dict[str, int] = field(default_factory=dict)
    examples: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def level_label(self) -> str:
        return _LEVEL_LABELS.get(self.level, self.level)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_line": self.source_line,
            "count": self.count,
            "level": self.level,
            "level_label": self.level_label,
            "relation_counts": dict(self.relation_counts),
            "examples": list(self.examples),
        }


@dataclass
class ContributionReport:
    """`summarize_contributions()` 的返回值：目标字段 + 按来源线排序
    （出现次数降序）的贡献清单 + 可选的低置信度提示。"""

    target_field: str
    total_matched_links: int
    sources: List[ContributionSource] = field(default_factory=list)
    caveat: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_field": self.target_field,
            "total_matched_links": self.total_matched_links,
            "sources": [s.to_dict() for s in self.sources],
            "caveat": self.caveat,
        }


def _find_uncertainty_caveat(history: Sequence[Any], target_field: str) -> Optional[str]:
    """如果 `target_field` 在任意一步的 `uncertain_fields` 里被标注过
    （不要求是最后一步——只要标注过，说明这个字段本质上是主观估计，
    归因清单本身也应该带上同样的保留态度），返回一句提示文案；从未
    标注过时返回 `None`。"""
    for state in history:
        for item in _get_attr_or_key(state, "uncertain_fields") or []:
            if not isinstance(item, dict):
                continue
            if _field_matches(item.get("field"), target_field):
                return "这个结果本身包含较大不确定性，归因仅供参考。"
    return None


def summarize_contributions(
    history: Sequence[Any], target_field: str
) -> ContributionReport:
    """从历史 `causal_links` 里筛出提到过 `target_field`
    （`affected_fields` 命中）的条目，按来源线（优先 `source_line_id`，
    没有则退化用 `line_id` 表示"同线内部产生的影响"，都没有则归到
    `(未归属)`）分组统计"提及次数 + 关系类型分布 + 最多 3 条具体因果
    关系示例"。

    Args:
        history: `SimStore.load_history()` 的返回值，或任意带
            `causal_links`/`uncertain_fields` 属性/键的对象列表。
        target_field: 要归因的字段路径（通常来自
            `manifest.settings.objectives` 里声明过 `field` 的目标）。

    Returns:
        `ContributionReport`；`target_field` 为空、或历史中没有任何
        `causal_links` 提到过这个字段时，返回 `sources=[]` 的空报告
        （不是异常），调用方应展示"暂无归因线索"之类的引导文案。
    """
    target_field = str(target_field or "").strip()
    buckets: Dict[str, Dict[str, Any]] = {}
    total = 0

    if target_field:
        for state in history:
            links = _get_attr_or_key(state, "causal_links") or []
            for link in links:
                if not isinstance(link, dict):
                    continue
                affected = link.get("affected_fields") or []
                if not any(_field_matches(a, target_field) for a in affected):
                    continue
                source_line = str(link.get("source_line_id") or "").strip()
                if not source_line:
                    source_line = str(link.get("line_id") or "").strip() or _UNASSIGNED

                bucket = buckets.setdefault(
                    source_line, {"count": 0, "relation_counts": Counter(), "examples": []}
                )
                bucket["count"] += 1
                total += 1
                relation_type = str(link.get("relation_type") or "one_way").strip() or "one_way"
                bucket["relation_counts"][relation_type] += 1
                if len(bucket["examples"]) < 3:
                    bucket["examples"].append(
                        {
                            "driver": link.get("driver"),
                            "effect": link.get("effect"),
                            "step": _get_attr_or_key(state, "step"),
                        }
                    )

    sources = [
        ContributionSource(
            source_line=source_line,
            count=bucket["count"],
            level=_classify_level(bucket["count"], total),
            relation_counts=dict(bucket["relation_counts"]),
            examples=bucket["examples"],
        )
        for source_line, bucket in buckets.items()
    ]
    sources.sort(key=lambda s: s.count, reverse=True)

    caveat = _find_uncertainty_caveat(history, target_field) if target_field else None

    return ContributionReport(
        target_field=target_field,
        total_matched_links=total,
        sources=sources,
        caveat=caveat,
    )
