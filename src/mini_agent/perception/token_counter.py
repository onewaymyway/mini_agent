"""
perception/token_counter.py — 轻量 token 估算。

优先使用 tiktoken；不可用时退化为字符数/3 估算。
对中文、日文等 CJK 字符按更高比例估算（约 1.5 字符/token）。
"""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """
    估算字符串的 token 数量。

    优先级：
      1. tiktoken cl100k_base（GPT-4 / Claude 通用编码）
      2. 启发式字符计数（英文 ~4 chars/token，CJK ~1.5 chars/token）
    """
    if not text:
        return 0
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.token_counter.estimate_tokens')
        return _heuristic_count(text)


def estimate_messages_tokens(messages: list[dict], system: str = "") -> int:
    """估算整个 messages 列表 + system 的总 token 数。"""
    total = estimate_tokens(system)
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += estimate_tokens(str(block.get("text", "") or block.get("content", "")))
        # 每条消息有少量 overhead（role 字段等）
        total += 4
    return total


def _heuristic_count(text: str) -> int:
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff"
              or "\u3040" <= c <= "\u30ff"
              or "\uac00" <= c <= "\ud7af")
    non_cjk = len(text) - cjk
    return int(cjk / 1.5 + non_cjk / 4) + 1
