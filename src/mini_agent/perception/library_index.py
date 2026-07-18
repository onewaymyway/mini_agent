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
      巩固循环 巡检调用：批量处理未分类候选（改进2：新增/合并分类节点）、
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
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.wiki.dedup import EmbedCall

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
        wiki_paths: Optional["AgentPaths"] = None,
    ) -> None:
        self.tree = ClassificationTree(classification_tree_path)
        self.entities = EntityStore(entity_index_path)
        self.catalog = CategoryCatalog(category_catalog_path)
        self._candidates_path = unclassified_candidates_path
        self._timeline_path = knowledge_timeline_path
        self._entity_index_path = entity_index_path
        # wiki式知识库重构计划 5.5 节：过渡期新知识双写。wiki_paths=None
        # （默认）时完全不触碰 wiki/ 目录，旧行为零改动；传入后 on_new_entry
        # / consolidate 会尽力镜像到 wiki，镜像失败不影响图书馆索引主流程。
        self._wiki_paths = wiki_paths
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
            # 规则和 LLM 都未命中已有书架：记候选，等 巩固循环 批量生长，
            # 记忆本身临时挂在根节点下（检索时仍可通过全库回退命中）。
            record_unclassified_candidate(self._candidates_path, text, entry.entry_id)
            code = "000"
        else:
            self.tree._bump_entry_count(code)

        entry.category = code
        self.catalog.add_entry(code, entry.entry_id)

        entity_ids = self.entities.link_entry(entry.entry_id, text, category=code)
        entry.entity_ids = entity_ids
        self._mirror_entities_to_wiki(entity_ids)

        append_knowledge_event(
            self._timeline_path,
            entry_id=entry.entry_id,
            event_type="created",
            category=code,
            entity_ids=entity_ids,
            index_path=self._timeline_index_path,
        )

    # ── wiki式知识库重构计划 5.5 节：过渡期双写 ──────────────────────────

    def _mirror_entities_to_wiki(
        self, entity_ids: list[str], note: Optional[str] = None, *, source_kind: str = "entity_mirror"
    ) -> None:
        """把给定实体的当前状态镜像进 wiki/entities/*.md，最佳努力、不阻断主流程。

        失败原因通常是：wiki_paths 未配置（默认）、pyyaml 未安装、磁盘写
        入偶发失败。这些都不应该影响图书馆索引（分类/实体/编年目录）本身
        的写入成功——wiki 是过渡期的镜像层，不是当前的主索引。

        source_kind 透传给 wiki/migration.py::mirror_entity，供
        wiki/stats.py 统计来源分布（wiki 改进计划 P0）：默认
        "entity_mirror"（on_new_entry 的常规镜像），
        mark_stale_from_correction() 调用时会传 "correction"。
        """
        if self._wiki_paths is None or not entity_ids:
            return
        try:
            from mini_agent.wiki.migration import mirror_entity
        except ImportError:
            return
        for eid in entity_ids:
            entity = self.entities.get(eid)
            if entity is None:
                continue
            try:
                mirror_entity(entity, self._wiki_paths, note=note, source_kind=source_kind)
            except Exception:
                continue

    # ── wiki式知识库重构计划阶段三：三段式检索的平行实现 ──────────────────

    def wiki_search(
        self,
        query: str,
        k: int = 5,
        llm_call: Optional[Callable[[str], str]] = None,
        tags: Optional[list] = None,
        confidence_weight: Optional[float] = None,
        use_index: bool = True,
        deep: Optional[bool] = None,
    ):
        """
        三段式检索（规则粗筛 → 图扩展 → LLM 精排）的入口，与 shelf_search
        并存、互不替换，供 A/B 对比新旧检索路径效果（重构计划 5.4 节 /
        阶段三）。wiki_paths 未配置（默认）或 wiki/ 下没有页面时返回一个
        空的 WikiSearchResult，调用方应据此回退到 shelf_search。

        confidence_weight / use_index：wiki 提取层与组织层改进计划 O1
        （分层索引 + 信度加权）的透传参数，对应 MemoryConfig.
        wiki_confidence_weight / wiki_index_reuse_enabled；调用方不传时
        使用 wiki/search.py 自身的默认值。

        deep：O2（多跳衰减图扩展）的透传参数，`None`=按候选数量自动
        判断，`True`=强制多跳，`False`=强制维持一跳；不传时使用
        wiki/search.py 自身的默认值（`None`）。
        """
        from mini_agent.wiki.search import WikiSearchResult, wiki_shelf_search

        if self._wiki_paths is None:
            return WikiSearchResult()
        kwargs = {}
        if confidence_weight is not None:
            kwargs["confidence_weight"] = confidence_weight
        if deep is not None:
            kwargs["deep"] = deep
        return wiki_shelf_search(
            self._wiki_paths, query, tags=tags, k=k, llm_call=llm_call,
            use_index=use_index, **kwargs,
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

    # ── wiki 式知识库改进计划 P4：转正评估 ────────────────────────────────

    def record_search_comparison(
        self, *, wiki_grounded: bool, shelf_grounded: bool, query: str = ""
    ) -> None:
        """记一条 wiki_search vs shelf_search 的 A/B 命中对比（P4 标准 2）。

        调用方通常在同一次检索请求里先后跑了 `wiki_search()` 和
        `shelf_search()` 两条路径做人工/自动对比时调用；`wiki_paths` 未配置
        时静默跳过（没有 wiki 就无从谈起"转正"）。
        """
        if self._wiki_paths is None:
            return
        from mini_agent.wiki.promotion import record_search_comparison as _record

        _record(
            self._wiki_paths,
            wiki_grounded=wiki_grounded,
            shelf_grounded=shelf_grounded,
            query=query,
        )

    def promotion_status(self):
        """返回 P4 三项"转正"标准的当前达成情况（`PromotionReadiness`）。

        `wiki_paths` 未配置时返回一个全部指标默认值（未达标）的空结果——
        没有 wiki 就没有转正的前提。数据来源见 `consolidate()` 步骤 7b
        （每日快照）与 `record_search_comparison()`（A/B 对比）。
        """
        from mini_agent.wiki.promotion import PromotionReadiness, evaluate_promotion_readiness

        if self._wiki_paths is None:
            return PromotionReadiness()
        return evaluate_promotion_readiness(self._wiki_paths)

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
                reason = f"人类纠正：{correction_text[:150]}"
                self.entities.mark_superseded(entity_id, reason=reason)
                self._mirror_entities_to_wiki(
                    [entity_id], note=f"⚠已标记 superseded — {reason}", source_kind="correction",
                )
                # wiki 提取层与组织层改进计划 O4 §7.2.2：纠正检测覆盖面从
                # "仅 decision 页面（旧 mark_stale_from_correction 只标注
                # 遗留 EntityStore 的 status）"扩展到"任意页面类型"——只要
                # 这条被纠正的实体已经镜像进 wiki（load_entity_map 能解析
                # 出对应 page_id），就用统一入口 mark_page_state() 补一次
                # knowledge_state=superseded 标记，与旧的 EntityStore 标注
                # 并行存在，互不冲突。镜像未开启（wiki_paths=None）或页面
                # 不存在时静默跳过，不影响上面已经完成的旧路径标注。
                if self._wiki_paths is not None:
                    try:
                        from mini_agent.wiki.migration import load_entity_map
                        from mini_agent.wiki.lifecycle import mark_page_state

                        page_id = load_entity_map(self._wiki_paths).get(entity_id)
                        if page_id:
                            mark_page_state(
                                self._wiki_paths, page_id,
                                confidence="superseded",
                                reason=reason,
                                validated_by="correction_check",
                            )
                    except Exception:
                        pass
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

    # ── 巩固循环：批量巩固 ────────────────────────────────────────────────

    def consolidate(
        self,
        store: "MemoryStore",
        llm_call: Optional[Callable[[str], str]] = None,
        min_cluster_size: int = 5,
        summary_threshold: int = 3,
        merge_threshold: float = 0.6,
        entity_similarity_threshold: float = 0.82,
        wiki_dedup: bool = True,
        wiki_embed_call: Optional["EmbedCall"] = None,
    ) -> dict:
        """
        返回本次巩固的统计，供 /evolve consolidate 展示。顺序：
          1. 分类树生长（未分类候选聚类出新节点）
          2. 分类树合并（改进2：语义重合的节点收敛）
          3. 实体摘要批量重写（含改进1冲突检测）
          4. 实体巩固：去噪 + 近重复合并（改进3）
          5. wiki 镜像（wiki式知识库重构计划阶段二）：
             a. 把本轮重写过摘要的实体，把新摘要作为一条"历史沿革"追加
                镜像进 wiki
             b. wiki_dedup=True（默认）时，在追加前先判断是否已有语义相近
                的既有页面：默认方案是规则打分（tag 重合度 + 关键词
                Jaccard）+ 对不确定区间的候选复用本方法已有的 llm_call
                做一次 YES/NO 确认，不需要额外配置、不依赖 embedding；
                命中则把镜像并入该页面而不是各自新建，替代
                entity_index.py 里基于 difflib 字符串相似度的近重复
                判断（重构计划问题 7）。
                若显式传入 wiki_embed_call，则改用 embedding 余弦相似度
                代替规则+LLM 方案（两者互斥，wiki_embed_call 优先）。
                wiki_dedup=False 时完全跳过判重，每个到期实体各自镜像/
                追加到自己的页面。
          6. wiki 索引重建（wiki式知识库重构计划阶段三）：步骤5产生了任何
             wiki 写入（新建/追加）时，触发一次 wiki/indexer.py 的增量
             重建，刷新 _index/ 下的 graph.json / tags.json /
             backlinks.json / search_index.json，让 wiki_search() 和
             /wiki 命令能看到最新状态，不需要人工单独跑重建。
          7. 专题页生成（wiki式知识库重构计划阶段四 + 改进计划 P3 + O3）：
             候选来自两条并存路径——a) 某个 tag 下页面数与 frontmatter 强
             链接密度都达标（规则）；b) 不依赖 embedding、直接用同一个
             llm_call 对规则路径没覆盖到的页面做一次语义聚类
             （wiki/topics.py::find_topic_candidates_llm_cluster，默认
             开启，可通过 consolidate_topics(use_llm_clustering=False)
             关闭）。两个候选池按页面重合度去重后统一生成综合叙事
             topics/*.md（wiki/topics.py::consolidate_topics）。只在传入
             llm_call 时生效——没有 llm_call 时两条路径都没有能力生成综合
             叙事正文，直接跳过。
             生成新候选簇之前，每隔
             `reconsolidation_interval_runs`（默认 5）次运行还会先做一遍
             已有 topic 页面的"再巩固"扫描（wiki 提取层与组织层改进计划
             O3）：把与已有 topic 关联 tag 集合重合度达标的新页面直接并入
             该 topic 正文（追加"新增关联" section + 补充 frontmatter
             `absorbs` 链接），而不是任其继续静态失真、或再凑一次聚类
             阈值生成内容重叠的新专题页。事件记录进
             wiki/_index/topics_reconsolidation_log.jsonl，供后续校准
             扫描频率与重合度阈值。
          7b. 转正评估每日快照（wiki式知识库改进计划 P4）：记录当天的
             source_kind 目标占比与校验错误数，供 `/wiki promotion` 命令
             累积判断"wiki 转正为主索引"的三项标准是否达成，仅观测记录，
             不触发任何索引路径切换。
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

        # 5. wiki 镜像（wiki式知识库重构计划阶段二）
        wiki_mirrored, wiki_dedup_merged = self._consolidate_wiki_mirror(
            due_entities,
            wiki_dedup=wiki_dedup,
            llm_call=llm_call,
            wiki_embed_call=wiki_embed_call,
        )

        # 5b. 世界模型候选批量落盘（wiki 式知识库改进计划 P1）：消费 compact
        # 阶段（history/compression.py::LLMSummaryStrategy）攒下的
        # entities[]/facts[] pending 队列——这是解决"wiki 内容只有错误信息"
        # 问题的核心补充链路，与步骤5（实体镜像）并列但来源不同：步骤5来自
        # 纠正/反思等错题本事件，这里来自对话中正常提到的世界知识。
        world_entities_created = 0
        world_facts_merged = 0
        if self._wiki_paths is not None:
            try:
                from mini_agent.wiki.world_writer import consolidate_pending as world_consolidate_pending

                world_report = world_consolidate_pending(self._wiki_paths, llm_call=llm_call)
                world_entities_created = sum(
                    1 for a in world_report.actions if a.kind in ("entity_created", "entity_updated")
                )
                world_facts_merged = sum(
                    1 for a in world_report.actions if a.kind in ("fact_merged", "fact_fallback")
                )
            except Exception:
                pass

        # 6. wiki 索引重建（wiki式知识库重构计划阶段三）
        wiki_index_rebuilt = False
        wiki_pages_indexed = 0
        if self._wiki_paths is not None and (
            wiki_mirrored or wiki_dedup_merged or world_entities_created or world_facts_merged
        ):
            try:
                from mini_agent.wiki.indexer import build_index

                idx_result = build_index(self._wiki_paths, incremental=True)
                wiki_index_rebuilt = True
                wiki_pages_indexed = len(idx_result.pages)
            except Exception:
                # 索引重建失败不应该让巩固循环本身报错——下次巩固循环或
                # 手动 /wiki rebuild 都能重试。
                pass

        # 7. 专题页生成（wiki式知识库重构计划阶段四）
        topics_generated: list[str] = []
        if self._wiki_paths is not None and llm_call is not None:
            try:
                from mini_agent.wiki.topics import consolidate_topics

                topics_generated = consolidate_topics(self._wiki_paths, llm_call)
            except Exception:
                topics_generated = []

        # 7b. wiki 转正评估每日快照（wiki 式知识库改进计划 P4）：记录当天的
        # source_kind 目标占比与校验错误数，供 /wiki promotion 命令累积判断
        # "转正"三项标准是否达成。同一天只记一次（record_daily_snapshot 内部
        # 幂等），复用步骤 6 已经算出的 idx_result.validation（没跑到步骤 6
        # 时——即本轮巩固循环没有任何 wiki 写入——传 None 让函数自己算一遍）。
        if self._wiki_paths is not None:
            try:
                from mini_agent.wiki.promotion import record_daily_snapshot

                record_daily_snapshot(
                    self._wiki_paths,
                    validation=idx_result.validation if wiki_index_rebuilt else None,
                )
            except Exception:
                pass

        return {
            "new_categories": len(new_nodes),
            "category_merges": len(merges),
            "remaining_unclassified": len(remaining),
            "entities_summarized": rewritten,
            "entities_deprecated": entity_stats.get("deprecated", 0),
            "entities_merged": entity_stats.get("merged", 0),
            "wiki_mirrored": wiki_mirrored,
            "wiki_dedup_merged": wiki_dedup_merged,
            "world_entities_created": world_entities_created,
            "world_facts_merged": world_facts_merged,
            "wiki_index_rebuilt": wiki_index_rebuilt,
            "wiki_pages_indexed": wiki_pages_indexed,
            "wiki_topics_generated": topics_generated,
        }

    def _consolidate_wiki_mirror(
        self,
        due_entities: list,
        wiki_dedup: bool = True,
        llm_call: Optional[Callable[[str], str]] = None,
        wiki_embed_call: Optional["EmbedCall"] = None,
    ) -> tuple[int, int]:
        """consolidate() 步骤 5：把本轮重写过摘要的实体镜像进 wiki。

        默认判重方案是规则打分 + llm_call 兜底确认（wiki/dedup.py::
        find_similar_page_rules），不需要 embedding；显式传入
        wiki_embed_call 时改用 embedding 余弦相似度。wiki_dedup=False 时
        跳过判重，直接逐个镜像。

        返回 (mirrored_count, dedup_merged_count)。任何环节失败都吞掉异常、
        返回已完成的部分统计——wiki 镜像是巩固循环里"锦上添花"的一步，
        不应该因为它失败而让分类/实体巩固的主统计也报错。
        """
        if self._wiki_paths is None or not due_entities:
            return (0, 0)
        try:
            from mini_agent.wiki.dedup import embed_pages, find_similar_page
            from mini_agent.wiki.indexer import discover_pages
            from mini_agent.wiki.migration import mirror_entity
            from mini_agent.wiki.parser import parse_page
            from mini_agent.wiki.writer import append_section
        except ImportError:
            return (0, 0)

        mirrored = 0
        dedup_merged = 0

        existing_pages: list = []
        page_embeddings: dict = {}
        if wiki_dedup:
            try:
                existing_pages = [parse_page(p) for p in discover_pages(self._wiki_paths)]
                if wiki_embed_call is not None:
                    page_embeddings = embed_pages(existing_pages, wiki_embed_call)
            except Exception:
                existing_pages, page_embeddings = [], {}

        for entity in due_entities:
            try:
                if wiki_dedup and existing_pages:
                    candidate_text = entity.summary or entity.name
                    match = find_similar_page(
                        candidate_text,
                        [entity.entity_type],
                        existing_pages,
                        llm_call=llm_call,
                        embed_call=wiki_embed_call,
                        page_embeddings=page_embeddings,
                    )
                    if match is not None:
                        # 已有语义相近的页面：把这条更新并入该页面的历史
                        # 沿革，而不是各自维护成两篇割裂的页面。默认路径
                        # 靠"规则打分 + 不确定时问一次 LLM"判定，不依赖
                        # embedding（替代 difflib 字符串相似度判重，见
                        # 重构计划问题 7）。
                        matched_page = next(
                            (p for p in existing_pages if p.id == match.page_id), None
                        )
                        if matched_page is not None:
                            append_section(
                                self._wiki_paths,
                                matched_page,
                                heading="历史沿革",
                                content=f"（合并自 {entity.name}，判定方式：{match.method}，分数 {match.score:.2f}）{candidate_text}",
                            )
                            dedup_merged += 1
                            continue
                mirror_entity(entity, self._wiki_paths)
                mirrored += 1
            except Exception:
                continue

        return (mirrored, dedup_merged)
