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
    world_model                   —— wiki/world_writer.py（wiki 改进计划 P1，对话来源）
    external_watch                —— external_input/knowledge_extractor.py（外部数据知识化计划 P1，
                                      agent_watch 频道 RSS 事件抽取）
    external_search               —— 外部数据知识化计划 P3（sys:tech_radar_search，尚未实现，预留值）
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
    by_knowledge_state: dict = field(default_factory=dict)  # O4：fresh/stale/superseded -> count

    def to_dict(self) -> dict:
        return {
            "total_pages": self.total_pages,
            "by_type": dict(sorted(self.by_type.items())),
            "by_entity_type": dict(sorted(self.by_entity_type.items())),
            "by_source_kind": dict(sorted(self.by_source_kind.items())),
            "by_knowledge_state": dict(sorted(self.by_knowledge_state.items())),
        }


@dataclass
class ExtractionStats:
    """
    wiki 提取层改进计划 E2 方案B：结构化抽取批次（compact 时与摘要同一次
    LLM 调用产出的 decisions/entities/facts）的数量统计，读取
    paths.extraction_stats_log（history/compression.py::_log_extraction_stats
    追加写入）。用于对比 schema 字段顺序调整前后的抽取充分性，纯只读。
    """

    total_batches: int = 0
    avg_decisions_per_extraction: float = 0.0
    avg_entities_per_extraction: float = 0.0
    avg_facts_per_extraction: float = 0.0
    zero_entities_and_facts_ratio: float = 0.0  # 两个数组同时为空的批次占比

    def to_dict(self) -> dict:
        return {
            "total_batches": self.total_batches,
            "avg_decisions_per_extraction": round(self.avg_decisions_per_extraction, 3),
            "avg_entities_per_extraction": round(self.avg_entities_per_extraction, 3),
            "avg_facts_per_extraction": round(self.avg_facts_per_extraction, 3),
            "zero_entities_and_facts_ratio": round(self.zero_entities_and_facts_ratio, 3),
        }


def compute_extraction_stats(paths: AgentPaths, *, last_n: int | None = None) -> ExtractionStats:
    """扫描 extraction_stats_log 计算均值指标，供 /wiki stats 命令 /
    E2 方案B 验收（改动前后各跑 20 次 compact 对比）使用。

    last_n: 只统计最近 N 条记录（改动前后对比时可以传 20），默认全量。
    读取失败（文件不存在/损坏行）静默跳过对应记录，不抛异常。
    """
    import json

    log_path = paths.extraction_stats_log
    records: list[dict] = []
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.wiki.stats.compute_extraction_stats')
                continue

    if last_n is not None:
        records = records[-last_n:]

    stats = ExtractionStats()
    stats.total_batches = len(records)
    if not records:
        return stats

    total_decisions = sum(int(r.get("decisions") or 0) for r in records)
    total_entities = sum(int(r.get("entities") or 0) for r in records)
    total_facts = sum(int(r.get("facts") or 0) for r in records)
    zero_both = sum(
        1 for r in records
        if not (r.get("entities") or 0) and not (r.get("facts") or 0)
    )

    n = len(records)
    stats.avg_decisions_per_extraction = total_decisions / n
    stats.avg_entities_per_extraction = total_entities / n
    stats.avg_facts_per_extraction = total_facts / n
    stats.zero_entities_and_facts_ratio = zero_both / n

    return stats


