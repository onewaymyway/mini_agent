"""
history/compression.py — 历史压缩策略框架

设计参考 llm/retry.py 的重试条件模式：
  - 每个策略是独立的类，实现 compress() 接口
  - HistoryManager 持有策略实例，压缩时委托给它
  - 新增策略只需继承 CompressionStrategy，无需改 HistoryManager 或 Agent

内置策略：
  TurnAlignedStrategy   — 按 turn 边界切割 + 字符串摘要（默认，无额外依赖）
  SlidingWindowStrategy — 始终保留最近 N 轮，超出直接丢弃
  LLMSummaryStrategy    — 用 LLM 生成语义摘要（质量最高，需要 llm_client）

注册/切换策略（通过 CompressConfig.strategy 字段名）：
  cfg.compress.strategy = "turn_aligned"   # 默认
  cfg.compress.strategy = "sliding_window"
  cfg.compress.strategy = "llm_summary"

  # 自定义策略：
  from mini_agent.history.compression import register_strategy
  register_strategy("my_strategy", MyStrategy)

类型化版本改动：
  - 所有字符串前缀判断（startswith "<tool_result"、"[Previous" 等）
    改为通过 _type 字段判断（is_turn_boundary / is_tool_result 等辅助函数）
  - 向后兼容：辅助函数在无 _type 字段时降级到字符串前缀判断
  - compress() 返回的新 history 条目使用 make_compressed() 等构造函数，自带 _type
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

from mini_agent.history.entry import (
    HType,
    is_real_user_input,
    is_tool_result,
    is_turn_boundary,
    make_compressed,
    make_compact_summary,
)

if TYPE_CHECKING:
    from mini_agent.llm.base import LLMClient
    from mini_agent.config import AppConfig


# ════════════════════════════════════════════════════════════════════════════════
# 抽象基类
# ════════════════════════════════════════════════════════════════════════════════

class CompressionStrategy(ABC):
    """
    历史压缩策略抽象基类。

    compress() 接收完整历史列表，返回压缩后的新列表（不修改输入）。
    HistoryManager 负责原地更新 self._history（clear + extend），
    保持所有外部共享引用有效。
    """

    @abstractmethod
    def compress(
        self,
        history: list[dict],
        cfg: "AppConfig",
        llm_client: Optional["LLMClient"] = None,
    ) -> list[dict]:
        """
        Args:
            history:    当前完整对话历史（不要原地修改）
            cfg:        AppConfig（含 compress 子块参数）
            llm_client: LLM 客户端（LLMSummaryStrategy 需要，其他策略可忽略）

        Returns:
            压缩后的新历史列表（条目含 _type 字段）
        """
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__

    def __repr__(self) -> str:
        return f"{self.name}()"


# ════════════════════════════════════════════════════════════════════════════════
# 内置策略
# ════════════════════════════════════════════════════════════════════════════════

class TurnAlignedStrategy(CompressionStrategy):
    """
    【默认】对齐到 turn 边界切割，字符串拼接摘要。

    - 切割点找最靠近中点的 user 消息索引，保证：
      · 保留段第一条始终是 user 消息
      · 不产生孤立的 tool_result（无对应 tool_use）
    - 摘要文字：拼接被压缩的 user 消息 + 工具调用计数
    - 无额外依赖，离线可用
    - 使用 _type 字段精确判断 turn 边界（而非字符串前缀）
    """

    def compress(self, history, cfg, llm_client=None) -> list[dict]:
        if len(history) < 6:
            return list(history)

        cutoff = _find_turn_aligned_cutoff(history)
        old_turns = history[:cutoff]
        keep = history[cutoff:]

        # 可选：剔除保留段中孤立的 tool_result
        if cfg.compress.forget_orphan_tool_results:
            keep = _drop_orphan_tool_results(keep)

        summary_text = _build_summary_text(old_turns, cutoff)

        return [
            make_compressed(),
            make_compact_summary(f"[Compressed summary: {summary_text}]"),
        ] + list(keep)


class SlidingWindowStrategy(CompressionStrategy):
    """
    滑动窗口：始终保留最近 N 轮（每轮 = 一条 user + 后续 assistant/tool_result）。

    优点：实现简单，上下文长度严格可控
    缺点：丢失所有早期历史，无任何摘要
    适用：短任务、对话轮次多但单轮信息密度低的场景

    配置：通过 cfg.compress 中未来可扩展的 window_turns 字段控制；
          当前版本默认保留最近 5 个完整 turn。

    使用 _type 字段精确识别真实用户输入（而非字符串前缀）。
    """

    def __init__(self, window_turns: int = 5) -> None:
        self.window_turns = window_turns

    def compress(self, history, cfg, llm_client=None) -> list[dict]:
        if len(history) < 6:
            return list(history)

        # 收集所有真实用户消息的起始索引（用 is_turn_boundary 精确识别）
        turn_starts = [
            i for i, m in enumerate(history)
            if is_turn_boundary(m)
        ]

        if len(turn_starts) <= self.window_turns:
            return list(history)

        keep_from = turn_starts[-self.window_turns]
        keep = history[keep_from:]

        dropped_turns = len(turn_starts) - self.window_turns
        return [
            make_compressed(),
            make_compact_summary(f"[{dropped_turns} earlier turns dropped by sliding window]"),
        ] + list(keep)

    @property
    def name(self) -> str:
        return f"SlidingWindowStrategy(window={self.window_turns})"


class LLMSummaryStrategy(CompressionStrategy):
    """
    用 LLM 生成语义摘要，质量最高。

    - 将被压缩的对话历史发送给 LLM，请求生成结构化摘要
    - 摘要包含：用户目标、已完成的工作、关键决策、当前状态
    - 需要 llm_client；若 llm_client 为 None，自动降级为 TurnAlignedStrategy

    注意：会产生额外的 LLM 调用（费用 + 延迟）。
    建议：仅在手动触发（/compact 命令）时使用，不作为自动压缩默认策略。
    """

    def compress(self, history, cfg, llm_client=None) -> list[dict]:
        if len(history) < 6:
            return list(history)

        if llm_client is None:
            # 降级到 TurnAlignedStrategy
            return TurnAlignedStrategy().compress(history, cfg, None)

        cutoff = _find_turn_aligned_cutoff(history)
        old_turns = history[:cutoff]
        keep = history[cutoff:]

        from mini_agent.prompts import pm
        from mini_agent.history.entry import to_llm_messages
        # 构建摘要请求：把被压缩的历史（剥离 _type）+ 摘要指令发给 LLM
        summary_messages = to_llm_messages(list(old_turns)) + [
            {"role": "user", "content": pm.render("user/compress_summary_request")}
        ]

        try:
            response = llm_client.chat_with_retry(
                messages=summary_messages,
                system=pm.render("system/compress_summarizer"),
                tools=[],
                max_retries=10
            )
            summary_text = response.text.strip() or _build_summary_text(old_turns, cutoff)
        except Exception:
            # 任何 LLM 调用失败都降级到字符串摘要
            summary_text = _build_summary_text(old_turns, cutoff)

        if cfg.compress.forget_orphan_tool_results:
            keep = _drop_orphan_tool_results(keep)

        return [
            make_compressed(),
            make_compact_summary(f"[Summary of earlier conversation:\n{summary_text}]"),
        ] + list(keep)


# ════════════════════════════════════════════════════════════════════════════════
# 策略注册表与工厂
# ════════════════════════════════════════════════════════════════════════════════

_STRATEGY_REGISTRY: dict[str, type[CompressionStrategy]] = {
    "turn_aligned":    TurnAlignedStrategy,
    "sliding_window":  SlidingWindowStrategy,
    "llm_summary":     LLMSummaryStrategy,
}


def create_strategy(cfg: "AppConfig") -> CompressionStrategy:
    """
    根据 cfg.compress.strategy 创建对应的压缩策略实例。

    Example:
        strategy = create_strategy(cfg)
        new_history = strategy.compress(history, cfg, llm_client)
    """
    strategy_key = cfg.compress.strategy.lower().strip()
    cls = _STRATEGY_REGISTRY.get(strategy_key)
    if cls is None:
        available = sorted(_STRATEGY_REGISTRY)
        raise ValueError(
            f"Unknown compression strategy: {strategy_key!r}.\n"
            f"Available: {available}"
        )
    return cls()


def register_strategy(name: str, cls: type[CompressionStrategy]) -> None:
    """
    注册自定义压缩策略。

    Example:
        class MyStrategy(CompressionStrategy):
            def compress(self, history, cfg, llm_client=None):
                ...

        register_strategy("my_strategy", MyStrategy)
        # 之后在 agent_config.json 中设置 "auto_compress_strategy": "my_strategy"
    """
    _STRATEGY_REGISTRY[name.lower()] = cls


def list_strategies() -> list[str]:
    return sorted(_STRATEGY_REGISTRY)


# ════════════════════════════════════════════════════════════════════════════════
# 共享辅助函数（使用 _type 字段，向后兼容字符串前缀）
# ════════════════════════════════════════════════════════════════════════════════

def _find_turn_aligned_cutoff(history: list[dict]) -> int:
    """找最靠近历史中点的真实用户消息索引作为切割点。
    使用 is_turn_boundary() 精确识别（含向后兼容）。
    """
    user_indices = [
        i for i, m in enumerate(history)
        if is_turn_boundary(m)
    ]
    if len(user_indices) < 2:
        return len(history) // 2
    mid = len(history) // 2
    cutoff = min(user_indices, key=lambda i: abs(i - mid))
    if cutoff >= user_indices[-1]:
        cutoff = user_indices[len(user_indices) // 2]
    return cutoff


def _drop_orphan_tool_results(history: list[dict]) -> list[dict]:
    """剔除保留段中没有对应 tool_use 的孤立 tool_result 消息。
    使用 is_tool_result() 精确识别（含向后兼容）。
    """
    return [m for m in history if not is_tool_result(m)]


def _build_summary_text(old_turns: list[dict], cutoff: int) -> str:
    """从被压缩的消息中生成摘要字符串。
    使用 is_real_user_input() 精确识别真实用户消息（含向后兼容）。
    """
    user_msgs = [
        m["content"] for m in old_turns
        if is_real_user_input(m) and isinstance(m.get("content"), str)
    ]
    tool_call_count = sum(
        sum(1 for b in m.get("content", [])
            if isinstance(b, dict) and b.get("type") == "tool_use")
        for m in old_turns
        if m.get("role") == "assistant" and isinstance(m.get("content"), list)
    )
    parts = []
    if user_msgs:
        parts.append("User requests: " + "; ".join(
            (msg[:80] + "…" if len(msg) > 80 else msg)
            for msg in user_msgs[:6]
        ))
        if len(user_msgs) > 6:
            parts.append(f"... and {len(user_msgs)-6} more turns")
    if tool_call_count:
        parts.append(f"({tool_call_count} tool calls executed)")
    return " ".join(parts) if parts else f"({cutoff} messages)"
