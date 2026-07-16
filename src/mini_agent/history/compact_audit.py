"""
history/compact_audit.py — 压缩质量事后自检
（compact_mechanism_improvement_plan.md P2-A）

现状：compact 执行后没有质量校验，压缩是否丢失关键信息只能等下游任务失败时
才被动发现。raw_history.py 已经全量保留原始记录，append_compact_event 已
记录每次 compact 事件，具备做校验的原材料。

设计取舍（与项目一贯风格一致）：
  - 这是**事后**校验，不阻塞/不影响 compact 主流程——任何异常静默吞掉，
    返回"未发现问题"，绝不向上抛出。
  - 只对 deep compact（topic_shift_* / stuck_recovery_deep 等非高频触发）生效，
    避免每次 turn_count/tool_call_count 这类高频触发都额外增加一次 LLM 调用成本。
  - 单次 LLM 调用：输入压缩摘要 + 被丢弃的原始片段（截断到预算内），判断是否
    存在决定性信息（约束条件/失败原因/用户明确要求）被遗漏。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.llm.base import LLMClient


# 喂给审计 LLM 调用的原始历史文本预算（字符数），避免这次校验本身占用过多 token
_AUDIT_HISTORY_CHAR_BUDGET = 6000

_NO_ISSUE_MARKERS = ("NO_ISSUE", "NO ISSUE", "没有遗漏", "无遗漏")


@dataclass
class CompactAuditResult:
    has_issue: bool = False
    missing_info: str = ""     # 遗漏信息的说明文本（LLM 给出），has_issue=False 时为空
    raw_response: str = ""     # 原始 LLM 回复，便于排查


def _extract_text(msg: dict) -> str:
    """从 history 条目里提取纯文本（与 triggers.py::_extract_text 逻辑一致，
    独立实现避免两个模块间产生非必要的相互依赖）。"""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "tool_use":
                parts.append(f"[tool_use:{block.get('name', '')}] {block.get('input', '')}")
        return " ".join(str(p) for p in parts)
    return ""


def _build_pre_compact_excerpt(pre_compact_history: list, char_budget: int) -> str:
    """把压缩前的原始历史拼成文本，从最新的消息往前取，直到达到字符预算。
    越靠后的消息通常越贴近"当前正在做什么"，比从头开始截断更有信息量。
    """
    parts = []
    total = 0
    for msg in reversed(pre_compact_history):
        text = _extract_text(msg)
        if not text:
            continue
        role = msg.get("role", "?")
        piece = f"[{role}] {text}"
        total += len(piece)
        parts.append(piece)
        if total >= char_budget:
            break
    parts.reverse()
    return "\n".join(parts)


def audit_compact_quality(
    pre_compact_history: list,
    summary_text: str,
    llm_client: "LLMClient",
    char_budget: int = _AUDIT_HISTORY_CHAR_BUDGET,
) -> CompactAuditResult:
    """
    单次 LLM 调用，输入压缩摘要 + 被丢弃的原始片段（截断到预算内），
    判断是否存在决定性信息（约束条件/失败原因/用户明确要求）被遗漏。

    失败时静默返回 has_issue=False，不影响主流程（这是事后校验，不应阻塞 compact）。
    """
    if llm_client is None or not summary_text or not pre_compact_history:
        return CompactAuditResult()

    try:
        excerpt = _build_pre_compact_excerpt(pre_compact_history, char_budget)
        if not excerpt:
            return CompactAuditResult()

        prompt = (
            "以下是一次历史压缩前后的对比：\n\n"
            "=== 压缩前的原始对话片段（节选，可能不完整）===\n"
            f"{excerpt}\n\n"
            "=== 压缩后生成的摘要 ===\n"
            f"{summary_text}\n\n"
            "请判断摘要是否遗漏了原始片段中的决定性信息，包括但不限于：\n"
            "1. 明确的约束条件（例如必须使用某个方案/禁止某种做法）\n"
            "2. 已发生的失败及其原因（避免后续重复同样的错误）\n"
            "3. 用户明确提出的要求或偏好\n\n"
            "如果没有发现遗漏，只回复：NO_ISSUE\n"
            "如果发现遗漏，用一到三句话具体描述遗漏了什么信息，"
            "不要复述已经在摘要里的内容，只说遗漏的部分。"
        )
        response = llm_client.chat_with_retry(
            messages=[{"role": "user", "content": prompt}],
            system=(
                "你是一个简洁、谨慎的压缩质量审计员。只在确信有决定性信息遗漏时才报告问题，"
                "不要吹毛求疵地挑剔措辞或次要细节。"
            ),
            tools=[],
            max_retries=2,
        )
        answer = (response.text or "").strip()
        if not answer:
            return CompactAuditResult()
        if any(marker in answer.upper() or marker in answer for marker in _NO_ISSUE_MARKERS):
            return CompactAuditResult(raw_response=answer)
        return CompactAuditResult(has_issue=True, missing_info=answer, raw_response=answer)
    except Exception as e:
        return CompactAuditResult(raw_response=f"[audit failed, treated as no_issue: {e}]")
