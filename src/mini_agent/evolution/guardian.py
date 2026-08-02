"""
evolution/guardian.py — [daemon_autonomous_state_recovery_plan.md 阶段四 / P2]
看护模式 GuardianRunner：不依赖 GoalSpec/GoalJudge（不要求验收标准）的轻量
监督层，用于 `autonomous` Objective 执行过程中的"卡住检测 → 恢复 → 必要时
终止"。

与 goal_mode 的关系：复用 `role_agents/stuck_detector.py` 里已经和"验收判定"
解耦的 `StuckDetector`（`goal_mode/runner.py::GoalRunner` 和
`agent/role_judge.py::_maybe_run_turn_judge` 已经在用同一个类）——这个模块
不重新发明相似度比较/恢复额度计数逻辑，只是把它包成一个不需要 GoalJudge
介入的、按"客观终止条件"收尾的轻量壳。

与 `evolution/cron_job_executor.py` 的关系：cron 任务已经在 `run_job()` 内联
直接使用 `StuckDetector` 完成了同等效果的卡住检测（见该文件 `detector =
StuckDetector(...)` 及后续 `if signal is StuckSignal.GIVE_UP` 分支），不需要
迁移到本模块——本模块主要补给此前完全没有跨 step 卡住检测能力的
`autonomous` Objective 执行路径（`evolution/objective_executor.py`）。

设计要点：
  - 不做 DONE/CONTINUE 裁定（那是 GoalJudge 的职责，需要验收标准
    GoalSpec）；只回答"最近几步是不是在原地打转"这一个问题。
  - 终止条件是客观的："执行完预定步骤"（由调用方自己判断，不归 Guardian
    管）、"达到最大轮次"（`should_terminate_by_rounds()`）、"多次恢复无效"
    （`StuckSignal.GIVE_UP`）——不涉及任何语义/质量判断。
  - 每个 Objective execution 一个独立的 `GuardianRunner` 实例（互不共享
    `StuckDetector`/`ProgressTracker`/dead_ends 状态），调用方（如
    `ObjectiveExecutor`）负责按 execution_id 维护这个映射关系。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from mini_agent.role_agents.stuck_detector import ProgressTracker, StuckDetector, StuckSignal

__all__ = ["GuardianRunner", "StuckSignal"]


@dataclass
class GuardianRunner:
    """一个 Objective execution 专属的轻量看护实例。

    用法（每完成一个 step 调用一次）：
        signal = guardian.observe_step(step_idx, step.result_summary)
        if signal is StuckSignal.GIVE_UP:
            ...  # 多次恢复无效，调用方应尝试重新分解 / 判定 Objective failed
        elif signal is StuckSignal.RECOVER:
            ...  # 判定卡住但还有恢复额度，调用方可以给下一步注入"换个思路"
                 # 的 guidance，不需要终止执行
    """

    max_rounds: int = 20
    similarity_threshold: float = 0.92
    consecutive_limit: int = 3
    max_recoveries: int = 2

    _round: int = field(default=0, init=False, repr=False)
    _stuck: StuckDetector = field(init=False, repr=False)
    _progress: ProgressTracker = field(init=False, repr=False)
    _dead_ends: list = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._stuck = StuckDetector(
            similarity_threshold=self.similarity_threshold,
            consecutive_limit=self.consecutive_limit,
            max_recoveries=self.max_recoveries,
        )
        self._progress = ProgressTracker()

    # ── 核心观察 API ─────────────────────────────────────────────────────

    def observe_step(
        self, step_idx: int, result_summary: str, progress_score: Optional[float] = None,
    ) -> StuckSignal:
        """观察一个已完成 step 的结果摘要，返回 NONE/RECOVER/GIVE_UP。

        `progress_score` 可选：调用方如果有办法给出一个粗粒度的"这一步比
        上一步推进了多少"的分数（0~1），会额外喂给内部的 `ProgressTracker`
        识别"平缓但非零"的伪进展趋势；不提供时只依赖文本相似度判定，仍然
        能覆盖"原地打转"这个最常见的场景。
        """
        self._round += 1
        signal = self._stuck.observe(result_summary or "")
        if signal is not StuckSignal.NONE:
            return signal

        if progress_score is not None:
            if self._progress.observe(float(progress_score)):
                return self._stuck.trigger_recovery()

        return StuckSignal.NONE

    def should_terminate_by_rounds(self) -> bool:
        """客观终止条件之一：达到最大轮次上限（不代表任务失败，调用方可以
        据此选择"判失败"或"标记为需要人工介入"，Guardian 本身不做这个判断，
        只负责报告"到点了"）。`max_rounds <= 0` 表示不限制。"""
        return self.max_rounds > 0 and self._round >= self.max_rounds

    # ── dead-end 记录（与 goal_mode 同一套"已验证无效路径"哲学）────────────

    def record_dead_end(self, step_idx: int, reason: str) -> None:
        reason = (reason or "").strip()
        if not reason:
            return
        if any(_is_near_duplicate(reason, d["reason"]) for d in self._dead_ends):
            return
        self._dead_ends.append({"step_idx": step_idx, "reason": reason})

    def render_dead_ends_block(self) -> str:
        if not self._dead_ends:
            return ""
        lines = [f"- 步骤{d['step_idx']+1}: {d['reason']}" for d in self._dead_ends]
        return "[已验证无效的思路，不要重复尝试]\n" + "\n".join(lines)

    # ── 状态查询 / 重置 ──────────────────────────────────────────────────

    @property
    def round_count(self) -> int:
        return self._round

    @property
    def recoveries_used(self) -> int:
        return self._stuck.recoveries_used

    def reset(self) -> None:
        self._round = 0
        self._stuck.reset()
        self._progress.reset()
        self._dead_ends = []


def _is_near_duplicate(a: str, b: str, threshold: float = 0.85) -> bool:
    import difflib

    if not a or not b:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= threshold
