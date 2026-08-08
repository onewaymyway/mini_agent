"""
wiki/indexer.py — 遍历 wiki/ 目录，生成 _index/ 下的派生索引

_index/ 下的四个文件全部是编译产物，可以随时删除、随时用本模块从 md 重新
生成（重构计划 5.1/5.3 节）：
    graph.json          — GraphIndex.to_dict()
    tags.json           — tag -> [page_id, ...]
    backlinks.json       — GraphIndex.backlinks_to_dict()
    search_index.json   — 关键词倒排索引（page_id -> 词条列表的反向映射）

向量化粗筛复用 perception/local_embedding.py（重构计划 5.3 节），本阶段
先落地关键词倒排，向量部分作为 build_index() 的可选开关，避免阶段一（基础
设施，声明"不影响现有功能"）引入对 embedding 模型加载的强依赖。

增量模式：维护一个 `_index/_manifest.json`（非文档中列出的四个"正式"索引
之一，是 indexer 自用的内部状态），记录每个源文件的 mtime + 内容 hash，
重建时只重新解析改动过的文件，未改动文件复用上次解析结果对应的边/关键词，
避免每次巩固循环触发都要整库重新解析。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.utils.atomic_write import atomic_write_json
from mini_agent.wiki.graph import GraphIndex
from mini_agent.wiki.parser import WikiPage, parse_page
from mini_agent.wiki.validator import ValidationReport, validate_pages

# [顺手修复，与本次隔离区改动无关] 全文件多处用的是 `_atomic_write_json`
# 这个名字，但此前只导入了不带下划线的 `atomic_write_json`，从未定义过
# 别名——`build_index()` 一旦真正写到索引落盘那几行就会 NameError。
# `tests/test_wiki_index_reuse.py` 里 4 个测试因此从最初的代码起就是
# 失败的（在本次改动引入的隔离区功能之前就已经这样，非本次改动导致）。
_atomic_write_json = atomic_write_json

_MANIFEST_NAME = "_manifest.json"
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "of", "to", "in", "on",
    "for", "and", "or", "with", "this", "that", "it", "as", "by", "be",
    "的", "了", "是", "在", "和", "与", "为", "对", "对于", "以及", "这个", "那个",
}
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")



def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tokenize(text: str) -> list[str]:
    """极简分词：英文按 word boundary，中文按单字 — 够用于关键词倒排粗筛，
    不追求分词质量（真正的语义排序留给 LLM 精排 / 向量粗筛）。"""
    tokens = _TOKEN_RE.findall(text.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


def discover_pages(paths: AgentPaths) -> list[Path]:
    """列出 wiki/ 下全部 md 页面文件（不含 _index/ 与 _templates/）。"""
    wiki_dir = paths.wiki_dir
    if not wiki_dir.exists():
        return []
    out: list[Path] = []
    for type_dir in (
        paths.wiki_entities_dir,
        paths.wiki_decisions_dir,
        paths.wiki_processes_dir,
        paths.wiki_experiences_dir,
        paths.wiki_topics_dir,
    ):
        if type_dir.exists():
            out.extend(sorted(type_dir.glob("*.md")))
    return out


@dataclass
class IndexResult:
    pages: list[WikiPage] = field(default_factory=list)
    validation: Optional[ValidationReport] = None
    parse_errors: list[str] = field(default_factory=list)
    reparsed_count: int = 0
    reused_count: int = 0


def _load_manifest(paths: AgentPaths) -> dict:
    manifest_path = paths.wiki_index_dir / _MANIFEST_NAME
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_manifest(paths: AgentPaths, manifest: dict) -> None:
    _atomic_write_json(paths.wiki_index_dir / _MANIFEST_NAME, manifest)


def build_index(paths: AgentPaths, *, incremental: bool = True) -> IndexResult:
    """全量或增量重建 _index/ 下的四个派生文件。

    Args:
        paths: 项目 AgentPaths。
        incremental: True 时只重新解析 mtime/hash 有变化的文件，未变化文件
            跳过重新解析（解析结果轻量，主要省的是重复 IO + yaml 解析开销）。
            解析失败的文件会被跳过并记录到 IndexResult.parse_errors，不中断
            整体重建——一个页面写坏不应该让全库索引都不可用。
    """
    paths.ensure_wiki_dirs()
    manifest = _load_manifest(paths) if incremental else {}
    new_manifest: dict = {}
    result = IndexResult()

    for md_path in discover_pages(paths):
        key = str(md_path.relative_to(paths.wiki_dir))
        stat = md_path.stat()
        cached = manifest.get(key)
        file_hash = _file_hash(md_path)
        if incremental and cached and cached.get("hash") == file_hash:
            result.reused_count += 1
        else:
            result.reparsed_count += 1
        new_manifest[key] = {"mtime": stat.st_mtime, "hash": file_hash}

        try:
            page = parse_page(md_path)
        except Exception as exc:  # noqa: BLE001 - 单页失败不阻断整体重建
            from mini_agent.errors import log_exception
            log_exception(exc, where='mini_agent.wiki.indexer.build_index')
            result.parse_errors.append(f"{md_path}: {exc}")
            try:
                from mini_agent.wiki.quarantine import record_issue
                record_issue(paths, md_path, exc)
            except Exception:
                pass
            continue
        result.pages.append(page)

    graph = GraphIndex.build(result.pages)
    tags_index: dict[str, list[str]] = {}
    for p in result.pages:
        for tag in p.tags:
            tags_index.setdefault(tag, []).append(p.id)
    for tag in tags_index:
        tags_index[tag].sort()

    search_index: dict[str, list[str]] = {}
    for p in result.pages:
        for token in set(_tokenize(p.body) + _tokenize(p.id) + p.tags):
            search_index.setdefault(token, []).append(p.id)
    for token in search_index:
        search_index[token].sort()

    _atomic_write_json(paths.wiki_graph_index, graph.to_dict())
    _atomic_write_json(paths.wiki_tags_index, tags_index)
    _atomic_write_json(paths.wiki_backlinks_index, graph.backlinks_to_dict())
    _atomic_write_json(
        paths.wiki_search_index,
        {"tokens": search_index, "page_count": len(result.pages)},
    )
    _save_manifest(paths, new_manifest)

    result.validation = validate_pages(result.pages)
    return result
