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

    if bool(ap.get("allow_custom_options")):
        lines.append(
            "系统给出的候选分支选项终究只是建议，如果你确信候选列表里"
            "没有一个足够合理，允许你跳出这个列表，自己提出一个新的候选"
            "方向（输出 custom_option_label/custom_option_description，"
            "而不是 chosen_option_id）——请谨慎使用这个权限，多数情况下"
            "应该优先从候选列表里选，只有明显没有一个合理选项时才这么做。"
        )
    else:
        lines.append("请从给定的候选分支选项里选择，不要自己发明列表之外的新选项。")
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
    allow_custom_options = bool((manifest.autopilot or {}).get("allow_custom_options"))
    next_state = advance(
        cfg, workspace_root, data_dir, sim_id,
        choice_option_id=None, decision_context=decision_context, chosen_by="autopilot",
        allow_custom_options=allow_custom_options,
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


@dataclass
class ExperimentBranchResult:
    """对比实验里，某一条策略画像分支跑完之后的结果摘要。"""

    branch: str
    profile_name: str
    steps_done: int
    ended_early: bool
    error: Optional[str] = None
    final_vars: Optional[Dict[str, Any]] = None
    """这条分支跑完之后当前状态的 `vars`（阶段十新增，供
    `analysis.aggregate_field_stats()` 做统计聚合用）。分支一步都没跑成
    （fork 失败等）时为 `None`；否则即使中途因暂停/报错提前结束，也是
    "跑到哪一步就用哪一步的 vars"，不代表这条分支的模拟已经完整跑完
    `steps` 步。"""


def run_comparison_experiment(
    cfg,
    workspace_root: Path,
    data_dir: Path,
    sim_id: str,
    *,
    source_branch: str,
    from_step: int,
    steps: int,
    profiles: List[Dict[str, Any]],
) -> List[ExperimentBranchResult]:
    """对比实验：从同一个历史节点分叉出多条分支，每条分支套用一份不同
    的自动挡策略画像，各自独立、依次推进相同步数，跑完之后就可以在
    「对比视图」里并排查看"同样的起点，不同的策略画像分别走出了什么
    结果"——这正是"生成多种自动挡策略、依次执行、对比实验"要支持的
    用法，不需要用户手动一条条分叉、一条条切换、一条条配置自动挡再
    一步步点推进。

    Args:
        source_branch / from_step: 从哪条分支的第几步开始分叉（所有
            策略分支共享同一个起点，这样对比才有意义）。
        steps: 每条策略分支各自推进的步数（`review_mode:
            pause_on_major_decision` 触发暂停、或推进出错时会提前
            结束这一条分支，不影响其它分支继续跑）。
        profiles: 策略画像列表，每个元素形如
            `{"name": ..., "principles": [...], "risk_preference": ...,
              "review_mode": ..., "allow_custom_options": bool}`；
            `name` 只用于结果展示，不参与决策逻辑本身。

    实现上的取舍：`engine.advance()`/`run_autopilot_step()` 只认
    "当前活跃分支"（`manifest.branch`），本身不支持"对着一条非活跃
    分支推进"；这里用分叉时 `switch=True` 依次把活跃分支切到每条策略
    分支上去跑，跑完全部策略之后再切回实验开始前用户正在看的那条
    分支——避免"跑了个实验，结果页面莫名其妙停在最后一条策略分支上"
    这种观感，用户应该主动去「对比视图」/「分支」列表里查看结果。
    """
    from world_simulator.branch_manager import BranchError, fork_branch, switch_branch
    from world_simulator.engine import get_simulation, set_pilot_config

    store_manifest = get_simulation(data_dir, sim_id)[0]
    original_active_branch = store_manifest.branch

    results: List[ExperimentBranchResult] = []
    for profile in profiles:
        name = str(profile.get("name") or "策略")
        try:
            new_branch = fork_branch(
                data_dir, sim_id, from_step=from_step,
                source_branch=source_branch, switch=True,
            )
            set_pilot_config(
                data_dir, sim_id, pilot_mode="autopilot",
                autopilot={
                    "enabled": True,
                    "principles": list(profile.get("principles") or []),
                    "risk_preference": profile.get("risk_preference", "balanced"),
                    "review_mode": profile.get("review_mode", "silent"),
                    "allow_custom_options": bool(profile.get("allow_custom_options")),
                },
            )
        except (BranchError, SimEngineError) as exc:
            results.append(
                ExperimentBranchResult(branch="?", profile_name=name, steps_done=0, ended_early=True, error=str(exc))
            )
            continue

        done = 0
        ended_early = False
        err: Optional[str] = None
        for _ in range(max(1, steps)):
            try:
                latest_manifest, _c, _h = get_simulation(data_dir, sim_id)
                if latest_manifest.status != "active":
                    ended_early = True
                    break
                next_state = run_autopilot_step(cfg, workspace_root, data_dir, sim_id)
                done += 1
                if next_state.major_decision:
                    ended_early = True
                    break
            except (SimEngineError, SimAlreadyEndedError, SimPausedError, AutopilotDisabledError) as exc:
                err = str(exc)
                ended_early = True
                break
            except Exception as exc:  # noqa: BLE001 — 单条策略分支异常不该拖垮整个实验
                logger.exception("对比实验中策略分支推进出现未预期异常：sim_id=%s branch=%s", sim_id, new_branch)
                err = str(exc)
                ended_early = True
                break

        results.append(
            ExperimentBranchResult(
                branch=new_branch, profile_name=name, steps_done=done,
                ended_early=ended_early, error=err,
                final_vars=dict(get_simulation(data_dir, sim_id)[1].vars),
            )
        )

    try:
        switch_branch(data_dir, sim_id, original_active_branch)
    except BranchError:
        pass  # 原分支理论上不会消失，容错兜底即可，不影响实验结果本身

    return results


def run_repeated_experiment(
    cfg,
    workspace_root: Path,
    data_dir: Path,
    sim_id: str,
    *,
    source_branch: str,
    from_step: int,
    steps: int,
    profile: Dict[str, Any],
    n_repeats: int,
) -> List[ExperimentBranchResult]:
    """重复采样（Monte Carlo 雏形，阶段十 / 演进计划 4.2.1 节）：给定
    **同一份**策略画像，从同一个历史节点 fork 出 `n_repeats` 条分支，
    各自独立推进相同步数——差异只来自 LLM 输出本身的随机性，不像
    `run_comparison_experiment()` 那样每条分支套用不同的策略。

    跑完之后可以看出"这个策略的产出其实波动很大"还是"很稳定"这类
    `run_comparison_experiment()`（确定性横向对比）完全看不出来的信息；
    典型用法是把返回结果里每条分支的 `final_vars` 交给
    `analysis.aggregate_field_stats()` 算均值/极差/标准差摘要。

    敏感性分析（单变量扰动）是这个函数的直接复用：调用方把"随机性"换成
    "人为设定的初始 `vars` 差异"——比如对同一个策略跑几次，每次调用前
    先用不同的 `initial_cash` 重新创建/编辑起点分支——不需要这个函数
    本身支持额外参数（演进计划 4.2 节"敏感性分析"一段的实现取舍）。

    实现上复用 `run_comparison_experiment()` 的整套"依次 fork→切换→跑
    steps 步→切回原分支"逻辑，只是把"每条分支一份不同 profile"换成
    "每条分支都是同一份 profile"，为了不产生两份几乎相同的大函数体，
    直接委托过去。

    Args:
        profile: 单份策略画像（格式同 `run_comparison_experiment` 的
            `profiles` 单个元素），`name` 会被忽略，结果里统一用
            `profile.get("name") or "重复采样"` 加序号区分各条分支。
        n_repeats: 重复次数（至少 1）。
    """
    base_name = str(profile.get("name") or "重复采样")
    n = max(1, n_repeats)
    profiles = [{**profile, "name": f"{base_name} #{i + 1}"} for i in range(n)]
    results = run_comparison_experiment(
        cfg, workspace_root, data_dir, sim_id,
        source_branch=source_branch, from_step=from_step, steps=steps, profiles=profiles,
    )
    return results
