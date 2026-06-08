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
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

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
            压缩后的新历史列表
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
            {"role": "user",      "content": "[Previous conversation compressed]"},
            {"role": "assistant", "content": f"[Compressed summary: {summary_text}]"},
        ] + keep


class SlidingWindowStrategy(CompressionStrategy):
    """
    滑动窗口：始终保留最近 N 轮（每轮 = 一条 user + 后续 assistant/tool_result）。

    优点：实现简单，上下文长度严格可控
    缺点：丢失所有早期历史，无任何摘要
    适用：短任务、对话轮次多但单轮信息密度低的场景

    配置：通过 cfg.compress 中未来可扩展的 window_turns 字段控制；
          当前版本默认保留最近 5 个完整 turn。
    """

    def __init__(self, window_turns: int = 5) -> None:
        self.window_turns = window_turns

    def compress(self, history, cfg, llm_client=None) -> list[dict]:
        if len(history) < 6:
            return list(history)

        # 收集所有 user 消息起始索引（过滤掉 tool_result 和压缩占位符）
        turn_starts = [
            i for i, m in enumerate(history)
            if m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and not m["content"].startswith("<tool_result")
            and not m["content"].startswith("[Previous")
            and not m["content"].startswith("[Compressed")
        ]

        if len(turn_starts) <= self.window_turns:
            return list(history)

        keep_from = turn_starts[-self.window_turns]
        keep = history[keep_from:]

        dropped_turns = len(turn_starts) - self.window_turns
        return [
            {"role": "user",      "content": "[Previous conversation compressed]"},
            {"role": "assistant", "content": f"[{dropped_turns} earlier turns dropped by sliding window]"},
        ] + keep

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

    _SUMMARY_PROMPT = """\
Please create a concise but complete summary of the conversation above.
The summary will replace the full conversation history, so it must contain:
1. The user's overall goal
2. What has been accomplished so far (with key details like file paths, commands run, results)
3. Important decisions or findings
4. The current state / what still needs to be done

Format your response as a single paragraph of 150-250 words.
Do NOT include meta-commentary like "Here is a summary:" — just the summary text."""

    def compress(self, history, cfg, llm_client=None) -> list[dict]:
        if len(history) < 6:
            return list(history)

        if llm_client is None:
            # 降级到 TurnAlignedStrategy
            return TurnAlignedStrategy().compress(history, cfg, None)

        cutoff = _find_turn_aligned_cutoff(history)
        old_turns = history[:cutoff]
        keep = history[cutoff:]

        # 构建摘要请求：把被压缩的历史 + 摘要指令发给 LLM
        summary_messages = list(old_turns) + [
            {"role": "user", "content": self._SUMMARY_PROMPT}
        ]

        try:
            from mini_agent.llm.base import ToolSchema
            response = llm_client.chat(
                messages=summary_messages,
                system="You are a precise assistant that summarizes conversations.",
                tools=[],
            )
            summary_text = response.text.strip() or _build_summary_text(old_turns, cutoff)
        except Exception:
            # 任何 LLM 调用失败都降级到字符串摘要
            summary_text = _build_summary_text(old_turns, cutoff)

        if cfg.compress.forget_orphan_tool_results:
            keep = _drop_orphan_tool_results(keep)

        return [
            {"role": "user",      "content": "[Previous conversation compressed]"},
            {"role": "assistant", "content": f"[Summary of earlier conversation:\n{summary_text}]"},
        ] + keep


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
# 共享辅助函数
# ════════════════════════════════════════════════════════════════════════════════

def _find_turn_aligned_cutoff(history: list[dict]) -> int:
    """找最靠近历史中点的 user 消息索引作为切割点。"""
    user_indices = [
        i for i, m in enumerate(history)
        if m.get("role") == "user"
        and isinstance(m.get("content"), str)
        and not m["content"].startswith("<tool_result")
        and not m["content"].startswith("[Previous")
        and not m["content"].startswith("[Compressed")
    ]
    if len(user_indices) < 2:
        return len(history) // 2
    mid = len(history) // 2
    cutoff = min(user_indices, key=lambda i: abs(i - mid))
    if cutoff >= user_indices[-1]:
        cutoff = user_indices[len(user_indices) // 2]
    return cutoff


def _drop_orphan_tool_results(history: list[dict]) -> list[dict]:
    """剔除保留段中没有对应 tool_use 的孤立 tool_result 消息。"""
    return [
        m for m in history
        if not (
            m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and m["content"].startswith("<tool_result")
        )
    ]


def _build_summary_text(old_turns: list[dict], cutoff: int) -> str:
    """从被压缩的消息中生成摘要字符串。"""
    user_msgs = [
        m["content"] for m in old_turns
        if m.get("role") == "user"
        and isinstance(m.get("content"), str)
        and not m["content"].startswith("<tool_result")
        and not m["content"].startswith("[Previous session")
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
