"""
wiki/stats.py — wiki 内容来源分布统计（wiki 式知识库改进计划 P0）

目的：量化“wiki 是不是只有错误信息”这件事，作为改进前后的基线对比。
只读统计，不做任何写入；可以随时对当前 wiki/ 目录跑一次，不依赖
_index/ 下的派生索引（直接 parse_page 全量扫描，用法与
wiki/search.py::wiki_shelf_search 一致，当前规模下足够快）。

source_kind 取值约定（各写入模块负责在 extra_frontmatter 里打上）：
    correction                    —— perception/library_index.py 因人类纠正触发的镜像
    entity_mirror                 —— perception/library_index.py 常规实体镜像（on_new_entry）
    decision                      —— wiki/decision_writer.py
    world_model                   —— wiki/world_writer.py（wiki 改进计划 P1）
    experience_success            —— wiki/experience_writer.py，自我进化 verdict=improved 触发（P2）
    experience_session_reflection —— wiki/experience_writer.py，session 结束且全程无纠正触发（P2）
    (缺失/其它)                    —— 历史遗留页面，写入时尚未打上该字段
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.indexer import discover_pages
from mini_agent.wiki.parser import parse_page

_UNKNOWN = "(unknown)"


@dataclass
class WikiStats:
    total_pages: int = 0
    by_type: dict = field(default_factory=dict)          # page_type -> count
    by_entity_type: dict = field(default_factory=dict)   # entity_type（仅 entity 页面）-> count
    by_source_kind: dict = field(default_factory=dict)   # source_kind -> count

    def to_dict(self) -> dict:
        return {
            "total_pages": self.total_pages,
            "by_type": dict(sorted(self.by_type.items())),
            "by_entity_type": dict(sorted(self.by_entity_type.items())),
            "by_source_kind": dict(sorted(self.by_source_kind.items())),
        }


def compute_stats(paths: AgentPaths) -> WikiStats:
    """扫描 wiki/ 全量页面并统计分布，供 /wiki stats 命令 / 改进计划验收记录使用。"""
    stats = WikiStats()
    for md_path in discover_pages(paths):
        try:
            page = parse_page(md_path)
        except Exception:
            continue
        stats.total_pages += 1
        stats.by_type[page.type] = stats.by_type.get(page.type, 0) + 1

        if page.type == "entity":
            # tags[0] 通常是 entity_type（wiki/migration.py::_entity_tags /
            # wiki/world_writer.py 都把 entity_type 作为第一个 tag 写入）；
            # 没有 tags 时归入 _UNKNOWN，不猜测。
            entity_type = page.tags[0] if page.tags else _UNKNOWN
            stats.by_entity_type[entity_type] = stats.by_entity_type.get(entity_type, 0) + 1

        source_kind = str(page.raw_frontmatter.get("source_kind") or _UNKNOWN)
        stats.by_source_kind[source_kind] = stats.by_source_kind.get(source_kind, 0) + 1

    return stats


__all__ = ["WikiStats", "compute_stats"]