def compute_stats(paths: AgentPaths) -> WikiStats:
    """扫描 wiki/ 全量页面并统计分布，供 /wiki stats 命令 / 改进计划验收记录使用。"""
    stats = WikiStats()
    for md_path in discover_pages(paths):
        try:
            page = parse_page(md_path)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.wiki.stats.compute_stats')
            # 解析失败的页面除了打日志，还记一条持久化的问题记录——
            # 日志会被淹没在其它输出里没人看，隔离区记录会被
            # sys:wiki_quarantine_repair 周期性捞出来尝试自动修复，
            # 见 wiki/quarantine.py。这里的记录动作本身失败也不该
            # 拖垮统计主流程，单独 try/except。
            try:
                from mini_agent.wiki.quarantine import record_issue
                record_issue(paths, md_path, _mini_agent_exc)
            except Exception:
                pass
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

        # O4：未标记过的历史遗留页面视为 fresh（frontmatter 缺省语义与
        # wiki/lifecycle.py 保持一致）。
        knowledge_state = str(page.raw_frontmatter.get("knowledge_state") or "fresh")
        stats.by_knowledge_state[knowledge_state] = stats.by_knowledge_state.get(knowledge_state, 0) + 1

    return stats


__all__ = [
    "WikiStats", "compute_stats", "ExtractionStats", "compute_extraction_stats",
    "build_wiki_recent_updates_snapshot",
]


def build_wiki_recent_updates_snapshot(paths: AgentPaths, *, max_items: int = 8) -> str:
    """[next_doc/profile_context_sources_completeness_plan.md 方向 C，
    第一步：零 LLM 成本版本] 为 `UserProfileManager.generate()` 准备一份
    "最近更新的长期调研/学习类 wiki 条目"轻量快照。

    只扫描 `wiki_research_dir`（`research_topic` 执行规范对应的持续调研
    正文，如用户反馈里举例的 `research/agent_and_ai`）和
    `wiki_growth_dir`（成长顾问学习素材，数据结构与推进逻辑相同，见
    `AgentPaths.wiki_research_dir` 文档字符串）这两个命名空间——它们是
    "用户主导的长期任务持续产出"的直接体现，跟画像"summary"里想表达的
    "用户在做什么、做到什么程度"高度相关；不扫描整个 wiki（entities/
    decisions/experiences 等其它命名空间是 agent 自身的知识沉淀，跟
    "用户是谁"这层画像信息关系不大，扫了也只会稀释真正有用的部分）。

    只取标题（页面 id）+ 更新时间，不读取正文——正文摘要如果需要，
    应该是第二步（评估是否要对 wiki 正文做单独摘要），不在这个零成本
    版本的范围内。任一环节异常直接返回空串，与其它 profile snapshot
    函数（`build_goal_tree_profile_snapshot` / `build_watchlist_profile_
    snapshot`）保持一致的降级风格。
    """
    # 注意：`discover_pages()` 只扫描 entities/decisions/processes/
    # experiences/topics 五个命名空间（见 wiki/indexer.py），本来就不
    # 包含 research/growth——这两个目录是独立的"用户主导长期任务"命名
    # 空间，不挂在通用 wiki 索引下（避免跟 entities 等 agent 自身知识
    # 沉淀混在一起）。因此这里不能复用 discover_pages() 的结果，需要
    # 直接对 wiki_research_dir / wiki_growth_dir 各自 glob。
    try:
        research_dir = paths.wiki_research_dir
        growth_dir = paths.wiki_growth_dir
    except Exception:
        return ""

    page_paths: list = []
    for d in (research_dir, growth_dir):
        try:
            if d.exists():
                page_paths.extend(sorted(d.glob("*.md")))
        except Exception:
            continue

    if not page_paths:
        return ""

    relevant_pages = []
    for page_path in page_paths:
        try:
            relevant_pages.append(parse_page(page_path))
        except Exception:
            continue

    if not relevant_pages:
        return ""

    relevant_pages.sort(key=lambda pg: pg.updated or pg.created or "", reverse=True)

    lines = ["Recently updated long-running research/learning wiki entries "
             "(most recently updated first):"]
    for pg in relevant_pages[:max_items]:
        title = pg.id or pg.path.stem
        when = pg.updated or pg.created
        lines.append(f"- {title}" + (f" (last updated: {when})" if when else ""))

    if len(lines) <= 1:
        return ""
    return "\n".join(lines)
