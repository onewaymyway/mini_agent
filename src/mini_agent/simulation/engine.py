"""simulation/engine.py — Phase 7 Sprint 7-1：Candidate Actions 生成 +
最小 Simulation 引擎。

对应 `next_doc/refactor_plan/08-phase7-decision-simulation-sprint-plan.md`
Sprint 7-1 任务表第二、三项：

  - Candidate Actions 生成：基于 Phase 5 的 gap，用 LLM 生成 2-3 个
    候选 `ActionSpec`（不是无限枚举）。
  - `simulation/engine.py` 第一版：对每个候选 Action，调用 LLM + 检索
    Phase 3 的相关 Experience，生成"如果这么做，可能发生什么"的自然
    语言描述。

设计与 `goals/gap.py::detect_gap(llm_judge=...)` 同一种风格：LLM 调用
点全部做成调用方注入的 `Callable`，本模块**不内置任何真正发起网络
请求的默认实现**——原因与 `goals/gap.py` 文档字符串一致：LLM 输出不是
确定性的，模块自己内置一份"默认 LLM 实现"既没法保证可测试性，也会让
"具体接哪个 LLM/哪套 prompt 模板"这个后续可能变化的决定被锁死在
底层模块里。调用方（例如后续接入 `goal_mode/runner.py` 的代码）负责
提供真正调用 LLM 的函数。

止损条件落地：Phase 7 文档"目标与边界"明确禁止复杂因果树/数值化打分，
本模块的两个函数都只做"调用一次注入的 LLM 函数 + 做最基本的形状校验"，
不在这里实现任何打分/树形展开逻辑。
"""

from __future__ import annotations

from typing import Callable, Optional

from mini_agent.core.action import ActionSpec
from mini_agent.core.experience_retrieval import (
    render_experiences_as_context,
    retrieve_similar_experiences,
)
from mini_agent.core.experience_store import ExperienceStore
from mini_agent.core.simulation import SimulationResult, SimulationScenario

# (gap_item, goal_text, constraints) -> 候选 ActionSpec 列表（调用方自行
# 决定怎么问 LLM、怎么把 LLM 输出解析成 ActionSpec，本模块不关心）。
CandidateActionGenerator = Callable[[str, str, "list[str]"], "list[ActionSpec]"]

# (action, goal_text, current_state, experience_context) -> 一个字典，
# 至少包含 "possible_futures"/"risks"/"tradeoffs" 三个键（形状与
# `SimulationResult` 对应字段一致），调用方自行决定怎么问 LLM。
ScenarioNarrator = Callable[[ActionSpec, str, str, str], dict]


def generate_candidate_actions(
    gap_item: str,
    goal_text: str,
    constraints: "list[str]",
    llm_generate: CandidateActionGenerator,
    *,
    max_candidates: int = 3,
) -> "list[ActionSpec]":
    """基于一条 gap，生成 2-3 个候选 `ActionSpec`。

    `llm_generate` 是必填的注入函数（原因见文件顶部文档字符串），不传
    时直接 `ValueError`，不静默返回空列表掩盖"没有真的生成候选"这个
    事实。

    只做一层形状约束：`llm_generate` 返回的候选数量裁剪到最多
    `max_candidates` 个（对应任务表"2-3 个，不是无限枚举"里的上界），
    **不**在这里补齐到至少 2 个——"生成的候选数量是否足够"是验收标准
    要核对的事情，交给调用方/测试判断，本函数不通过复制候选或编造
    候选来凑数字。
    """
    if llm_generate is None:
        raise ValueError(
            "generate_candidate_actions: 必须提供 llm_generate（本模块"
            "不内置默认 LLM 实现，见 simulation/engine.py 文档字符串）"
        )

    candidates = list(llm_generate(gap_item, goal_text, list(constraints)))
    return candidates[:max_candidates]


def simulate_candidates(
    scenario: SimulationScenario,
    llm_narrate: ScenarioNarrator,
    *,
    experience_store: Optional[ExperienceStore] = None,
    experience_limit: int = 3,
) -> "list[SimulationResult]":
    """对 `scenario.candidate_actions` 里的每个候选，生成一条
    `SimulationResult`。

    每个候选独立检索一次 Phase 3 的相似历史 Experience（按
    `f"{scenario.goal} {action.capability}"` 作为检索文本，让每个候选
    的检索结果能体现候选本身的差异，而不是所有候选共用同一份检索结果），
    渲染成上下文文本后连同 `scenario.current_state`/`scenario.goal` 一起
    交给 `llm_narrate`。检索不到相关历史时 `experience_context` 是空
    字符串、`experience_refs` 是空列表，如实反映"这次没有历史经验可
    参考"，不伪造引用。

    `llm_narrate` 是必填的注入函数，原因同 `generate_candidate_actions`。
    """
    if llm_narrate is None:
        raise ValueError(
            "simulate_candidates: 必须提供 llm_narrate（本模块不内置"
            "默认 LLM 实现，见 simulation/engine.py 文档字符串）"
        )

    results: "list[SimulationResult]" = []
    for action in scenario.candidate_actions:
        query_text = f"{scenario.goal} {action.capability}".strip()
        similar = retrieve_similar_experiences(
            query_text, store=experience_store, limit=experience_limit,
        )
        experience_context = render_experiences_as_context(similar)

        narration = llm_narrate(
            action, scenario.goal, scenario.current_state, experience_context,
        )

        results.append(SimulationResult(
            action=action,
            possible_futures=list(narration.get("possible_futures", [])),
            risks=list(narration.get("risks", [])),
            tradeoffs=str(narration.get("tradeoffs", "")),
            experience_refs=[exp.id for exp in similar],
        ))
    return results
