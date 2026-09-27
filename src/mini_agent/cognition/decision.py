"""cognition/decision.py — Phase 7 Sprint 7-2：DecisionEngine。

对应 `next_doc/refactor_plan/08-phase7-decision-simulation-sprint-plan.md`
Sprint 7-2 任务表第一项：`DecisionEngine.select(candidates:
list[SimulationResult]) -> ActionSpec`，"第一版可以是把权衡描述交给
LLM 做最终选择，也可以先支持人工确认模式"。

本实现两种模式都支持，构造时二选一注入（`llm_select`/`human_confirm`），
与 `simulation/engine.py`/`goals/gap.py::detect_gap(llm_judge=...)` 同一种
"不内置默认 LLM 实现，调用方自己注入"的风格：

  - `human_confirm(candidates) -> (index, reason)`：交给人工从候选里选
    一个（例如 CLI 场景下打印权衡描述、读用户输入），第一版"人工确认
    模式"就是这个签名的一个具体实现，本模块不内置任何 CLI 交互逻辑。
  - `llm_select(candidates, goal_text, gap_item) -> (index, reason)`：
    交给 LLM 做最终选择，"reason" 是 LLM 给出的选择理由（自然语言），
    用于下面的可读 trace。

`select()` 的第二个返回值 `DecisionTrace` 对应 Sprint 7-2 验收标准：
"一次真实 Goal 执行中，能看到完整的决策 trace：为什么生成了这几个
候选、每个候选的权衡是什么、最终为什么选了其中一个……这个 trace 应该
是可读的自然语言，而不是一堆内部对象的 dump"——`DecisionTrace.to_text()`
就是这段可读文本的产出点。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, TYPE_CHECKING

from mini_agent.core.simulation import SimulationResult

if TYPE_CHECKING:
    from mini_agent.core.action import ActionSpec

# (candidates, goal_text, gap_item) -> (选中候选在 candidates 里的下标, 选择理由)
LLMSelector = Callable[["list[SimulationResult]", str, str], "tuple[int, str]"]
# (candidates) -> (选中候选在 candidates 里的下标, 选择理由)
HumanConfirmer = Callable[["list[SimulationResult]"], "tuple[int, str]"]


@dataclass
class DecisionTrace:
    """一次 Decision 的可读记录，对应 Sprint 7-2 验收标准的"完整决策
    trace"。字段本身不是给程序消费的结构化数据（那是
    `SimulationScenario`/`SimulationResult` 的职责），而是给人读的。
    """

    gap_item: str
    goal_text: str
    candidate_summaries: "list[str]" = field(default_factory=list)
    selected_capability: str = ""
    reason: str = ""

    def to_text(self) -> str:
        """渲染成一段可读的自然语言 trace（不是内部对象 dump）。"""
        lines = [f"针对 gap「{self.gap_item}」（目标：{self.goal_text}），"
                 f"生成了 {len(self.candidate_summaries)} 个候选方案："]
        for i, summary in enumerate(self.candidate_summaries, start=1):
            lines.append(f"  方案{i}：{summary}")
        lines.append(f"最终选择：{self.selected_capability}")
        lines.append(f"理由：{self.reason}")
        return "\n".join(lines)


def _summarize_candidate(result: SimulationResult) -> str:
    """把一个 `SimulationResult` 压成一行摘要，供 `DecisionTrace` 使用。

    只取"权衡描述"和"风险"两个对人类决策最相关的字段，不把
    `possible_futures`/`experience_refs` 也塞进摘要——那些留给需要
    细节时读原始 `SimulationResult`，摘要行本身要保持可读、不冗长。
    """
    text = f"{result.action.capability}｜权衡：{result.tradeoffs or '（无）'}"
    if result.risks:
        text += f"｜风险：{'；'.join(result.risks)}"
    return text


class DecisionEngine:
    """从若干 `SimulationResult` 里选出最终执行的 `ActionSpec`。

    构造参数（二选一，都不传时 `select()` 会 `ValueError`，不猜一个
    默认策略）：
      llm_select      — 见模块文档字符串。
      human_confirm   — 见模块文档字符串。同时传入两者时优先用
                        `human_confirm`（人工确认的优先级高于 LLM
                        自动选择，与项目里权限审批"人工优先"的一贯
                        原则一致）。
    """

    def __init__(
        self,
        llm_select: Optional[LLMSelector] = None,
        human_confirm: Optional[HumanConfirmer] = None,
    ) -> None:
        self._llm_select = llm_select
        self._human_confirm = human_confirm

    def select(
        self,
        candidates: "list[SimulationResult]",
        *,
        goal_text: str = "",
        gap_item: str = "",
    ) -> "tuple[ActionSpec, DecisionTrace]":
        if not candidates:
            raise ValueError("DecisionEngine.select: candidates 不能为空")

        if self._human_confirm is not None:
            index, reason = self._human_confirm(candidates)
        elif self._llm_select is not None:
            index, reason = self._llm_select(candidates, goal_text, gap_item)
        else:
            raise ValueError(
                "DecisionEngine: 必须在构造时提供 llm_select 或 "
                "human_confirm 之一（本模块不内置默认选择策略，见 "
                "cognition/decision.py 文档字符串）"
            )

        if not (0 <= index < len(candidates)):
            raise ValueError(
                f"DecisionEngine: 选择结果下标 {index} 超出候选范围 "
                f"[0, {len(candidates)})"
            )

        chosen = candidates[index]
        trace = DecisionTrace(
            gap_item=gap_item,
            goal_text=goal_text,
            candidate_summaries=[_summarize_candidate(c) for c in candidates],
            selected_capability=chosen.action.capability,
            reason=reason,
        )
        return chosen.action, trace
