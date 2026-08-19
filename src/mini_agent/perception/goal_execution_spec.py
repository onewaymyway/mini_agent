"""
perception/goal_execution_spec.py — Goal 执行规范（GoalExecutionSpec）
（next_doc/goal_execution_spec_generation_plan.md）

镜像 goal_mode/spec.py::GoalSpecBuilder 的"草稿 → 反馈迭代 → 确认冻结"架构，
但产出内容不同：GoalSpec 是"验收标准"，GoalExecutionSpec 是"这个 Goal 具体
怎么执行"——每一轮该产出什么文件、跨轮需要显式记住/传递什么结构化信息、
要不要额外的子目录组织、用什么标准判断"这一轮算做到位了"。

存储：独立文件 `.agent/goal_execution_specs/<goal_id>.json`，不塞进
`goals.json` 的 GoalNode（理由见方案 §4）。GoalNode 只保留一个轻量指针字段
`execution_spec_confirmed: bool`。

第一版实现范围（对照方案的取舍）：
  - builder_mode 支持 "llm"（裸单轮 chat completion）、"agent"（只读受限
    Agent，镜像 goal_mode/spec.py::GoalSpecBuilder._run_builder_agent）和
    "auto"（关键词规则粗略判断是否需要项目上下文，命中走 agent，否则走
    llm；比 GoalSpecBuilder 的 "auto" 少了"LLM 自报 needs_project_context
    后二次重生成"那层兜底，见 GoalExecutionSpecBuilder._run_builder 的
    说明）。
  - §5.1 轻量核对（纯文件名/key 字符串匹配）在本模块提供纯函数实现，
    由 evolution/goal_cron_bridge.py 在每轮触发时调用。
  - 模板库（§7）随本模块一起提供，见
    `perception/goal_execution_spec_templates/*.json`。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from mini_agent.utils.atomic_write import atomic_write_json

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from mini_agent.storage.paths import AgentPaths


_VALID_VERIFICATION_METHODS = ("run_command", "file_check", "manual_review")


def _safe_item_dict(x, primary_field: str) -> dict:
    """把 LLM 输出的列表元素安全转换成 dict，供各 `*.from_dict()` 使用。

    LLM 偶尔会把"对象数组"字段（如 `execution_routine`）偷懒写成纯字符串
    数组（如 `["每周检查一次", ...]`），如果各 `from_dict()` 直接对元素调用
    `.get(...)` 会因为元素是 `str` 而抛 `AttributeError`（曾经出现过的线上
    报错：`'str' object has no attribute 'get'`）。这里统一兜底：
      - 元素本身就是 dict → 原样返回。
      - 元素是字符串 → 包装成 `{primary_field: 字符串}`，尽量保留信息而不是
        直接丢弃（大多数场景下这就是模型想表达的内容）。
      - 其它类型（None/数字/列表等）→ 返回空 dict，交由各字段自身的默认值
        兜底，不让类型错误冒泡成 500。
    """
    if isinstance(x, dict):
        return x
    if isinstance(x, str):
        return {primary_field: x}
    return {}


def _safe_repr(x, limit: int = 500) -> str:
    """把任意值转成便于写进日志的字符串，截断避免单条日志过大。"""
    try:
        s = json.dumps(x, ensure_ascii=False, default=str)
    except Exception:
        s = str(x)
    if len(s) > limit:
        s = s[:limit] + f"...(截断，原长度 {len(s)})"
    return s


def _build_items(cls, items, primary_field: str, field_name: str, context: Optional[dict] = None) -> list:
    """把 LLM 输出的某个数组字段安全转换成一组 dataclass 实例。

    与逐个直接 `[cls.from_dict(x) for x in items]` 相比：
      - `items` 本身类型不对（LLM 把数组字段写成了字符串/对象）时，不抛异常，
        记录一条包含完整字段名/原始值/goal 上下文的日志后返回空列表。
      - 数组内单个元素解析失败（理论上 `_safe_item_dict` 兜底后已经很难再
        失败，这里是双保险）时，跳过这一个元素、记录日志，不影响其它元素
        和整份 spec 的生成。
    """
    from mini_agent.errors import log_exception

    out = []
    if items is None:
        return out
    if not isinstance(items, list):
        log_exception(
            TypeError(f"字段 {field_name} 期望是数组，实际类型是 {type(items).__name__}"),
            where="mini_agent.perception.goal_execution_spec._build_items",
            extra={**(context or {}), "field": field_name, "raw_value": _safe_repr(items)},
        )
        return out
    for idx, x in enumerate(items):
        try:
            out.append(cls.from_dict(_safe_item_dict(x, primary_field)))
        except Exception as e:
            log_exception(
                e,
                where="mini_agent.perception.goal_execution_spec._build_items",
                extra={
                    **(context or {}),
                    "field": field_name,
                    "index": idx,
                    "raw_item": _safe_repr(x),
                },
            )
            continue
    return out


class GoalExecutionSpecBuildError(RuntimeError):
    """GoalExecutionSpecBuilder 生成/修订草案时的不可恢复失败。

    与"LLM 正常返回但字段为空"（合法结果，代表"这个 Goal 不需要特殊规范"）
    是两码事——只有解析彻底失败、且兜底草稿也构造不出来时才会抛出。
    实际实现里 build_draft()/revise() 均带兜底草稿，正常路径不会抛出，
    保留这个异常类是为了与 GoalSpecBuildError 的命名/职责对称，供未来
    需要"严格模式"的调用方使用。
    """
    pass


# ── 数据模型 ──────────────────────────────────────────────────────────────────

@dataclass
class Deliverable:
    name: str
    description: str = ""
    naming_pattern: str = ""
    required_every_cycle: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d) -> "Deliverable":
        # 兜底：LLM 偶尔会把数组元素写成非 dict（字符串/None 等），统一转换
        # 后再取值，避免 `.get()` 直接抛 AttributeError。
        d = d if isinstance(d, dict) else _safe_item_dict(d, "name")
        return Deliverable(
            name=d.get("name", ""),
            description=d.get("description", ""),
            naming_pattern=d.get("naming_pattern", ""),
            required_every_cycle=bool(d.get("required_every_cycle", True)),
        )


@dataclass
class HandoffField:
    key: str
    description: str = ""
    example: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d) -> "HandoffField":
        d = d if isinstance(d, dict) else _safe_item_dict(d, "key")
        return HandoffField(
            key=d.get("key", ""),
            description=d.get("description", ""),
            example=d.get("example", ""),
        )


_VALID_RETENTIONS = ("latest_only", "append", "unbounded")


@dataclass
class SubDirectory:
    name: str
    purpose: str = ""
    # [goal_output_directory_and_execution_phase_redesign_plan.md §2.4]
    # retention 让 tidy 阶段的核查变成确定性代码逻辑：
    #   latest_only ← 每轮覆写，只保留最新一份
    #   append      ← 按轮次累积保留
    #   unbounded   ← 不做特殊管理，人工决定（缺省值，兼容旧 spec 文件）
    retention: str = "unbounded"
    # 与 Deliverable.naming_pattern 语义一致，避免命名风格逐轮漂移
    naming_pattern: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d) -> "SubDirectory":
        d = d if isinstance(d, dict) else _safe_item_dict(d, "name")
        retention = d.get("retention", "unbounded")
        if retention not in _VALID_RETENTIONS:
            retention = "unbounded"
        return SubDirectory(
            name=d.get("name", ""),
            purpose=d.get("purpose", ""),
            retention=retention,
            naming_pattern=d.get("naming_pattern", ""),
        )


_VALID_OUTPUT_MODES = ("converging", "accretive", "capability_hardening", "hybrid")
_VALID_NEW_TOPIC_DISCOVERY = ("none", "intrinsic")


@dataclass
class RoutineStep:
    """[goal_output_directory_and_execution_phase_redesign_plan.md §Stage8]
    execution_routine 的单个步骤——规范层收敛后，"这一轮该做的标准动作
    序列"，用来把阶段判定信号从"本轮内容像不像"换成"routine 有没有变化"。
    """
    step: str

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d) -> "RoutineStep":
        # [线上报错兜底] LLM 曾经把 execution_routine 写成纯字符串数组
        # （`["每周检查一次", ...]`）而不是 `[{"step": "..."}, ...]`，
        # 元素是 str 时直接 `.get()` 会抛 AttributeError（HTTP 500 的根因）。
        # 这里兼容处理：str 直接当作 step 内容，其它非 dict 类型给空字符串。
        if isinstance(d, str):
            return RoutineStep(step=d)
        if not isinstance(d, dict):
            return RoutineStep(step="")
        return RoutineStep(step=d.get("step", ""))


@dataclass
class Criterion:
    text: str
    verification_method: str = "manual_review"   # run_command | file_check | manual_review

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d) -> "Criterion":
        d = d if isinstance(d, dict) else _safe_item_dict(d, "text")
        vm = d.get("verification_method", "manual_review")
        if vm not in _VALID_VERIFICATION_METHODS:
            vm = "manual_review"
        return Criterion(text=d.get("text", ""), verification_method=vm)


@dataclass
class GoalExecutionSpec:
    """一个 Goal 的执行规范（每 Goal 一份，可覆盖/可留空回退默认）。"""

    version: int = 1
    goal_id: str = ""
    generated_at: float = field(default_factory=time.time)
    confirmed: bool = False
    confirmed_at: Optional[float] = None
    locked_fields: list[str] = field(default_factory=list)

    deliverables: list[Deliverable] = field(default_factory=list)
    handoff_fields: list[HandoffField] = field(default_factory=list)
    sub_directories: list[SubDirectory] = field(default_factory=list)
    per_cycle_criteria: list[Criterion] = field(default_factory=list)
    overall_completion_criteria: list[Criterion] = field(default_factory=list)
    special_constraints: list[str] = field(default_factory=list)

    # [goal_output_directory_and_execution_phase_redesign_plan.md §Stage8]
    # 规范层/内容层两层模型新增字段，均可选、缺省不影响任何既有行为：
    #   output_mode: 决定 tidy/阶段判定用哪套默认模板，缺省 "converging"
    #     与 Stage 0-7 上线时的既有行为完全一致（向后兼容）。
    #   execution_routine: 收敛后的"每一轮标准动作序列"，阶段判定改看这个
    #     序列有没有实质性变化，而不是看本轮产出内容本身。
    #   cadence: 执行节奏说明（纯文本展示用，不参与调度判定）。
    #   new_topic_discovery: "intrinsic" 显式声明"内容层常新是正常现象"，
    #     避免 accretive/hybrid 型 goal 被误判成规范未收敛。
    #   hardening_target: capability_hardening 型 goal 的外部固化目标路径
    #     （如 .claude/skills/browser-cdp），converge 阶段"搬迁"的落地目标
    #     从本地 output/ 换成这里；为空代表沿用 output/ 内部落地（默认）。
    #   sub_exploration: hybrid 型 goal 用，声明主轨之外独立生命周期的
    #     内容子探索说明（纯文本，不参与主轨 spec_phase 判定）。
    # 依赖声明按当前决策走"软约束过渡"：不新增结构化字段，用户在 goal
    # description 里手写依赖路径即可，spec 层暂不建模（见 Stage8 未决问题）。
    output_mode: str = "converging"
    execution_routine: list[RoutineStep] = field(default_factory=list)
    cadence: str = ""
    new_topic_discovery: str = "none"
    hardening_target: str = ""
    sub_exploration: str = ""

    # 生成失败时的兜底诊断信息（正常路径为 None）
    generation_error: Optional[str] = None

    # §5.1 轻量核对：连续多少轮未匹配到（分开记 deliverables/handoff 两类，
    # 但为了简单起见，用一个共享计数器——任一类没匹配上就 +1，任一类匹配上
    # 就清零，理由见 goal_cron_bridge._soft_check_execution_spec 的注释）。
    soft_check_miss_streak: int = 0
    soft_check_alerted: bool = False

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "goal_id": self.goal_id,
            "generated_at": self.generated_at,
            "confirmed": self.confirmed,
            "confirmed_at": self.confirmed_at,
            "locked_fields": list(self.locked_fields),
            "deliverables": [d.to_dict() for d in self.deliverables],
            "handoff_fields": [h.to_dict() for h in self.handoff_fields],
            "sub_directories": [s.to_dict() for s in self.sub_directories],
            "per_cycle_criteria": [c.to_dict() for c in self.per_cycle_criteria],
            "overall_completion_criteria": [c.to_dict() for c in self.overall_completion_criteria],
            "special_constraints": list(self.special_constraints),
            "output_mode": self.output_mode,
            "execution_routine": [r.to_dict() for r in self.execution_routine],
            "cadence": self.cadence,
            "new_topic_discovery": self.new_topic_discovery,
            "hardening_target": self.hardening_target,
            "sub_exploration": self.sub_exploration,
            "generation_error": self.generation_error,
            "soft_check_miss_streak": self.soft_check_miss_streak,
            "soft_check_alerted": self.soft_check_alerted,
        }

    @staticmethod
    def from_dict(d: dict) -> "GoalExecutionSpec":
        output_mode = d.get("output_mode", "converging")
        if output_mode not in _VALID_OUTPUT_MODES:
            output_mode = "converging"
        new_topic_discovery = d.get("new_topic_discovery", "none")
        if new_topic_discovery not in _VALID_NEW_TOPIC_DISCOVERY:
            new_topic_discovery = "none"
        return GoalExecutionSpec(
            version=int(d.get("version", 1)),
            goal_id=d.get("goal_id", ""),
            generated_at=float(d.get("generated_at", time.time())),
            confirmed=bool(d.get("confirmed", False)),
            confirmed_at=d.get("confirmed_at"),
            locked_fields=list(d.get("locked_fields", [])),
            # [兜底] 权威存储文件理论上只会写入本模块自己 to_dict() 产出的合法
            # 结构，但仍用 _build_items 兜底防御手改/历史遗留脏数据——单个元素
            # 格式不对时跳过并记日志，不让 load_spec() 直接抛异常。
            deliverables=_build_items(Deliverable, d.get("deliverables"), "name", "deliverables",
                                       {"goal_id": d.get("goal_id", ""), "source": "GoalExecutionSpec.from_dict"}),
            handoff_fields=_build_items(HandoffField, d.get("handoff_fields"), "key", "handoff_fields",
                                         {"goal_id": d.get("goal_id", ""), "source": "GoalExecutionSpec.from_dict"}),
            sub_directories=_build_items(SubDirectory, d.get("sub_directories"), "name", "sub_directories",
                                          {"goal_id": d.get("goal_id", ""), "source": "GoalExecutionSpec.from_dict"}),
            per_cycle_criteria=_build_items(Criterion, d.get("per_cycle_criteria"), "text", "per_cycle_criteria",
                                             {"goal_id": d.get("goal_id", ""), "source": "GoalExecutionSpec.from_dict"}),
            overall_completion_criteria=_build_items(
                Criterion, d.get("overall_completion_criteria"), "text", "overall_completion_criteria",
                {"goal_id": d.get("goal_id", ""), "source": "GoalExecutionSpec.from_dict"},
            ),
            special_constraints=[str(s) for s in (d.get("special_constraints") or [])],
            output_mode=output_mode,
            execution_routine=_build_items(RoutineStep, d.get("execution_routine"), "step", "execution_routine",
                                            {"goal_id": d.get("goal_id", ""), "source": "GoalExecutionSpec.from_dict"}),
            cadence=d.get("cadence", ""),
            new_topic_discovery=new_topic_discovery,
            hardening_target=d.get("hardening_target", ""),
            sub_exploration=d.get("sub_exploration", ""),
            generation_error=d.get("generation_error"),
            soft_check_miss_streak=int(d.get("soft_check_miss_streak", 0)),
            soft_check_alerted=bool(d.get("soft_check_alerted", False)),
        )

    def is_empty(self) -> bool:
        """全部字段为空 == 等价于"沿用 output_workspace.py 通用行为"。"""
        return not (
            self.deliverables or self.handoff_fields or self.sub_directories
            or self.per_cycle_criteria or self.overall_completion_criteria
            or self.special_constraints or self.execution_routine
        )

    def render_summary_for_user(self) -> str:
        """渲染成给用户展示确认用的可读文本（看板/CLI 共用）。"""
        lines = [f"执行规范（第 {self.version} 版，{'已确认' if self.confirmed else '草稿'}）："]
        if self.generation_error:
            lines.append(f"⚠️ 上次生成存在问题：{self.generation_error}")
        lines.append("")
        lines.append(f"产出模式 output_mode：{self.output_mode}")
        if self.cadence:
            lines.append(f"执行节奏 cadence：{self.cadence}")
        if self.new_topic_discovery == "intrinsic":
            lines.append("内容常新声明：new_topic_discovery=intrinsic（本轮出现新内容属正常现象，不代表规范未收敛）")
        if self.hardening_target:
            lines.append(f"外部固化目标 hardening_target：{self.hardening_target}")
        if self.sub_exploration:
            lines.append(f"子探索说明 sub_exploration：{self.sub_exploration}")
        lines.append("")
        lines.append("执行例程 execution_routine：")
        if self.execution_routine:
            for i, r in enumerate(self.execution_routine, 1):
                lines.append(f"  {i}. {r.step}")
        else:
            lines.append("  （无，沿用阶段默认 prompt 引导）")
        lines.append("")
        lines.append("产出物 deliverables：")
        if self.deliverables:
            for d in self.deliverables:
                req = "每轮必需" if d.required_every_cycle else "可选"
                lines.append(f"  - {d.name}（{req}）：{d.description}")
        else:
            lines.append("  （无）")
        lines.append("")
        lines.append("跨轮传递 handoff_fields：")
        if self.handoff_fields:
            for h in self.handoff_fields:
                lines.append(f"  - {h.key}：{h.description}" + (f"（示例：{h.example}）" if h.example else ""))
        else:
            lines.append("  （无）")
        lines.append("")
        lines.append("子目录 sub_directories：")
        if self.sub_directories:
            for s in self.sub_directories:
                extra = f"（retention: {s.retention}"
                extra += f"，命名：{s.naming_pattern}）" if s.naming_pattern else "）"
                lines.append(f"  - {s.name}：{s.purpose} {extra}")
        else:
            lines.append("  （无）")
        lines.append("")
        lines.append("每轮完成标准 per_cycle_criteria：")
        if self.per_cycle_criteria:
            for c in self.per_cycle_criteria:
                lines.append(f"  - [{c.verification_method}] {c.text}")
        else:
            lines.append("  （无）")
        if self.overall_completion_criteria:
            lines.append("")
            lines.append("整体完成标准 overall_completion_criteria：")
            for c in self.overall_completion_criteria:
                lines.append(f"  - [{c.verification_method}] {c.text}")
        if self.special_constraints:
            lines.append("")
            lines.append("特殊约束 special_constraints：")
            for s in self.special_constraints:
                lines.append(f"  - {s}")
        return "\n".join(lines)

    def render_prompt_block(self) -> str:
        """格式化成拼进子 Objective description 的"本 Goal 的执行规范"文字块。

        供 goal_cron_bridge._append_execution_spec_context() /
        goal_backlog._append_onetime_execution_spec_context() 调用，两处对称
        复用同一份格式化逻辑。
        """
        if self.is_empty():
            return ""
        lines = ["【本 Goal 的执行规范（用户已确认）】"]
        if self.new_topic_discovery == "intrinsic":
            lines.append("说明：本 Goal 内容层天然常新（new_topic_discovery=intrinsic），"
                          "出现未见过的主题/信息源属正常现象，不代表规范需要重新探索。")
        if self.hardening_target:
            lines.append(f"外部固化目标：{self.hardening_target}（验证有效的产出应最终落地到这里，而非仅留在本 Goal 私有目录）")
        if self.sub_exploration:
            lines.append(f"子探索说明：{self.sub_exploration}")
        if self.execution_routine:
            lines.append("本轮请遵循以下已收敛的标准执行例程：")
            for i, r in enumerate(self.execution_routine, 1):
                lines.append(f"  {i}. {r.step}")
        if self.deliverables:
            lines.append("本轮应产出：")
            for d in self.deliverables:
                req = "（每轮必需）" if d.required_every_cycle else "（可选）"
                pattern = f"，命名遵循：{d.naming_pattern}" if d.naming_pattern else ""
                lines.append(f"  - {d.name}{req}：{d.description}{pattern}")
        if self.sub_directories:
            lines.append("子目录组织：")
            for s in self.sub_directories:
                extra = f"（retention: {s.retention}"
                extra += f"，命名：{s.naming_pattern}）" if s.naming_pattern else "）"
                lines.append(f"  - {s.name}：{s.purpose} {extra}")
        if self.per_cycle_criteria:
            lines.append("本轮完成需满足：")
            for c in self.per_cycle_criteria:
                lines.append(f"  - {c.text}")
        if self.special_constraints:
            lines.append("特殊约束（务必遵守）：")
            for s in self.special_constraints:
                lines.append(f"  - {s}")
        if self.handoff_fields:
            keys = ", ".join(h.key for h in self.handoff_fields)
            lines.append(
                "跨轮需要显式记住/传递的信息：完成后请在回答末尾用一个"
                " ```handoff\\n{...}\\n``` JSON 代码块，按以下 key 填写"
                f"（{keys}）："
            )
            for h in self.handoff_fields:
                example = f"，示例值：{h.example}" if h.example else ""
                lines.append(f"  - {h.key}：{h.description}{example}")
        return "\n".join(lines)


# ── 存储 ──────────────────────────────────────────────────────────────────────

def _spec_dir(paths: "AgentPaths") -> Path:
    return Path(paths.project_root) / ".agent" / "goal_execution_specs"


def _spec_path(paths: "AgentPaths", goal_id: str) -> Path:
    return _spec_dir(paths) / f"{goal_id}.json"


def load_spec(paths: "AgentPaths", goal_id: str) -> Optional[GoalExecutionSpec]:
    """读取指定 Goal 的执行规范。文件不存在/损坏时返回 None（不抛异常）。"""
    p = _spec_path(paths, goal_id)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.perception.goal_execution_spec.load_spec")
        return None
    return GoalExecutionSpec.from_dict(data)


def save_spec(paths: "AgentPaths", goal_id: str, spec: GoalExecutionSpec) -> Path:
    """把执行规范写入独立文件（`.agent/goal_execution_specs/<goal_id>.json`，
    这是权威存储，行为不变）。返回写入路径。

    [goal_output_directory_and_execution_phase_redesign_plan.md §4 / Stage 2]
    额外做两件事，失败均不影响权威存储的写入（try/except 兜底，只记录，不
    抛出——落盘 SPEC.md/SPEC.json 和历史归档是"锦上添花"的可见性能力，不应
    因为 recurring Goal 的产出目录一时不可写而导致 spec 保存本身失败）：
      1. 若旧版本文件存在，先把它复制进
         `spec/history/v{旧version}_{confirmed_at 或 generated_at 时间}.md/.json`，
         形成审计轨迹。
      2. 把当前版本渲染落盘到 `spec/SPEC.md`（`render_summary_for_user()` 的
         落盘结果，人类可读）+ `spec/SPEC.json`（结构化数据），供用户直接在
         文件系统里打开查看，不用跑命令。
    """
    spec.goal_id = goal_id
    d = _spec_dir(paths)
    d.mkdir(parents=True, exist_ok=True)
    p = _spec_path(paths, goal_id)

    try:
        _archive_prior_spec_version(paths, goal_id)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where="mini_agent.perception.goal_execution_spec.save_spec(_archive_prior_spec_version)")

    atomic_write_json(p, spec.to_dict())

    try:
        _write_spec_snapshot(paths, goal_id, spec)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where="mini_agent.perception.goal_execution_spec.save_spec(_write_spec_snapshot)")

    return p


def delete_spec(paths: "AgentPaths", goal_id: str) -> bool:
    """[看板目标删除功能] 删除指定 Goal 的执行规范权威存储文件
    （`.agent/goal_execution_specs/<goal_id>.json`）。

    不处理 `spec/`（history/SPEC.md/SPEC.json，见 `_spec_workspace_dir()`）
    这份可见性快照——它落在 `goal_spec_dir()`（`.agent/daemon_run_outputs/
    goals/<goal_id>/spec/`）下，属于 `output_workspace.goal_output_base_dir()`
    整棵子树的一部分，调用方（API 路由层）删除 Goal 时会连同该目录整体
    一起 `rmtree`，不需要在这里重复处理，避免两处删除逻辑各管一半、
    互相依赖顺序。

    文件不存在时视为已经"删除成功"（幂等），返回 True；真正的 OSError
    才返回 False，供调用方决定要不要在响应里提示"部分清理失败"。
    """
    p = _spec_path(paths, goal_id)
    try:
        p.unlink(missing_ok=True)
        return True
    except OSError as e:
        from mini_agent.errors import log_exception
        log_exception(e, where="mini_agent.perception.goal_execution_spec.delete_spec")
        return False


def _spec_workspace_dir(paths: "AgentPaths", goal_id: str) -> Optional[Path]:
    """定位 `goal_output_directory_and_execution_phase_redesign_plan.md` §4
    里的 `spec/` 目录（当前版本 SPEC.md/SPEC.json + history/）。依赖
    evolution/output_workspace.py::goal_spec_dir()——反向 import 会形成循环
    依赖（output_workspace 不依赖本模块，本模块按需惰性 import 它），因此
    延迟到函数体内 import；导入失败（理论上不会发生，只是防御）时返回 None，
    调用方据此跳过快照/归档逻辑，不影响权威存储写入。
    """
    try:
        from mini_agent.evolution.output_workspace import goal_spec_dir
    except Exception:
        return None
    return goal_spec_dir(paths, goal_id)


def _write_spec_snapshot(paths: "AgentPaths", goal_id: str, spec: GoalExecutionSpec) -> None:
    spec_dir = _spec_workspace_dir(paths, goal_id)
    if spec_dir is None:
        return
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "SPEC.md").write_text(spec.render_summary_for_user(), encoding="utf-8")
    atomic_write_json(spec_dir / "SPEC.json", spec.to_dict())


def _archive_prior_spec_version(paths: "AgentPaths", goal_id: str) -> Optional[Path]:
    """写入新版本前，把 `spec/SPEC.md`/`SPEC.json` 当前内容（即"即将被覆盖
    的旧版本"）复制进 `spec/history/v{旧version}_{时间戳}.md/.json`。

    时间戳优先取旧 spec 的 confirmed_at，其次 generated_at，都没有则用当前
    时间——尽量反映"这份旧版本实际生效的时间"，而不是归档动作本身发生的
    时间。旧版本文件不存在（例如第一次保存）时静默跳过，返回 None。
    """
    spec_dir = _spec_workspace_dir(paths, goal_id)
    if spec_dir is None:
        return None
    old_json = spec_dir / "SPEC.json"
    old_md = spec_dir / "SPEC.md"
    if not old_json.exists():
        return None

    try:
        old_data = json.loads(old_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    old_version = old_data.get("version", "0")
    ts = old_data.get("confirmed_at") or old_data.get("generated_at") or time.time()
    date_str = time.strftime("%Y-%m-%d", time.localtime(ts))

    history_dir = spec_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    stem = f"v{old_version}_{date_str}"

    dest_json = history_dir / f"{stem}.json"
    dest_md = history_dir / f"{stem}.md"
    # 同一天内对同一版本号重复保存（罕见但可能）时避免互相覆盖
    suffix = 2
    while dest_json.exists() or dest_md.exists():
        stem = f"v{old_version}_{date_str}_{suffix}"
        dest_json = history_dir / f"{stem}.json"
        dest_md = history_dir / f"{stem}.md"
        suffix += 1

    dest_json.write_text(json.dumps(old_data, ensure_ascii=False, indent=2), encoding="utf-8")
    if old_md.exists():
        dest_md.write_text(old_md.read_text(encoding="utf-8"), encoding="utf-8")
    return dest_json


def list_spec_history(paths: "AgentPaths", goal_id: str) -> list[dict]:
    """列出某个 Goal 的历史 spec 版本摘要（供 CLI/看板展示"这个 Goal 什么
    时候、为什么改变了产出规则"），按归档时间倒序。`spec/history/` 不存在
    或为空时返回空列表。"""
    spec_dir = _spec_workspace_dir(paths, goal_id)
    if spec_dir is None:
        return []
    history_dir = spec_dir / "history"
    if not history_dir.is_dir():
        return []
    out = []
    for p in history_dir.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "file": p.name,
            "version": data.get("version"),
            "confirmed_at": data.get("confirmed_at"),
            "generated_at": data.get("generated_at"),
            "confirmed": data.get("confirmed", False),
            # [goal_output_directory_and_execution_phase_redesign_plan.md
            # §Stage8c] 附带该历史版本的 execution_routine 原始步骤列表，
            # 供 goal_cron_bridge.py 组装 routine_texts 传给
            # compute_routine_stability_signal()，不需要额外再读一次文件。
            # 是"摘要 dict"里新增的一个 key，不影响既有调用方（目前只有
            # CLI/看板展示场景，均未读取本字段，纯增量，无回归风险）。
            "execution_routine": [
                (r.get("step", "") if isinstance(r, dict) else "")
                for r in (data.get("execution_routine") or [])
            ],
        })
    out.sort(key=lambda x: (x.get("confirmed_at") or x.get("generated_at") or 0), reverse=True)
    return out


def serialize_routine_steps(steps) -> str:
    """[goal_output_directory_and_execution_phase_redesign_plan.md §Stage8c]
    把一份 `execution_routine`（`RoutineStep` 列表，或已经是字符串列表）
    序列化成 `compute_routine_stability_signal()` 期望的单段文本，规则与
    该函数 docstring 里约定的 `"\\n".join(r.step for r in routine)` 一致。

    空列表返回空字符串（调用方据此判断这个版本"没有 routine 内容"，
    组装 `routine_texts` 时应跳过而不是传入空字符串占位）。
    """
    texts = []
    for r in steps or []:
        if isinstance(r, RoutineStep):
            texts.append(r.step)
        elif isinstance(r, dict):
            texts.append(r.get("step", ""))
        else:
            texts.append(str(r))
    return "\n".join(t for t in texts if t)


def delete_spec(paths: "AgentPaths", goal_id: str) -> bool:
    """删除指定 Goal 的执行规范文件（Goal 本身被删除/重置时可选调用）。"""
    p = _spec_path(paths, goal_id)
    try:
        p.unlink()
        return True
    except OSError:
        return False


# ── 模板库（§7） ──────────────────────────────────────────────────────────────

_TEMPLATES_DIR = Path(__file__).parent / "goal_execution_spec_templates"


def list_templates() -> list[dict]:
    """列出模板库里全部模板的 {id, name, applicable_to, keywords} 摘要，供
    UI/CLI 展示；`keywords` 供 `suggest_template()` 做关键词匹配，也直接
    暴露出来供调用方自行展示"为什么推荐了这个模板"。"""
    out = []
    if not _TEMPLATES_DIR.is_dir():
        return out
    for p in sorted(_TEMPLATES_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "id": data.get("template_id", p.stem),
            "name": data.get("name", p.stem),
            "applicable_to": data.get("applicable_to", ""),
            "keywords": list(data.get("keywords") or []),
        })
    return out


def suggest_template(goal_title: str, goal_description: str = "") -> Optional[str]:
    """[goal_execution_spec_generation_plan.md §7 末段 / implementation_
    record.md 未实施清单第 3 项] 关键词规则粗略匹配 Goal 的 title+description，
    命中某个模板的 `keywords` 数量最多的即为推荐模板；全部模板都 0 命中时
    返回 None（代表"不用模板"，调用方应展示"完全从零生成"选项且不预选
    任何模板，而不是随便选一个凑数）。

    只做最朴素的子串计数匹配，不做分词/语义匹配——第一版的目标是"给用户
    一个默认预选，减少手动挑选的心智负担"，不是"精确判断 Goal 类型"，
    用户始终可以在 UI 里改选或选"不用模板"，匹配错了代价很低。
    """
    text = f"{goal_title or ''} {goal_description or ''}"
    if not text.strip():
        return None
    best_id: Optional[str] = None
    best_score = 0
    for tpl in list_templates():
        score = sum(1 for kw in tpl.get("keywords", []) if kw and kw in text)
        if score > best_score:
            best_score = score
            best_id = tpl["id"]
    return best_id if best_score > 0 else None


def load_template(template_id: str) -> Optional[dict]:
    """按 id 加载单个模板的原始 dict（含 skeleton 字段），不存在返回 None。"""
    if not template_id:
        return None
    p = _TEMPLATES_DIR / f"{template_id}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ── JSON 提取（与 goal_mode/spec.py 同款容错逻辑，故意不做成公共 util——两处
#    各自独立演进的解析细节曾经出现过分歧，保持各自一份更省心） ──────────────

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_BRACE_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str, context: Optional[dict] = None) -> Optional[dict]:
    """从 LLM 原始输出里抠出 JSON 对象。

    正则命中的片段哪怕能被 `json.loads` 成功解析，也不保证解析结果是
    dict——LLM 偶尔会在正文里夹带一段本身也合法但类型不对的 JSON（比如
    示例性的字符串/数组），被 `_BRACE_RE`/`_JSON_FENCE_RE` 误抠中并解析
    成功，此时下游 `_spec_from_llm_data(data, ...)` 里 `data.get(...)`
    会直接因为类型不对而崩掉（曾经出现过 `'str' object has no attribute
    'get'` 的线上报错）。这里统一加一道类型校验，非 dict 一律按"解析
    失败"处理，交由调用方已有的兜底逻辑（保留上一版内容 / 返回
    generation_error）接管，不让类型错误冒泡成 500。

    `context`：可选，调用方传入 goal_id/version/effective_path 等排障信息，
    连同 LLM 原始输出一起写进日志，方便事后定位是"哪个 Goal 的哪次生成"
    出现了解析问题，而不是只有一句孤立的错误信息。
    """
    ctx = dict(context or {})
    m = _JSON_FENCE_RE.search(text or "")
    candidate = m.group(1) if m else None
    if candidate is None:
        m2 = _BRACE_RE.search(text or "")
        candidate = m2.group(0) if m2 else None
    if candidate is None:
        if text and text.strip():
            from mini_agent.errors import log_exception
            log_exception(
                ValueError("LLM 原始输出里未找到任何 JSON 对象片段（既无 ```json 代码块也无花括号包裹内容）"),
                where="mini_agent.perception.goal_execution_spec._extract_json",
                extra={**ctx, "raw_llm_output": _safe_repr(text, limit=4000)},
            )
        return None
    try:
        parsed = json.loads(candidate)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(
            _mini_agent_exc,
            where="mini_agent.perception.goal_execution_spec._extract_json",
            extra={**ctx, "raw_llm_output": _safe_repr(text, limit=4000), "candidate": _safe_repr(candidate, limit=4000)},
        )
        return None
    if not isinstance(parsed, dict):
        from mini_agent.errors import log_exception
        log_exception(
            TypeError(f"LLM JSON 解析结果类型不是 dict，而是 {type(parsed).__name__}：{candidate[:200]!r}"),
            where="mini_agent.perception.goal_execution_spec._extract_json",
            extra={**ctx, "raw_llm_output": _safe_repr(text, limit=4000)},
        )
        return None
    return parsed


def _spec_from_llm_data(data: dict, goal_id: str, version: int, locked_fields: Optional[list[str]] = None) -> GoalExecutionSpec:
    """把 LLM 返回的原始 JSON dict 转换成 `GoalExecutionSpec`。

    [线上报错修复] 这里是 LLM 自由文本 JSON → 结构化数据的边界，输入完全
    不可信——LLM 可能把某个数组字段写成字符串数组、把 dict 字段写成字符串、
    漏掉 key、给出值域之外的枚举值等各种"看起来像但类型不对"的输出。整个
    函数体现在分两层兜底：
      1. 数组字段一律走 `_build_items()`：单个元素格式不对时跳过该元素、
         记一条包含原始内容的日志，不影响其它元素和整份 spec 的生成。
      2. 万一出现 `_build_items()` 兜底不住的意外类型错误（比如 `data`
         本身某个 key 的值是嵌套结构导致后续渲染出错），外层调用方
         （`build_draft`/`revise`）还会再包一层 try/except，把完整的
         `data`、`goal_id`、`version` 记入日志后返回兜底草稿，不会让
         异常冒泡成 HTTP 500。
    """
    context = {"goal_id": goal_id, "version": version, "source": "_spec_from_llm_data"}

    # [Stage 8e] LLM 输出的 output_mode/new_topic_discovery 是自由文本，
    # 校验值域与 `GoalExecutionSpec.from_dict()` 保持一致——非法值/幻觉值
    # 静默回退默认，不因为 LLM 输出了值域之外的字符串而报错或污染数据。
    output_mode = data.get("output_mode") or "converging"
    if not isinstance(output_mode, str) or output_mode not in _VALID_OUTPUT_MODES:
        output_mode = "converging"
    new_topic_discovery = data.get("new_topic_discovery") or "none"
    if not isinstance(new_topic_discovery, str) or new_topic_discovery not in _VALID_NEW_TOPIC_DISCOVERY:
        new_topic_discovery = "none"

    # cadence/hardening_target/sub_exploration 都是"自由文本"字段，LLM 偶尔
    # 会给出非字符串值（比如把 hardening_target 写成 {"path": "..."} 这种
    # 结构化对象）——统一转成字符串，而不是让下游拼 Markdown 时报错。
    def _as_text(v) -> str:
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        return _safe_repr(v, limit=200)

    return GoalExecutionSpec(
        version=version,
        goal_id=goal_id,
        confirmed=False,
        locked_fields=list(locked_fields or []),
        deliverables=_build_items(Deliverable, data.get("deliverables"), "name", "deliverables", context),
        handoff_fields=_build_items(HandoffField, data.get("handoff_fields"), "key", "handoff_fields", context),
        sub_directories=_build_items(SubDirectory, data.get("sub_directories"), "name", "sub_directories", context),
        per_cycle_criteria=_build_items(Criterion, data.get("per_cycle_criteria"), "text", "per_cycle_criteria", context),
        overall_completion_criteria=_build_items(
            Criterion, data.get("overall_completion_criteria"), "text", "overall_completion_criteria", context,
        ),
        # special_constraints 是"字符串数组"，同样兜底：元素不是字符串时
        # （比如 LLM 写成了 {"text": "..."} 对象）转成字符串而不是直接崩掉。
        special_constraints=[s if isinstance(s, str) else _safe_repr(s, limit=200)
                              for s in (data.get("special_constraints") or []) if s],
        # [goal_output_directory_and_execution_phase_redesign_plan.md
        # §Stage8e] Stage 8a 新增的 6 个字段——LLM 未产出这些 key（旧版
        # prompt 或响应本身不含）时 `data.get(...)` 返回 `None`，走各字段
        # 自身构造函数的默认值分支，与"存量 spec 文件里没有这些 key"的
        # 向后兼容路径完全一致，不需要额外判空。
        output_mode=output_mode,
        execution_routine=_build_items(RoutineStep, data.get("execution_routine"), "step", "execution_routine", context),
        cadence=_as_text(data.get("cadence")),
        new_topic_discovery=new_topic_discovery,
        hardening_target=_as_text(data.get("hardening_target")),
        sub_exploration=_as_text(data.get("sub_exploration")),
    )


_PROJECT_CONTEXT_KEYWORD_RE = re.compile(
    r"(项目里|项目中|现有的?(代码|文件|目录|报告|数据|格式|命名)|"
    r"已有的?(代码|文件|目录|报告|数据)|"
    r"沿用|参考.{0,6}(现有|已有|项目)|skill|workflow|工作流|"
    r"代码风格|目录结构|命名(约定|规范)|复用)",
    re.IGNORECASE,
)


def _rule_based_needs_project_context(text: str) -> bool:
    """[goal_execution_spec_generation_plan.md §3 输入源 1，实施记录 Stage
    "builder_mode=agent" 章节] 判断一段文本是否"看起来"需要先看一眼项目
    实际情况才能写出具体、可核查的执行规范。

    镜像 goal_mode/spec.py::_rule_based_needs_project_context 的思路，但
    简化为纯关键词匹配——不做"已知 skill/workflow 名称"的二次匹配（那一层
    依赖 cfg 遍历项目实际 skill/workflow 列表，对"要不要起一个受限 Agent"
    这个初筛决策而言收益有限，先用最朴素的规则覆盖最常见的场景："参考/
    沿用/复用项目已有的 XXX"“提到 skill/workflow"这类表述）。
    """
    if not text:
        return False
    return bool(_PROJECT_CONTEXT_KEYWORD_RE.search(text))


def _empty_draft(goal_id: str, version: int, reason: str) -> GoalExecutionSpec:
    """生成失败时的最小兜底草稿：全部字段为空，等价于"沿用通用行为"。"""
    return GoalExecutionSpec(
        version=version,
        goal_id=goal_id,
        confirmed=False,
        generation_error=reason,
    )


class GoalExecutionSpecBuilder:
    """把 Goal 的 title/description（+ 可选模板/历史）转化为结构化
    GoalExecutionSpec，支持基于用户反馈的字段级锁定迭代。

    与 GoalSpecBuilder 一样：每次调用都是独立的一次性 LLM 调用，不占用/污染
    主 Agent 的对话历史。
    """

    _VALID_MODES = ("llm", "agent", "auto")

    def __init__(self, cfg: "AppConfig", llm_helper=None, mode: Optional[str] = None,
                 parent_session_id: Optional[str] = None, parent_session_dir: Optional[str] = None) -> None:
        self._cfg = cfg
        self._llm_helper = llm_helper
        self.last_error: Optional[str] = None
        self.last_effective_path: Optional[str] = None
        # [implementation_record.md §11 后续建议顺序第 2 条] 仅
        # `evaluate_overall_completion()` 使用：这次判定实际是否走了受限
        # Agent 路径（`True`/`False`），调用前为 `None`——与
        # `last_effective_path` 是同一风格的"实际生效结果"记录，只是
        # 服务于"整体关闭判定"而不是"草稿生成"。
        self.last_used_agent: Optional[bool] = None
        # 仅 "agent"/"auto" 命中 agent 路径时使用：把受限 Agent 的会话记录
        # 挂到父会话下面，与 GoalSpecBuilder 的同名参数用途一致——纯粹是
        # 存储层面的归档路径，调用方不传时（多数场景）就落在顶层目录。
        self._parent_session_id = parent_session_id
        self._parent_session_dir = parent_session_dir

        ges_cfg = getattr(cfg, "goal_execution_spec", None)
        raw_mode = (mode or getattr(ges_cfg, "builder_mode", None) or "auto")
        normalized = str(raw_mode).strip().lower()
        if normalized not in self._VALID_MODES:
            normalized = "auto"
        self.mode = normalized

    def _resolve_model(self):
        from mini_agent.role_agents.model_resolution import resolve_role_model
        from types import SimpleNamespace

        ges_cfg = getattr(self._cfg, "goal_execution_spec", None)
        role_cfg_block = SimpleNamespace(
            judge_model=getattr(ges_cfg, "builder_model", None),
            judge_provider=getattr(ges_cfg, "builder_provider", None),
        )
        return resolve_role_model(None, role_cfg_block, self._cfg)

    def _run_builder(self, prompt: str, *, detection_text: Optional[str] = None) -> str:
        """按 self.mode 分诊到具体生成路径，是 build_draft/revise 的唯一
        入口，镜像 goal_mode/spec.py::GoalSpecBuilder._run_builder。

        - "llm"   → 直接走 `_run_llm`。
        - "agent" → 直接走 `_run_builder_agent`。
        - "auto"  → 先用关键词规则粗略判断 `detection_text`（build_draft 传
          title+description，revise 传用户反馈文本）是否提到"参考/沿用
          项目已有内容"类诉求，命中则直接走 agent 路径；规则没命中则先走
          llm 路径，解析其输出 JSON 里的 `needs_project_context` 字段——
          为 `true`（模型自报"这道题我答不好，需要先看看项目"）则丢弃这次
          结果，改用 agent 路径重新生成一次。

        [implementation_record.md §15 后续建议顺序第 2 条] 这层"LLM 自报
        needs_project_context 后二次重生成"的兜底与 `goal_mode/spec.py::
        GoalSpecBuilder._run_builder` 完全对称——`goal_execution_spec_
        builder.md` 的输出 schema 已加上 `needs_project_context` 字段
        （默认 `false`），规则漏判时不再是"只能显式传 mode=agent 绕过"，
        LLM 自己也有一次纠正机会。
        """
        if self.mode == "agent":
            self.last_effective_path = "agent"
            return self._run_builder_agent(prompt)

        if self.mode == "llm":
            self.last_effective_path = "llm"
            return self._run_llm(prompt)

        # mode == "auto"
        if _rule_based_needs_project_context(detection_text or prompt):
            self.last_effective_path = "agent"
            return self._run_builder_agent(prompt)

        raw = self._run_llm(prompt)
        self.last_effective_path = "llm"
        data = _extract_json(raw) or {}
        if data.get("needs_project_context") is True:
            import mini_agent.ui.renderer as R
            R.print_info(
                "[GoalExecutionSpecBuilder] 规则未命中，但模型自报需要读取项目内容"
                "（skill/workflow 等）才能写好验收标准，改用受限 Agent 路径重新生成…"
            )
            self.last_effective_path = "agent"
            return self._run_builder_agent(prompt)
        return raw

    def _run_llm(self, prompt: str) -> str:
        """裸单轮 chat completion——不挂工具，模型唯一能做的事就是把 JSON
        写出来。适合 Goal 本身足够自解释、不涉及项目内部结构的场景；涉及
        项目内部信息时应该用 `_run_builder_agent`（见 `_run_builder` 的
        分诊逻辑），否则模型只能凭训练知识猜测项目里的实际情况。
        """
        from mini_agent.llm.service import LLMHelper
        from mini_agent.prompts import pm

        (model, provider) = self._resolve_model()
        helper = self._llm_helper or LLMHelper.from_config(self._cfg)
        system_prompt = pm.render("system/goal_execution_spec_builder")

        try:
            text = helper.ask(
                prompt,
                system=system_prompt,
                max_retries=2,
                override_model=model,
                override_provider=provider,
            )
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.perception.goal_execution_spec.GoalExecutionSpecBuilder._run_llm")
            self.last_error = str(e)
            return ""

        if text and text.strip():
            self.last_error = None
            return text
        self.last_error = "GoalExecutionSpecBuilder 未产出任何文本输出"
        return ""

    def _run_builder_agent(self, prompt: str) -> str:
        """构造一个只读、有限工具的受限 Agent 来生成/修订执行规范草案。

        镜像 goal_mode/spec.py::GoalSpecBuilder._run_builder_agent——同样是
        "只读、有限工具、纯文本 JSON 产出"的受限 Agent，工具白名单默认与
        GoalSpecBuilder 相同（skill_list/list_workflows/show_workflow/
        read_file/list_dir/tree_summary/grep/glob），不包含 bash、不包含
        任何写文件/写 workflow 的工具。system prompt 用
        `goal_execution_spec_builder` 基础说明 + `_agent_addendum` 附录
        （告知模型"你现在有只读工具可用"）拼接而成。
        """
        from mini_agent.prompts import pm
        from mini_agent.role_agents.judge_factory import spawn_judge_agent, run_judge_turn

        ges_cfg = getattr(self._cfg, "goal_execution_spec", None)
        from types import SimpleNamespace
        role_cfg_block = SimpleNamespace(
            judge_model=getattr(ges_cfg, "builder_model", None),
            judge_provider=getattr(ges_cfg, "builder_provider", None),
        )

        allowed_tools = list(getattr(ges_cfg, "builder_agent_allowed_tools", None) or [])
        allowed_tool_groups = list(getattr(ges_cfg, "builder_agent_allowed_tool_groups", None) or [])
        max_turns = int(getattr(ges_cfg, "builder_agent_max_turns", None) or 6)

        agent_system_prompt = (
            pm.render("system/goal_execution_spec_builder")
            + "\n\n"
            + pm.render("system/goal_execution_spec_builder_agent_addendum")
        )

        try:
            agent = spawn_judge_agent(
                profile=None,
                base_cfg=self._cfg,
                role_cfg_block=role_cfg_block,
                display_name="🎯 GoalExecutionSpecBuilder(agent)",
                system_prompt=agent_system_prompt,
                max_turns=max_turns,
                tools_enabled=True,
                allowed_tools=allowed_tools,
                allowed_tool_groups=allowed_tool_groups,
                force_sandbox_when_tools=True,
                parent_session_id=self._parent_session_id,
                parent_session_dir=self._parent_session_dir,
            )
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.perception.goal_execution_spec.GoalExecutionSpecBuilder._run_builder_agent")
            self.last_error = f"构造受限 Agent 失败：{e}"
            return ""

        result = run_judge_turn(agent, prompt, failure_role_label="GoalExecutionSpecBuilder(agent)")
        if not result.ok:
            self.last_error = result.error or "GoalExecutionSpecBuilder(agent) 运行失败"
            return ""

        if result.raw_output and result.raw_output.strip():
            self.last_error = None
            return result.raw_output

        self.last_error = (
            "GoalExecutionSpecBuilder(agent) 未产出任何文本输出"
            "（可能一直在调用只读工具探索，未在 max_turns 内收敛）"
        )
        return ""

    def build_draft(
        self,
        goal_id: str,
        goal_title: str,
        goal_description: str = "",
        *,
        schedule: Optional[str] = None,
        task_template: Optional[str] = None,
        template_id: Optional[str] = None,
        history_manifests: Optional[list[dict]] = None,
    ) -> GoalExecutionSpec:
        """生成第 1 版草稿。三种输入源（见方案 §3）都收敛到这一个入口：
          - template_id 为空 → 完全从零生成
          - template_id 非空 → 从模板起步（骨架作为 few-shot 拼进 prompt）
          - history_manifests 非空 → 额外把历史产出摘要拼进 prompt（可与
            template_id 同时使用）
        """
        from mini_agent.prompts import pm

        template = load_template(template_id) if template_id else None
        template_block = ""
        if template:
            template_block = "参考模板骨架（可在此基础上增删改，不要机械照搬）：\n" + json.dumps(
                template.get("skeleton", {}), ensure_ascii=False, indent=2
            )

        history_block = ""
        if history_manifests:
            from mini_agent.evolution.output_workspace import format_manifest_for_prompt
            parts = []
            for m in history_manifests[-3:]:
                text = format_manifest_for_prompt(m)
                if text:
                    parts.append(text)
            if parts:
                history_block = "该 Goal 过去若干轮的实际产出（供校验规范是否可行）：\n" + "\n---\n".join(parts)

        prompt = pm.render(
            "user/goal_execution_spec_initial_request",
            goal_title=goal_title,
            goal_description=goal_description or "（无）",
            schedule=schedule or "（未设置，非周期性 Goal）",
            task_template=task_template or "（无）",
            template_block=template_block,
            history_block=history_block,
        )

        raw = self._run_builder(prompt, detection_text=f"{goal_title} {goal_description}")
        data = _extract_json(raw, context={
            "goal_id": goal_id, "goal_title": goal_title, "stage": "build_draft",
            "effective_path": self.last_effective_path,
        })
        if data is None:
            reason = self.last_error or "LLM 返回内容解析失败（未找到合法 JSON）"
            return _empty_draft(goal_id, 1, reason)

        try:
            spec = _spec_from_llm_data(data, goal_id, version=1)
        except Exception as e:
            # [排障兜底] `_spec_from_llm_data` 内部各字段已经有逐项兜底
            # （见 `_build_items`），理论上不应该再抛到这里；这一层是"万一
            # 出现没预料到的类型错误"的最后防线——记录完整的 LLM 原始输出
            # + 解析后的 data + goal 上下文，方便事后在 error.jsonl 里定位
            # 具体是哪个 Goal、哪次生成、LLM 到底返回了什么导致解析失败，
            # 而不是只有一句 500 错误信息。同时仍然返回可用的兜底草稿，
            # 不让整个接口 500。
            from mini_agent.errors import log_exception
            log_exception(
                e,
                where="mini_agent.perception.goal_execution_spec.GoalExecutionSpecBuilder.build_draft",
                extra={
                    "goal_id": goal_id,
                    "goal_title": goal_title,
                    "effective_path": self.last_effective_path,
                    "raw_llm_output": _safe_repr(raw, limit=4000),
                    "parsed_data": _safe_repr(data, limit=4000),
                },
            )
            return _empty_draft(goal_id, 1, f"生成结果解析后转换失败：{e}")
        return spec

    def revise(
        self,
        prior_spec: GoalExecutionSpec,
        feedback: str,
        locked_fields: Optional[list[str]] = None,
    ) -> GoalExecutionSpec:
        """基于"上一版 + 反馈 + 锁定字段"重新生成，只调整未锁定的部分。"""
        from mini_agent.prompts import pm

        locked = list(locked_fields if locked_fields is not None else prior_spec.locked_fields)
        locked_block = (
            "以下字段用户已明确表示满意，请原样保留、不要改动（原样复制其内容到输出 JSON 里）："
            + "、".join(locked) if locked else "（无字段被锁定，全部字段都可以根据反馈调整）"
        )

        prompt = pm.render(
            "user/goal_execution_spec_revise_request",
            prior_version=prior_spec.version,
            prior_summary=prior_spec.render_summary_for_user(),
            user_feedback=feedback,
            locked_block=locked_block,
        )

        raw = self._run_builder(prompt, detection_text=feedback)
        data = _extract_json(raw, context={
            "goal_id": prior_spec.goal_id, "prior_version": prior_spec.version, "stage": "revise",
            "effective_path": self.last_effective_path,
        })
        if data is None:
            # 失败时保留上一版内容（不是清空），只是版本号不变、附上错误说明——
            # 修订失败不应该让用户已经确认过的字段凭空丢失。
            fallback = GoalExecutionSpec.from_dict(prior_spec.to_dict())
            fallback.confirmed = False
            fallback.generation_error = self.last_error or "LLM 返回内容解析失败（未找到合法 JSON）"
            return fallback

        try:
            new_spec = _spec_from_llm_data(data, prior_spec.goal_id, version=prior_spec.version + 1, locked_fields=locked)
        except Exception as e:
            # 与 build_draft 同款兜底：修订失败时保留上一版内容，不清空用户
            # 已确认满意的字段，同时把完整上下文记入日志便于排查。
            from mini_agent.errors import log_exception
            log_exception(
                e,
                where="mini_agent.perception.goal_execution_spec.GoalExecutionSpecBuilder.revise",
                extra={
                    "goal_id": prior_spec.goal_id,
                    "prior_version": prior_spec.version,
                    "feedback": _safe_repr(feedback, limit=1000),
                    "locked_fields": locked,
                    "effective_path": self.last_effective_path,
                    "raw_llm_output": _safe_repr(raw, limit=4000),
                    "parsed_data": _safe_repr(data, limit=4000),
                },
            )
            fallback = GoalExecutionSpec.from_dict(prior_spec.to_dict())
            fallback.confirmed = False
            fallback.generation_error = f"生成结果解析后转换失败：{e}"
            return fallback

        # 对锁定字段做字符串级"原样保留"兜底：即便 LLM 没有严格遵守 prompt
        # 指示而改动了锁定字段，这里用上一版的值强制覆盖回去，保证"锁定"
        # 是一条硬约束，不完全依赖 LLM 的听话程度。
        field_map = {
            "deliverables": "deliverables",
            "handoff_fields": "handoff_fields",
            "sub_directories": "sub_directories",
            "per_cycle_criteria": "per_cycle_criteria",
            "overall_completion_criteria": "overall_completion_criteria",
            "special_constraints": "special_constraints",
            # [Stage 8e] Stage 8a 新字段同样支持按名字锁定——用户对
            # execution_routine/hardening_target 等某个具体字段满意后，
            # revise() 只调整其余字段，与既有 6 个字段的锁定行为一致。
            "output_mode": "output_mode",
            "execution_routine": "execution_routine",
            "cadence": "cadence",
            "new_topic_discovery": "new_topic_discovery",
            "hardening_target": "hardening_target",
            "sub_exploration": "sub_exploration",
        }
        for name in locked:
            attr = field_map.get(name)
            if attr is not None and hasattr(prior_spec, attr):
                setattr(new_spec, attr, getattr(prior_spec, attr))

        return new_spec

    @staticmethod
    def confirm(spec: GoalExecutionSpec) -> GoalExecutionSpec:
        spec.confirmed = True
        spec.confirmed_at = time.time()
        return spec

    def evaluate_overall_completion(
        self,
        goal_title: str,
        goal_description: str,
        spec: GoalExecutionSpec,
        children: list[tuple[str, str]],
        manifests: list[dict],
        output_base_dir: Optional[str] = None,
        use_agent_override: Optional[bool] = None,
    ) -> dict:
        """[goal_execution_spec_generation_plan.md §5 第二段 /
        implementation_record.md 未实施清单第 5 项/第 8 项] 判断"整个一次性
        Goal 是否可以整体关闭"——只在调用方已经确认"全部子 Objective 均已
        进入终态"且 `spec.overall_completion_criteria` 非空时才有意义调用，
        本方法自己不做这两条前置判断（由 `GoalBacklog.maybe_close_goal_
        by_overall_criteria()` 负责，纯函数与调用时机分开职责）。

        children：`[(title, status), ...]`，全部子 Objective 的标题与终态。
        manifests：该 Goal 历史全部轮次的 manifest dict 列表（通常来自
        `output_workspace.read_all_manifests()`），用于给判官提供"实际产出
        了什么"的证据，而不是只凭标题空判断。
        output_base_dir：该 Goal 产出目录的实际路径（通常来自
        `output_workspace.goal_output_base_dir()`）。仅在实际走 Agent 路径
        时才会用到——告诉受限 Agent 去哪里打开文件核查内容；裸 LLM 单轮
        路径下这个参数不产生任何效果（不传也完全兼容旧调用方）。
        use_agent_override：[implementation_record.md §11 后续建议顺序第 2
        条"CLI/看板暴露单次覆盖 overall_completion_use_agent 的入口"] 单次
        调用覆盖是否走受限 Agent 路径，不修改配置文件——`True`/`False` 时
        直接决定这次判定走哪条路径，`None`（默认，不传）时回退配置文件
        `goal_execution_spec.overall_completion_use_agent`（Stage 9 引入前
        的行为，完全兼容旧调用方）。实际使用的路径记在
        `self.last_used_agent`（`bool`），供调用方回写持久化状态/展示。

        返回 `{"decision": "close"|"continue", "reasoning": str}`；调用
        失败/解析失败时保守返回 `{"decision": "continue", "reasoning": "..."}`
        ——不确定时绝不主动关闭 Goal，这是"确认优先于生效"哲学在这里的体现。
        """
        from mini_agent.prompts import pm
        from mini_agent.evolution.output_workspace import format_manifest_for_prompt

        criteria_lines = "\n".join(
            f"{i+1}. [{c.verification_method}] {c.text}"
            for i, c in enumerate(spec.overall_completion_criteria)
        ) or "（无）"

        children_lines = "\n".join(
            f"{i+1}. {title}（{status}）" for i, (title, status) in enumerate(children)
        ) or "（无）"

        manifest_parts = []
        for m in manifests:
            text = format_manifest_for_prompt(m)
            if text:
                manifest_parts.append(text)
        manifest_block = "\n---\n".join(manifest_parts) if manifest_parts else "（无历史产出记录）"

        ges_cfg = getattr(self._cfg, "goal_execution_spec", None)
        if use_agent_override is not None:
            use_agent = bool(use_agent_override)
        else:
            use_agent = bool(getattr(ges_cfg, "overall_completion_use_agent", False))
        self.last_used_agent = use_agent
        output_dir_block = ""
        if use_agent and output_base_dir:
            output_dir_block = f"\n该 Goal 的产出目录：{output_base_dir}\n（可用工具打开该目录下的文件核查具体内容）\n"

        prompt = pm.render(
            "user/goal_overall_completion_request",
            goal_title=goal_title,
            goal_description=goal_description or "（无）",
            criteria_lines=criteria_lines,
            children_lines=children_lines,
            manifest_block=manifest_block,
            output_dir_block=output_dir_block,
        )

        if use_agent:
            raw = self._run_overall_completion_judge_agent(prompt)
        else:
            raw = self._run_judge_llm(prompt)
        data = _extract_json(raw, context={
            "goal_title": goal_title, "stage": "evaluate_overall_completion", "used_agent": use_agent,
        })
        if not isinstance(data, dict) or data.get("decision") not in ("close", "continue"):
            reason = self.last_error or "LLM 返回内容解析失败（未找到合法 JSON）"
            return {"decision": "continue", "reasoning": f"判定失败，保守判定为需继续：{reason}"}
        return {
            "decision": data.get("decision"),
            "reasoning": str(data.get("reasoning") or ""),
        }

    def _run_judge_llm(self, prompt: str) -> str:
        """与 `_run_llm()` 逻辑相同，只是 system prompt 换成整体完成判定专用
        的一份（见 prompts/system/goal_overall_completion_judge.md）——判定
        任务与"生成执行规范草案"是两种不同性质的输出，分开一份 system prompt
        而不是复用 `_run_llm()`，避免以后各自演进指令时互相牵连。
        """
        from mini_agent.llm.service import LLMHelper
        from mini_agent.prompts import pm

        (model, provider) = self._resolve_model()
        helper = self._llm_helper or LLMHelper.from_config(self._cfg)
        system_prompt = pm.render("system/goal_overall_completion_judge")

        try:
            text = helper.ask(
                prompt,
                system=system_prompt,
                max_retries=2,
                override_model=model,
                override_provider=provider,
            )
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.perception.goal_execution_spec.GoalExecutionSpecBuilder._run_judge_llm")
            self.last_error = str(e)
            return ""

        if text and text.strip():
            self.last_error = None
            return text
        self.last_error = "GoalExecutionSpecBuilder（整体完成判定）未产出任何文本输出"
        return ""

    def _run_overall_completion_judge_agent(self, prompt: str) -> str:
        """构造一个只读、有限工具的受限 Agent 来做"整体是否可以关闭"判定，
        与 `_run_builder_agent()` 是同一套 `judge_factory.py::
        spawn_judge_agent`/`run_judge_turn` 基础设施，区别只在 system
        prompt（`goal_overall_completion_judge` + `_agent_addendum`）、
        工具白名单（`overall_completion_agent_allowed_tools`，默认不含
        skill_list/list_workflows，只需要看该 Goal 自己的产出目录）、
        以及配置来源（`overall_completion_agent_max_turns`）。

        [goal_execution_spec_generation_plan.md §10 后续建议顺序第 2 条 /
        implementation_record.md 未实施清单第 8 项] 补齐"评委不会亲自打开
        文件核实内容，只依赖 manifest 摘要文本"这一缺口。
        """
        from mini_agent.prompts import pm
        from mini_agent.role_agents.judge_factory import spawn_judge_agent, run_judge_turn

        ges_cfg = getattr(self._cfg, "goal_execution_spec", None)
        from types import SimpleNamespace
        role_cfg_block = SimpleNamespace(
            judge_model=getattr(ges_cfg, "builder_model", None),
            judge_provider=getattr(ges_cfg, "builder_provider", None),
        )

        allowed_tools = list(getattr(ges_cfg, "overall_completion_agent_allowed_tools", None) or [])
        allowed_tool_groups = list(getattr(ges_cfg, "overall_completion_agent_allowed_tool_groups", None) or [])
        max_turns = int(getattr(ges_cfg, "overall_completion_agent_max_turns", None) or 8)

        agent_system_prompt = (
            pm.render("system/goal_overall_completion_judge")
            + "\n\n"
            + pm.render("system/goal_overall_completion_judge_agent_addendum")
        )

        try:
            agent = spawn_judge_agent(
                profile=None,
                base_cfg=self._cfg,
                role_cfg_block=role_cfg_block,
                display_name="🏁 GoalOverallCompletionJudge(agent)",
                system_prompt=agent_system_prompt,
                max_turns=max_turns,
                tools_enabled=True,
                allowed_tools=allowed_tools,
                allowed_tool_groups=allowed_tool_groups,
                force_sandbox_when_tools=True,
                parent_session_id=self._parent_session_id,
                parent_session_dir=self._parent_session_dir,
            )
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.perception.goal_execution_spec.GoalExecutionSpecBuilder._run_overall_completion_judge_agent")
            self.last_error = f"构造受限 Agent 失败：{e}"
            return ""

        result = run_judge_turn(agent, prompt, failure_role_label="GoalOverallCompletionJudge(agent)")
        if not result.ok:
            self.last_error = result.error or "GoalOverallCompletionJudge(agent) 运行失败"
            return ""

        if result.raw_output and result.raw_output.strip():
            self.last_error = None
            return result.raw_output

        self.last_error = (
            "GoalOverallCompletionJudge(agent) 未产出任何文本输出"
            "（可能一直在调用只读工具核查，未在 max_turns 内收敛）"
        )
        return ""


# ── §5.1 轻量核对：纯文件名/key 字符串匹配，不做语义判断 ─────────────────────

def soft_check_manifest(spec: GoalExecutionSpec, manifest: dict) -> dict:
    """检查这一轮 manifest 是否满足 spec 里挂了 file_check 的
    deliverables/per_cycle_criteria，以及全部 handoff_fields。

    只做字符串匹配（文件名/key 是否出现在 artifacts 文件名 / progress_note
    的 ```handoff``` JSON 块里），成本接近零，不判失败、不阻断，纯粹给
    调用方（goal_cron_bridge）拼软提示用。

    返回：{"missing_deliverables": [...], "missing_handoff_keys": [...], "ok": bool}
    """
    artifacts = manifest.get("artifacts") or []
    artifact_names = []
    for a in artifacts:
        if isinstance(a, dict):
            artifact_names.append(a.get("path", "") or a.get("name", ""))
        else:
            artifact_names.append(str(a))
    artifact_blob = "\n".join(artifact_names)

    missing_deliverables = []
    for d in spec.deliverables:
        if not d.required_every_cycle or not d.naming_pattern:
            continue
        if d.naming_pattern not in artifact_blob:
            missing_deliverables.append(d.name or d.naming_pattern)

    handoff_data = _extract_handoff_block(manifest.get("progress_note") or "")
    missing_handoff_keys = []
    for h in spec.handoff_fields:
        if not h.key:
            continue
        if handoff_data is None or h.key not in handoff_data:
            missing_handoff_keys.append(h.key)

    ok = not missing_deliverables and not missing_handoff_keys
    return {
        "missing_deliverables": missing_deliverables,
        "missing_handoff_keys": missing_handoff_keys,
        "ok": ok,
    }


_HANDOFF_FENCE_RE = re.compile(r"```handoff\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_handoff_block(progress_note: str) -> Optional[dict]:
    m = _HANDOFF_FENCE_RE.search(progress_note or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def get_handoff_data(progress_note: str) -> Optional[dict]:
    """供消费方按 key 精确取值（与 _extract_handoff_block 同一实现，暴露为
    公共函数名，语义更贴近调用意图）。"""
    return _extract_handoff_block(progress_note)


__all__ = [
    "GoalExecutionSpec",
    "GoalExecutionSpecBuilder",
    "GoalExecutionSpecBuildError",
    "Deliverable",
    "HandoffField",
    "SubDirectory",
    "Criterion",
    "load_spec",
    "save_spec",
    "delete_spec",
    "list_spec_history",
    "serialize_routine_steps",
    "list_templates",
    "load_template",
    "suggest_template",
    "soft_check_manifest",
    "get_handoff_data",
]
