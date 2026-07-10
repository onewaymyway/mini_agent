"""
perception/library_index.py — 图书馆式索引的组合外观（Facade）

把 classification.py（分类树/书架）、entity_index.py（实体/著者目录）、
catalog.py（分类指针索引 + 知识编年目录）三者串起来，对外提供：

  on_new_entry(entry, llm_call=None)
      新记忆写入 MemoryStore 时调用一次：分类 → 挂实体 → 更新目录 → 记编年事件。

  shelf_search(store, query, k, llm_call=None)
      两步检索：先定位书架，再只在书架范围内精排；书架内容太少时回退全库检索。

  record_retrieval_feedback(query, useful, llm_call=None)
      改进4：检索命中质量的自我反馈。命中书架后续被验证有效/无效时调用，
      累积调整该书架的 feedback_score，让分类器越用越准。

  mark_stale_from_correction(store, injected_entry_ids, correction_text)
      改进1+改进5：人类纠正 → 定位刚被检索命中、可能已过时的旧知识 → 标记
      冲突/推翻，而不是任由新旧知识并存靠时间衰减慢慢盖过去。

  consolidate(store, llm_call=None)
      Phase G 巡检调用：批量处理未分类候选（改进2：新增/合并分类节点）、
      批量重写攒够证据的实体摘要（含改进1冲突检测）、实体去噪与近重复合并
      （改进3）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from mini_agent.perception.classification import (
    ClassificationTree,
    load_unclassified_candidates,
    record_unclassified_candidate,
    save_unclassified_candidates,
)
from mini_agent.perception.entity_index import EntityStore
from mini_agent.perception.catalog import (
    CategoryCatalog,
    append_knowledge_event,
    load_timeline_for,
)

if TYPE_CHECKING:
    from mini_agent.perception.memory_store import MemoryEntry, MemoryStore

_MIN_SHELF_SIZE = 3   # 书架内候选数少于此值时，扩展到"参见"分类或回退全库检索


class LibraryIndex:
    """挂在某个 scope（project 或 global）的一组索引文件路径上。"""

    def __init__(
        self,
        classification_tree_path: Path,
        unclassified_candidates_path: Path,
        entity_index_path: Path,
        category_catalog_path: Path,
        knowledge_timeline_path: Path,
        knowledge_timeline_index_path: Optional[Path] = None,
    ) -> None:
        self.tree = ClassificationTree(classification_tree_path)
        self.entities = EntityStore(entity_index_path)
        self.catalog = CategoryCatalog(category_catalog_path)
        self._candidates_path = unclassified_candidates_path
        self._timeline_path = knowledge_timeline_path
        # 改进6：编年目录的侧车索引；不给时退化为"只能读最近 N 条"，不支持
        # 按实体/分类过滤（旧调用点/测试仍然可用，只是查询能力弱一点）。
        self._timeline_index_path = (
            knowledge_timeline_index_path
            or knowledge_timeline_path.parent / (knowledge_timeline_path.stem + "_index.json")
        )
        # 上一次 shelf_search 命中的分类号，供 record_retrieval_feedback /
        # mark_stale_from_correction 在调用方没有显式传 category 时兜底使用。
        self._last_shelf_code: Optional[str] = None

    # ── 写入侧：新记忆上架 ───────────────────────────────────────────────

    def on_new_entry(
        self, entry: "MemoryEntry", llm_call: Optional[Callable[[str], str]] = None
    ) -> None:
        text = entry.to_search_text() if hasattr(entry, "to_search_text") else str(entry)

        rule_code = self.tree.classify_by_rule(text)
        if rule_code is not None:
            code = rule_code
        elif llm_call is not None:
            llm_code = self.tree.classify_by_llm(text, llm_call)
            code = llm_code if llm_code is not None else None
        else:
            code = None

        if code is None:
            # 规则和 LLM 都未命中已有书架：记候选，等 Phase G 批量生长，
            # 记忆本身临时挂在根节点下（检索时仍可通过全库回退命中）。
            record_unclassified_candidate(self._candidates_path, text, entry.entry_id)
            code = "000"
        else:
            self.tree._bump_entry_count(code)

        entry.category = code
        self.catalog.add_entry(code, entry.entry_id)

        entity_ids = self.entities.link_entry(entry.entry_id, text, category=code)
        entry.entity_ids = entity_ids

        append_knowledge_event(
            self._timeline_path,
            entry_id=entry.entry_id,
            event_type="created",
            category=code,
            entity_ids=entity_ids,
            index_path=self._timeline_index_path,
        )

    # ── 读取侧：两步检索 ─────────────────────────────────────────────────

    def shelf_search(
        self,
        store: "MemoryStore",
        query: str,
        k: int = 3,
        llm_call: Optional[Callable[[str], str]] = None,
    ) -> list:
        """
        第一步定位书架，第二步只在候选集合内做精细检索。
        书架候选不足 _MIN_SHELF_SIZE 时，先扩展到"参见"分类，仍不足则
        回退到全库检索（保证任何情况下都不会因为分类失误而查不到东西）。
        """
        rule_code = self.tree.classify_by_rule(query)
        code = rule_code
        if code is None and llm_call is not None:
            code = self.tree.classify_by_llm(query, llm_call)

        if code is None:
            self._last_shelf_code = None
            return store.search(query, k=k)

        self._last_shelf_code = code
        candidate_codes = self.tree.related_codes(code)
        entry_ids: set[str] = set()
        for c in candidate_codes:
            entry_ids.update(self.catalog.entry_ids_for(c))

        if len(entry_ids) < _MIN_SHELF_SIZE:
            return store.search(query, k=k)

        all_entries = store.all_entries()
        shelf_entries = [e for e in all_entries if e.entry_id in entry_ids]
        if not shelf_entries:
            return store.search(query, k=k)

        return store.rank_subset(query, shelf_entries, k=k)

    # ── 改进4：检索命中质量的自我反馈 ────────────────────────────────────

    def record_retrieval_feedback(
        self, useful: bool, category: Optional[str] = None
    ) -> None:
        """
        对最近一次（或指定的）书架分类号记一次反馈。useful=True 强化该书架的
        关键词权重（下次同类 query 更容易命中它），useful=False 则弱化。
        category 为 None 时用 shelf_search 最近一次命中的分类号——这对应
        "这次检索到的东西后来被证明有用/没用"的典型调用场景（调用方通常
        不需要重新判断分类号是什么）。
        """
        code = category or self._last_shelf_code
        if code is None:
            return
        self.tree.record_feedback(code, useful)

    # ── 改进1 + 改进5：人类纠正 → 定位并标记过时知识 ─────────────────────

    def mark_stale_from_correction(
        self,
        store: "MemoryStore",
        injected_entry_ids: list[str],
        correction_text: str = "",
    ) -> dict:
        """
        当 correction_detector 检测到人类纠正时调用：把上一轮实际注入到上下文
        里的记忆（injected_entry_ids，来自 ContextBuilder.last_injected_memory_ids）
        标记为"可能过时"——对其所属分类给负反馈（改进4 的具体应用场景），
        对其关联实体记一条 superseded 标注（改进1），并写一条 knowledge_timeline
        事件，形成"纠正 → 定位旧知识 → 标记过时"的完整闭环，而不是让新旧知识
        并存、靠时间衰减慢慢覆盖。

        这是"最近命中确实和这次纠正相关"的一个保守假设——不追求精确因果关系
        判定，宁可标记宽一点（多标一次 superseded 的成本远低于放任过时知识
        继续被检索命中）。
        """
        if not injected_entry_ids:
            return {"marked_categories": 0, "marked_entities": 0}

        all_entries = {e.entry_id: e for e in store.all_entries()}
        marked_categories: set[str] = set()
        marked_entities: set[str] = set()

        for eid in injected_entry_ids:
            entry = all_entries.get(eid)
            if entry is None:
                continue
            category = getattr(entry, "category", "") or ""
            if category and category not in marked_categories:
                self.tree.record_feedback(category, useful=False)
                marked_categories.add(category)
            for entity_id in getattr(entry, "entity_ids", []) or []:
                if entity_id in marked_entities:
                    continue
                self.entities.mark_superseded(
                    entity_id, reason=f"人类纠正：{correction_text[:150]}"
                )
                marked_entities.add(entity_id)
            append_knowledge_event(
                self._timeline_path,
                entry_id=eid,
                event_type="superseded",
                category=category,
                entity_ids=list(getattr(entry, "entity_ids", []) or []),
                detail=correction_text[:200],
                index_path=self._timeline_index_path,
            )

        return {
            "marked_categories": len(marked_categories),
            "marked_entities": len(marked_entities),
        }

    # ── 改进6：时间线查询 ────────────────────────────────────────────────

    def timeline_for(
        self,
        *,
        entity_id: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """按实体或分类号查询知识生命周期编年事件，供 /evolve timeline 等命令使用。"""
        return load_timeline_for(
            self._timeline_path,
            self._timeline_index_path,
            entity_id=entity_id,
            category=category,
            limit=limit,
        )

    # ── Phase G：批量巩固 ────────────────────────────────────────────────

    def consolidate(
        self,
        store: "MemoryStore",
        llm_call: Optional[Callable[[str], str]] = None,
        min_cluster_size: int = 5,
        summary_threshold: int = 3,
        merge_threshold: float = 0.6,
        entity_similarity_threshold: float = 0.82,
    ) -> dict:
        """
        返回本次巩固的统计，供 /evolve phase-g 展示。顺序：
          1. 分类树生长（未分类候选聚类出新节点）
          2. 分类树合并（改进2：语义重合的节点收敛）
          3. 实体摘要批量重写（含改进1冲突检测）
          4. 实体巩固：去噪 + 近重复合并（改进3）
        """
        # 1. 分类树生长
        candidates = load_unclassified_candidates(self._candidates_path)
        new_nodes, remaining = self.tree.grow_from_candidates(
            candidates, min_cluster_size=min_cluster_size,
        )
        save_unclassified_candidates(self._candidates_path, remaining)
        for node in new_nodes:
            append_knowledge_event(
                self._timeline_path,
                entry_id="",
                event_type="new_category",
                category=node.code,
                detail=node.name,
                index_path=self._timeline_index_path,
            )

        # 新分类节点诞生后，把已被 grow_from_candidates 划入某个聚类的记忆
        # （即从 candidates 里消失、不在 remaining 里的那些）重新过一遍规则
        # 分类，更新到分类目录里，替换掉它们此前临时挂载的 "000"。
        assigned_ids = {c["entry_id"] for c in candidates} - {
            c["entry_id"] for c in remaining
        }
        if assigned_ids and new_nodes:
            all_entries = {e.entry_id: e for e in store.all_entries()}
            updates: dict[str, str] = {}
            for eid in assigned_ids:
                entry = all_entries.get(eid)
                if entry is None:
                    continue
                new_code = self.tree.classify_by_rule(entry.to_search_text()) or "000"
                self.catalog.add_entry(new_code, eid)
                updates[eid] = new_code
            if updates:
                store.rewrite_categories(updates)

        # 2. 分类树合并（改进2）：语义重合的书架收敛为一个，避免"只生长不收敛"
        merges = self.tree.merge_similar_nodes(threshold=merge_threshold)
        for old_code, new_code in merges:
            self.catalog.redirect(old_code, new_code)
            append_knowledge_event(
                self._timeline_path,
                entry_id="",
                event_type="category_merged",
                category=new_code,
                detail=f"合并自 {old_code}",
                index_path=self._timeline_index_path,
            )
            # 被合并节点下的记忆 category 字段也需要更新到规范分类号，
            # 否则下次 shelf_search 命中新节点时，rank_subset 反而查不到
            # 这些历史记忆（它们的 category 还停留在旧分类号上）。
            old_ids = set()
            all_entries = {e.entry_id: e for e in store.all_entries()}
            for eid, e in all_entries.items():
                if getattr(e, "category", "") == old_code:
                    old_ids.add(eid)
            if old_ids:
                store.rewrite_categories({eid: new_code for eid in old_ids})

        # 3. 实体摘要批量重写（含改进1冲突检测，见 EntityStore.rewrite_summary）
        due_entities = self.entities.due_for_summary_rewrite(threshold=summary_threshold)
        all_entries_by_id = {e.entry_id: e for e in store.all_entries()}
        rewritten = 0
        for entity in due_entities:
            texts = [
                all_entries_by_id[eid].to_search_text()
                for eid in entity.related_entry_ids
                if eid in all_entries_by_id
            ]
            if not texts:
                continue
            self.entities.rewrite_summary(entity, texts, llm_call=llm_call)
            rewritten += 1

        # 4. 实体巩固：去噪 + 近重复合并（改进3）
        entity_stats = self.entities.consolidate_entities(
            llm_call=llm_call, similarity_threshold=entity_similarity_threshold,
        )

        return {
            "new_categories": len(new_nodes),
            "category_merges": len(merges),
            "remaining_unclassified": len(remaining),
            "entities_summarized": rewritten,
            "entities_deprecated": entity_stats.get("deprecated", 0),
            "entities_merged": entity_stats.get("merged", 0),
        }
