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
    # [P1-B 触发信号强度叠加] 硬命中时该字段无实际用途（固定为 0.0，仅语义完整）；
    # 未命中时各触发器通过 intensity_hint() 单独提供"接近阈值程度"，不写入本字段。
    intensity: float = 0.0


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

    def intensity_hint(self, ctx: TriggerContext, cfg: "AppConfig") -> float:
        """[P1-B] 未硬命中时的"接近阈值程度"，0=完全不接近，1=达到自身阈值。
        默认返回 0（不参与强度叠加），子类可覆盖。开关关闭/触发器本身
        未启用时也应返回 0，避免污染叠加总和。"""
        return 0.0

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

    def intensity_hint(self, ctx: TriggerContext, cfg: "AppConfig") -> float:
        if not self.is_enabled(cfg):
            return 0.0
        delta = ctx.turns - ctx.last_compact_turns
        return min(1.0, delta / max(1, cfg.compress.max_turns_before_compact))


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

    def intensity_hint(self, ctx: TriggerContext, cfg: "AppConfig") -> float:
        if not self.is_enabled(cfg):
            return 0.0
        delta = ctx.tool_calls - ctx.last_compact_tool_calls
        return min(1.0, delta / max(1, cfg.compress.max_tool_calls_before_compact))


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

    def intensity_hint(self, ctx: TriggerContext, cfg: "AppConfig") -> float:
        if not self.is_enabled(cfg):
            return 0.0
        history = ctx.history
        if len(history) < 6:
            return 0.0
        tool_result_count = sum(
            1 for m in history if str(m.get("_type", "")) == "tool_result"
        )
        ratio = tool_result_count / len(history)
        return min(1.0, ratio / max(1e-6, cfg.compress.redundancy_tool_result_ratio))


