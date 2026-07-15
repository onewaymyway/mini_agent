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
  cfg.compress.strategy = "compact_with_skills"  # 默认，见 agent/compaction.py::_auto_compress_history
                                                  # （不在本文件的 _STRATEGY_REGISTRY 中，由 agent 层直接
                                                  #  复用 compact_with_skills() 实现，非 CompressionStrategy 子类）
  cfg.compress.strategy = "turn_aligned"   # 轻量可插拔策略
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
# 单条消息过长兜底截断（compact 专用，供 LLMSummaryStrategy / _compact_chunked 共用）
# ════════════════════════════════════════════════════════════════════════════════

DEFAULT_MAX_MESSAGE_CHARS_FOR_COMPACT = 10000

# 截断后保留的头/尾比例（头部保留更多，因为通常包含关键上下文/指令）
_TRUNC_HEAD_RATIO = 0.6
_TRUNC_TAIL_RATIO = 0.2


def _truncate_str(text: str, max_chars: int) -> str:
    """保留头尾、截断中段。仅在 len(text) > max_chars 时生效。"""
    if len(text) <= max_chars:
        return text
    head_len = int(max_chars * _TRUNC_HEAD_RATIO)
    tail_len = int(max_chars * _TRUNC_TAIL_RATIO)
    omitted = len(text) - head_len - tail_len
    marker = f"\n\n… [{omitted} chars truncated for compaction — content too long for a single LLM request] …\n\n"
    return text[:head_len] + marker + text[-tail_len:] if tail_len > 0 else text[:head_len] + marker


def _truncate_content_value(value, max_chars: int):
    """
    递归截断消息 content 里超长的字符串叶子节点。

    content 可能是：
      - str（最常见，工具输出/长文本粘贴等）
      - list[dict]（多模态/分块格式，如 [{"type": "text", "text": "..."}, ...]）
      - 其他（None / dict 等）原样返回，不做处理

    只截断字符串本身，不改变消息的结构（role / tool_call_id / type 等字段保留）。
    """
    if isinstance(value, str):
        return _truncate_str(value, max_chars)
    if isinstance(value, list):
        return [_truncate_content_value(item, max_chars) for item in value]
    if isinstance(value, dict):
        new_d = dict(value)
        for key in ("text", "content"):
            if key in new_d:
                new_d[key] = _truncate_content_value(new_d[key], max_chars)
        return new_d
    return value


