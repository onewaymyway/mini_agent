"""
history/decision_extraction.py — 解析 compact 阶段 LLM 输出的结构化决策候选

对应《决策/取舍知识提炼计划》5.2 节：LLMSummaryStrategy 原本只请求纯文本摘要，
本模块负责把改造后的输出（`{compact_summary, decisions[]}` JSON）解析成
DecisionCandidate 列表，供 wiki/decision_writer.py 落盘。

设计取舍：
  - 只做“解析”，不做“落盘判断”（落盘的匹配/更新/推翻/新建逻辑在
    wiki/decision_writer.py，职责分离，方便分别测试）。
  - 输出格式在实践中经常被 LLM 包一层 ```json 代码块，或者在 JSON 前后
    附带说明文字——parse_decision_response() 做了防御性提取，解析失败时
    返回空 decisions 列表 + 原始文本当作 compact_summary，不让整个 compact
    流程因为一次格式不稳定的 LLM 输出而报错（LLMSummaryStrategy 本身也有
    try/except 兜底，这里是双保险）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_BARE_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class DecisionCandidate:
    """单条从 compact 摘要中提炼出的决策候选（未经落盘匹配处理）。"""

    topic: str
    options_considered: list[str] = field(default_factory=list)
    chosen: str = ""
    rejected_because: dict[str, str] = field(default_factory=dict)
    related_entities: list[str] = field(default_factory=list)

    @property
    def is_meaningful(self) -> bool:
        """过滤掉解析出来但内容空洞的候选（没有 topic 或没有 chosen 方案）。"""
        return bool(self.topic.strip()) and bool(self.chosen.strip())


@dataclass
class DecisionExtractionResult:
    compact_summary: str
    decisions: list[DecisionCandidate] = field(default_factory=list)
    # 解析失败时为 True：compact_summary 退化为原始文本，decisions 为空。
    # 调用方（LLMSummaryStrategy）应据此继续走"纯摘要"路径，不阻断 compact。
    parse_failed: bool = False


def _extract_json_blob(text: str) -> Optional[str]:
    m = _JSON_FENCE_RE.search(text)
    if m:
        return m.group(1)
    m = _BARE_JSON_RE.search(text)
    if m:
        return m.group(0)
    return None


def parse_decision_response(raw_text: str) -> DecisionExtractionResult:
    """解析 LLM 返回的 `{compact_summary, decisions[]}` JSON（可能带 ```json 围栏）。

    解析失败（非 JSON / 缺少 compact_summary 字段）时降级：把整段原始文本当作
    compact_summary，decisions 置空，parse_failed=True。
    """
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return DecisionExtractionResult(compact_summary="", parse_failed=True)

    blob = _extract_json_blob(raw_text)
    if blob is None:
        return DecisionExtractionResult(compact_summary=raw_text, parse_failed=True)

    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return DecisionExtractionResult(compact_summary=raw_text, parse_failed=True)

    if not isinstance(data, dict) or "compact_summary" not in data:
        return DecisionExtractionResult(compact_summary=raw_text, parse_failed=True)

    summary = str(data.get("compact_summary") or "").strip()
    raw_decisions = data.get("decisions")
    decisions: list[DecisionCandidate] = []
    if isinstance(raw_decisions, list):
        for item in raw_decisions:
            if not isinstance(item, dict):
                continue
            rejected_because = item.get("rejected_because") or {}
            if not isinstance(rejected_because, dict):
                rejected_because = {}
            candidate = DecisionCandidate(
                topic=str(item.get("topic") or "").strip(),
                options_considered=[str(o) for o in (item.get("options_considered") or []) if str(o).strip()],
                chosen=str(item.get("chosen") or "").strip(),
                rejected_because={str(k): str(v) for k, v in rejected_because.items()},
                related_entities=[str(e) for e in (item.get("related_entities") or []) if str(e).strip()],
            )
            if candidate.is_meaningful:
                decisions.append(candidate)

    return DecisionExtractionResult(
        compact_summary=summary or raw_text,
        decisions=decisions,
        parse_failed=not summary,
    )


__all__ = ["DecisionCandidate", "DecisionExtractionResult", "parse_decision_response"]
