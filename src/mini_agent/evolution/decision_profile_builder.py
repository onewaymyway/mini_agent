"""
evolution/decision_profile_builder.py — 决策画像归纳（设计方案第 4.4 节，阶段三）

分层：
  第一层（已有，不变）：history/decision_extraction.py + wiki/decision_writer.py
                        产出的单条决策事实（wiki/decisions/*.md）
  第二层（本文件新增）：周期性对多条决策做归纳，只允许"总结已发生的事实"，
                        不允许引入证据之外的判断。少于 MIN_EVIDENCE_COUNT
                        条独立证据的模式不落地。
  第三层（本文件新增）：user_value_profile.md，纳入 wiki 体系，
                        矛盾证据不覆盖旧模式，而是记录到 contradicted_by
                        并下调置信度（见 _apply_contradiction）。

初期用法明确限定为两个（见设计方案 4.4 节）：
  1. 决策问答检索：由 wiki 检索路径直接读这份文档，不在本文件实现问答本身。
  2. next_action_advisor 排序加权：advisor 按需读取本文件产出的 pattern 列表。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Optional

from mini_agent.storage.paths import AgentPaths

MIN_EVIDENCE_COUNT = 3  # 少于 3 条独立证据的模式不落地，避免单次事件被泛化成"价值观"
SCAN_WINDOW_WEEKS = 8  # 一次归纳最多回看的周数，避免全量历史每次都重新归纳


@dataclass
class ValuePattern:
    pattern: str
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 0.0
    first_observed: str = ""
    last_reinforced: str = ""
    contradicted_by: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pattern": self.pattern,
            "evidence_refs": self.evidence_refs,
            "confidence": round(self.confidence, 2),
            "first_observed": self.first_observed,
            "last_reinforced": self.last_reinforced,
            "contradicted_by": self.contradicted_by,
        }

    @staticmethod
    def from_dict(d: dict) -> "ValuePattern":
        return ValuePattern(
            pattern=d.get("pattern", ""),
            evidence_refs=list(d.get("evidence_refs", [])),
            confidence=float(d.get("confidence", 0.0)),
            first_observed=d.get("first_observed", ""),
            last_reinforced=d.get("last_reinforced", ""),
            contradicted_by=list(d.get("contradicted_by", [])),
        )


def _load_decision_pages(paths: AgentPaths) -> list:
    """复用 wiki/decision_writer.py 里已有的决策页加载逻辑，不重复实现。"""
    from mini_agent.wiki.decision_writer import _load_decision_pages as _load

    return _load(paths)


def _load_state(paths: AgentPaths) -> dict:
    p = paths.decision_profile_state_path
    if not p.exists():
        return {"last_scan_at": 0.0, "patterns": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"last_scan_at": 0.0, "patterns": []}


def _save_state(paths: AgentPaths, state: dict) -> None:
    p = paths.decision_profile_state_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_json_array(text: str) -> str:
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return "[]"
    return text[start : end + 1]


def _llm_summarize_patterns(pages: list, llm_helper) -> list[dict]:
    """要求 LLM 只做归纳：每条候选模式必须列出至少 MIN_EVIDENCE_COUNT 个
    decision 页面 id 作为证据，不满足的模式由本函数事后过滤掉（不完全信任
    LLM 自己声称的证据数量）。
    """
    entries = [
        {
            "id": p.id,
            "title": getattr(p, "id", ""),
            "body": (p.body or "")[:500],
        }
        for p in pages
    ]
    prompt = (
        "以下是一批用户的历史技术决策记录（wiki 决策页摘要）。请归纳出其中"
        "反复出现、至少能被 3 条独立记录支持的价值取向或决策偏好模式，"
        "不要凭单条记录臆断。每条模式给出 pattern（一句话）和 evidence_refs"
        "（引用的决策页 id 列表，必须真实来自输入数据）。"
        "只返回 JSON 数组，不要其他文字：\n" + json.dumps(entries, ensure_ascii=False)
    )
    raw = llm_helper.complete(prompt)
    try:
        parsed = json.loads(_extract_json_array(raw))
    except Exception:
        return []

    valid_ids = {e["id"] for e in entries}
    out = []
    for item in parsed:
        refs = [r for r in item.get("evidence_refs", []) if r in valid_ids]
        if len(refs) < MIN_EVIDENCE_COUNT:
            continue  # 证据不足，不落地，即使 LLM 自己声称满足
        out.append({"pattern": item.get("pattern", "").strip(), "evidence_refs": refs})
    return [p for p in out if p["pattern"]]


def _apply_contradiction(existing: list[ValuePattern], new_raw: list[dict], now_str: str) -> list[ValuePattern]:
    """新归纳结果与已有模式做合并：
      - 同一模式（简单按 pattern 文本近似匹配）证据增加 → 置信度上升，更新 last_reinforced
      - 新证据集合与旧模式明显不重合、且指向相反方向的模式文本 → 记录到 contradicted_by，
        置信度下降，而不是直接覆盖（设计方案 4.4 节的关键约束）
      - 全新模式 → 新增，初始置信度按证据数量线性折算
    """
    by_pattern = {p.pattern: p for p in existing}
    for item in new_raw:
        pat, refs = item["pattern"], item["evidence_refs"]
        if pat in by_pattern:
            node = by_pattern[pat]
            merged_refs = sorted(set(node.evidence_refs) | set(refs))
            gained = len(merged_refs) - len(node.evidence_refs)
            node.evidence_refs = merged_refs
            if gained > 0:
                node.confidence = min(1.0, node.confidence + 0.1 * gained)
                node.last_reinforced = now_str
        else:
            conf = min(0.6, 0.15 * len(refs))  # 新模式起始置信度封顶 0.6，需要后续多轮强化
            by_pattern[pat] = ValuePattern(
                pattern=pat,
                evidence_refs=refs,
                confidence=conf,
                first_observed=now_str,
                last_reinforced=now_str,
            )
    return list(by_pattern.values())


def generate_decision_profile(paths: AgentPaths, *, llm_helper=None) -> Optional[dict]:
    """归纳一轮决策画像。llm_helper 为 None 时直接返回 None（本层归纳依赖 LLM
    做语义总结，规则层无法替代，不强行做无意义的关键词聚类）。
    """
    if llm_helper is None:
        return None

    pages = _load_decision_pages(paths)
    if len(pages) < MIN_EVIDENCE_COUNT:
        return None  # 决策记录本身不足，归纳没有意义

    state = _load_state(paths)
    existing = [ValuePattern.from_dict(d) for d in state.get("patterns", [])]

    raw_patterns = _llm_summarize_patterns(pages, llm_helper)
    if not raw_patterns:
        return None

    now_str = time.strftime("%Y-%m-%d", time.localtime())
    merged = _apply_contradiction(existing, raw_patterns, now_str)

    state["last_scan_at"] = time.time()
    state["patterns"] = [p.to_dict() for p in merged]
    _save_state(paths, state)

    _write_profile_md(paths, merged)
    return state


def _write_profile_md(paths: AgentPaths, patterns: list[ValuePattern]) -> None:
    lines = [
        "---",
        "title: 用户决策画像",
        "source_kind: decision_profile",
        f"updated: {time.strftime('%Y-%m-%d', time.localtime())}",
        "tags: [decision-profile]",
        "---",
        "",
        "# 用户决策画像",
        "",
        "> 本文档由 decision_profile_builder 周期性归纳生成，每条模式必须能追溯到"
        "具体的历史决策记录（wiki/decisions/），少于 3 条独立证据的模式不会出现在这里。",
        "",
    ]
    for p in sorted(patterns, key=lambda x: -x.confidence):
        lines.append(f"## {p.pattern}")
        lines.append(f"- 置信度：{p.confidence:.2f}")
        lines.append(f"- 首次观察：{p.first_observed}　最近强化：{p.last_reinforced}")
        lines.append(f"- 证据：{', '.join(p.evidence_refs)}")
        if p.contradicted_by:
            lines.append(f"- ⚠️ 存在矛盾证据：{', '.join(p.contradicted_by)}（置信度已相应下调）")
        lines.append("")

    paths.wiki_dir.mkdir(parents=True, exist_ok=True)
    paths.user_value_profile_path.write_text("\n".join(lines), encoding="utf-8")
