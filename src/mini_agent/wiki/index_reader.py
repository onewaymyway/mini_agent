"""
wiki/index_reader.py — 复用 indexer.py 生成的 _index/ 派生索引作为
search.py 的粗筛数据源（wiki 提取层与组织层改进计划 O1 §4.2.1）。

只读，不做任何重建；索引缺失或与磁盘内容明显不一致（有文件的 mtime 没有
反映在 `_manifest.json` 里，说明自上次 `indexer.py::build_index()` 之后
又有页面被新增/修改/删除过）时，`load_index()` 返回 None，调用方据此退回
全量 `parse_page` 扫描——这一步不改变对外行为，只改变数据来源。

索引重建的触发时机不变，仍由 `evolution/consolidation.py::run_consolidation`
驱动的 `indexer.py::build_index()` 负责（本模块不触发重建）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.graph import GraphIndex
from mini_agent.wiki.indexer import _MANIFEST_NAME, discover_pages


@dataclass
class IndexData:
    """从 _index/ 下派生文件加载出的只读检索数据源。"""

    tokens: dict[str, list[str]] = field(default_factory=dict)
    tags: dict[str, list[str]] = field(default_factory=dict)
    graph: GraphIndex = field(default_factory=GraphIndex)
    id_to_path: dict[str, Path] = field(default_factory=dict)

    def candidate_ids_for(self, query_tokens: set[str], query_tags: set[str]) -> set[str]:
        """query 分词/tag 命中的倒排索引候选 id 并集（零解析成本的粗筛）。"""
        ids: set[str] = set()
        for tok in query_tokens:
            ids.update(self.tokens.get(tok, ()))
        for tag in query_tags:
            ids.update(self.tags.get(tag, ()))
        return ids


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _index_is_fresh(paths: AgentPaths, md_paths: list[Path]) -> bool:
    """比对 `_manifest.json` 记录的文件列表/mtime 与磁盘当前状态是否一致。

    不一致（文件数不同、缺记录、mtime 不匹配）一律判定为"过期"，宁可多做
    一次全量扫描兜底，也不能让检索结果基于陈旧索引——这是"读时校验"，
    不负责修复，只负责判断能不能安全复用。
    """
    manifest = _read_json(paths.wiki_index_dir / _MANIFEST_NAME)
    if manifest is None:
        return False
    if len(manifest) != len(md_paths):
        return False
    for md_path in md_paths:
        key = str(md_path.relative_to(paths.wiki_dir))
        entry = manifest.get(key)
        if entry is None:
            return False
        try:
            if md_path.stat().st_mtime != entry.get("mtime"):
                return False
        except OSError:
            return False
    return True


def load_index(paths: AgentPaths) -> Optional[IndexData]:
    """尝试加载可安全复用的派生索引；缺失/过期时返回 None，调用方应退回
    全量 parse_page 扫描（wiki/search.py::wiki_shelf_search 的现有行为）。
    """
    md_paths = discover_pages(paths)
    if not md_paths:
        return None
    if not _index_is_fresh(paths, md_paths):
        return None

    search_raw = _read_json(paths.wiki_search_index)
    tags_raw = _read_json(paths.wiki_tags_index)
    graph_raw = _read_json(paths.wiki_graph_index)
    if search_raw is None or tags_raw is None or graph_raw is None:
        return None

    id_to_path = {p.stem: p for p in md_paths}
    try:
        graph = GraphIndex.from_dict(graph_raw, known_ids=id_to_path.keys())
    except Exception:
        return None

    return IndexData(
        tokens=search_raw.get("tokens", {}) if isinstance(search_raw, dict) else {},
        tags=tags_raw if isinstance(tags_raw, dict) else {},
        graph=graph,
        id_to_path=id_to_path,
    )


def find_page_path(paths: AgentPaths, page_id: str) -> Optional[Path]:
    """按 page_id 定位其 md 文件路径，不解析内容（文件名固定为
    `<page_id>.md`，wiki/writer.py::write_page 写入时保证这一约定）。
    用于 grounded_hit_count 回写等只需要单页面、不需要全库扫描的场景。
    """
    for md_path in discover_pages(paths):
        if md_path.stem == page_id:
            return md_path
    return None


__all__ = ["IndexData", "load_index", "find_page_path"]
