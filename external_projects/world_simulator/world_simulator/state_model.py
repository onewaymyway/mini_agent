"""world_simulator/state_model.py — State 数据结构

设计依据：`next_doc/world_simulator_external_project_plan.md` 第 6 节。

不做成单一大而全的 schema，而是"基础字段（id/时间步/摘要）+ 模板私有
字段（自由 JSON）"的组合：基础字段是引擎（`engine.py`/`store.py`）唯一
关心的部分，模板私有字段（`vars`）的结构语义由对应的 skill
（`skills/<template>-template/SKILL.md`）定义，引擎本身不关心具体
字段含义，因此新增一种模拟类型只需要新增一个 skill + 对应的模板私有
字段约定，不需要改这里的代码。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ChoiceOption:
    """引擎给出的一个候选分支选项（供用户/代理选择）。"""

    id: str
    label: str
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChoiceOption":
        return cls(
            id=str(data.get("id", "")),
            label=str(data.get("label", "")),
            description=str(data.get("description", "")),
        )


@dataclass
class SimState:
    """一次模拟推进产生的一个状态节点。

    - `step`：从 0 开始的时间步序号（0 = 初始状态，由
      `spec_generator` 生成，不经过 `engine.advance()`）。
    - `summary`：本状态的一句话摘要，供时间线/看板展示，不要求覆盖
      `vars` 的全部细节。
    - `narrative`：本步推进产生的叙事文本（初始状态可为空）。
    - `vars`：模板私有字段，自由 JSON，结构由对应 skill 定义。
    - `options`：本状态下可选的候选分支（用于下一步推进时选择）；
      为空表示引擎判断"无需用户选择，直接给出默认走向"。
    - `chosen_option_id` / `chosen_by` / `chosen_reason`：产生*下一个*
      状态时实际选择了哪个选项、由谁选的（`user` | `autopilot`）、
      选择理由（自动挡代选时由 LLM 给出）。记在被选择所在的那个状态节点
      上（而不是下一个状态上），这样"某一步为什么会走到下一状态"的
      依据始终和该步的候选项摆在一起，便于时间线回放。
    - `major_decision`：这一步推进产生*本状态*是否被 skill 判定为
      "人生重大转折点"（阶段四自动挡的 `review_mode:
      pause_on_major_decision` 用它决定要不要暂停自动推进，见
      `engine.py`/`autopilot.py`）；阶段一手动挡场景恒为 False，不影响
      任何行为。
    """

    step: int
    summary: str
    narrative: str = ""
    vars: Dict[str, Any] = field(default_factory=dict)
    options: List[ChoiceOption] = field(default_factory=list)
    chosen_option_id: Optional[str] = None
    chosen_by: Optional[str] = None
    chosen_reason: Optional[str] = None
    major_decision: bool = False
    time_label: str = ""
    """这个状态对应"模拟内经过了多长时间"的人类可读描述（比如
    `起点`/`3 个月后`/`第 2 年`），由 skill 按 `manifest.settings`
    里的时间粒度设置给出，engine 本身不解析/计算，原样存取、原样
    展示——粒度是"1 步 = 1 年"还是"1 步 = 1 周"完全由 skill 按提示
    决定，engine 不假设任何具体单位。留空表示 skill 没给（旧数据/
    旧 skill 版本），展示层按"未知时间跨度"处理，不强行补一个假值。
    """

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["options"] = [o.to_dict() if isinstance(o, ChoiceOption) else o for o in self.options]
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SimState":
        options_raw = data.get("options") or []
        options = [
            o if isinstance(o, ChoiceOption) else ChoiceOption.from_dict(o)
            for o in options_raw
        ]
        return cls(
            step=int(data.get("step", 0)),
            summary=str(data.get("summary", "")),
            narrative=str(data.get("narrative", "")),
            vars=dict(data.get("vars") or {}),
            options=options,
            chosen_option_id=data.get("chosen_option_id"),
            chosen_by=data.get("chosen_by"),
            chosen_reason=data.get("chosen_reason"),
            major_decision=bool(data.get("major_decision", False)),
            time_label=str(data.get("time_label", "") or ""),
        )


@dataclass
class SimManifest:
    """一个模拟实例的元信息（对应 `data/<sim_id>/manifest.json`）。

    阶段一只落地"手动挡 + 进行中/已暂停/已结束"这三个最基础的字段；
    `pilot_mode` / `autopilot` 结构在第 4.1 节已有定义，阶段一先把字段
    预留出来（默认手动挡），具体的代理决策逻辑留给阶段四实现，避免
    阶段一为了"字段将来要用"而引入还没有消费方的复杂行为。
    """

    sim_id: str
    template: str
    intent: str
    title: str
    created_at: str
    updated_at: str
    status: str = "active"  # active | paused | ended
    pilot_mode: str = "manual"  # manual | autopilot
    autopilot: Dict[str, Any] = field(default_factory=dict)
    current_step: int = 0
    branch: str = "main"
    settings: Dict[str, Any] = field(default_factory=dict)
    """模拟级别的可配置项，自由 JSON，引擎不关心具体字段含义（同
    `SimState.vars` 的设计取舍），当前约定使用两个字段（见
    `spec_generator.py::resolve_hints()`）：
    - `options_count`：整数，每一步希望给出的候选方向数量（生成初始
      状态和每一步推进都用同一个值），用户在创建向导里设置，未设置时
      沿用 skill 里写的默认建议。
    - `time_granularity`：字符串，"每一步代表多长的模拟内时间"，比如
      `"1 个月"`/`"1 年"`/`"5 年"`/`"自动（由情节决定）"`；不是引擎
      解析的结构化值，只是原样喂给 skill 的一句话提示。
    """

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SimManifest":
        return cls(
            sim_id=str(data["sim_id"]),
            template=str(data.get("template", "")),
            intent=str(data.get("intent", "")),
            title=str(data.get("title", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            status=str(data.get("status", "active")),
            pilot_mode=str(data.get("pilot_mode", "manual")),
            autopilot=dict(data.get("autopilot") or {}),
            current_step=int(data.get("current_step", 0)),
            branch=str(data.get("branch", "main")),
            settings=dict(data.get("settings") or {}),
        )
