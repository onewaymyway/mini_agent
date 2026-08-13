"""
perception/execution_phase.py — Goal 执行阶段（ExecutionPhaseState）

（next_doc/goal_execution_phase_improvement_plan.md）

在 GoalExecutionSpec（"每轮该产出什么"）之上加一层"现在该怎么干"：
explore（探索）/ converge（收敛）/ stable（稳定）/ tidy（整理）/ auto（自动）。

存储：独立文件 `.agent/goal_execution_phase/<goal_id>.json`，不改动
`goals.json` 的 GoalNode 结构，与 GoalExecutionSpec 的隔离存储方式一致。
文件不存在时视为默认状态（mode="auto", locked=False），行为与引入本机制
之前完全一致。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from mini_agent.utils.atomic_write import atomic_write_json

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


VALID_MODES = ("explore", "converge", "stable", "tidy", "auto")

# auto 模式下的默认规则参数（§2）。后续如需可配置，从 AppConfig 读取覆盖。
DEFAULT_EXPLORE_MIN_CYCLES = 3
DEFAULT_SPEC_STABLE_CYCLES = 2
DEFAULT_TIDY_EVERY_N_CYCLES = 0  # 0 = 关闭自动 tidy 插入


@dataclass
class ModeChange:
    at: float
    from_mode: str
    to_mode: str
    reason: str = ""

    def to_dict(self) -> dict:
        return {"at": self.at, "from": self.from_mode, "to": self.to_mode, "reason": self.reason}

    @staticmethod
    def from_dict(d: dict) -> "ModeChange":
        return ModeChange(
            at=float(d.get("at", 0.0)),
            from_mode=d.get("from", ""),
            to_mode=d.get("to", ""),
            reason=d.get("reason", ""),
        )


@dataclass
class ExecutionPhaseState:
    version: int = 1
    goal_id: str = ""
    mode: str = "auto"                # explore | converge | stable | tidy | auto
    locked: bool = False
    stability_score: float = 0.0
    cycles_in_mode: int = 0
    last_tidy_cycle: Optional[int] = None
    mode_history: list[ModeChange] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["mode_history"] = [m.to_dict() for m in self.mode_history]
        return d

    @staticmethod
    def from_dict(d: dict) -> "ExecutionPhaseState":
        mode = d.get("mode", "auto")
        if mode not in VALID_MODES:
            mode = "auto"
        return ExecutionPhaseState(
            version=int(d.get("version", 1)),
            goal_id=d.get("goal_id", ""),
            mode=mode,
            locked=bool(d.get("locked", False)),
            stability_score=float(d.get("stability_score", 0.0)),
            cycles_in_mode=int(d.get("cycles_in_mode", 0)),
            last_tidy_cycle=d.get("last_tidy_cycle"),
            mode_history=[ModeChange.from_dict(m) for m in d.get("mode_history", [])],
            updated_at=float(d.get("updated_at", time.time())),
        )

    def record_transition(self, to_mode: str, reason: str = "") -> None:
        if to_mode == self.mode:
            return
        self.mode_history.append(ModeChange(at=time.time(), from_mode=self.mode, to_mode=to_mode, reason=reason))
        # mode_history 只做诊断展示用，避免无限增长
        if len(self.mode_history) > 50:
            self.mode_history = self.mode_history[-50:]
        self.mode = to_mode
        self.cycles_in_mode = 0


def _phase_dir(paths: "AgentPaths") -> Path:
    return Path(paths.project_root) / ".agent" / "goal_execution_phase"


def _phase_path(paths: "AgentPaths", goal_id: str) -> Path:
    safe_id = goal_id.replace("/", "_")
    return _phase_dir(paths) / f"{safe_id}.json"


def load_phase(paths: "AgentPaths", goal_id: str) -> ExecutionPhaseState:
    """不存在/损坏时返回默认状态（mode="auto", locked=False），不抛异常。"""
    p = _phase_path(paths, goal_id)
    if not p.exists():
        return ExecutionPhaseState(goal_id=goal_id)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        state = ExecutionPhaseState.from_dict(data)
        state.goal_id = goal_id
        return state
    except Exception:
        return ExecutionPhaseState(goal_id=goal_id)


def save_phase(paths: "AgentPaths", state: ExecutionPhaseState) -> None:
    _phase_dir(paths).mkdir(parents=True, exist_ok=True)
    state.updated_at = time.time()
    atomic_write_json(_phase_path(paths, state.goal_id), state.to_dict())


def set_mode(paths: "AgentPaths", goal_id: str, mode: str, *, lock: Optional[bool] = None,
             reason: str = "user_set") -> ExecutionPhaseState:
    """用户手动切换阶段。mode 必须是 VALID_MODES 之一。"""
    if mode not in VALID_MODES:
        raise ValueError(f"invalid execution phase mode: {mode!r}, must be one of {VALID_MODES}")
    state = load_phase(paths, goal_id)
    state.record_transition(mode, reason=reason)
    if lock is not None:
        state.locked = lock
    elif mode != "auto":
        # 手动指定非 auto 阶段时，默认视为用户意图明确，隐式锁定，避免
        # 下一轮自动判定立刻把用户刚设置的阶段覆盖掉。
        state.locked = True
    else:
        state.locked = False
    save_phase(paths, state)
    return state


def unlock_mode(paths: "AgentPaths", goal_id: str) -> ExecutionPhaseState:
    state = load_phase(paths, goal_id)
    state.locked = False
    save_phase(paths, state)
    return state


def resolve_effective_mode(
    state: ExecutionPhaseState,
    *,
    cycle_no: int,
    spec_confirmed: bool,
    spec_recently_revised: bool,
    miss_streak: int = 0,
    tidy_every_n_cycles: int = DEFAULT_TIDY_EVERY_N_CYCLES,
) -> tuple[str, ExecutionPhaseState]:
    """计算本轮的"有效阶段"，并按需推进/落盘 mode_history（调用方负责 save）。

    - locked=True 或 mode != "auto"：直接返回用户指定的阶段，不做规则判定。
    - mode == "auto"：按 §2 规则粗略判定：
        cycle_no <= explore_min_cycles                → explore
        spec 未确认 / 最近仍在被 revise / miss_streak 高 → explore（还不稳定）
        spec 已确认且近期未 revise 且 miss_streak 低    → stable（跳过 converge，
          第一版不做"候选方案对比"识别，converge 仅作为可手动进入的阶段）
        否则                                            → converge（过渡态）
    """
    if state.locked or state.mode != "auto":
        # [Stage B] tidy 阶段是"一次性插入"的维护动作：手动/自动进入 tidy 后，
        # 执行完一轮（cycles_in_mode 已经 >=1，说明已经跑过一次 tidy 提示）
        # 就自动回到 stable 并解除锁定，不需要用户手动切回，避免每轮都停在
        # 整理模式不产出正常内容。
        if state.mode == "tidy" and state.cycles_in_mode >= 1:
            state.last_tidy_cycle = cycle_no
            state.record_transition("stable", reason="tidy_auto_revert")
            state.locked = False
            return "stable", state
        state.cycles_in_mode += 1
        return state.mode, state

    if cycle_no <= DEFAULT_EXPLORE_MIN_CYCLES:
        target = "explore"
    elif not spec_confirmed or spec_recently_revised or miss_streak >= 2:
        target = "explore"
    elif spec_confirmed and not spec_recently_revised and miss_streak == 0:
        target = "stable"
    else:
        target = "converge"

    # [Stage B §2.4] 稳定期周期性自动插入 tidy：仅当已经判定为 stable、
    # 配置了 tidy_every_n_cycles>0、且距上次 tidy 已满足轮次间隔时触发。
    # 触发后本轮 effective mode 直接给 tidy（下一轮由上面的
    # "tidy 一轮后自动回 stable"逻辑收尾），不改变 state.mode 本身
    # （仍是 "auto"）。
    if target == "stable" and tidy_every_n_cycles and tidy_every_n_cycles > 0:
        last_tidy = state.last_tidy_cycle or 0
        if cycle_no - last_tidy >= tidy_every_n_cycles:
            target = "tidy"
            state.last_tidy_cycle = cycle_no

    # stability_score：粗略地用"是否达到 stable 判定条件"映射到 0~1，
    # 仅供展示参考，不参与其他逻辑。
    if target == "stable":
        state.stability_score = 1.0
    elif target == "converge":
        state.stability_score = 0.6
    else:
        state.stability_score = min(0.4, cycle_no / max(DEFAULT_EXPLORE_MIN_CYCLES, 1) * 0.4)

    if target != state.mode:
        # auto 模式下的自动切换不改变 state.mode 字段本身（mode 仍保持
        # "auto"，代表"跟随自动判定"），只记一条历史，effective mode 单独
        # 返回。真正把 state.mode 写死为具体阶段的，只有用户手动 set_mode。
        state.mode_history.append(
            ModeChange(at=time.time(), from_mode=f"auto:{state.mode if state.mode != 'auto' else 'auto'}",
                       to_mode=f"auto:{target}", reason="rule_based_auto")
        )
        if len(state.mode_history) > 50:
            state.mode_history = state.mode_history[-50:]
    state.cycles_in_mode += 1
    return target, state
