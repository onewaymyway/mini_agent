"""
perception/entity_index.py — 实体目录（图书馆"著者目录"的对应物）

分类目录回答"这类问题有哪些"，实体目录回答"关于这个具体模块/bug模式/概念，
历史上都有哪些记忆，当前的共识是什么"。一条记忆可以挂在 0 个或多个实体下。

摘要滚动更新采用"攒够证据才重写"策略：每条新记忆挂到某实体时只追加
related_entry_ids、递增 pending_evidence_count，不立即重写 summary；
真正的 summary 重写只在 巩固循环 巡检时、pending_evidence_count 达到阈值
的实体上批量发生（见 rewrite_due_summaries），避免同一实体被频繁触发的
lesson 反复重写摘要造成的抖动和不必要的 LLM 调用成本。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

_SUMMARY_REWRITE_THRESHOLD = 3   # pending_evidence_count 达到此值才重写摘要
_MAX_RELATED_ENTRIES = 50        # 单实体挂载的记忆上限，超出淘汰最旧


def _atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)


def _read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


_NAME_TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_./]{2,}\.py|[a-zA-Z_][a-zA-Z0-9_]{3,}")


def guess_entity_names(text: str) -> list[str]:
    """
    从文本里粗略猜测实体名候选：优先识别形如 `xxx.py` 的模块名，
    其次是长度>=4 的英文标识符（类名/函数名/工具名常见形态）。
    这是启发式规则，不追求精确，只作为"挂载到哪个实体"的候选来源；
    未命中已有实体时不会盲目新建，由 link_entry 决定是否新建。
    """
    if not text:
        return []
    found = _NAME_TOKEN_RE.findall(text)
    # 去重并保序
    seen: set[str] = set()
    result = []
    for name in found:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            result.append(name)
    return result[:5]


_NEGATION_MARKERS = (
    "不再", "已修复", "已解决", "不需要", "无需", "已废弃", "过时", "已经不",
    "no longer", "fixed", "resolved", "deprecated", "not needed", "outdated",
)


def _looks_contradictory(old_summary: str, new_texts: list[str]) -> bool:
    """
    极简启发式冲突判定（改进1，无 LLM 时的兜底）：旧摘要本身不含"否定/已解决"
    类标记，但新证据里出现了，粗略判断为"新证据在推翻旧结论"。不追求精确，
    只用来决定要不要在摘要里显式标注"⚠"，误判的代价是摘要多一句提示，
    比误判为"没有冲突、悄悄丢弃矛盾信息"更安全。
    """
    old_has = any(m in old_summary for m in _NEGATION_MARKERS)
    new_has = any(m in t for t in new_texts for m in _NEGATION_MARKERS)
    return new_has and not old_has


@dataclass
class Entity:
    entity_id: str
    name: str
    entity_type: str = "concept"       # module | bug_pattern | tool | decision | concept
    aliases: list[str] = field(default_factory=list)
    category: str = ""                 # 关联的分类号（classification.py）
    related_entry_ids: list[str] = field(default_factory=list)
    summary: str = ""                  # 当前"共识"描述，滚动更新
    status: str = "active"             # active | deprecated | superseded
    pending_evidence_count: int = 0
    first_seen: float = field(default_factory=time.time)
    last_summary_update: float = 0.0
    superseded_notes: list[str] = field(default_factory=list)  # 改进1：被推翻的历史结论记录


class EntityStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._entities: dict[str, Entity] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        data = _read_json(self._path, {})
        self._entities = {eid: Entity(**e) for eid, e in data.items()}

    def _save(self) -> None:
        data = {eid: e.__dict__ for eid, e in self._entities.items()}
        _atomic_write_json(self._path, data)

    def all_entities(self) -> list[Entity]:
        self._ensure_loaded()
        return list(self._entities.values())

    def get(self, entity_id: str) -> Optional[Entity]:
        self._ensure_loaded()
        return self._entities.get(entity_id)

    def find_by_name(self, name: str) -> Optional[Entity]:
        self._ensure_loaded()
        key = name.lower()
        for e in self._entities.values():
            if e.name.lower() == key or key in [a.lower() for a in e.aliases]:
                return e
        return None

    # ── 挂载：新记忆归入已有实体，或新建实体卡片 ─────────────────────────

    def link_entry(
        self, entry_id: str, text: str, category: str = "", entity_type: str = "concept"
    ) -> list[str]:
        """
        把一条记忆挂到候选实体上。候选实体名来自 guess_entity_names(text)；
        命中已有实体（含别名）则挂载并计入待重写证据数；未命中任何已有实体
        才新建实体卡片——新实体的第一条记忆同时作为其初始 summary 的种子
        （非常粗糙，等待够 3 条证据后由 巩固循环 正式重写为更精炼的摘要）。

        返回本条记忆最终关联到的 entity_id 列表。
        """
        self._ensure_loaded()
        names = guess_entity_names(text)
        linked_ids: list[str] = []
        for name in names:
            entity = self.find_by_name(name)
            if entity is None:
                import uuid
                entity = Entity(
                    entity_id=uuid.uuid4().hex[:10],
                    name=name,
                    entity_type=entity_type,
                    category=category,
                    summary=text[:200],
                )
                self._entities[entity.entity_id] = entity
            entity.related_entry_ids.append(entry_id)
            if len(entity.related_entry_ids) > _MAX_RELATED_ENTRIES:
                entity.related_entry_ids = entity.related_entry_ids[-_MAX_RELATED_ENTRIES:]
            entity.pending_evidence_count += 1
            linked_ids.append(entity.entity_id)
        if linked_ids:
            self._save()
        return linked_ids

    # ── 巩固循环：批量重写摘要 ─────────────────────────────────────────────

    def due_for_summary_rewrite(
        self, threshold: int = _SUMMARY_REWRITE_THRESHOLD
    ) -> list[Entity]:
        self._ensure_loaded()
        return [e for e in self._entities.values() if e.pending_evidence_count >= threshold]

    def rewrite_summary(
        self,
        entity: Entity,
        entry_texts: list[str],
        llm_call: Optional[Callable[[str], str]] = None,
    ) -> None:
        """
        用累积的证据文本重写实体摘要。有 llm_call 时用一次轻量摘要调用，
        并显式要求模型标注新旧证据之间的矛盾（改进1：冲突检测与知识版本化）；
        没有 llm_call 时退化为"取最近 3 条证据拼接"+ 简单否定词启发式，
        保证无 LLM 依赖也能跑，只是不如 LLM 版本精确。
        """
        self._ensure_loaded()
        old_summary = entity.summary
        if llm_call is not None:
            prompt = (
                f"以下是关于「{entity.name}」的历史记录片段和当前已有的共识摘要。"
                f"请用 2-3 句话给出新的共识摘要：以更晚近的记录为准；如果新记录"
                f"与旧摘要存在矛盾（比如旧摘要说某问题存在，新记录说已经修复/"
                f"不再需要），必须在摘要开头加上「⚠矛盾已更新：」并简述旧结论"
                f"被推翻的原因，不要把矛盾的新旧结论并列堆砌。\n\n"
                f"当前摘要: {old_summary or '（无）'}\n\n"
                f"新记录:\n" + "\n---\n".join(entry_texts[-10:])
            )
            try:
                new_summary = (llm_call(prompt) or "").strip()
            except Exception:
                new_summary = ""
        else:
            new_summary = ""
            if _looks_contradictory(old_summary, entry_texts):
                new_summary = "⚠矛盾待复核：" + " | ".join(t[:120] for t in entry_texts[-3:])
        if not new_summary:
            new_summary = " | ".join(t[:120] for t in entry_texts[-3:])

        if new_summary.startswith("⚠矛盾") and old_summary:
            entity.superseded_notes.append(old_summary[:200])
            entity.superseded_notes = entity.superseded_notes[-5:]

        entity.summary = new_summary
        entity.pending_evidence_count = 0
        entity.last_summary_update = time.time()
        self._save()

    def mark_superseded(self, entity_id: str, reason: str = "") -> None:
        """标记实体的当前共识已被更新的证据推翻（供冲突检测/巩固流程调用）。"""
        self._ensure_loaded()
        entity = self._entities.get(entity_id)
        if entity is not None:
            entity.status = "superseded"
            if reason:
                entity.superseded_notes.append(reason[:200])
                entity.superseded_notes = entity.superseded_notes[-5:]
            self._save()

    # ── 改进3：实体巩固（去噪 + 近重复合并）─────────────────────────────

    def consolidate_entities(
        self,
        llm_call: Optional[Callable[[str], str]] = None,
        min_name_len: int = 4,
        similarity_threshold: float = 0.82,
    ) -> dict:
        """
        巩固循环 巡检时调用，做两件事：
          1. 去噪：正则抽取难免抓到噪音（常见英文单词、过短标识符），把明显
             不像模块名/工具名/概念名的实体标记 deprecated，避免污染实体目录。
          2. 近重复合并：名字高度相似的实体（比如 "daemon" 和 "daemon.py"，
             或大小写/下划线差异）大概率是同一个实体，合并成一个规范实体，
             其余的作为 alias 挂进去，related_entry_ids 也合并。

        都只在 巩固循环 批量触发，不在写入侧实时做，原因和分类树合并一样：
        避免单次判断的抖动污染实体目录。
        """
        self._ensure_loaded()
        deprecated = 0
        merged = 0

        # 1. 去噪：过短、或是常见停用词/泛化词的实体标记废弃
        _stoplist = {
            "self", "this", "that", "true", "false", "none", "null", "return",
            "import", "class", "print", "value", "error", "config", "result",
        }
        for e in list(self._entities.values()):
            if e.status != "active":
                continue
            bare = e.name.replace(".py", "").strip().lower()
            if len(bare) < min_name_len or bare in _stoplist:
                e.status = "deprecated"
                deprecated += 1

        # 2. 近重复合并：对剩余 active 实体两两比较名字相似度
        import difflib
        actives = [e for e in self._entities.values() if e.status == "active"]
        actives.sort(key=lambda e: e.first_seen)
        merged_ids: set[str] = set()
        for i, a in enumerate(actives):
            if a.entity_id in merged_ids:
                continue
            for b in actives[i + 1:]:
                if b.entity_id in merged_ids:
                    continue
                name_a = a.name.replace(".py", "").lower()
                name_b = b.name.replace(".py", "").lower()
                ratio = difflib.SequenceMatcher(None, name_a, name_b).ratio()
                is_dup = ratio >= similarity_threshold
                if not is_dup and llm_call is not None and ratio >= 0.5:
                    # 相似度中等、规则判断不确定时才兜底问一次 LLM，
                    # 避免对所有实体对都调用 LLM（组合数会爆炸）。
                    try:
                        reply = (llm_call(
                            f"这两个名字是否指同一个实体（模块/工具/概念）？"
                            f"只回答 YES 或 NO。\nA: {a.name}\nB: {b.name}"
                        ) or "").strip().upper()
                        is_dup = reply.startswith("Y")
                    except Exception:
                        is_dup = False
                if is_dup:
                    if b.name.lower() not in [x.lower() for x in a.aliases]:
                        a.aliases.append(b.name)
                    for eid in b.related_entry_ids:
                        if eid not in a.related_entry_ids:
                            a.related_entry_ids.append(eid)
                    a.pending_evidence_count += b.pending_evidence_count
                    b.status = "deprecated"
                    merged_ids.add(b.entity_id)
                    merged += 1

        if deprecated or merged:
            self._save()
        return {"deprecated": deprecated, "merged": merged}
