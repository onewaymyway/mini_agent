"""
perception/classification.py — 图书馆式分类树（书架结构）

设计目标：不是"关键词→文档"的倒排索引，而是先有一套分类体系（书架），
新记忆先"上架"到某个分类节点，检索时先定位书架、再在架内精细检索。

分类树完全由系统运行时自动归纳生长（不预置人工分类表）：
  1. 冷启动时只有一个根节点 "000 未分类"。
  2. 新记忆生成时，先用当前树的节点关键词做规则匹配（classify_by_rule）。
  3. 规则匹配不中时，允许兜底调用一次 LLM，但 LLM 只被要求"从现有节点里
     选一个最接近的，或明确回答 NONE"——不会凭一次判断就新建分类节点，
     避免树被单条易变的记忆污染。
  4. 真正的"新增分类节点"只在 巩固循环 巡检时批量发生：未分类候选积累到
     一定数量、且彼此关键词高度重合时，才聚类归纳出一个新节点（见
     grow_from_candidates）。这对应图书馆"新学科出现才增设类目"的稳态性。

持久化：classification_tree.json（节点表）+ unclassified_candidates.jsonl
（候选队列，巩固循环 处理后清空/归档）。两者都是可重建的观察性数据，不经
StateRepo，写入沿用项目内其它 W2/W3 模块的 tmp+rename 原子写风格。
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
from mini_agent.time_utils import ts_to_str

ROOT_CODE = "000"
_MIN_RULE_SCORE = 2          # 关键词命中数达到此值才算规则命中
_MIN_CLUSTER_SIZE = 5        # 未分类候选积累到多少条才可能长出新节点
_MAX_CANDIDATES_KEPT = 500   # 候选队列上限，超出淘汰最旧


from mini_agent.utils.atomic_write import atomic_write_json, atomic_write_jsonl

# 保留原有函数名作为别名，避免破坏现有调用
_atomic_write_json = atomic_write_json


def _read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.classification._read_json')
        return default


def _tokenize(text: str) -> list[str]:
    """粗粒度关键词提取：英文单词 + 中文双字/三字 n-gram，与 memory_store 的
    分词逻辑保持一致风格，避免两套不同的分词规则导致召回不一致。"""
    text = (text or "").lower()
    tokens: list[str] = []
    for word in re.findall(r"[a-z0-9_]+", text):
        if len(word) > 1:
            tokens.append(word)
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        for i in range(len(seg) - 1):
            tokens.append(seg[i:i + 2])
        for i in range(len(seg) - 2):
            tokens.append(seg[i:i + 3])
    return tokens


@dataclass
class CategoryNode:
    code: str                                  # 如 "000" / "000.001" / "000.001.002"
    name: str                                   # 人类可读名称
    parent: Optional[str] = None
    keywords: list[str] = field(default_factory=list)   # 用于规则匹配的关键词集合
    see_also: list[str] = field(default_factory=list)    # 参见的其它分类号
    status: str = "active"                      # active | deprecated
    created_at: float = field(default_factory=time.time)
    entry_count: int = 0                        # 冗余计数，供 巩固循环 判断是否该细分
    feedback_score: float = 0.0                 # 检索反馈累积权重（改进4），影响 classify_by_rule 打分
    merged_into: Optional[str] = None           # 改进2：被合并掉的旧节点指向新的规范节点


class ClassificationTree:
    """
    分类树的加载/持久化 + 匹配 + 生长。

    冷启动只有根节点；所有子节点都在运行时由 grow_from_candidates 生成。
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._nodes: dict[str, CategoryNode] = {}
        self._loaded = False

    # ── 加载/持久化 ──────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        data = _read_json(self._path, None)
        if not data:
            self._nodes = {
                ROOT_CODE: CategoryNode(code=ROOT_CODE, name="未分类", parent=None)
            }
            self._save()
            return
        self._nodes = {
            code: CategoryNode(**node) for code, node in data.items()
        }
        if ROOT_CODE not in self._nodes:
            self._nodes[ROOT_CODE] = CategoryNode(code=ROOT_CODE, name="未分类", parent=None)

    def _save(self) -> None:
        data = {code: node.__dict__ for code, node in self._nodes.items()}
        _atomic_write_json(self._path, data)

    def all_nodes(self) -> list[CategoryNode]:
        self._ensure_loaded()
        return list(self._nodes.values())

    def get(self, code: str) -> Optional[CategoryNode]:
        self._ensure_loaded()
        return self._nodes.get(code)

    # ── 第一步：规则匹配（书架定位）───────────────────────────────────────

    def classify_by_rule(self, text: str) -> Optional[str]:
        """用当前树里各节点的关键词做打分匹配，返回命中最高的分类号，
        分数不足 _MIN_RULE_SCORE 则返回 None（交给 LLM 兜底或计入候选队列）。"""
        self._ensure_loaded()
        tokens = set(_tokenize(text))
        if not tokens:
            return None
        best_code, best_score = None, 0.0
        for code, node in self._nodes.items():
            if code == ROOT_CODE or node.status != "active" or not node.keywords:
                continue
            if node.merged_into is not None:
                continue
            raw = sum(1 for kw in node.keywords if kw in tokens)
            if raw == 0:
                continue
            # 改进4：检索反馈会累积调整 feedback_score（[-0.5, 1.0]），
            # 命中次数被反复验证有用的书架优先，被纠正过的书架被压低。
            score = raw * (1.0 + node.feedback_score)
            if score > best_score:
                best_code, best_score = code, score
        if best_score >= _MIN_RULE_SCORE:
            return self.resolve_code(best_code)
        return None

    # ── 第二步：LLM 兜底（只能"入座"已有节点，不能新建）───────────────────

    def classify_by_llm(
        self, text: str, llm_call: Callable[[str], str]
    ) -> Optional[str]:
        """
        调用方传入一个 llm_call(prompt) -> str 的轻量函数（通常是一次低 token
        的分类调用，而非完整对话）。Prompt 要求模型只能从现有节点里选择，
        或回答 NONE——分类树的"新增节点"权力收在 巩固循环 批量生长里，
        单次 LLM 调用不应该有权直接扩张树结构，否则相似说法的多次调用会
        制造大量语义重复的节点。
        """
        self._ensure_loaded()
        candidates = [
            f"{code}: {node.name}"
            for code, node in self._nodes.items()
            if code != ROOT_CODE and node.status == "active" and node.merged_into is None
        ]
        if not candidates:
            return None
        prompt = (
            "以下是一棵知识分类树的现有节点（分类号: 名称），请判断下面这段文本"
            "最应该归入哪个节点。只能从列表中选择，如果都不合适请回答 NONE，"
            "只输出分类号或 NONE，不要输出其它内容。\n\n"
            f"节点列表:\n{chr(10).join(candidates)}\n\n文本: {text}"
        )
        try:
            reply = (llm_call(prompt) or "").strip()
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.classification.ClassificationTree.classify_by_llm')
            return None
        reply = reply.splitlines()[0].strip() if reply else ""
        if reply.upper() == "NONE":
            return None
        return self.resolve_code(reply) if reply in self._nodes else None

    # ── 综合分类入口 ─────────────────────────────────────────────────────

    def classify(
        self, text: str, llm_call: Optional[Callable[[str], str]] = None
    ) -> str:
        """
        规则优先，LLM 兜底，都不中则挂 ROOT_CODE 并记为未分类候选（由调用方
        负责写入 unclassified_candidates.jsonl，本方法只负责返回分类号）。
        """
        code = self.classify_by_rule(text)
        if code is not None:
            self._bump_entry_count(code)
            return code
        if llm_call is not None:
            code = self.classify_by_llm(text, llm_call)
            if code is not None:
                self._bump_entry_count(code)
                return code
        return ROOT_CODE

    def _bump_entry_count(self, code: str) -> None:
        node = self._nodes.get(code)
        if node is not None:
            node.entry_count += 1
            self._save()

    def resolve_code(self, code: str) -> str:
        """跟随 merged_into 链条，把一个可能已被合并掉的旧分类号解析成当前的
        规范分类号（改进2：分类树合并）。链条理论上很短，做个保险的步数上限。"""
        self._ensure_loaded()
        seen = set()
        cur = code
        for _ in range(10):
            node = self._nodes.get(cur)
            if node is None or node.merged_into is None or cur in seen:
                return cur
            seen.add(cur)
            cur = node.merged_into
        return cur

    def record_feedback(self, code: str, useful: bool, delta: float = 0.15) -> None:
        """
        改进4：检索命中质量的自我反馈。useful=True 时说明这次从该书架取出的
        记忆后来被验证有效（比如没有被人纠正），useful=False 说明取出的记忆
        过时/不相关（比如后续被人类纠正）。用简单的累加+衰减而非复杂的贝叶斯
        模型，足够解决"越用越准"这个朴素目标，也方便人工检查 feedback_score
        是否失控。
        """
        self._ensure_loaded()
        code = self.resolve_code(code)
        node = self._nodes.get(code)
        if node is None:
            return
        node.feedback_score += delta if useful else -delta
        node.feedback_score = max(-0.5, min(1.0, node.feedback_score))
        self._save()

    # ── 改进2：分类树合并（只生长不收敛会导致书架越来越碎）─────────────────

    def merge_similar_nodes(
        self, threshold: float = 0.6
    ) -> list[tuple[str, str]]:
        """
        对同一父节点下的活跃节点两两比较关键词集合的 Jaccard 相似度，超过
        threshold 就合并：较早创建的节点作为规范节点保留，较晚的节点标记
        deprecated + merged_into 指向规范节点，关键词/参见关系并入规范节点。

        只在 巩固循环 巡检时调用（不是每次分类都检查），避免频繁合并造成
        分类号语义抖动。返回本次发生的 (旧节点, 新规范节点) 合并列表。
        """
        self._ensure_loaded()
        nodes = [
            n for n in self._nodes.values()
            if n.code != ROOT_CODE and n.status == "active" and n.merged_into is None
        ]
        nodes.sort(key=lambda n: n.created_at)
        merges: list[tuple[str, str]] = []
        merged_codes: set[str] = set()
        for i, a in enumerate(nodes):
            if a.code in merged_codes:
                continue
            set_a = set(a.keywords)
            if not set_a:
                continue
            for b in nodes[i + 1:]:
                if b.code in merged_codes or b.parent != a.parent:
                    continue
                set_b = set(b.keywords)
                if not set_b:
                    continue
                union = set_a | set_b
                jaccard = len(set_a & set_b) / len(union) if union else 0.0
                if jaccard >= threshold:
                    b.status = "deprecated"
                    b.merged_into = a.code
                    a.keywords = list(set_a | set_b)
                    a.entry_count += b.entry_count
                    for sa in b.see_also:
                        if sa not in a.see_also and sa != a.code:
                            a.see_also.append(sa)
                    merged_codes.add(b.code)
                    merges.append((b.code, a.code))
        if merges:
            self._save()
        return merges


    def grow_from_candidates(
        self,
        candidates: list[dict],
        *,
        min_cluster_size: int = _MIN_CLUSTER_SIZE,
        llm_name_fn: Optional[Callable[[list[str], list[str]], str]] = None,
    ) -> tuple[list[CategoryNode], list[dict]]:
        """
        对未分类候选做一次简单的关键词重合聚类，凡是聚出 >= min_cluster_size
        条、且共享关键词数达标的簇，就新增一个分类节点（挂在 ROOT_CODE 下，
        后续可由 巩固循环 后续巡检根据关键词重合度再决定要不要建立父子关系）。

        candidates: [{"text": str, "entry_id": str, "created_at": float}, ...]
        llm_name_fn: 可选，(top_keywords, sample_texts) -> 节点名称；不提供时
                     用 top_keywords 拼接生成一个可读但不那么精致的名称。

        返回 (新建节点列表, 未被聚类、应继续保留在候选队列里的剩余候选)。
        """
        self._ensure_loaded()
        remaining = list(candidates)
        new_nodes: list[CategoryNode] = []

        # 简单聚类：以候选的 top token 作为聚类 key（同一高频词归一簇）
        buckets: dict[str, list[dict]] = {}
        for cand in candidates:
            tokens = _tokenize(cand.get("text", ""))
            if not tokens:
                continue
            # 取出现频率最高的 token 作为该候选的主 key
            freq: dict[str, int] = {}
            for t in tokens:
                freq[t] = freq.get(t, 0) + 1
            top_token = max(freq, key=freq.get)
            buckets.setdefault(top_token, []).append(cand)

        existing_max_child = 0
        for code in self._nodes:
            if code.startswith(ROOT_CODE + ".") and "." not in code[len(ROOT_CODE) + 1:]:
                try:
                    existing_max_child = max(existing_max_child, int(code.split(".")[1]))
                except (ValueError, IndexError):
                    pass

        for top_token, items in buckets.items():
            if len(items) < min_cluster_size:
                continue
            sample_texts = [it.get("text", "") for it in items[:5]]
            all_tokens: list[str] = []
            for it in items:
                all_tokens.extend(_tokenize(it.get("text", "")))
            freq_all: dict[str, int] = {}
            for t in all_tokens:
                freq_all[t] = freq_all.get(t, 0) + 1
            top_keywords = [t for t, _ in sorted(freq_all.items(), key=lambda x: -x[1])[:8]]

            if llm_name_fn is not None:
                try:
                    name = llm_name_fn(top_keywords, sample_texts)
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.perception.classification.ClassificationTree.grow_from_candidates')
                    name = "、".join(top_keywords[:3]) or top_token
            else:
                name = "、".join(top_keywords[:3]) or top_token

            existing_max_child += 1
            new_code = f"{ROOT_CODE}.{existing_max_child:03d}"
            node = CategoryNode(
                code=new_code,
                name=name,
                parent=ROOT_CODE,
                keywords=top_keywords,
                entry_count=len(items),
            )
            self._nodes[new_code] = node
            new_nodes.append(node)

            item_ids = {it.get("entry_id") for it in items}
            remaining = [c for c in remaining if c.get("entry_id") not in item_ids]

        if new_nodes:
            self._save()
        return new_nodes, remaining

    def add_see_also(self, code_a: str, code_b: str) -> None:
        """建立两个分类之间的"参见"关联（双向）。"""
        self._ensure_loaded()
        for a, b in ((code_a, code_b), (code_b, code_a)):
            node = self._nodes.get(a)
            if node is not None and b not in node.see_also:
                node.see_also.append(b)
        self._save()

    def related_codes(self, code: str) -> list[str]:
        """返回某分类号自身 + 其"参见"分类号（用于书架内容不足时的扩展检索）。
        自动解析合并链，且不返回已废弃（deprecated）的旧节点。"""
        self._ensure_loaded()
        code = self.resolve_code(code)
        node = self._nodes.get(code)
        if node is None:
            return [code]
        result = [code]
        for sa in node.see_also:
            sa = self.resolve_code(sa)
            if sa not in result:
                result.append(sa)
        return result


# ── 未分类候选队列（巩固循环 消费）─────────────────────────────────────────

def record_unclassified_candidate(path: Path, text: str, entry_id: str) -> None:
    """把一条规则+LLM都未命中的记忆记为候选，等待 巩固循环 批量聚类生长。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    _now = time.time()
    record = {"text": text, "entry_id": entry_id, "created_at": _now, "created_at_str": ts_to_str(_now)}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_unclassified_candidates(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.perception.classification.load_unclassified_candidates')
                continue
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.classification.load_unclassified_candidates')
        return []
    return records[-_MAX_CANDIDATES_KEPT:]


def save_unclassified_candidates(path: Path, records: list[dict]) -> None:
    """巩固循环 处理完一批后，用剩余候选整体重写文件（原子写）。"""
    atomic_write_jsonl(path, records)
