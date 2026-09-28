"""runtime/decision_stage.py — AgentRuntime 的 `plan/simulate/decide` 步骤
（Phase 10 S-A A3，advisory-only 旁路）。

见 `next_doc/refactor_plan/14-phase10-sa-item-plan.md` 第四节与第十节执行记录。
`AgentRuntime.run_once()` 骨架里 `plan → simulate → decide` 三步自 Phase 8
Sprint 8-1 起一直被直接跳过（沿用 Phase 7 Sprint 7-2 的范围决策）。本模块
把 Phase 7 已经产出的三件套接上：

    generate_candidate_actions → simulate_candidates → DecisionEngine.select

**这是“决策记录”，不是“决策执行”**（必须让使用者知道的边界）：

  - `GoalRunner` 自己驱动多轮，**不接受 `ActionSpec`**，所以选出的动作没有
    地方可以被执行。本模块只把 `DecisionTrace` 发布成 `DecisionMade` 事件
    （`executed=False`）并返回报告，**不执行所选动作**。要让决策真正驱动
    执行，需要改 `GoalRunner`，属于更大的改动，另立方案。
  - 默认关闭（`goal_mode.runtime_decision_enabled=False`）。关闭时
    `run_once()` 的行为与 payload 和此前完全一致。
  - 开启后每次运行会多出至多 `1 + max_candidates + 1` 次 LLM 调用
    （生成候选 1 次 + 每个候选的权衡描述各 1 次 + 最终选择 1 次，默认
    最多 5 次）。14 号文档原写“约 2–3 次”，低估了；已在执行记录中更正。
  - 一次运行只针对**第一条 gap** 做一次决策（不遍历所有 gap），以限制成本。
  - 候选里的工具/工作流名称**不会**对照注册表校验——它们只是被记录，
    不会被执行；若日后接入执行，必须在执行前校验。

**永不抛异常**：这是旁路，任何失败只记录到 `DecisionStageReport.error`，
不能改变 Goal 的执行结果。

LLM 调用点：`simulation/engine.py` 与 `cognition/decision.py` 都要求调用方
注入 `Callable`，本模块用 `agent.llm_helper.ask()` 实现这三个 Callable；
`llm_helper` 拿不到时不发起任何调用，报告状态为 `skipped_no_llm`。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from mini_agent.core.action import ActionSpec
from mini_agent.core.events import Event
from mini_agent.core.simulation import SimulationResult, SimulationScenario

if TYPE_CHECKING:
    from mini_agent.cognition.decision import DecisionTrace
    from mini_agent.core.event_bus import EventBus
    from mini_agent.core.experience_store import ExperienceStore

_logger = logging.getLogger("mini_agent.core.trace")

# DecisionStageReport.status 取值
STATUS_DECIDED = "decided"
STATUS_SKIPPED_NO_GAP = "skipped_no_gap"
STATUS_SKIPPED_NO_LLM = "skipped_no_llm"
STATUS_NO_CANDIDATES = "no_candidates"
STATUS_FAILED = "failed"

_VALID_ACTION_TYPES = ("tool", "workflow", "subagent")
_MAX_FIELD_CHARS = 500  # 写进事件 payload 的单个文本字段上限，防止刷爆 events.jsonl


@dataclass
class DecisionStageReport:
    status: str
    gap_item: str = ""
    candidate_count: int = 0
    action: Optional[ActionSpec] = None
    trace: "Optional[DecisionTrace]" = None
    experience_refs: list = field(default_factory=list)
    error: str = ""

    def summary(self) -> dict:
        """写进 `RuntimeCycleCompleted` payload 的精简摘要（不含完整 trace 文本）。"""
        out: dict = {"status": self.status, "candidate_count": self.candidate_count}
        if self.action is not None:
            out["selected_type"] = self.action.type
            out["selected_capability"] = self.action.capability
        if self.error:
            out["error"] = _clip(self.error)
        return out


def _clip(text: Any, limit: int = _MAX_FIELD_CHARS) -> str:
    s = str(text)
    return s if len(s) <= limit else s[:limit] + "…"


# ── JSON 解析（LLM 输出不可信，只做形状校验，解析失败就抛 ValueError）──────────

def _extract_json(raw: str) -> Any:
    """从 LLM 文本里取出第一个 JSON 值；容忍 ``` 围栏和前后多余文字。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    # 按“最先出现的括号”优先：`[{...}]` 必须先当数组解析，否则单元素数组会被
    # 内层对象抢先匹配（曾因此把只有 1 个候选的输出误判为空）。
    spans = []
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            spans.append((start, end))
    for start, end in sorted(spans):
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            continue
    raise ValueError("LLM 输出中没有可解析的 JSON")


def _parse_candidates(raw: str) -> "list[ActionSpec]":
    data = _extract_json(raw)
    if isinstance(data, dict):
        data = data.get("candidates", [])
    if not isinstance(data, list):
        raise ValueError("候选生成输出不是 JSON 数组")
    specs: "list[ActionSpec]" = []
    for item in data:
        if not isinstance(item, dict):
            continue
        a_type = item.get("type")
        capability = item.get("capability")
        if a_type not in _VALID_ACTION_TYPES:
            continue
        if not isinstance(capability, str) or not capability.strip():
            continue
        args = item.get("arguments")
        expected = item.get("expected_outcome")
        specs.append(ActionSpec(
            type=a_type,
            capability=capability.strip(),
            arguments=args if isinstance(args, dict) else {},
            expected_outcome=str(expected) if expected else None,
        ))
    return specs


def _as_str_list(value: Any) -> "list[str]":
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    return []


# ── 三个注入函数（基于 agent.llm_helper.ask）──────────────────────────────────

_GENERATE_SYSTEM = (
    "你是决策助手。针对给定的目标差距，提出 2-3 个互不相同的候选行动。"
    "只输出 JSON 数组，不要任何解释。每个元素形如："
    '{"type": "tool|workflow|subagent", "capability": "<名称>", '
    '"arguments": {}, "expected_outcome": "<一句话预期结果>"}。'
)

_NARRATE_SYSTEM = (
    "你是决策助手。用自然语言评估一个候选行动，不要打分或给概率。"
    "只输出 JSON 对象：" '{"possible_futures": ["..."], "risks": ["..."], "tradeoffs": "..."}。'
)

_SELECT_SYSTEM = (
    "你是决策助手。从候选方案中选出一个最合适的，并说明理由。"
    '只输出 JSON 对象：{"index": <从 0 开始的整数>, "reason": "..."}。'
)


def build_llm_generate(llm_helper: Any):
    def _generate(gap_item: str, goal_text: str, constraints: "list[str]") -> "list[ActionSpec]":
        prompt = json.dumps(
            {"goal": goal_text, "gap": gap_item, "constraints": constraints},
            ensure_ascii=False,
        )
        raw = llm_helper.ask(prompt, system=_GENERATE_SYSTEM, max_retries=2,
                             override_temperature=0.2)
        return _parse_candidates(raw)

    return _generate


def build_llm_narrate(llm_helper: Any):
    def _narrate(action: ActionSpec, goal_text: str, current_state: str,
                 experience_context: str) -> dict:
        prompt = json.dumps(
            {
                "goal": goal_text,
                "current_state": current_state,
                "candidate": {
                    "type": action.type,
                    "capability": action.capability,
                    "arguments": action.arguments,
                    "expected_outcome": action.expected_outcome,
                },
                "similar_history": experience_context,
            },
            ensure_ascii=False,
        )
        raw = llm_helper.ask(prompt, system=_NARRATE_SYSTEM, max_retries=2,
                             override_temperature=0.2)
        data = _extract_json(raw)
        if not isinstance(data, dict):
            raise ValueError("权衡描述输出不是 JSON 对象")
        return {
            "possible_futures": _as_str_list(data.get("possible_futures")),
            "risks": _as_str_list(data.get("risks")),
            "tradeoffs": str(data.get("tradeoffs", "")),
        }

    return _narrate


def build_llm_select(llm_helper: Any):
    def _select(candidates: "list[SimulationResult]", goal_text: str,
                gap_item: str) -> "tuple[int, str]":
        prompt = json.dumps(
            {
                "goal": goal_text,
                "gap": gap_item,
                "candidates": [
                    {
                        "index": i,
                        "action": f"{c.action.type}:{c.action.capability}",
                        "possible_futures": c.possible_futures,
                        "risks": c.risks,
                        "tradeoffs": c.tradeoffs,
                    }
                    for i, c in enumerate(candidates)
                ],
            },
            ensure_ascii=False,
        )
        raw = llm_helper.ask(prompt, system=_SELECT_SYSTEM, max_retries=2,
                             override_temperature=0.0)
        data = _extract_json(raw)
        if not isinstance(data, dict):
            raise ValueError("选择输出不是 JSON 对象")
        index = data.get("index")
        # bool 是 int 的子类，显式排除；下标越界由 DecisionEngine.select() 校验。
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("选择输出缺少整数 index")
        return index, str(data.get("reason", ""))

    return _select


# ── 入口 ─────────────────────────────────────────────────────────────────────

def run_decision_stage(
    *,
    llm_helper: Any,
    goal_text: str,
    gap: "list[str]",
    current_state: str = "",
    constraints: "Optional[list[str]]" = None,
    experience_store: "Optional[ExperienceStore]" = None,
    bus: "Optional[EventBus]" = None,
    correlation_id: Optional[str] = None,
    max_candidates: int = 3,
) -> DecisionStageReport:
    """对第一条 gap 走一遍“候选 → 模拟 → 决策”，发布 `DecisionMade`，**不执行**。

    永不抛异常。`gap` 为空时不发起任何 LLM 调用（没有差距就没有可决策的事）。
    """
    if not gap:
        return DecisionStageReport(status=STATUS_SKIPPED_NO_GAP)
    gap_item = str(gap[0])
    if llm_helper is None or not hasattr(llm_helper, "ask"):
        return DecisionStageReport(status=STATUS_SKIPPED_NO_LLM, gap_item=gap_item)

    try:
        from mini_agent.cognition.decision import DecisionEngine
        from mini_agent.core import get_event_bus
        from mini_agent.simulation.engine import (
            generate_candidate_actions,
            simulate_candidates,
        )

        constraints = list(constraints or [])
        candidates = generate_candidate_actions(
            gap_item, goal_text, constraints,
            build_llm_generate(llm_helper), max_candidates=max_candidates,
        )
        if not candidates:
            return DecisionStageReport(status=STATUS_NO_CANDIDATES, gap_item=gap_item)

        scenario = SimulationScenario(
            current_state=current_state, goal=goal_text,
            candidate_actions=candidates, constraints=constraints,
        )
        results = simulate_candidates(
            scenario, build_llm_narrate(llm_helper), experience_store=experience_store,
        )
        engine = DecisionEngine(llm_select=build_llm_select(llm_helper))
        action, trace = engine.select(results, goal_text=goal_text, gap_item=gap_item)

        refs: list = []
        for r in results:
            for ref in r.experience_refs:
                if ref not in refs:
                    refs.append(ref)

        report = DecisionStageReport(
            status=STATUS_DECIDED, gap_item=gap_item, candidate_count=len(results),
            action=action, trace=trace, experience_refs=refs,
        )
        trace_text = trace.to_text()
        _logger.info("runtime.decision advisory (not executed):\n%s", trace_text)
        (bus or get_event_bus()).publish(Event(
            kind="DecisionMade",
            payload={
                "gap_item": _clip(gap_item),
                "goal_text": _clip(goal_text),
                "candidate_count": len(results),
                "selected_type": action.type,
                "selected_capability": _clip(action.capability),
                "reason": _clip(trace.reason),
                "trace_text": _clip(trace_text, 2000),
                "experience_refs": refs,
                # advisory-only：选出的动作不会被执行（见模块文档字符串）。
                "advisory": True,
                "executed": False,
            },
            actor="runtime.decision_stage",
            correlation_id=correlation_id,
        ))
        return report
    except Exception as e:  # noqa: BLE001 — 旁路，任何失败都不能影响 Goal
        try:
            from mini_agent.errors import log_exception

            log_exception(e, where="mini_agent.runtime.decision_stage.run_decision_stage")
        except Exception:  # noqa: BLE001
            pass
        return DecisionStageReport(
            status=STATUS_FAILED, gap_item=gap_item, error=f"{type(e).__name__}: {e}",
        )
