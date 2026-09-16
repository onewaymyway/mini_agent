"""world_simulator/autopilot.py — 自动挡（代理执行）

设计依据：`world_simulator_external_project_plan.md` 4.1 节。

不是一个新的推理组件：自动挡"代理"就是给 `advance_step` workflow 多喂
一段"决策者画像"文本（`_build_decision_context()`），决策仍然是同一个
LLM 调用，只是 `engine.advance()` 传 `choice_option_id=None` +
非空 `decision_context`，让 skill 自己从候选列表里选一个（见
`engine.py::advance()` 的"确定这一步实际生效的选择"注释）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from world_simulator.engine import (
    SimAlreadyEndedError,
    SimEngineError,
    SimPausedError,
    advance,
    list_simulations,
    set_status,
)
from world_simulator.state_model import SimManifest, SimState

logger = logging.getLogger("world_simulator.autopilot")


class AutopilotDisabledError(RuntimeError):
    pass


def _build_decision_context(manifest: SimManifest) -> str:
    """把 `manifest.autopilot` 配置渲染成拼进 prompt 的"决策者画像"文本。

    渲染成人类可读的自然语言段落，而不是原样塞一段 JSON——
    `advance_step.yaml` 的 prompt 是给 LLM 读的，自然语言指令比"请解析
    这段 JSON 配置"更可靠。
    """
    ap = manifest.autopilot or {}
    principles: List[str] = list(ap.get("principles") or [])
    risk_preference = ap.get("risk_preference", "balanced")
    risk_label = {
        "conservative": "保守：优先规避风险、追求稳妥确定的结果",
        "balanced": "均衡：在风险和收益之间正常权衡，不刻意偏向任何一端",
        "aggressive": "进取：愿意为更大的潜在收益承担更高风险",
    }.get(risk_preference, risk_preference)

    lines = [
        "你现在是这个模拟实例的自动挡代理，需要代替用户在候选分支里做选择。",
        f"风险偏好：{risk_label}。",
    ]
    if principles:
        lines.append("用户设定的原则/偏好：")
        lines.extend(f"- {p}" for p in principles)
    else:
        lines.append("用户没有设定额外的原则/偏好，按风险偏好和常理判断即可。")
    return "\n".join(lines)


def is_autopilot_enabled(manifest: SimManifest) -> bool:
    return manifest.pilot_mode == "autopilot" and bool((manifest.autopilot or {}).get("enabled"))


@dataclass
class AutopilotStepResult:
    sim_id: str
    ok: bool
    next_step: Optional[int] = None
    paused_for_review: bool = False
    error: Optional[str] = None


def run_autopilot_step(cfg, workspace_root: Path, data_dir: Path, sim_id: str) -> SimState:
    """让指定实例的自动挡代理推进一步。

    调用方（`run_batch_autopilot()` 或看板的"运行一步"按钮）负责先确认
    `manifest.pilot_mode == "autopilot"` 且 `status == "active"`——本函数
    只在配置确实是自动挡时才允许调用，避免被误用到手动挡实例上。
    """
    from world_simulator.engine import get_simulation

    manifest, _current, _history = get_simulation(data_dir, sim_id)
    if not is_autopilot_enabled(manifest):
        raise AutopilotDisabledError(f"实例 {sim_id} 未开启自动挡，无法调用自动挡推进")

    decision_context = _build_decision_context(manifest)
    next_state = advance(
        cfg, workspace_root, data_dir, sim_id,
        choice_option_id=None, decision_context=decision_context, chosen_by="autopilot",
    )

    review_mode = (manifest.autopilot or {}).get("review_mode", "silent")
    if review_mode == "pause_on_major_decision" and next_state.major_decision:
        logger.info(
            "实例 %s 第 %s 步被判定为重大决策，按 review_mode=pause_on_major_decision 暂停自动推进",
            sim_id, next_state.step,
        )
        set_status(data_dir, sim_id, "paused")

    return next_state


def run_batch_autopilot(
    cfg, workspace_root: Path, data_dir: Path, *, steps: int = 1
) -> List[AutopilotStepResult]:
    """批量推进所有"开启了自动挡且处于进行中"的实例各 `steps` 步。

    对应 `project.yaml` 的 `batch_advance_daily` 调度入口。单个实例的
    失败（LLM 调用报错、workflow 异常等）不应该中断整批——一个实例的
    数据问题不该拖累其它实例当天的推进，因此这里对每个实例、每一步都
    单独 try/except，失败就记录进结果列表继续下一个。
    """
    results: List[AutopilotStepResult] = []
    manifests = list_simulations(data_dir)
    targets = [m for m in manifests if is_autopilot_enabled(m) and m.status == "active"]

    for manifest in targets:
        sim_id = manifest.sim_id
        for _ in range(max(1, steps)):
            try:
                # 每一步都重新读一次最新状态：上一步如果因为
                # pause_on_major_decision 把实例暂停了，这一步就应该
                # 停止，不能无视暂停状态硬推。
                from world_simulator.engine import get_simulation as _get_sim

                latest_manifest, _c, _h = _get_sim(data_dir, sim_id)
                if latest_manifest.status != "active":
                    results.append(
                        AutopilotStepResult(sim_id=sim_id, ok=True, paused_for_review=True)
                    )
                    break

                next_state = run_autopilot_step(cfg, workspace_root, data_dir, sim_id)
                results.append(
                    AutopilotStepResult(
                        sim_id=sim_id, ok=True, next_step=next_state.step,
                        paused_for_review=next_state.major_decision,
                    )
                )
                if next_state.major_decision:
                    break  # 这一步之后大概率已被暂停，不继续推剩余 steps
            except (SimEngineError, SimAlreadyEndedError, SimPausedError, AutopilotDisabledError) as exc:
                logger.error("自动挡推进失败：sim_id=%s error=%s", sim_id, exc)
                results.append(AutopilotStepResult(sim_id=sim_id, ok=False, error=str(exc)))
                break
            except Exception as exc:  # noqa: BLE001 — 批量任务：单实例异常不能拖垮整批
                logger.exception("自动挡推进出现未预期异常：sim_id=%s", sim_id)
                results.append(AutopilotStepResult(sim_id=sim_id, ok=False, error=str(exc)))
                break

    return results
