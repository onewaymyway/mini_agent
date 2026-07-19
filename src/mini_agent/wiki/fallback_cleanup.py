"""
wiki/fallback_cleanup.py — session-facts 兜底页面清理
（next_doc/wiki_next_phase_improvement_plan.md 第 5.2 节）

背景：`wiki/world_writer.py::_merge_fact()` 在找不到合适的正式 entity 页面
承接一条 fact 时，会把它落进按天分文件的兜底页 `entities/session-facts-<date>.md`。
这些页面没有 tag（除了统一的 "session-facts"）、没有强链接，几乎不会被图扩展
或专题页聚类捕获——本质上是一个新的垃圾箱：知识被提炼出来了，但又被打入冷宫，
长期看只会越堆越多。

本模块的思路（简化版，页面级粒度而非改进计划草稿设想的逐条 fact 粒度——
逐条拆分需要给每条 fact 独立维护 fact_id 生命周期，改动面更大，先用页面
级粒度验证"重新判重一次"的收益，效果好再考虑细化到 fact 级）：

对创建超过 `min_age_days` 天的兜底页：
  1. 用 `wiki/dedup.py::find_similar_page()` 把整页正文再判重一次——首次落盘
     时正式实体页可能还不全，随着时间推移大概率已经有更合适的实体页出现了；
  2. 命中：把兜底页内容 `append_section` 合并进匹配到的正式实体页，
     兜底页自身标记 `knowledge_state=superseded`（内容已经并入别处）；
  3. 未命中：兜底页标记 `knowledge_state=stale`（不删除——wiki 的可读可信
     原则，历史事实允许存在但要标注置信度下降，供后续人工/下一轮清理复查）。

同一个兜底页只处理一次：处理结果记进 frontmatter `validated_by`（追加
"wiki_fallback_cleanup" 标记，复用 O4 生命周期机制已有的字段，不新增独立
的"是否检查过"字段），已经处理过的页面直接跳过，避免每次清理任务都重复
判重同一批旧页面。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.dedup import find_similar_page
from mini_agent.wiki.indexer import discover_pages
from mini_agent.wiki.lifecycle import mark_page_state
from mini_agent.wiki.parser import WikiPage, parse_page
from mini_agent.wiki.writer import append_section

_FALLBACK_PAGE_PATTERN = re.compile(r"^session-facts-(\d{4}-\d{2}-\d{2})$")


@dataclass
class FallbackCleanupReport:
    scanned: int = 0
    merged: int = 0
    marked_stale: int = 0
    skipped_already_checked: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "merged": self.merged,
            "marked_stale": self.marked_stale,
            "skipped_already_checked": self.skipped_already_checked,
            "errors": self.errors,
        }


def _fallback_page_age_days(page_id: str) -> Optional[int]:
    m = _FALLBACK_PAGE_PATTERN.match(page_id)
    if not m:
        return None
    try:
        page_date = date.fromisoformat(m.group(1))
    except ValueError:
        return None
    return (date.today() - page_date).days


def _load_fallback_pages(paths: AgentPaths) -> list[WikiPage]:
    pages: list[WikiPage] = []
    for p in discover_pages(paths):
        if not p.stem.startswith("session-facts-"):
            continue
        try:
            pages.append(parse_page(p))
        except Exception:
            continue
    return pages


def _load_candidate_entity_pages(paths: AgentPaths) -> list[WikiPage]:
    """判重用的候选正式实体页——排除 session-facts 兜底页本身，避免
    兜底页互相"合并"进对方，失去"归并进正式实体"的意义。"""
    pages: list[WikiPage] = []
    for p in discover_pages(paths):
        if p.stem.startswith("session-facts-"):
            continue
        try:
            page = parse_page(p)
        except Exception:
            continue
        if page.type == "entity":
            pages.append(page)
    return pages


def cleanup_fallback_pages(
    paths: AgentPaths,
    *,
    min_age_days: int = 30,
    llm_call=None,
) -> FallbackCleanupReport:
    """扫描并处理超过 `min_age_days` 天、尚未被判重合并过的兜底页。

    任何单页处理失败都记进 `report.errors`，不影响其余页面继续处理——这是
    低频（默认 7 天一次）的后台清理任务，个别页面处理失败不应该阻断整批。
    """
    report = FallbackCleanupReport()
    fallback_pages = _load_fallback_pages(paths)
    if not fallback_pages:
        return report

    candidate_pages = None  # 惰性加载，没有需要处理的页面时不做无谓 IO

    for page in fallback_pages:
        age = _fallback_page_age_days(page.id)
        if age is None or age < min_age_days:
            continue
        if "wiki_fallback_cleanup" in (page.raw_frontmatter.get("validated_by") or []):
            report.skipped_already_checked += 1
            continue

        report.scanned += 1
        try:
            if candidate_pages is None:
                candidate_pages = _load_candidate_entity_pages(paths)

            match = find_similar_page(
                page.body, page.tags, candidate_pages, llm_call=llm_call,
            )
            if match is not None:
                append_section(
                    paths, match.page,
                    heading="来自 session-facts 兜底页的历史事实",
                    content=page.body.strip(),
                )
                mark_page_state(
                    paths, page.id,
                    confidence="superseded",
                    reason=f"merged_into:{match.page.id}",
                    validated_by="wiki_fallback_cleanup",
                )
                report.merged += 1
            else:
                mark_page_state(
                    paths, page.id,
                    confidence="stale",
                    reason="no_similar_page_found_on_recheck",
                    validated_by="wiki_fallback_cleanup",
                )
                report.marked_stale += 1
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"{page.id}: {exc}")

    return report


__all__ = ["FallbackCleanupReport", "cleanup_fallback_pages"]
