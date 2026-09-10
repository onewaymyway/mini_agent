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

import re
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
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.history.compact_audit.audit_compact_quality')
        return CompactAuditResult(raw_response=f"[audit failed, treated as no_issue: {e}]")


# ════════════════════════════════════════════════════════════════════════════════
# 路径锚点守卫（零 LLM 成本，所有 compact 触发原因都跑，包括高频的
# token_threshold / turn_count 等）——用于弥补上面 `audit_compact_quality`
# 只对 deep compact 生效、且需要一次额外 LLM 调用的缺口。
#
# 背景：工作目录/输出目录这类"环境锚点"一旦只在对话早期出现过一次、后面
# 从未重复提及，普通的 LLM 摘要很容易因为"看起来不像本轮进展"而不把它当
# 重点写进摘要——尤其是 token_threshold 这种高频触发，为了控制成本被排除在
# 昂贵的 LLM 质量审计之外，导致这类遗漏完全没有兜底。
#
# 这里用纯正则做一次廉价的"路径字符串是否还在摘要里"比对，不调用 LLM，
# 因此可以在**每一次** compact（无论哪种触发原因）之后都跑一遍。
# ════════════════════════════════════════════════════════════════════════════════

# Windows 绝对路径（C:\...）、POSIX 绝对路径（/home/... 等，要求至少两段避免
# 把 "a/b" 这种误判成路径）、以及常见的"相对输出目录"写法（output/xxx、
# ./xxx、../xxx）。刻意不匹配过短或过于通用的片段（如单独的 "/"）。
_PATH_LIKE_RE = re.compile(
    r"(?:[A-Za-z]:\\[^\s\"'`]+)"          # Windows: C:\Users\...
    r"|(?:/(?:[^\s\"'`/]+/){1,}[^\s\"'`/]*)"  # POSIX: /a/b/... (>=2 段)
    r"|(?:\.{1,2}/[^\s\"'`]+)"            # ./xxx or ../xxx
)

# 只在候选路径的"上下文"里出现这些关键词时才认为是真正的环境锚点（而不是
# 随口提到的某个不相关路径），降低误报。中英文都覆盖。
_ANCHOR_CONTEXT_RE = re.compile(
    r"(工作目录|项目目录|项目根目录|输出目录|输出到|保存到|存到|存放到|"
    r"working directory|working dir|project root|output dir|output directory|"
    r"save (?:it |them |results? )?to|--output|--save-path|--save-dir)",
    re.IGNORECASE,
)

_MIN_PATH_LEN = 4          # 太短的匹配大概率是噪声（比如 "./a"）
_MAX_ANCHORS_CHECKED = 12  # 只检查前 N 个候选，避免超长历史拖慢/误报过多


def _extract_path_anchors(text: str) -> list:
    """从一段文本里挑出"看起来像路径、且上下文像是在声明工作目录/输出目录"
    的候选字符串。返回去重后的列表，保持首次出现的顺序（越早出现的通常越
    是任务级别的根锚点，优先级更高）。
    """
    if not text:
        return []
    anchors: list = []
    seen = set()
    for m in _PATH_LIKE_RE.finditer(text):
        path = m.group(0).rstrip(").,;:，。；：")
        if len(path) < _MIN_PATH_LEN or path in seen:
            continue
        window = text[max(0, m.start() - 25): m.end() + 10]
        if not _ANCHOR_CONTEXT_RE.search(window):
            continue
        seen.add(path)
        anchors.append(path)
        if len(anchors) >= _MAX_ANCHORS_CHECKED:
            break
    return anchors


def check_path_anchors_preserved(
    pre_compact_history: list, summary_text: str,
) -> Optional[str]:
    """
    零 LLM 成本的确定性检查：压缩前历史里出现过的"工作目录/输出目录"类路径，
    是否原样出现在压缩后的摘要文本里。

    设计上刻意保守（宁可漏报，不可误报）：
      - 只在路径字符串附近有明确的"工作目录/输出目录/save to"等关键词时才
        当作候选锚点，避免把任意文件路径都当成"必须保留"。
      - 要求在摘要里**逐字符串**命中；摘要里换了种表达方式（比如把绝对路径
        换成了模糊描述）会被判定为"缺失"，这是有意为之——本来就是要防止
        这种退化。

    返回值：
      - None：没发现任何候选锚点，或所有候选锚点都在摘要里找到了（不代表
        绝对没问题，只是这一层廉价检查没发现问题）。
      - 非空字符串：发现至少一个候选锚点在摘要里找不到，返回值是可以直接
        追加进 compact_supplement 的提示文本，包含具体缺失的路径列表。

    任何异常都静默返回 None，不能影响 compact 主流程。
    """
    try:
        if not summary_text or not pre_compact_history:
            return None

        anchors: list = []
        seen = set()
        for msg in pre_compact_history:
            text = _extract_text(msg)
            if not text:
                continue
            for a in _extract_path_anchors(text):
                if a not in seen:
                    seen.add(a)
                    anchors.append(a)

        if not anchors:
            return None

        missing = [a for a in anchors if a not in summary_text]
        if not missing:
            return None

        lines = "\n".join(f"  - {a}" for a in missing)
        return (
            "[path-anchor-guard] 压缩前的对话中出现过以下工作目录/输出目录相关的路径，"
            "但没有在压缩后的摘要里原样找到，可能是关键的环境上下文被遗漏了，"
            "请在继续任务前和用户确认这些路径是否仍然有效：\n" + lines
        )
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.history.compact_audit.check_path_anchors_preserved')
        return None
