"""world_simulator/causal_graph.py — 因果线耦合结构化（阶段二十七）

设计依据：`next_doc/world_simulator_universal_simulator_gap_analysis_
and_roadmap_v2_plan.md` 4.19 节。

`SimState.causal_links` 此前只有 `line_id`（这条因果链归属到哪条线，
阶段二十二）——能回答"这条线上发生了什么"，但回答不了"哪条线在影响
哪条线、是什么类型的影响"。阶段二十七给 `causal_links` 每项新增两个
可选字段 `relation_type`/`source_line_id`（见 `state_model.py::
SimState.causal_links` docstring），本模块负责把散落在历史各步
`causal_links` 里的这些标注聚合成一个"线到线"的邻接关系视图。

刻意不引入图数据库/正式的 `CausalCoupling` 持久化对象：这里只是
读取已经落盘的历史、做一次性的纯函数聚合，`build_causal_graph()`
不缓存、不落盘，调用方（`app.py`）需要看的时候现算，延续
`analysis.py`/`achievements.py`"纯函数计算，无新增持久化结构"的
既有取舍。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

_VALID_RELATION_TYPES = {"one_way", "two_way", "indirect", "feedback_loop"}
_RELATION_TYPE_LABELS = {
    "one_way": "单向影响",
    "two_way": "双向影响",
    "indirect": "间接影响",
    "feedback_loop": "反馈循环",
}

_UNASSIGNED = "(未归属)"


def _normalize_relation_type(raw: Any) -> str:
    """未给出或值不在四选一之内时，兜底按 `one_way` 处理（展示层默认，
    不代表 `state_model.py` 里做了同样的默认值填充——那里保持"未给出
    即未知"的原样落盘语义）。
    """
    value = str(raw or "").strip()
    return value if value in _VALID_RELATION_TYPES else "one_way"


@dataclass
class CausalEdge:
    """一条"线到线"的聚合边：`source_line`（发起线，`(未归属)` 表示
    `source_line_id` 未给出）→ `target_line`（`causal_links.line_id`，
    同样可能是 `(未归属)`）。`relation_counts` 按 `relation_type` 统计
    这条边一共出现过多少次、各类型各多少次；`examples` 保留最多 3 条
    原始 `driver`/`effect` 文本供展开查看，不做去重合并（同一对线之间
    多次出现是正常的，去重会丢失"这条边一共发生了几次"这个信息）。

    `has_delay`（第十一轮 2.2 节）：这条边聚合过的原始 `causal_links`
    里，是否至少有一条给出了 `delay_steps > 0`——只是一个布尔汇总，
    不区分具体延迟几步（一条边可能来自多次不同延迟的因果链，展示层
    只需要知道"这条边上出现过滞后影响"，不需要精确到某一次）。默认
    `False`：历史数据没有 `delay_steps` 字段时行为不变，向后兼容。
    """

    source_line: str
    target_line: str
    relation_counts: Dict[str, int]
    examples: List[Dict[str, Any]]
    has_delay: bool = False

    @property
    def total(self) -> int:
        return sum(self.relation_counts.values())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_line": self.source_line,
            "target_line": self.target_line,
            "relation_counts": dict(self.relation_counts),
            "total": self.total,
            "examples": list(self.examples),
            "has_delay": self.has_delay,
        }


def build_causal_graph(history: Sequence[Any]) -> List[CausalEdge]:
    """从历史状态序列（`SimStore.load_history()` 的返回值，或任意带
    `causal_links` 属性/键的对象列表）里聚合出"线到线"邻接关系视图。

    对每一步 `causal_links` 里的每一项：
    - `target_line` 取 `line_id`（缺失记为 `(未归属)`）；
    - `source_line` 取 `source_line_id`（缺失记为 `(未归属)`，和
      `target_line` 相同时也保留——表示"同线内部的因果关系"，不特殊
      过滤，展示层自行决定是否要略去这类自环边）；
    - `relation_type` 按 `_normalize_relation_type()` 归一化。

    - `has_delay`：这条边聚合过的 `causal_links` 里只要有一条
      `delay_steps`（第十一轮 2.2 节）能解析成大于 0 的整数，就标记
      为 `True`；解析不出来（缺省、非数字、`<= 0`）不计入，同
      `relationship.py::normalize_relationships()` 对 `delay_steps`
      "解析不出来就退化为 0（不算数）"的既有取舍一致。

    返回按 `total`（出现次数）降序排列的边列表；`causal_links` 全部
    为空或历史为空时返回空列表，调用方应展示"暂无跨线影响记录"之类
    的引导文案，而不是把空列表当成异常。
    """
    buckets: Dict[tuple, Dict[str, Any]] = {}

    for state in history:
        links = getattr(state, "causal_links", None)
        if links is None and isinstance(state, dict):
            links = state.get("causal_links")
        for link in links or []:
            if not isinstance(link, dict):
                continue
            target_line = str(link.get("line_id") or "").strip() or _UNASSIGNED
            source_line = str(link.get("source_line_id") or "").strip() or _UNASSIGNED
            relation_type = _normalize_relation_type(link.get("relation_type"))

            key = (source_line, target_line)
            bucket = buckets.setdefault(
                key,
                {"relation_counts": Counter(), "examples": [], "has_delay": False},
            )
            bucket["relation_counts"][relation_type] += 1
            if len(bucket["examples"]) < 3:
                bucket["examples"].append(
                    {
                        "driver": link.get("driver"),
                        "effect": link.get("effect"),
                        "relation_type": relation_type,
                    }
                )
            try:
                delay_steps = int(link.get("delay_steps") or 0)
            except (TypeError, ValueError):
                delay_steps = 0
            if delay_steps > 0:
                bucket["has_delay"] = True

    edges = [
        CausalEdge(
            source_line=source,
            target_line=target,
            relation_counts=dict(bucket["relation_counts"]),
            examples=bucket["examples"],
            has_delay=bucket["has_delay"],
        )
        for (source, target), bucket in buckets.items()
    ]
    edges.sort(key=lambda e: e.total, reverse=True)
    return edges


def relation_type_label(relation_type: str) -> str:
    """展示层用：把内部枚举值转成中文标签，未知值原样返回。"""
    return _RELATION_TYPE_LABELS.get(relation_type, relation_type)


def format_edges_for_display(edges: Sequence[CausalEdge]) -> List[str]:
    """把 `build_causal_graph()` 的结果格式化成一组人类可读的摘要行，
    比如 `"技术线 → 产业线：3 次单向影响，1 次反馈循环"`，供不想自己
    拼格式的调用方直接使用（`app.py` 目前更倾向于自己按 `to_dict()`
    的结构渲染 HTML，这个函数主要服务于 CLI/日志/未来可能的文本
    摘要场景）。
    """
    lines: List[str] = []
    for edge in edges:
        parts = [
            f"{count} 次{relation_type_label(rt)}"
            for rt, count in sorted(edge.relation_counts.items(), key=lambda kv: -kv[1])
        ]
        lines.append(f"{edge.source_line} → {edge.target_line}：{'，'.join(parts)}")
    return lines
