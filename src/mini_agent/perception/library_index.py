"""
perception/library_index.py — 图书馆式索引的组合外观（Facade）

把 classification.py（分类树/书架）、entity_index.py（实体/著者目录）、
catalog.py（分类指针索引 + 知识编年目录）三者串起来，对外提供两个入口：

  on_new_entry(entry, llm_call=None)
      新记忆写入 MemoryStore 时调用一次：分类 → 挂实体 → 更新目录 → 记编年事件。
      规则命中或 LLM 命中已有节点时同步返回；两者都不中则记为未分类候选，
      交给 Phase G 批量生长分类树（不在这里新建节点）。

  shelf_search(store, query, k, llm_call=None)
      两步检索：先定位书架（1-2 个分类号，含"参见"扩展），再只在书架范围内
      做 MemoryStore 原有的 TF-IDF+时间衰减精排；书架内容太少时才回退到
      全库检索，保证覆盖率。

  consolidate(store, llm_call=None)
      Phase G 巡检调用：批量处理未分类候选（可能长出新分类节点）+
      批量重写攒够证据的实体摘要。
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
from mini_agent.perception.catalog import CategoryCatalog, append_knowledge_event

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
    ) -> None:
        self.tree = ClassificationTree(classification_tree_path)
        self.entities = EntityStore(entity_index_path)
        self.catalog = CategoryCatalog(category_catalog_path)
        self._candidates_path = unclassified_candidates_path
        self._timeline_path = knowledge_timeline_path

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
            return store.search(query, k=k)

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

    # ── Phase G：批量巩固 ────────────────────────────────────────────────

    def consolidate(
        self,
        store: "MemoryStore",
        llm_call: Optional[Callable[[str], str]] = None,
        min_cluster_size: int = 5,
        summary_threshold: int = 3,
    ) -> dict:
        """
        返回本次巩固的摘要统计，供 /evolve phase-g 展示。
        """
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
            )
            # 新节点诞生后，把之前挂在候选里、现已归入该节点的记忆重新登记到目录
            # （grow_from_candidates 已从 remaining 里剔除，这里同步写回 catalog）
        # 新分类节点诞生后，把已被 grow_from_candidates 划入某个聚类的记忆
        # （即从 candidates 里消失、不在 remaining 里的那些）重新过一遍规则
        # 分类（此时新节点已挂进树里，之前不命中的文本现在应该能命中新节点），
        # 更新到分类目录里，替换掉它们此前临时挂载的 "000"。
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

        return {
            "new_categories": len(new_nodes),
            "remaining_unclassified": len(remaining),
            "entities_summarized": rewritten,
        }
