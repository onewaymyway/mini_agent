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