class TopicShiftTrigger(CompactTrigger):
    """
    话题切换检测，两档实现：
      heuristic —— 关键词重合度 + 话题切换语关键词，无额外 LLM 调用
      llm       —— heuristic 命中疑似切换后，再用一次小模型调用二次确认

    话题切换是天然的压缩边界，命中后建议使用 compact_with_skills 策略（与手动
    /compact 完全一致：LLM 摘要 + skill 重附 + 压缩质量事后自检），而不是轻量的
    llm_summary 插件路径。
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

    # 续接/确认类短语：本质是"继续当前话题"，即使与上一条消息关键词重合度为 0
    # 也不应被判定为话题切换。必须是整句（去除首尾标点/空白后）完全匹配，
    # 避免误伤"继续帮我看看另一个项目"这类真正切换话题但带了"继续"二字的句子。
    _CONTINUATION_PHRASES = {
        "继续", "继续吧", "继续做", "继续干", "继续弄", "继续写", "继续改",
        "接着", "接着做", "接着写", "接着来", "接着弄", "往下", "往下走",
        "继续执行", "go on", "go ahead", "continue", "keep going",
        "keep going.", "proceed", "yes", "yes.", "yep", "ok", "ok.", "okay",
        "okay.", "好的", "好", "嗯", "嗯嗯", "可以", "行", "行的", "没问题",
    }

    # 当前输入过短时，关键词重合度天然趋近 0（分母很小/分子几乎不可能命中），
    # 不能作为话题切换信号，否则"继续"之类的短指令会被误判。
    _MIN_KEYWORDS_FOR_OVERLAP = 2

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
                # 与手动 /compact 使用完全一致的压缩实现（LLM 摘要 + skill 重附 +
                # 压缩质量事后自检），而不是走轻量的旧 llm_summary 插件路径。
                suggested_strategy="compact_with_skills",
                priority=self.priority,
            )

        # ── llm 档：二次确认 ────────────────────────────────────────────────
        if ctx.llm_client is None:
            # 没有可用 llm_client，降级为 heuristic 结果
            return TriggerResult(
                triggered=True,
                reason="topic_shift_heuristic",
                message=f"启发式检测到话题切换（llm 档降级，无可用 llm_client）：{heuristic_detail}",
                suggested_strategy="compact_with_skills",
                priority=self.priority,
            )

        confirmed, llm_detail = self._llm_confirm(prev_text, cur_text, ctx.llm_client, cfg)
        if confirmed:
            return TriggerResult(
                triggered=True,
                reason="topic_shift_llm",
                message=f"LLM 确认话题切换：{llm_detail}",
                suggested_strategy="compact_with_skills",
                priority=self.priority,
            )
        return _NOT_TRIGGERED

    @staticmethod
    def _heuristic_check(prev_text: str, cur_text: str, overlap_threshold: float):
        """返回 (是否疑似切换, 说明文字)。"""
        # 信号 0：续接/确认类短语白名单，直接豁免，不进入后续判断。
        # 例如"继续"“continue”这种回复，字面上和上一条消息几乎不可能有关键词
        # 重合，但语义上是延续当前话题，必须最先排除，优先级高于其他信号。
        stripped = cur_text.strip().strip("。.!！~～").lower()
        if stripped in TopicShiftTrigger._CONTINUATION_PHRASES:
            return False, ""
        # 信号 0b：以续接词开头的长句同样豁免。例如"继续想办法解决XXX问题"
        # 不会整句命中白名单（后面还带了具体任务描述），但开头的"继续/接着/
        # go on"等词已经明确表达"延续当前话题"的语义，不应仅因为后半句关键词
        # 和上一条消息重合度低就被判定为切换。只用真正的续接动词类短语做前缀
        # 匹配，纯确认词（yes/ok/好的等）太短太宽泛，不适合前缀豁免。
        for _prefix in ("继续", "接着", "往下", "go on", "go ahead", "continue", "keep going", "proceed"):
            if stripped.startswith(_prefix):
                return False, ""

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
        # 当前输入关键词过少（如短指令、确认语）时，重合度天然趋近 0，
        # 不具备判断力，跳过该信号，避免误判。
        if len(cur_kw) < TopicShiftTrigger._MIN_KEYWORDS_FOR_OVERLAP:
            return False, ""
        overlap = len(prev_kw & cur_kw) / max(1, len(prev_kw | cur_kw))
        if overlap < overlap_threshold:
            return True, f"关键词重合度 {overlap:.0%} 低于阈值 {overlap_threshold:.0%}"

        return False, ""

    @staticmethod
    def _llm_confirm(prev_text: str, cur_text: str, llm_client, cfg) -> tuple:
        """用一次简短 LLM 调用二次确认话题是否真的切换。失败时保守返回 False（不触发）。"""
        try:
            # 提供比单纯"上一句"更宽的窗口：小模型只看最近一句话时，很容易把
            # "继续处理同一个任务，但换了个说法/带了新的具体要求"误判为切换。
            # 把两句的截断长度适当放宽，并在 prompt 里显式提醒：只要是同一个
            # 任务/项目/问题下的后续步骤、补充要求、追问细节，都算延续，不算切换。
            prompt = (
                "判断【当前请求】是否是【上一个请求】所在任务/话题的延续。\n"
                "以下情况都应判定为「延续」（同一话题），即使措辞、关注点或\n"
                "具体要求发生了变化：\n"
                "  - 继续推进同一个任务的后续步骤；\n"
                "  - 针对同一个问题/模块/项目提出新的具体要求或修改意见；\n"
                "  - 补充说明、追问细节、纠正之前的理解。\n"
                "只有当当前请求明显在处理一个不相关的新任务/新项目/新话题时，\n"
                "才判定为「切换」。\n\n"
                "只回答一个词：yes（延续）或 no（切换）。\n\n"
                f"上一个请求：{prev_text[:500]}\n"
                f"当前请求：{cur_text[:500]}"
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
# 安全点判定（P1-A，对应 compact_mechanism_improvement_plan.md 第 3 节）
# ════════════════════════════════════════════════════════════════════════════════

#: 安全点判定时回溯检查的最近消息条数上限（超过这个窗口还没遇到 turn 边界，
#: 说明工具调用链条较长，只看窗口内出现的工具名即可，无需回溯全部历史）
_SAFE_POINT_LOOKBACK = 8


class SafePointGate:
    """
    判断当前是否处于"安全点"（可以被 compact 打断而不破坏执行连续性）。

    安全点定义：
      - history 为空，或最后一条消息本身就是 turn 边界（真实用户输入）——
        意味着还没开始新一轮的多步骤执行，此时打断没有代价；
      - 或者：从末尾往前回溯（直到遇到 turn 边界或超出回溯窗口）出现的工具调用
        全部是"只读探索型"（不在 permissions.py::_RISKY_TOOLS 里）。

    只要回溯窗口内出现过任意一次 _RISKY_TOOLS 命中的工具调用（bash / 写文件 /
    patch 等有副作用的操作），就认为当前处于一次不适合被打断的执行序列中间。
    """

    def is_safe_point(self, ctx: "TriggerContext") -> bool:
        history = ctx.history
        if not history:
            return True
        if is_turn_boundary(history[-1]):
            return True

        try:
            from mini_agent.permissions import _RISKY_TOOLS
        except Exception:
            # permissions 模块不可用时保守放行（不新增额外的打断限制）
            return True

        window = history[-_SAFE_POINT_LOOKBACK:]
        for msg in reversed(window):
            if is_turn_boundary(msg):
                break  # 回溯到上一个 turn 边界，窗口内检查结束
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    if block.get("name") in _RISKY_TOOLS:
                        return False
        return True


# ════════════════════════════════════════════════════════════════════════════════
# 组合触发器
# ════════════════════════════════════════════════════════════════════════════════

class CompositeTrigger:
    """
    多个 CompactTrigger 的 OR 组合。

    check() 依次调用所有子触发器，命中多个时取 priority 最高的一个，
    避免同一轮内被多次触发导致重复 compact。

    [P1-B] 若没有触发器硬命中，且 `composite_intensity_enabled` 开启，
    会额外用各触发器的 intensity_hint() 求和，达到阈值也视为命中一次
    `composite_intensity` 软触发。

    [P1-A] 若命中结果（硬命中或软触发信号叠加）落在"不安全点"（详见
    SafePointGate），且 `safe_point_gating_enabled` 开启，会把这次命中
    挂起（不立即执行 compact），保存在实例的 `_pending` 字段里，下一次
    `check()` 调用时若已到达安全点则直接放行挂起的结果——不落盘、不需要
    额外改动调用方（Agent 只需要像以前一样持有同一个 CompositeTrigger 实例）。
    token 阈值等 `bypass_cooldown=True` 的硬约束不受安全点限制，无条件立即执行。
    """

    def __init__(
        self,
        triggers: Optional[list] = None,
        safe_point_gate: Optional[SafePointGate] = None,
    ) -> None:
        self._triggers = triggers if triggers is not None else build_default_triggers()
        self._safe_point_gate = safe_point_gate if safe_point_gate is not None else SafePointGate()
        # 挂起的触发结果（P1-A），非空时表示上一次命中因不在安全点而被延迟执行
        self._pending: Optional[TriggerResult] = None

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

        # ── P1-B：硬触发都未命中时，尝试软触发信号强度叠加 ───────────────────
        if (
            not best.triggered
            and not in_cooldown
            and getattr(cfg.compress, "composite_intensity_enabled", False)
        ):
            total_intensity = sum(t.intensity_hint(ctx, cfg) for t in self._triggers)
            threshold = getattr(cfg.compress, "composite_intensity_threshold", 1.2)
            if total_intensity >= threshold:
                best = TriggerResult(
                    triggered=True,
                    reason="composite_intensity",
                    message=(
                        f"多个弱触发信号叠加强度 {total_intensity:.2f} "
                        f"达到阈值 {threshold:.2f}"
                    ),
                    suggested_strategy="selective",
                    priority=35,
                )

        # ── P1-A：安全点判定 ────────────────────────────────────────────────
        if best.triggered:
            if best.bypass_cooldown or not getattr(cfg.compress, "safe_point_gating_enabled", False):
                self._pending = None
                return best
            if self._safe_point_gate.is_safe_point(ctx):
                self._pending = None
                return best
            # 挂起：本轮不打断执行，等下次到达安全点时再放行
            self._pending = best
            return _NOT_TRIGGERED

        # 本轮没有新的命中；若存在挂起项且现在已到安全点，放行挂起的结果
        if self._pending is not None and self._safe_point_gate.is_safe_point(ctx):
            pending = self._pending
            self._pending = None
            return pending
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
