"""world_simulator/agent_preview.py — Agent Preview（阶段三十二后续，
4.5 节第二批，`next_doc/world_simulator_agent_preview_and_adaptive_
policy_plan.md` 4 节）。

设计理念：画像编辑好之后，用户目前只能靠"真的跑一次模拟"才能看到
画像会怎么选，反馈周期很长。本模块提供一个独立入口，用几个内置的
通用测试情境（`agent_preview_scenarios.py`），分别单独调用一次
LLM，让画像对测试情境做一次选择——**不写入任何 `data/<sim_id>/`
下的正式模拟数据**，是一次副作用完全隔离的调用，类似
`hypothesis.run_hypothesis_worlds()` 的"调用即弃"模式，但这里连
分支都不创建，直接是一次独立的 workflow 调用。

范围克制（详见子方案 4.6 节）：
- 测试情境固定内置，不支持用户自定义。
- 每次调用会产生真实的 LLM 调用开销，不做自动触发，完全由用户
  手动点击触发。
- "引用的原则"由 LLM 在同一次调用里自陈，不做代码层面的强制校验
  ——允许 LLM 如实说"没有明确依据某条已声明的原则"。
- 用户对结果的"符合/不符合预期"标注（如果 UI 层提供）本模块不做
  任何存储和聚合，是否要长期记录取决于第三批（用户反馈反哺画像）
  是否要复用，本模块只负责跑一次、返回结果。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from world_simulator.agent_preview_scenarios import BUILTIN_SCENARIOS, PreviewScenario
from world_simulator.autopilot import _build_decision_context
from world_simulator.state_model import SimManifest


class AgentPreviewError(RuntimeError):
    pass


@dataclass
class PreviewResult:
    scenario_id: str
    scenario_title: str
    chosen_option_id: str
    chosen_option_label: str
    reason: str
    referenced_policy: str = ""
    error: Optional[str] = None
    """这个测试情境单独失败时的错误信息（比如 LLM 输出解析失败）；
    单个情境失败不应该让其它情境的结果也拿不到，`run_agent_preview()`
    对每个情境独立 try/except，失败的情境在结果列表里体现为
    `chosen_option_id == ""`、`error` 非空，成功的情境不受影响。
    """

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "scenario_title": self.scenario_title,
            "chosen_option_id": self.chosen_option_id,
            "chosen_option_label": self.chosen_option_label,
            "reason": self.reason,
            "referenced_policy": self.referenced_policy,
            "error": self.error,
        }


def _run_one_scenario(
    cfg, wf, runner_cls, decision_context: str, scenario: PreviewScenario
) -> PreviewResult:
    option_by_id = {o.id: o for o in scenario.options}
    try:
        runner = runner_cls(cfg)
        result = runner.run(
            wf,
            {
                "decision_context": decision_context,
                "scenario_title": scenario.title,
                "scenario_description": scenario.description,
                "scenario_options_json": json.dumps(
                    [
                        {
                            "id": o.id, "label": o.label, "description": o.description,
                            "risk_level": o.risk_level, "reversibility": o.reversibility,
                        }
                        for o in scenario.options
                    ],
                    ensure_ascii=False,
                ),
            },
        )
        if result.status != "done":
            raise AgentPreviewError(f"workflow 执行未成功：status={result.status}")
        step_result = next(
            (sr for sr in result.step_results if sr.step_id == "agent_preview"), None
        )
        if step_result is None or not step_result.result_file:
            raise AgentPreviewError("agent_preview 步骤未产出 result_file")
        data = json.loads(Path(step_result.result_file).read_text(encoding="utf-8"))
        chosen_id = str(data.get("chosen_option_id") or "")
        chosen_opt = option_by_id.get(chosen_id)
        return PreviewResult(
            scenario_id=scenario.id,
            scenario_title=scenario.title,
            chosen_option_id=chosen_id,
            chosen_option_label=chosen_opt.label if chosen_opt else chosen_id,
            reason=str(data.get("reason") or ""),
            referenced_policy=str(data.get("referenced_policy") or ""),
        )
    except Exception as exc:  # noqa: BLE001 — 单个情境失败不应阻断其它情境
        return PreviewResult(
            scenario_id=scenario.id, scenario_title=scenario.title,
            chosen_option_id="", chosen_option_label="", reason="",
            error=str(exc),
        )


def run_agent_preview(
    cfg,
    workspace_root: Path,
    autopilot_profile: Dict[str, Any],
    *,
    scenario_ids: Optional[List[str]] = None,
) -> List[PreviewResult]:
    """对 `autopilot_profile` 在内置测试情境下各跑一次选择。

    Args:
        cfg: `mini_agent.config.load_config()` 返回的 `AppConfig`。
        workspace_root: world_simulator 项目根。
        autopilot_profile: 画像配置字典（`principles`/`risk_
            preference`/`conditional_policies`/... ，格式同
            `manifest.autopilot`），**不需要**这个画像已经挂在某个
            真实的模拟实例上——这里只是临时构造一个不落盘的
            `SimManifest` 用来复用 `_build_decision_context()`。
        scenario_ids: 只跑这几个情境（用于测试/调试），默认全部
            `BUILTIN_SCENARIOS`。

    Returns:
        每个测试情境一条 `PreviewResult`，顺序与情境列表一致；单个
        情境失败不影响其它情境（详见 `PreviewResult.error`）。

    Raises:
        AgentPreviewError: 找不到 workflow 定义、或 `scenario_ids`
            指定了不存在的情境 id。
    """
    from mini_agent.workflow.runner import WorkflowRunner
    from mini_agent.workflow.store import WorkflowStore

    scenarios = BUILTIN_SCENARIOS
    if scenario_ids is not None:
        by_id = {s.id: s for s in BUILTIN_SCENARIOS}
        missing = [sid for sid in scenario_ids if sid not in by_id]
        if missing:
            raise AgentPreviewError(f"未知的测试情境 id：{missing}")
        scenarios = [by_id[sid] for sid in scenario_ids]

    wf_store = WorkflowStore(Path(workspace_root))
    wf = wf_store.load("agent_preview")
    if wf is None:
        raise AgentPreviewError(
            "找不到 workflow 定义 'agent_preview'"
            f"（预期路径：{workspace_root}/workflows/agent_preview.yaml）"
        )

    # 临时构造一个不落盘的 SimManifest，只是为了复用
    # `_build_decision_context()` 现成的画像渲染逻辑，不代表这个画像
    # 挂在某个真实模拟实例上。
    fake_manifest = SimManifest(
        sim_id="__agent_preview__", template="", intent="", title="",
        created_at="", updated_at="", pilot_mode="autopilot",
        autopilot=dict(autopilot_profile or {}),
    )
    decision_context = _build_decision_context(fake_manifest)

    return [
        _run_one_scenario(cfg, wf, WorkflowRunner, decision_context, scenario)
        for scenario in scenarios
    ]
