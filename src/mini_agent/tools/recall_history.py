"""
tools/recall_history.py — Raw history 按需找回工具
（compact_mechanism_improvement_plan.md P2-B）

现状：history/raw_history.py 已经全量持久化了压缩前的所有原始记录，但目前只是
"死档案"，只能人工翻 .jsonl 文件查看，agent 自己在运行中无法主动检索找回。

本工具给 agent 提供一个只读、免审批的检索入口：当 agent 隐约记得处理过某事，
但当前（压缩后的）上下文里已经找不到细节时，可以调用 recall_from_raw_history
按关键词找回被 compact 掉的原始片段，而不是凭空猜测或重新执行一遍。

意义：给更激进的压缩策略（P0-A 的目标相关性降权等）兜底——反正删掉的东西
找得回来，压缩策略可以更敢于"压狠一点"，把"怕删错"这个心理负担从压缩阶段
转移到"按需找回"阶段。

实现只做轻量档：复用 history/triggers.py 已有的 _simple_keywords 做关键词
匹配 + 时间倒序，不引入向量检索依赖。若后续项目里出现可复用的 embedding
检索组件，可以在 mode="embedding" 分支里接入，当前该分支未实现，退回关键词档。

配置：
  tools.recall_history_enabled: bool = False  — 是否注册/启用本工具
  tools.recall_history_mode: str = "keyword"  — "keyword" | "embedding"（暂未实现）

线程本地注入模式与 notepad.py::configure_notepad_store 保持一致：
mini-agent 的并发编排以线程为并发单元，每个 Agent 实例通常运行在自己的
线程里，用 thread-local 而非模块级全局变量，避免多个 Agent 实例互相串扰。
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from mini_agent.tools import tool

# ── 线程本地懒引用 provider（由 agent/lifecycle.py 在 Agent.__init__ 中注入）────

_entries_local = threading.local()   # provider: () -> list[dict]（当前 session 的 raw entries）
_enabled_local = threading.local()   # provider: () -> bool


def configure_recall_history(
    entries_getter: Callable[[], list],
    enabled_getter: Optional[Callable[[], bool]] = None,
) -> None:
    """
    由 agent/lifecycle.py 在 Agent.__init__ 中调用，为**当前线程**注册
    "当前 session 的 raw history 条目列表" 懒引用回调。

    直接注入活的 list 引用（`RawHistory.entries` 属性，与 raw_history.jsonl
    同步更新），而不是重新解析磁盘文件——每次调用都能拿到最新数据，
    也避免了 session_id/路径解析这一层间接开销。

    enabled_getter 为 None 时视为"未显式配置"，`is_recall_history_enabled()`
    默认返回 False（与 `tools.recall_history_enabled` 默认关闭保持一致）。
    """
    _entries_local.provider = entries_getter
    _enabled_local.provider = enabled_getter


def is_recall_history_enabled() -> bool:
    """当前线程是否启用 recall_history 功能。未配置 enabled provider 时默认关闭。"""
    provider = getattr(_enabled_local, "provider", None)
    if provider is None:
        return False
    try:
        return bool(provider())
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.tools.recall_history.is_recall_history_enabled')
        return False


def _get_current_entries() -> Optional[list]:
    if not is_recall_history_enabled():
        return None
    provider = getattr(_entries_local, "provider", None)
    if provider is None:
        return None
    try:
        entries = provider()
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.tools.recall_history._get_current_entries')
        return None
    return entries if isinstance(entries, list) else None


def reset_recall_history_config() -> None:
    """测试/会话切换时清理当前线程的 provider 绑定。"""
    _entries_local.provider = None
    _enabled_local.provider = None


# ── 检索实现（轻量档：关键词匹配 + 时间倒序）───────────────────────────────────

def _extract_text_for_search(entry: dict) -> str:
    """从 raw history 条目里提取可供关键词匹配的纯文本。"""
    content = entry.get("content")
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
                parts.append(f"{block.get('name', '')} {block.get('input', '')}")
        return " ".join(str(p) for p in parts)
    return ""


# raw history 里不承载有效检索价值、应跳过的条目类型
_SKIP_TYPES = {"compact_event", "compressed", "compact_summary", "session_resume"}


def _search_raw_entries(entries: list, query: str, max_results: int) -> list[dict]:
    """
    返回命中片段列表，每项包含：
      text        — 命中片段的文本（截断到合理长度）
      turn_index  — 该条目之前出现过多少次真实用户输入（近似"第几轮"）
      turns_ago   — 距当前（entries 里最后一次真实用户输入）经过的轮数
      ts          — 原始时间戳（若有）
    按关键词重合度降序、同分时按时间倒序（越新越靠前）排列。
    """
    from mini_agent.history.entry import is_turn_boundary
    from mini_agent.history.triggers import _simple_keywords

    query_kw = _simple_keywords(query)
    if not query_kw:
        return []

    # 预计算每条目对应的"轮次编号"（第几次真实用户输入之后）
    turn_index_at = []
    running_turn = 0
    for entry in entries:
        if is_turn_boundary(entry):
            running_turn += 1
        turn_index_at.append(running_turn)
    total_turns = running_turn

    scored = []
    for i, entry in enumerate(entries):
        if str(entry.get("_type", "")) in _SKIP_TYPES:
            continue
        text = _extract_text_for_search(entry)
        if not text:
            continue
        entry_kw = _simple_keywords(text)
        if not entry_kw:
            continue
        overlap = len(query_kw & entry_kw)
        if overlap == 0:
            continue
        scored.append((overlap, i, entry, text))

    # 关键词重合数降序；同分时按 index 降序（越靠后越新）
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)

    results = []
    for overlap, i, entry, text in scored[:max_results]:
        snippet = text.strip()
        if len(snippet) > 500:
            snippet = snippet[:500] + "…"
        results.append({
            "text": snippet,
            "turn_index": turn_index_at[i],
            "turns_ago": max(0, total_turns - turn_index_at[i]),
            "ts": entry.get("_ts", ""),
            "match_score": overlap,
        })
    return results


# ── 工具注册 ─────────────────────────────────────────────────────────────────

@tool(
    name="recall_from_raw_history",
    description=(
        "Search the full raw conversation history (including content already removed "
        "by context compaction) for fragments matching a query. Use this when you "
        "vaguely remember having handled something earlier, but the details are no "
        "longer present in your current (possibly compacted) context — call this "
        "instead of guessing or redoing the work. Read-only, no side effects."
    ),
    schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keywords or a short description of what you're trying to recall.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of matching fragments to return (default 5).",
            },
        },
        "required": ["query"],
    },
    requires_approval=False,
)
def recall_from_raw_history(query: str, max_results: int = 5) -> str:
    """按关键词在当前 session 的 raw history 中检索被压缩掉的原始片段。"""
    entries = _get_current_entries()
    if entries is None:
        return (
            "[error: recall_from_raw_history is not enabled for this session "
            "(tools.recall_history_enabled=false, or not configured during agent init)]"
        )
    if not query or not query.strip():
        return "[error: query must not be empty]"

    max_results = max(1, min(int(max_results or 5), 20))

    try:
        results = _search_raw_entries(entries, query, max_results)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.recall_history.recall_from_raw_history')
        return f"[error: recall_from_raw_history failed: {e}]"

    if not results:
        return f"No matching fragments found in raw history for query: {query!r}"

    lines = [f"Found {len(results)} matching fragment(s) for query {query!r}:"]
    for r in results:
        lines.append(
            f"\n--- turn ~{r['turn_index']} ({r['turns_ago']} turns ago"
            + (f", {r['ts']}" if r["ts"] else "")
            + f", match_score={r['match_score']}) ---\n{r['text']}"
        )
    return "\n".join(lines)
