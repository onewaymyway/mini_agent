"""
history/world_extraction.py — 解析 compact 阶段 LLM 输出的“世界模型”候选
（entities[] / facts[]）

对应《wiki 式知识库改进计划》P1：现有 history/decision_extraction.py 只提炼
“做了什么决定”，perception/library_index.py 的实体镜像入口又几乎只被
纠正/反思/进化失败这类“错题本”事件触发——两条链路都不覆盖“对话里正常
提到的实体/事实”，导致 wiki 内容长期偏科。

本模块与 decision_extraction.py 复用同一次 LLM 输出（同一个 JSON 对象里
新增 entities/facts 两个字段，不产生额外 LLM 调用），只是职责上单独拆出
一个模块解析——保持与 decision_extraction.py 一致的“职责分离、分别测试”
风格。解析失败时返回空列表，不影响 compact 主流程（调用方已有 try/except
双保险，本模块自身也不抛出未捕获异常）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from mini_agent.history.decision_extraction import _extract_json_blob

_VALID_ENTITY_TYPES = (
    "module", "tool", "concept", "person", "project", "external_system",
)
_VALID_CONFIDENCE = ("confirmed", "inferred", "user_stated")


@dataclass
class EntityCandidate:
    """单条从 compact 摘要中提炼出的实体候选（未经落盘匹配处理）。"""

    name: str
    entity_type: str = "concept"
    description: str = ""
    related_entities: list[str] = field(default_factory=list)

    @property
    def is_meaningful(self) -> bool:
        return bool(self.name.strip()) and bool(self.description.strip())

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "entity_type": self.entity_type,
            "description": self.description,
            "related_entities": list(self.related_entities),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EntityCandidate":
        entity_type = str(data.get("entity_type") or "concept").strip().lower()
        if entity_type not in _VALID_ENTITY_TYPES:
            entity_type = "concept"
        return cls(
            name=str(data.get("name") or "").strip(),
            entity_type=entity_type,
            description=str(data.get("description") or "").strip(),
            related_entities=[
                str(e) for e in (data.get("related_entities") or []) if str(e).strip()
            ],
        )


@dataclass
class FactCandidate:
    """单条从 compact 摘要中提炼出的事实候选（未经落盘匹配处理）。"""

    statement: str
    confidence: str = "inferred"
    related_entities: list[str] = field(default_factory=list)

    @property
    def is_meaningful(self) -> bool:
        return bool(self.statement.strip())

    def to_dict(self) -> dict:
        return {
            "statement": self.statement,
            "confidence": self.confidence,
            "related_entities": list(self.related_entities),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FactCandidate":
        confidence = str(data.get("confidence") or "inferred").strip().lower()
        if confidence not in _VALID_CONFIDENCE:
            confidence = "inferred"
        return cls(
            statement=str(data.get("statement") or "").strip(),
            confidence=confidence,
            related_entities=[
                str(e) for e in (data.get("related_entities") or []) if str(e).strip()
            ],
        )


@dataclass
class WorldExtractionResult:
    entities: list[EntityCandidate] = field(default_factory=list)
    facts: list[FactCandidate] = field(default_factory=list)
    # 解析失败时为 True（非 JSON / 不是 compact 阶段的标准输出结构）——
    # 调用方应据此静默跳过，不影响 compact_summary/decisions 已经解析出的结果。
    parse_failed: bool = False


def parse_world_response(raw_text: str) -> WorldExtractionResult:
    """解析 LLM 返回的 compact JSON 里的 `entities[]` / `facts[]` 字段。

    与 decision_extraction.parse_decision_response() 各自独立解析同一段
    raw_text——两个函数职责单一、互不依赖，任一方解析失败不影响另一方。
    """
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return WorldExtractionResult(parse_failed=True)

    blob = _extract_json_blob(raw_text)
    if blob is None:
        return WorldExtractionResult(parse_failed=True)

    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return WorldExtractionResult(parse_failed=True)

    if not isinstance(data, dict):
        return WorldExtractionResult(parse_failed=True)

    entities: list[EntityCandidate] = []
    raw_entities = data.get("entities")
    if isinstance(raw_entities, list):
        for item in raw_entities:
            if not isinstance(item, dict):
                continue
            candidate = EntityCandidate.from_dict(item)
            if candidate.is_meaningful:
                entities.append(candidate)

    facts: list[FactCandidate] = []
    raw_facts = data.get("facts")
    if isinstance(raw_facts, list):
        for item in raw_facts:
            if not isinstance(item, dict):
                continue
            candidate = FactCandidate.from_dict(item)
            if candidate.is_meaningful:
                facts.append(candidate)

    return WorldExtractionResult(entities=entities, facts=facts)


__all__ = [
    "EntityCandidate",
    "FactCandidate",
    "WorldExtractionResult",
    "parse_world_response",
]
