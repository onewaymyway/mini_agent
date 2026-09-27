"""core/simulation.py — Phase 7 Sprint 7-1：SimulationScenario / SimulationResult。

对应 `next_doc/refactor_plan/08-phase7-decision-simulation-sprint-plan.md`
Sprint 7-1 任务表第一项：定义 `SimulationScenario(current_state, goal,
candidate_actions, constraints)`、`SimulationResult(possible_futures,
risks, tradeoffs)`。

Phase 7 文档"目标与边界"明确写死了第一版的红线：**禁止**复杂因果树/
Counterfactual 推演，**禁止**数值化打分系统；只做"LLM scenario
generation + 规则约束 + 历史经验（Phase 3 Experience Retriever）"，
输出自然语言权衡描述。这两个 dataclass 的字段形状直接体现这条红线——
`SimulationResult` 里没有任何 score/confidence 数值字段，`tradeoffs`
是一段自由文本，不是可排序的数字。

与 `core/action.py::ActionSpec`/`ActionResult` 保持同一种极简风格：
`SimulationScenario`/`SimulationResult` 只是"一次 Simulation 的输入
输出协议"，不在这里重复设计追踪字段（`id`/`correlation_id` 等），
调用方（`simulation/engine.py`）自己在需要时生成。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.core.action import ActionSpec


@dataclass
class SimulationScenario:
    """一次 Simulation 的输入协议（原文 §11/§40）。

    字段：
      current_state      — 当前状态描述（通常来自 Phase 5
                            `GoalState.current_state`，或
                            `state_manager.get_state("world"/"self")`
                            的摘要）。
      goal                — 目标描述（`GoalState.goal_text`）。
      candidate_actions   — 候选 `ActionSpec` 列表（Sprint 7-1 任务表
                            要求"2-3 个，不是无限枚举"，本类不强制校验
                            数量——校验逻辑在
                            `simulation/engine.py::generate_candidate_actions()`
                            里，这里只是承载数据的容器）。
      constraints         — 约束条件列表（自由文本，例如
                            `GoalState.constraints`/`resources`），供
                            LLM 生成候选/权衡描述时参考，不做规则引擎
                            式的强制校验（对应 Phase 7 边界"不做复杂
                            算法"）。
    """

    current_state: str
    goal: str
    candidate_actions: "list[ActionSpec]" = field(default_factory=list)
    constraints: "list[str]" = field(default_factory=list)


@dataclass
class SimulationResult:
    """一次候选 Action 的 Simulation 输出（原文 §11 A/B/C 权衡示例）。

    字段：
      action             — 本次结果对应的候选 `ActionSpec`（任务表原
                           字段列表里没有这一项，但没有它就无法把
                           `list[SimulationResult]` 里的每一条对应回
                           是哪个候选——`cognition/decision.py::
                           DecisionEngine.select()` 需要靠它选出最终
                           `ActionSpec`，因此在任务表基础上补充这一个
                           字段，不影响另外三个字段的形状）。
      possible_futures   — 自然语言描述的"如果这么做，可能发生什么"
                           列表（不是分支概率树，只是几条平行的自然语言
                           描述，对应 Phase 7 边界"禁止复杂因果树"）。
      risks              — 自然语言描述的风险点列表。
      tradeoffs          — 一段自然语言的权衡总结（短期成本/长期影响，
                           对应原文 §11 的 A/B/C 权衡示例），**不是**
                           数值分数。
      experience_refs    — 本次描述实际引用的历史 `Experience.id` 列表
                           （可能为空——检索不到相关历史时如实留空，不
                           伪造引用）。用于验收标准"这段描述里确实引用
                           了 Phase 3 检索到的历史 Experience（不是纯
                           凭空生成）"的可核验证据，而不是只靠人工读
                           `tradeoffs` 文本判断有没有引用历史。
    """

    action: "ActionSpec"
    possible_futures: "list[str]" = field(default_factory=list)
    risks: "list[str]" = field(default_factory=list)
    tradeoffs: str = ""
    experience_refs: "list[str]" = field(default_factory=list)
