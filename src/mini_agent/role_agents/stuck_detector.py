"""
role_agents/stuck_detector.py — 通用"连续输出高度相似 → 判定卡住"检测器

背景：goal_mode/runner.py（GoalRunner）和 agent/role_judge.py
（_maybe_run_turn_judge）此前各自手写了一份逻辑完全一致、但代码不共享的
"卡住检测"：连续 N 轮输出/反馈的相似度超过阈值就判定为"卡住"，给一次
"compact + 换角度提示"的恢复机会，恢复额度耗尽后再次卡住就终止/交还用户。

本模块把这份逻辑抽成一个不感知调用方语义的纯工具类：GoalRunner 拿
"judge 反馈文本"做比较，TurnJudge 拿"主 Agent 输出"做比较，`StuckDetector`
对输入内容不做任何假设，两边可以各自传入合适的字符串。
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from enum import Enum


class StuckSignal(str, Enum):
    NONE = "none"           # 无问题，正常继续
    RECOVER = "recover"     # 判定卡住，且还有恢复额度 → 调用方应 compact + 换角度提示
    GIVE_UP = "give_up"     # 判定卡住，且恢复额度已耗尽 → 调用方应终止/交还用户


@dataclass
class StuckDetector:
    """连续输出相似度卡死检测器。

    用法（每次拿到主 Agent 一轮输出后调用一次）：
        signal = detector.observe(assistant_output)
        if signal is StuckSignal.RECOVER:
            ...  # compact + 换角度提示，detector 内部已经计数
        elif signal is StuckSignal.GIVE_UP:
            ...  # 终止 / 交还用户，调用方负责 reset()
    """
    similarity_threshold: float = 0.92
    consecutive_limit: int = 3          # 连续多少轮相似判定为"卡住"
    max_recoveries: int = 2             # 恢复额度上限

    _prior_output: str | None = field(default=None, repr=False)
    _consecutive_same: int = 0
    _recoveries_used: int = 0

    def observe(self, output: str) -> StuckSignal:
        if self.consecutive_limit <= 0:
            self._prior_output = output
            return StuckSignal.NONE

        if self._prior_output is not None:
            ratio = difflib.SequenceMatcher(None, self._prior_output, output).ratio()
            if ratio >= self.similarity_threshold:
                self._consecutive_same += 1
            else:
                # 出现真实进展，重置卡住计数和恢复额度
                self._consecutive_same = 0
                self._recoveries_used = 0
        self._prior_output = output

        if self._consecutive_same >= (self.consecutive_limit - 1):
            if self._recoveries_used >= self.max_recoveries:
                return StuckSignal.GIVE_UP
            self._recoveries_used += 1
            self._consecutive_same = 0
            return StuckSignal.RECOVER

        return StuckSignal.NONE

    def observe_signal(self, *, is_same: bool) -> StuckSignal:
        """[next_doc/goal_mode_completion_improvement_plan.md 改造项一]

        跳过内部的 difflib 文本比较，直接接受调用方已经判断好的"本轮是否等同
        于卡住（没有实质进展）"结论，复用既有的连续计数 / 恢复额度 / GIVE_UP
        逻辑。用于 GoalRunner 在 `progress_judge_mode="llm"` 下，把 GoalJudge
        结构化输出的 `progress` 字段（SUBSTANTIVE_ADVANCE /
        SAME_APPROACH_NO_GAIN / REGRESSED）转换为 `is_same` 布尔值后调用。

        与 `observe(text)` 完全独立：TurnJudge 等仍基于文本相似度的调用方
        继续使用 `observe()`，不受影响；同一个 StuckDetector 实例不应该在
        同一条调用链路里混用这两个方法。
        """
        if self.consecutive_limit <= 0:
            return StuckSignal.NONE

        if is_same:
            self._consecutive_same += 1
        else:
            # 出现真实进展，重置卡住计数和恢复额度（与 observe() 语义一致）
            self._consecutive_same = 0
            self._recoveries_used = 0

        if self._consecutive_same >= (self.consecutive_limit - 1):
            if self._recoveries_used >= self.max_recoveries:
                return StuckSignal.GIVE_UP
            self._recoveries_used += 1
            self._consecutive_same = 0
            return StuckSignal.RECOVER

        return StuckSignal.NONE

    def trigger_recovery(self) -> StuckSignal:
        """[goal_mode_stuck_compact_plan.md §3.2] 供外部信号源（如 ProgressTracker
        判定的"伪进展"）复用同一份恢复额度计数，而不必先经过 observe()/observe_signal()
        的文本或语义相似度比较。语义上等价于"这一轮被判定为需要恢复"，直接走
        现有的"消耗一次恢复额度，额度耗尽则 GIVE_UP"逻辑，并重置连续计数（下一轮
        重新开始累计）。

        与 observe()/observe_signal() 共享同一套 `_recoveries_used` 计数器，因此
        两种触发来源（规则相似度 / LLM 语义判断 / 伪进展趋势）会共同消耗同一份
        `max_recoveries` 额度，不会因为触发路径不同而变相获得双倍额度。
        """
        if self._recoveries_used >= self.max_recoveries:
            return StuckSignal.GIVE_UP
        self._recoveries_used += 1
        self._consecutive_same = 0
        return StuckSignal.RECOVER

    def reset(self) -> None:
        self._prior_output = None
        self._consecutive_same = 0
        self._recoveries_used = 0

    @property
    def recoveries_used(self) -> int:
        return self._recoveries_used

    @property
    def consecutive_same(self) -> int:
        return self._consecutive_same

    @property
    def prior_output(self) -> str | None:
        return self._prior_output

    # ── 落盘 / 恢复（供 GoalState 等需要持久化内部状态的调用方使用）───────────

    def to_dict(self) -> dict:
        return {
            "consecutive_same": self._consecutive_same,
            "recoveries_used": self._recoveries_used,
        }

    def load_counts(self, *, consecutive_same: int = 0, recoveries_used: int = 0) -> None:
        """从落盘状态恢复计数（不恢复 prior_output——落盘状态里不保存上一轮
        完整输出文本，恢复后的第一次 observe() 只会记录基准，不会误判）。"""
        self._consecutive_same = int(consecutive_same)
        self._recoveries_used = int(recoveries_used)


@dataclass
class ProgressTracker:
    """[goal_mode_stuck_compact_plan.md §3.2] 伪进展趋势识别。

    `StuckDetector` 只回答"当前是否等同于上一次"这个二元问题，识别不了
    "每轮都有一点点进展，但累积起来毫无实质意义"这种模式（比如连续 N 轮
    进展分数都非负，但从来没有真正积累出实质推进）。`ProgressTracker`
    在 `StuckDetector` 之外新增一层，跟踪最近 `window` 轮的进展分数序列
    （见 `GoalRunner._compute_progress_score`），用"早期均值 vs 后期均值"
    的粗粒度趋势估计识别这种"平缓但非零"的伪进展。

    与 `StuckDetector` 是互补关系，不是替代：`StuckDetector` 抓"完全没有
    变化/退步"的情况，`ProgressTracker` 抓"看起来一直有点变化，但趋势没有
    实质抬升"的情况——两者任一个判定为需要干预，调用方都应该触发恢复流程
    （见 `GoalRunner._check_stuck`）。
    """
    window: int = 5
    stagnation_score_threshold: float = 0.15   # 早期均值/后期均值之差低于此值视为"平缓"
    max_score_cap: float = 0.5                 # 窗口内最高分仍低于此值，才可能是"伪进展"

    _scores: list = field(default_factory=list)

    def observe(self, score: float) -> bool:
        """返回 True 表示"检测到伪进展趋势"，调用方应据此触发和 stuck 同等级别
        的干预（见 `StuckDetector.trigger_recovery`）。

        窗口未填满（不足 `window` 个样本）时始终返回 False——数据点太少时
        任何趋势估计都不可靠，宁可漏检也不要在早期就误判。
        """
        self._scores.append(score)
        if len(self._scores) > self.window:
            self._scores = self._scores[-self.window:]
        if len(self._scores) < self.window:
            return False

        half = self.window // 2
        early_avg = sum(self._scores[:half]) / half
        late_avg = sum(self._scores[half:]) / (self.window - half)
        # checklist 通过数长期没有实质累积增长，即便主观 progress 一直非负，
        # 也判定为伪进展；窗口内出现过明显高分（>= max_score_cap）说明确实有
        # 过实质性推进，不应该被判定为"伪进展"。
        return (late_avg - early_avg) < self.stagnation_score_threshold and max(self._scores) < self.max_score_cap

    def reset(self) -> None:
        self._scores = []

    # ── 落盘 / 恢复：ProgressTracker 本身不需要独立持久化字段——它的全部
    # 状态就是"最近 window 个进展分数"，而这份数据已经作为
    # `GoalState.progress_scores`（§3.1）持久化。GoalRunner 恢复时直接用
    # `replay()` 把落盘的分数序列末尾 `window` 个重新喂给一个新实例即可，
    # 不需要额外的落盘字段，避免数据重复维护。
    def replay(self, scores: list) -> None:
        """用已有的历史分数序列（如 GoalState.progress_scores）重建窗口状态，
        不触发任何返回值判断（仅用于恢复内部状态，不代表"这些历史分数刚刚
        被观察到"）。"""
        self._scores = list(scores[-self.window:]) if scores else []