def cap_oversized_messages(
    messages: list[dict],
    max_chars: int = DEFAULT_MAX_MESSAGE_CHARS_FOR_COMPACT,
) -> list[dict]:
    """
    [SYS-COMPACT-TRUNCATE] compact 发起 LLM 摘要请求前的兜底防线。

    背景：compact 会把（部分）历史消息整体发给 LLM 生成摘要。如果历史里混入
    一条异常长的消息（典型场景：某次工具调用返回了超大输出，且因
    raw_output / 未走常规截断路径等原因绕过了 tool_executor 的输出截断），
    单条消息本身就可能超过模型单次请求的限制，导致该次 LLM 调用直接报错——
    而且 chunked 路径的切分粒度是"轮"而非"消息"，巨型单消息无法再被切小，
    会反复失败，拖垮整个 compact 流程。

    这里在送入 LLM 之前，对每条消息的 content 做保留头尾、截断中段的兜底
    处理，只影响这次摘要请求本身，不修改传入的原始消息（新建副本），也
    不影响原始历史 / 记事本 / 正式回复。

    Args:
        messages: 待发送给 LLM 的消息列表（已经过 to_llm_messages 剥离内部字段）
        max_chars: 单条消息 content 允许的最大字符数，超过则截断

    Returns:
        新的消息列表（未超限的消息原样引用，超限的消息被替换为截断后的副本）
    """
    if max_chars <= 0:
        return messages
    result: list[dict] = []
    for msg in messages:
        content = msg.get("content")
        # 快速路径：str content 直接量长度判断，避免对每条消息都做递归遍历
        if isinstance(content, str) and len(content) <= max_chars:
            result.append(msg)
            continue
        if content is None:
            result.append(msg)
            continue
        new_msg = dict(msg)
        new_msg["content"] = _truncate_content_value(content, max_chars)
        result.append(new_msg)
    return result


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
        max_chars = getattr(cfg.compress, "max_message_chars_for_compact", DEFAULT_MAX_MESSAGE_CHARS_FOR_COMPACT)
        summary_messages = cap_oversized_messages(to_llm_messages(list(old_turns)), max_chars) + [
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


class SelectiveStrategy(CompressionStrategy):
    """
    按 _type 差异化保留的选择性压缩策略。

    核心思想：不同来源的消息对"保留价值"不同：
      user_input      高价值 — 记录用户真实意图，优先保留
      assistant_reply 高价值 — 回复质量的体现，优先保留
      tool_result     低价值 — 内容长、随时间衰减快，优先截断
      reminder        低价值 — 可重新注入，不需要在历史里堆积
      skill_context   低价值 — 可重新注入，只保留最新一条
      compressed      必须保留 — 已是摘要，代表之前的全部内容
      compact_summary 必须保留 — 同上

    压缩流程：
      1. 按 _type 权重给每条消息打分
      2. 按"时间倒序 + 权重"决定保留哪些消息，直到满足 budget
      3. 被截断的部分用占位符替代
      4. 保证 turn 结构完整（不出现孤立 tool_result 或孤立 assistant_reply）

    与 TurnAlignedStrategy 的区别：
      - TurnAligned 按 turn 边界整块切割，简单但不区分内容价值
      - Selective 逐条评分，在有限 budget 内最大化保留高价值内容
      - 适合工具调用密集（tool_result 占比高）的场景，可显著节省 token

    配置（通过 CompressConfig）：
      selective_weights: dict         自定义各 _type 的权重（覆盖默认）
      selective_min_user_turns: int   无论如何至少保留的用户轮数（默认 3）
    """

    # 内置默认权重（0.0 = 最先丢弃，1.0 = 始终保留）
    DEFAULT_WEIGHTS: dict = {
        "user_input":      1.0,   # 用户意图，最高价值
        "assistant_reply": 0.9,   # 回复内容，次高价值
        "compressed":      1.0,   # 已有摘要，必须保留
        "compact_summary": 1.0,   # 已有摘要，必须保留
        "session_resume":  1.0,   # 跨 session 恢复锚点
        "skill_context":   0.3,   # 可重注入，只保留最近一条
        "reminder":        0.2,   # 可重注入，最低价值
        "role_agent":      0.4,   # role agent 反馈，中低价值
        "hook_context":    0.3,   # hook 注入，可重注入
        "tool_result":     0.4,   # 工具结果，大但可丢
        # 未知/旧格式条目给中等权重，不激进丢弃
        "__default__":     0.6,
    }

    def __init__(self, weights: dict = None, min_user_turns: int = 3) -> None:
        self._weights = dict(self.DEFAULT_WEIGHTS)
        if weights:
            self._weights.update(weights)
        self._min_user_turns = min_user_turns

    def compress(self, history, cfg, llm_client=None) -> list[dict]:
        if len(history) < 6:
            return list(history)

        # 读取配置（cfg 中的权重覆盖构造时的权重）
        if cfg.compress.selective_weights:
            weights = dict(self._weights)
            weights.update(cfg.compress.selective_weights)
        else:
            weights = self._weights
        min_user = cfg.compress.selective_min_user_turns

        # 目标：保留约一半 token（按消息数近似）
        target_keep = max(4, len(history) // 2)

        # ── 1. 标注每条消息的权重和位置分（越新越重要）──────────────────────
        scored = []
        total = len(history)
        for i, msg in enumerate(history):
            t = str(msg.get("_type", "__default__"))
            w = weights.get(t, weights["__default__"])
            # 位置加权：最近 25% 的消息额外 +0.2，保证最新上下文不丢
            recency_bonus = 0.2 if i >= total * 0.75 else 0.0
            scored.append((i, msg, w + recency_bonus))

        # ── 2. 确保 user_input 最少保留数 ────────────────────────────────────
        user_input_indices = [
            i for i, msg, _ in scored
            if str(msg.get("_type", "")) == "user_input"
        ]
        # 最近 min_user 条 user_input 强制保留
        protected = set(user_input_indices[-min_user:])

        # ── 3. 按权重排序，选出保留集合 ──────────────────────────────────────
        # 强制保留的 + 按权重降序填满 target_keep
        non_protected = [(i, msg, w) for i, msg, w in scored if i not in protected]
        non_protected.sort(key=lambda x: x[2], reverse=True)

        keep_extra = target_keep - len(protected)
        keep_indices = set(protected)
        for i, msg, w in non_protected[:max(0, keep_extra)]:
            keep_indices.add(i)

        # ── 4. 修复孤立结构（保证 turn 完整性）────────────────────────────────
        keep_indices = _fix_orphans(history, keep_indices)

        # ── 5. 构建压缩后的历史列表 ───────────────────────────────────────────
        # skill_context / reminder 只保留最新一条（去重）
        seen_dedup_types = set()
        dedup_types = {"skill_context", "reminder", "hook_context"}

        kept = []
        has_gap = False  # 是否有被跳过的消息

        for i, msg in enumerate(history):
            if i not in keep_indices:
                has_gap = True
                continue
            t = str(msg.get("_type", ""))
            if t in dedup_types:
                # 只保留第一次出现（最旧的），后续的在 keep 里的也要去重
                # 实际上我们想保留最新的：先收集所有 keep 里该类型的，只取最后一个
                pass  # 见下面的二次过滤
            kept.append((i, msg))

        # 对可去重类型：只保留下标最大（最新）的那条
        final = []
        latest_dedup: dict[str, int] = {}  # type -> 最大下标
        for idx, msg in kept:
            t = str(msg.get("_type", ""))
            if t in dedup_types:
                latest_dedup[t] = max(latest_dedup.get(t, -1), idx)

        for idx, msg in kept:
            t = str(msg.get("_type", ""))
            if t in dedup_types and latest_dedup.get(t, -1) != idx:
                has_gap = True
                continue
            final.append(msg)

        if not has_gap:
            return list(history)   # 没有实际压缩，返回原列表

        # ── 6. 构建摘要文字并加入占位符 ──────────────────────────────────────
        dropped_count = len(history) - len(final)
        dropped_user = sum(
            1 for i, msg in enumerate(history)
            if i not in keep_indices
            and str(msg.get("_type", "")) == "user_input"
        )
        summary_parts = [f"{dropped_count} messages dropped by selective compression"]
        if dropped_user:
            summary_parts.append(f"({dropped_user} user turns summarized)")

        # 前面插入占位符
        result = [
            make_compressed(),
            make_compact_summary(f"[Selective compression: {', '.join(summary_parts)}]"),
        ] + final

        return result

    @property
    def name(self) -> str:
        return "SelectiveStrategy"


def _fix_orphans(history: list[dict], keep_indices: set) -> set:
    """
    修复孤立结构，保证 turn 完整性：
    - 如果保留了 tool_result，必须同时保留其前面紧邻的 assistant_reply（含 tool_use）
    - 如果保留了 assistant_reply（含 tool_use），其后续的 tool_result 也保留
    - 操作：只添加缺失的依赖，不从 keep_indices 里删除
    """
    result = set(keep_indices)

    for i, msg in enumerate(history):
        if i not in result:
            continue
        t = str(msg.get("_type", ""))

        # tool_result 必须有前置的含 tool_use 的 assistant_reply
        if t == "tool_result":
            # 向前找最近的 assistant_reply
            for j in range(i - 1, -1, -1):
                prev = history[j]
                if prev.get("role") == "assistant":
                    content = prev.get("content", [])
                    has_tool_use = (
                        isinstance(content, list)
                        and any(b.get("type") == "tool_use" for b in content if isinstance(b, dict))
                    )
                    if has_tool_use:
                        result.add(j)
                    break

        # 含 tool_use 的 assistant_reply 必须有后续 tool_result
        if t == "assistant_reply":
            content = msg.get("content", [])
            has_tool_use = (
                isinstance(content, list)
                and any(b.get("type") == "tool_use" for b in content if isinstance(b, dict))
            )
            if has_tool_use and i + 1 < len(history):
                next_msg = history[i + 1]
                if str(next_msg.get("_type", "")) == "tool_result":
                    result.add(i + 1)

    return result


# 注册 SelectiveStrategy
_STRATEGY_REGISTRY["selective"] = SelectiveStrategy
