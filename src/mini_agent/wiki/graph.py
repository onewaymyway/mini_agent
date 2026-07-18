"""
wiki/graph.py — 页面间链接图（内存结构，不引入额外依赖）

对应重构计划 5.3 节。图结构本身很简单（dict[id, list[edge]]），刻意不引入
networkx 之类的依赖——当前规模下不需要，且减少一个依赖比"用得上但没用满"
更重要，真的出现大规模图算法需求时再评估引入。

用途：
    - indexer.py 用本模块生成 graph.json / backlinks.json
    - 检索的"图扩展"阶段（重构计划 5.4 节第 2 步）用 GraphIndex.
      expand_legacy() 对命中页面做一跳扩展（默认路径，行为不变）；深度
      检索场景用 GraphIndex.expand() 做多跳衰减扩展（wiki 提取层与
      组织层改进计划 O2 §5.2.1）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from mini_agent.wiki.parser import WikiPage


@dataclass
class Edge:
    source: str
    target: str
    relation: str
    note: str = ""
    strong: bool = True  # frontmatter.links=True，正文 [[..]] 弱引用=False


class GraphIndex:
    """全部页面 links 的内存图，正向边 + 反向边一起维护。"""

    def __init__(self) -> None:
        self._forward: dict[str, list[Edge]] = {}
        self._backward: dict[str, list[Edge]] = {}
        self._known_ids: set[str] = set()

    @classmethod
    def build(cls, pages: Iterable[WikiPage]) -> "GraphIndex":
        g = cls()
        pages = list(pages)
        for p in pages:
            g._known_ids.add(p.id)
        for p in pages:
            for link in p.links:
                g.add_edge(
                    source=p.id,
                    target=link.target,
                    relation=link.relation,
                    note=link.note,
                    strong=(link.source == "frontmatter"),
                )
        return g

    def add_edge(self, source: str, target: str, relation: str, note: str = "", strong: bool = True) -> None:
        edge = Edge(source=source, target=target, relation=relation, note=note, strong=strong)
        self._forward.setdefault(source, []).append(edge)
        self._backward.setdefault(target, []).append(edge)

    def outgoing(self, page_id: str) -> list[Edge]:
        return list(self._forward.get(page_id, []))

    def incoming(self, page_id: str) -> list[Edge]:
        return list(self._backward.get(page_id, []))

    def dead_links(self) -> list[Edge]:
        """target 不在已知页面集合中的边（死链候选，供 validator.py 使用）。"""
        out = []
        for edges in self._forward.values():
            for e in edges:
                if e.target not in self._known_ids:
                    out.append(e)
        return out

    def expand_legacy(self, page_ids: Iterable[str], *, strong_only: bool = False) -> set[str]:
        """对命中的页面集合做一跳扩展，返回扩展后新增的页面 id 集合（不含原集合）。

        对应重构计划 5.4 节"图扩展"：把命中页面的强关系（依赖/取代/因果）
        自动带入候选池；strong_only=True 时只走 frontmatter 强链接，不走
        正文弱引用，避免粗筛阶段被泛泛的 mentions 关系稀释候选质量。

        这是 O2 改进（多跳衰减扩展，见下方 `expand()`）之前的原始实现，
        原样保留、原样命名，供尚未迁移到新签名的调用点使用（wiki 提取层
        与组织层改进计划 O2 §5.2.1："`search.py` 现有调用点先不动，新
        调用点用新签名。待新签名验证稳定后再逐步替换旧调用点"）。
        """
        seed = set(page_ids)
        expanded: set[str] = set()
        for pid in seed:
            for e in self.outgoing(pid):
                if strong_only and not e.strong:
                    continue
                if e.target in self._known_ids:
                    expanded.add(e.target)
            for e in self.incoming(pid):
                if strong_only and not e.strong:
                    continue
                expanded.add(e.source)
        return expanded - seed

    def expand(
        self,
        page_ids: Iterable[str],
        *,
        strong_only: bool = False,
        max_hops: int = 1,
        decay: float = 0.5,
        max_candidates: Optional[int] = None,
    ) -> dict[str, float]:
        """多跳衰减扩展（wiki 提取层与组织层改进计划 O2 §5.2.1）。

        返回 `{page_id: weight}`（不含原 `page_ids` 本身）。第一跳权重为
        `decay`，第二跳为 `decay**2`，以此类推；同一节点通过多条路径 /
        多个跳数可达时取**最大**权重，不是累加——避免热门枢纽节点（比如
        一个被大量页面依赖的基础模块）权重爆炸式偏高、挤占真正更相关的
        候选。

        `max_candidates` 不为 `None` 时，按权重降序只保留前
        `max_candidates` 个（硬上限，计划 §5.4："多跳扩展在图密度高的
        情况下候选数量可能爆炸式增长"）；为 `None`（默认）时不截断，
        由调用方自行决定是否需要上限——`search.py` 的深度检索路径会传入
        `rerank_top_n * 3` 作为上限。

        `max_hops=1` 时候选集合（忽略权重值本身）与 `expand_legacy()`
        完全一致，权重统一为 `decay`；这一等价性由
        `tests/test_graph_expand.py` 覆盖。
        """
        seed = set(page_ids)
        weights: dict[str, float] = {}
        frontier = set(seed)
        hop_weight = 1.0
        for _hop in range(max(0, max_hops)):
            hop_weight *= decay
            next_frontier: set[str] = set()
            for pid in frontier:
                for e in self.outgoing(pid):
                    if strong_only and not e.strong:
                        continue
                    if e.target not in self._known_ids or e.target in seed:
                        continue
                    next_frontier.add(e.target)
                    if hop_weight > weights.get(e.target, 0.0):
                        weights[e.target] = hop_weight
                for e in self.incoming(pid):
                    if strong_only and not e.strong:
                        continue
                    if e.source in seed:
                        continue
                    next_frontier.add(e.source)
                    if hop_weight > weights.get(e.source, 0.0):
                        weights[e.source] = hop_weight
            if not next_frontier:
                break
            frontier = next_frontier

        if max_candidates is not None and len(weights) > max_candidates:
            top = sorted(weights.items(), key=lambda kv: -kv[1])[:max_candidates]
            weights = dict(top)

        return weights

    @classmethod
    def from_dict(cls, graph_data: dict, *, known_ids: Iterable[str]) -> "GraphIndex":
        """从 indexer.py 落盘的 graph.json 结构（to_dict() 的逆操作）重建
        GraphIndex，不需要重新 parse_page 全部页面（wiki 提取层与组织层
        改进计划 O1 §4.2.1：search.py 复用已有派生索引时用它重建图，
        反向边由正向边推导，行为与 build() 完全一致）。

        known_ids 由调用方传入当前磁盘上实际存在的页面 id 集合（比如
        wiki/index_reader.py 通过文件名 stem 得到，零解析成本），用于
        dead_links() 等判断，不从 graph_data 本身推断（graph_data 只有
        出边，可能引用已删除的页面）。
        """
        g = cls()
        g._known_ids = set(known_ids)
        for source, edges in graph_data.items():
            for e in edges:
                g.add_edge(
                    source=source,
                    target=e.get("target", ""),
                    relation=e.get("relation", "mentions"),
                    note=e.get("note", ""),
                    strong=bool(e.get("strong", True)),
                )
        return g

    def to_dict(self) -> dict:
        """序列化为 graph.json 结构：{page_id: [{target, relation, note, strong}, ...]}"""
        return {
            pid: [
                {"target": e.target, "relation": e.relation, "note": e.note, "strong": e.strong}
                for e in edges
            ]
            for pid, edges in sorted(self._forward.items())
        }

    def backlinks_to_dict(self) -> dict:
        """序列化为 backlinks.json 结构：{page_id: [{source, relation, note, strong}, ...]}"""
        return {
            pid: [
                {"source": e.source, "relation": e.relation, "note": e.note, "strong": e.strong}
                for e in edges
            ]
            for pid, edges in sorted(self._backward.items())
        }
