"""
goal_mode/executor.py — GoalStepExecutor：把"跑一步 goal 迭代"抽象成可替换的策略

当前只有 CoarseStepExecutor（粗粒度：每步调用一次完整的 agent.run_turn）。

为将来的细粒度版本预留的接口设计：
  细粒度版本会在 _agentic_loop 内部、每次工具调用后就有机会插入 Judge 判断，
  而不必等一次完整 run_turn 跑完。要做到这一点，只需要新增一个
  FineGrainedStepExecutor，实现同样的 GoalStepExecutor 接口，
  GoalRunner 的主循环完全不用改。

GoalStepResult 现在就把 tool_calls_made 等字段填上（即使粗粒度版本用不上），
方便以后细粒度版本按"工具调用次数"做中断判断，而不需要再改一次数据结构。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.agent import Agent


@dataclass
class GoalStepResult:
    """一次"goal 迭代步"的执行结果。"""
    output: str                          # 本步产出的文本（run_turn 返回值）
    hit_max_turns: bool = False          # 本步是否因撞到 cfg.max_turns 被截断（而非正常收尾）
    tool_calls_made: int = 0             # 本步内发生的工具调用次数（细粒度版本会用到，粗粒度版本尽力填充）
    turns_used: int = 0                  # 本步消耗的 stats.turns 增量


class GoalStepExecutor(ABC):
    """一次 goal 迭代步的执行策略接口。"""

    @abstractmethod
    def execute(self, agent: "Agent", prompt: str) -> GoalStepResult:
        """驱动主 Agent 执行一步，返回执行结果。

        Args:
            agent:  主 Agent 实例（持有历史、stats 等状态）
            prompt: 本步要发给主 Agent 的 user 消息（目标提醒 / 上一轮反馈 / 续跑指令）
        """
        ...


class CoarseStepExecutor(GoalStepExecutor):
    """粗粒度实现：直接调用 agent.run_turn(prompt) 跑完一整步。

    简单、复用度高，缺点是无法在过程中（比如工具调用到一半）就中断纠偏，
    只能等一整个 run_turn 跑完才能评审。
    """

    def execute(self, agent: "Agent", prompt: str) -> GoalStepResult:
        turns_before = agent.stats.turns
        tool_calls_before = agent.stats.tool_calls

        output = agent.run_turn(prompt)

        hit_max_turns = bool(getattr(agent, "last_turn_hit_max_turns", False))
        turns_used = agent.stats.turns - turns_before
        tool_calls_made = agent.stats.tool_calls - tool_calls_before

        return GoalStepResult(
            output=output,
            hit_max_turns=hit_max_turns,
            tool_calls_made=tool_calls_made,
            turns_used=turns_used,
        )
