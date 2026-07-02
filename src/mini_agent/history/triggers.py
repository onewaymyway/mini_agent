"""
history/triggers.py — Compact 触发器框架

设计参考 history/compression.py 的 CompressionStrategy 模式：
  - 每个触发条件是独立的类，实现 should_trigger() 接口
  - 每个触发器都有自己的开关（cfg.compress.xxx_enabled），默认关闭，
    不影响现有行为
  - Agent 持有一个 CompositeTrigger（多个触发器的 OR 组合），
    每轮循环调用一次 check()，取最高优先级命中的结果
  - 新增触发器只需继承 CompactTrigger + 在 build_default_triggers() 里注册，
    无需改 Agent 主循环逻辑

内置触发器：
  TokenThresholdTrigger    — token 占用率超过阈值（现有逻辑，硬约束，最高优先级）
  TurnCountTrigger         — 距上次 compact 满 N 轮
  ToolCallCountTrigger     — 距上次 compact 累计 N 次工具调用
  RedundancyTrigger        — tool_result 占比过高 / 信息冗余
  TopicShiftTrigger        — 话题切换检测（heuristic / llm 两档）

优先级（数值越大越优先，多个同时命中时取最高者）：
  token_threshold      100  （硬约束，必须压缩，无视冷却时间）
  topic_shift           60  （天然的压缩边界，效果最好）
  redundancy             40
  tool_call_count        30
  turn_count              20
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from mini_agent.history.entry import is_turn_boundary, is_real_user_input

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from mini_agent.llm.base import LLMClient


# ════════════════════════════════════════════════════════════════════════════════
# 触发上下文 / 结果
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class TriggerContext:
    """判断是否需要 compact 所需的只读上下文。"""
    history: list                      # active history（含 _type）
    budget_pct: float                  # 当前 token 占用率（0~1），未启用估算时为 0
    turns: int                         # 累计对话轮数（stats.turns）
    tool_calls: int                    # 累计工具调用次数（stats.tool_calls）
    last_compact_turns: int            # 上次 compact 时的 turns 快照
    last_compact_tool_calls: int       # 上次 compact 时的 tool_calls 快照
    turns_since_last_compact: int      # 距上次 compact 经过的轮数（用于冷却判断）
    llm_client: Optional["LLMClient"] = None


@dataclass
class TriggerResult:
    triggered: bool
    reason: str = ""                   # 机器可读标识，写入 raw_history 的 trigger_reason
    message: str = ""                  # 人类可读说明，用于提示/日志
    suggested_strategy: Optional[str] = None   # 建议使用的压缩策略，None=使用 cfg 默认策略
    priority: int = 0
    bypass_cooldown: bool = False      # 是否无视冷却时间（仅 token 硬阈值应设 True）


_NOT_TRIGGERED = TriggerResult(triggered=False)


# ════════════════════════════════════════════════════════════════════════════════
# 抽象基类
# ════════════════════════════════════════════════════════════════════════════════

class CompactTrigger:
    """Compact 触发器基类。子类实现 should_trigger()。"""

    #: 用于日志/统计的机器可读标识
    reason_key: str = "unknown"
    #: 多触发器同时命中时的优先级，数值越大越优先
    priority: int = 0

    def is_enabled(self, cfg: "AppConfig") -> bool:
        """子类应覆盖：读取对应的开关字段。默认返回 True（无独立开关的触发器）。"""
        return True

    def should_trigger(self, ctx: TriggerContext, cfg: "AppConfig") -> TriggerResult:
        raise NotImplementedError

    def check(self, ctx: TriggerContext, cfg: "AppConfig") -> TriggerResult:
        """外部统一入口：先查开关，再判断。"""
        if not self.is_enabled(cfg):
            return _NOT_TRIGGERED
        return self.should_trigger(ctx, cfg)


# ════════════════════════════════════════════════════════════════════════════════
# 内置触发器
# ════════════════════════════════════════════════════════════════════════════════

class TokenThresholdTrigger(CompactTrigger):
    """[现有逻辑] token 占用率超过阈值。硬约束，无视冷却时间。"""
    reason_key = "token_threshold"
    priority = 100

    def is_enabled(self, cfg: "AppConfig") -> bool:
        return cfg.compress.enabled

    def should_trigger(self, ctx: TriggerContext, cfg: "AppConfig") -> TriggerResult:
        if ctx.budget_pct >= cfg.compress.threshold:
            return TriggerResult(
                triggered=True,
                reason=self.reason_key,
                message=f"token 占用率 {ctx.budget_pct:.0%} 达到阈值 {cfg.compress.threshold:.0%}",
                suggested_strategy=None,   # 使用 cfg.compress.strategy
                priority=self.priority,
                bypass_cooldown=True,
            )
        return _NOT_TRIGGERED


class TurnCountTrigger(CompactTrigger):
    """距上次 compact 满 N 轮自动触发（常规维护性压缩）。"""
    reason_key = "turn_count"
    priority = 20

    def is_enabled(self, cfg: "AppConfig") -> bool:
        return cfg.compress.turn_count_trigger_enabled

    def should_trigger(self, ctx: TriggerContext, cfg: "AppConfig") -> TriggerResult:
        delta = ctx.turns - ctx.last_compact_turns
        if delta >= cfg.compress.max_turns_before_compact:
            return TriggerResult(
                triggered=True,
                reason=self.reason_key,
                message=f"距上次 compact 已进行 {delta} 轮（阈值 {cfg.compress.max_turns_before_compact}）",
                suggested_strategy="selective",
                priority=self.priority,
            )
        return _NOT_TRIGGERED


class ToolCallCountTrigger(CompactTrigger):
    """距上次 compact 累计 N 次工具调用自动触发。"""
    reason_key = "tool_call_count"
    priority = 30

    def is_enabled(self, cfg: "AppConfig") -> bool:
        return cfg.compress.tool_call_count_trigger_enabled

    def should_trigger(self, ctx: TriggerContext, cfg: "AppConfig") -> TriggerResult:
        delta = ctx.tool_calls - ctx.last_compact_tool_calls
        if delta >= cfg.compress.max_tool_calls_before_compact:
            return TriggerResult(
                triggered=True,
                reason=self.reason_key,
                message=f"距上次 compact 已累计 {delta} 次工具调用（阈值 {cfg.compress.max_tool_calls_before_compact}）",
                suggested_strategy="selective",
                priority=self.priority,
            )
        return _NOT_TRIGGERED


class RedundancyTrigger(CompactTrigger):
    """
    冗余信息检测：tool_result 占比过高，说明历史里堆积了大量低价值工具输出。

    复用 SelectiveStrategy 已有的 _type 权重体系逻辑，只做占比统计，
    不重复实现压缩本身。
    """
    reason_key = "redundancy"
    priority = 40

    def is_enabled(self, cfg: "AppConfig") -> bool:
        return cfg.compress.redundancy_detection_enabled

    def should_trigger(self, ctx: TriggerContext, cfg: "AppConfig") -> TriggerResult:
        history = ctx.history
        if len(history) < 6:
            return _NOT_TRIGGERED

        tool_result_count = sum(
            1 for m in history if str(m.get("_type", "")) == "tool_result"
        )
        ratio = tool_result_count / len(history)
        if ratio >= cfg.compress.redundancy_tool_result_ratio:
            return TriggerResult(
                triggered=True,
                reason=self.reason_key,
                message=f"tool_result 占比 {ratio:.0%} 超过阈值 {cfg.compress.redundancy_tool_result_ratio:.0%}，历史信息冗余",
                suggested_strategy="selective",
                priority=self.priority,
            )
        return _NOT_TRIGGERED


class TopicShiftTrigger(CompactTrigger):
    """
    话题切换检测，两档实现：
      heuristic —— 关键词重合度 + 话题切换语关键词，无额外 LLM 调用
      llm       —— heuristic 命中疑似切换后，再用一次小模型调用二次确认

    话题切换是天然的压缩边界，命中后建议使用 llm_summary 策略生成
    干净的"旧话题收尾摘要"。
    """
    reason_key = "topic_shift"
    priority = 60

    # 中英文均覆盖的话题切换语关键词表
    _SHIFT_PHRASES = [
        "另外", "换个话题", "换个话题吧", "对了", "顺便问一下", "顺便问下",
        "先不说这个", "先不管这个", "先放一放", "新的任务", "新任务",
        "帮我看看另一个", "帮我处理另一个", "另一个项目", "换一个项目",
        "by the way", "btw", "unrelated", "different topic", "switching topics",
        "new task", "on another note", "separately",
    ]

    def is_enabled(self, cfg: "AppConfig") -> bool:
        return cfg.compress.topic_shift_detection in ("heuristic", "llm")

    def should_trigger(self, ctx: TriggerContext, cfg: "AppConfig") -> TriggerResult:
        history = ctx.history
        user_indices = [i for i, m in enumerate(history) if is_turn_boundary(m)]
        if len(user_indices) < 2:
            return _NOT_TRIGGERED

        prev_idx, cur_idx = user_indices[-2], user_indices[-1]
        prev_text = _extract_text(history[prev_idx])
        cur_text = _extract_text(history[cur_idx])
        if not prev_text or not cur_text:
            return _NOT_TRIGGERED

        heuristic_hit, heuristic_detail = self._heuristic_check(
            prev_text, cur_text, cfg.compress.topic_shift_keyword_overlap_threshold
        )
        if not heuristic_hit:
            return _NOT_TRIGGERED

        if cfg.compress.topic_shift_detection == "heuristic":
            return TriggerResult(
                triggered=True,
                reason="topic_shift_heuristic",
                message=f"启发式检测到话题切换：{heuristic_detail}",
                suggested_strategy="llm_summary",
                priority=self.priority,
            )

        # ── llm 档：二次确认 ────────────────────────────────────────────────
        if ctx.llm_client is None:
            # 没有可用 llm_client，降级为 heuristic 结果
            return TriggerResult(
                triggered=True,
                reason="topic_shift_heuristic",
                message=f"启发式检测到话题切换（llm 档降级，无可用 llm_client）：{heuristic_detail}",
                suggested_strategy="llm_summary",
                priority=self.priority,
            )

        confirmed, llm_detail = self._llm_confirm(prev_text, cur_text, ctx.llm_client, cfg)
        if confirmed:
            return TriggerResult(
                triggered=True,
                reason="topic_shift_llm",
                message=f"LLM 确认话题切换：{llm_detail}",
                suggested_strategy="llm_summary",
                priority=self.priority,
            )
        return _NOT_TRIGGERED

    @staticmethod
    def _heuristic_check(prev_text: str, cur_text: str, overlap_threshold: float):
        """返回 (是否疑似切换, 说明文字)。"""
        # 信号 1：话题切换语关键词命中
        lowered = cur_text.lower()
        for phrase in TopicShiftTrigger._SHIFT_PHRASES:
            if phrase in cur_text or phrase in lowered:
                return True, f"检测到切换语「{phrase}」"

        # 信号 2：关键词重合度（简单分词，中英文粗略切分）
        prev_kw = _simple_keywords(prev_text)
        cur_kw = _simple_keywords(cur_text)
        if not prev_kw or not cur_kw:
            return False, ""
        overlap = len(prev_kw & cur_kw) / max(1, len(prev_kw | cur_kw))
        if overlap < overlap_threshold:
            return True, f"关键词重合度 {overlap:.0%} 低于阈值 {overlap_threshold:.0%}"

        return False, ""

    @staticmethod
    def _llm_confirm(prev_text: str, cur_text: str, llm_client, cfg) -> tuple:
        """用一次简短 LLM 调用二次确认话题是否真的切换。失败时保守返回 False（不触发）。"""
        try:
            prompt = (
                "判断下面两个用户请求是否属于同一个任务/话题的延续。"
                "只回答一个词：yes（是同一话题延续）或 no（话题已切换）。\n\n"
                f"上一个请求：{prev_text[:300]}\n"
                f"当前请求：{cur_text[:300]}"
            )
            response = llm_client.chat_with_retry(
                messages=[{"role": "user", "content": prompt}],
                system="你是一个简洁的判定器，只输出 yes 或 no，不要输出其他内容。",
                tools=[],
                max_retries=2,
            )
            answer = (response.text or "").strip().lower()
            is_shift = answer.startswith("no")
            return is_shift, f"模型回答: {answer[:20]}"
        except Exception as e:
            return False, f"LLM 判定失败，跳过本次检测: {e}"


# ════════════════════════════════════════════════════════════════════════════════
# 组合触发器
# ════════════════════════════════════════════════════════════════════════════════

class CompositeTrigger:
    """
    多个 CompactTrigger 的 OR 组合。

    check() 依次调用所有子触发器，命中多个时取 priority 最高的一个，
    避免同一轮内被多次触发导致重复 compact。
    """

    def __init__(self, triggers: Optional[list] = None) -> None:
        self._triggers = triggers if triggers is not None else build_default_triggers()

    def check(self, ctx: TriggerContext, cfg: "AppConfig") -> TriggerResult:
        # ── 冷却期：非硬约束触发器在冷却期内不生效 ──────────────────────────
        in_cooldown = ctx.turns_since_last_compact < cfg.compress.compact_cooldown_turns

        best: TriggerResult = _NOT_TRIGGERED
        for trigger in self._triggers:
            result = trigger.check(ctx, cfg)
            if not result.triggered:
                continue
            if in_cooldown and not result.bypass_cooldown:
                continue
            if result.priority > best.priority:
                best = result
        return best


def build_default_triggers() -> list:
    return [
        TokenThresholdTrigger(),
        TopicShiftTrigger(),
        RedundancyTrigger(),
        ToolCallCountTrigger(),
        TurnCountTrigger(),
    ]


# ════════════════════════════════════════════════════════════════════════════════
# 辅助函数
# ════════════════════════════════════════════════════════════════════════════════

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_WORD_RE = re.compile(r"[A-Za-z0-9]+")

_STOPWORDS = {
    "的", "了", "是", "我", "你", "他", "她", "它", "在", "和", "与", "就",
    "都", "而", "及", "或", "一个", "这个", "那个", "帮我", "请", "一下",
    "the", "a", "an", "is", "are", "to", "of", "in", "on", "for", "and",
    "or", "please", "help", "me", "with", "this", "that",
}


def _simple_keywords(text: str) -> set:
    """粗略分词：英文按单词切，中文按单字符切（去掉停用词），用于关键词重合度估算。
    不追求分词准确性，只作为轻量启发式信号，不引入分词依赖。
    """
    words = {w.lower() for w in _WORD_RE.findall(text)}
    cjk_chars = set(_CJK_RE.findall(text))
    keywords = (words | cjk_chars) - _STOPWORDS
    return keywords


def _extract_text(msg: dict) -> str:
    """从历史条目里提取纯文本内容（user_input 通常是字符串，兜底处理 list content）。"""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return " ".join(parts)
    return ""
