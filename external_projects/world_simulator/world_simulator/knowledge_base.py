"""world_simulator/knowledge_base.py — 跨模拟复用的因果知识库（阶段二十）

设计依据：`next_doc/world_simulator_toward_universal_simulator_plan.md`
4.12 节。这是六个"迈向万能模拟器"方向里排第一个、也是唯一"无前置
依赖"的一个：把每次模拟里出现的结构化因果链（`SimState.causal_links`）
沉淀成一个独立于任何 `sim_id` 的全局知识库，供之后新建/推进的模拟
检索复用。

范围克制（对照 4.12 节"范围克制"一段）：
- 不做参考文档完整的 `Cause/Effect/Mechanism/Direction/Strength/
  Delay/Condition/Confidence/Evidence/Context/Valid Range/Version`
  全字段 schema，只做最小字段集（见 `KnowledgeItem`）。
- 不引入向量检索/语义相似度模型，"是否相似"用简单的关键词重叠
  （Jaccard 相似度）判断，足够验证"存不存得下、检不检索得到、有没有
  用"这个最基本的闭环。
- 不做知识库的管理界面（增删改查 UI），只验证"自动写入 + 自动检索
  拼入 prompt"这条最基本的闭环。
- 不做完整的 `Candidate → Experimental → Repeated Simulation →
  Validated` 知识晋升流水线，只做 `validated_count`/
  `contradicted_count` 两个计数器。

落盘位置：`data/_knowledge/causal_knowledge.jsonl`，与任何具体
`sim_id` 目录平级——这是它"跨模拟"属性在文件系统上的直接体现。
复用 `store.py` 里已经验证过的 `atomic_write_json`/`atomic_write_jsonl`
降级导入方式，保持"没装 mini_agent 环境也能独立跑"的既有约定。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from mini_agent.utils.atomic_write import atomic_write_jsonl
except ImportError:  # 独立运行、未装 mini_agent 时的降级实现，同 store.py

    def atomic_write_jsonl(path: Path, records: list) -> None:  # type: ignore[misc]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
            + ("\n" if records else ""),
            encoding="utf-8",
        )


_VALID_CONFIDENCE = ("confirmed", "supported", "hypothesis", "speculative")

# 相似度判定的阈值：两条知识的关键词集合 Jaccard 相似度达到这个比例，
# 就认为是"同一条因果关系反复出现"，走计数递增而不是新建条目。取值
# 偏保守（0.6）——因果关系的 cause/effect 通常就是一两个短语，太低的
# 阈值容易把明显不同的因果关系错误合并。
_SIMILARITY_THRESHOLD = 0.6

# 用于关键词切分的正则：连续的中日韩文字或字母数字串。中文没有天然的
# 词边界，这里不引入分词库（jieba 等），退化为"每个汉字单独当一个
# token，连续的字母数字当一个 token"——足够支撑关键词重叠这种粗粒度的
# 相似度判断，代价是无法识别"合成词"级别的语义相似，符合 4.12 节
# "不引入语义相似度模型"的范围克制。
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")

# 停用词：对相似度判断没有区分力的高频虚词，过滤掉避免"因为都有'的'
# 字就被判定为相似"这种噪音。
_STOPWORDS = {
    "的", "了", "在", "是", "和", "与", "对", "被", "把", "又",
    "也", "都", "而", "从", "到", "为", "有", "会", "更", "很",
}


def _tokenize(text: str) -> set:
    if not text:
        return set()
    tokens = _TOKEN_RE.findall(text)
    return {t for t in tokens if t not in _STOPWORDS}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class KnowledgeItem:
    """一条最小可行版本的因果知识条目（4.12 节 1. 的字段集）。"""

    id: str
    cause: str
    effect: str
    mechanism: str = ""
    confidence: str = "hypothesis"  # confirmed / supported / hypothesis / speculative
    source_sim_id: str = ""
    source_template: str = ""
    created_at: str = ""
    validated_count: int = 0
    contradicted_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "cause": self.cause,
            "effect": self.effect,
            "mechanism": self.mechanism,
            "confidence": self.confidence,
            "source_sim_id": self.source_sim_id,
            "source_template": self.source_template,
            "created_at": self.created_at,
            "validated_count": self.validated_count,
            "contradicted_count": self.contradicted_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KnowledgeItem":
        confidence = str(data.get("confidence") or "hypothesis")
        if confidence not in _VALID_CONFIDENCE:
            confidence = "hypothesis"
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            cause=str(data.get("cause") or ""),
            effect=str(data.get("effect") or ""),
            mechanism=str(data.get("mechanism") or ""),
            confidence=confidence,
            source_sim_id=str(data.get("source_sim_id") or ""),
            source_template=str(data.get("source_template") or ""),
            created_at=str(data.get("created_at") or ""),
            validated_count=int(data.get("validated_count") or 0),
            contradicted_count=int(data.get("contradicted_count") or 0),
        )

    def _keyword_set(self) -> set:
        return _tokenize(self.cause) | _tokenize(self.effect) | _tokenize(self.mechanism)


# ── 落盘路径与读写 ───────────────────────────────────────────────────


def knowledge_path(data_dir: Path) -> Path:
    return Path(data_dir) / "_knowledge" / "causal_knowledge.jsonl"


def load_all(data_dir: Path) -> List[KnowledgeItem]:
    path = knowledge_path(data_dir)
    if not path.exists():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(KnowledgeItem.from_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue  # 单行损坏不应该让整个知识库不可用，跳过即可
    return items


def _save_all(data_dir: Path, items: List[KnowledgeItem]) -> None:
    """整体重写落盘（阶段二十七评估后**刻意保留**，不改成追加写——见
    `next_doc/world_simulator_universal_simulator_gap_analysis_and_
    roadmap_v2_plan.md` 4.18 节"范围说明"）：`record_causal_links()`
    每次写入都可能原地更新一条*已有*知识条目（Jaccard 相似度匹配到
    重复因果关系时合并计数），`record_contradiction()` 同样是原地
    更新，这和 `state_history.jsonl`（每条记录只在生成时写一次、
    之后永不修改）的语义完全不同——"可原地更新既有记录"的存储天然
    需要"读全部→改→整体写回"，纯追加写只适合`只增不改`的数据，
    勉强套用反而需要额外的 compaction 步骤才能让"更新"生效，得不
    偿失。数据量大了之后的优化方向应该是"定期压缩历史 jsonl"或换
    真正支持原地更新的存储（比如 sqlite），而不是简单的追加写。
    """
    atomic_write_jsonl(knowledge_path(data_dir), [it.to_dict() for it in items])


# ── 写入：从一次推进产生的 causal_links 提炼知识 ──────────────────────


def record_causal_links(
    data_dir: Path,
    *,
    sim_id: str,
    template: str,
    causal_links: List[Dict[str, Any]],
) -> int:
    """把一次 `advance()` 落盘的 `causal_links` 转成知识条目，追加/合并进
    知识库（4.12 节 2.）。

    纯旁路操作：调用方（`engine.advance()`）应该用 `try/except` 包住
    这次调用，任何异常都不应该影响本次推进——这里内部不主动抛出，但
    仍然保留正常的异常传播（不吞掉调用方需要感知的 bug），"失败不影响
    推进"这条约束由调用方负责兜底，职责边界更清楚。

    Returns:
        本次新增的知识条目数量（被判定为"已有条目的重复出现"、只做了
        `validated_count` 递增的不计入，方便调用方/测试观察效果）。
    """
    if not causal_links:
        return 0

    existing = load_all(data_dir)
    added = 0

    for link in causal_links:
        if not isinstance(link, dict):
            continue
        cause = str(link.get("driver") or "").strip()
        effect = str(link.get("effect") or "").strip()
        if not cause or not effect:
            continue  # 因果链条目本身残缺，不构成一条可复用的知识
        affected = link.get("affected_fields") or []
        mechanism = ""
        if isinstance(affected, list) and affected:
            mechanism = "涉及字段：" + "、".join(str(f) for f in affected)

        candidate_keywords = _tokenize(cause) | _tokenize(effect)
        match = None
        for item in existing:
            if _jaccard(candidate_keywords, item._keyword_set()) >= _SIMILARITY_THRESHOLD:
                match = item
                break

        if match is not None:
            match.validated_count += 1
            continue

        new_item = KnowledgeItem(
            id=uuid.uuid4().hex[:12],
            cause=cause,
            effect=effect,
            mechanism=mechanism,
            confidence="hypothesis",
            source_sim_id=sim_id,
            source_template=template,
            created_at=_now_iso(),
        )
        existing.append(new_item)
        added += 1

    _save_all(data_dir, existing)
    return added


def record_contradiction(data_dir: Path, item_id: str) -> bool:
    """把某条知识的 `contradicted_count` 加一（供阶段二十四 Reality
    Loop 完整版调用）。本阶段（二十）暂无调用方，先提供最小接口，
    避免阶段二十四实现时需要回头改这个模块的存储格式。

    Returns:
        是否找到了对应 id 的条目并完成更新；找不到返回 False，不抛出。
    """
    items = load_all(data_dir)
    for item in items:
        if item.id == item_id:
            item.contradicted_count += 1
            _save_all(data_dir, items)
            return True
    return False


def update_confidence_from_reality_check(
    data_dir: Path, causal_links: List[Dict[str, Any]]
) -> List[str]:
    """`reality_check.record_and_apply()` 在 `verdict == "diverged"` 时
    调用（阶段二十四，4.16 节 3.，"现实反馈 → 修正因果知识库"的最小
    闭环）：对某一步的 `causal_links`（通常是对应 `SimState.
    causal_links`，由调用方带入）逐条尝试匹配知识库里已有的条目——
    匹配方式与 `record_causal_links()` 写入时判断"是否是同一条因果
    关系"完全一致（cause/effect 关键词 Jaccard 相似度 ≥
    `_SIMILARITY_THRESHOLD`），命中即调用 `record_contradiction()`
    把该条目的 `contradicted_count` 加一。

    不做"预测对了就加 `validated_count`"的对称逻辑——那已经由
    `record_causal_links()` 的跨模拟重复出现机制负责，这里重复加会
    造成双重计数（见 `reality_check.record_and_apply()` docstring）。

    Returns:
        本次实际被标记为"证伪"的知识条目 id 列表（可能为空——知识库
        里本来就没有匹配得上的条目，或者 `causal_links` 本身残缺）。
    """
    if not causal_links:
        return []
    existing = load_all(data_dir)
    if not existing:
        return []
    contradicted_ids: List[str] = []
    for link in causal_links:
        if not isinstance(link, dict):
            continue
        cause = str(link.get("driver") or "").strip()
        effect = str(link.get("effect") or "").strip()
        if not cause or not effect:
            continue
        candidate_keywords = _tokenize(cause) | _tokenize(effect)
        for candidate in existing:
            if _jaccard(candidate_keywords, candidate._keyword_set()) >= _SIMILARITY_THRESHOLD:
                if record_contradiction(data_dir, candidate.id):
                    contradicted_ids.append(candidate.id)
                break
    return contradicted_ids


# ── 检索：拼进 prompt 的"已知相关因果知识" ────────────────────────────


def search(
    data_dir: Path,
    query_text: str,
    *,
    template: str = "",
    limit: int = 5,
) -> List[KnowledgeItem]:
    """按关键词重叠检索最相关的若干条知识（4.12 节 3.）。

    不引入向量检索：`query_text`（通常是用户意图 + 当前状态摘要）与
    每条知识的 cause/effect/mechanism 做关键词 Jaccard 相似度，同模板
    来源的条目额外加一点权重（同类场景的因果模式更可能适用）。按
    `(score, validated_count, -contradicted_count)` 排序，只返回
    score > 0 的条目——找不到相关知识时返回空列表，不勉强凑数。
    """
    items = load_all(data_dir)
    if not items:
        return []
    query_keywords = _tokenize(query_text)
    if not query_keywords:
        return []

    scored = []
    for item in items:
        score = _jaccard(query_keywords, item._keyword_set())
        if score <= 0:
            continue
        if template and item.source_template == template:
            score += 0.1  # 同模板小幅加权，不喧宾夺主
        scored.append((score, item))

    scored.sort(
        key=lambda pair: (pair[0], pair[1].validated_count, -pair[1].contradicted_count),
        reverse=True,
    )
    return [item for _score, item in scored[: max(0, limit)]]


def format_for_prompt(items: List[KnowledgeItem]) -> str:
    """把检索结果渲染成一段可以直接拼进 prompt 的文本。"""
    if not items:
        return "（暂无相关的已知因果知识）"
    lines = []
    for item in items:
        confidence_note = f"置信度：{item.confidence}"
        track_record = (
            f"已被印证 {item.validated_count} 次" if item.validated_count else "尚未被反复印证"
        )
        if item.contradicted_count:
            track_record += f"，曾被证伪 {item.contradicted_count} 次"
        line = f"- {item.cause} → {item.effect}（{confidence_note}，{track_record}）"
        if item.mechanism:
            line += f" {item.mechanism}"
        lines.append(line)
    return "\n".join(lines)


def suggest_for_prompt(
    data_dir: Path,
    query_text: str,
    *,
    template: str = "",
    limit: int = 5,
) -> str:
    """`search()` + `format_for_prompt()` 的组合快捷方式，供
    `spec_generator.py`/`engine.py` 直接调用。"""
    return format_for_prompt(search(data_dir, query_text, template=template, limit=limit))
