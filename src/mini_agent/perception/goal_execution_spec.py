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
    def from_dict(d: dict) -> "Deliverable":
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
    def from_dict(d: dict) -> "HandoffField":
        return HandoffField(
            key=d.get("key", ""),
            description=d.get("description", ""),
            example=d.get("example", ""),
        )


@dataclass
class SubDirectory:
    name: str
    purpose: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "SubDirectory":
        return SubDirectory(name=d.get("name", ""), purpose=d.get("purpose", ""))


@dataclass
class Criterion:
    text: str
    verification_method: str = "manual_review"   # run_command | file_check | manual_review

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Criterion":
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
            "generation_error": self.generation_error,
            "soft_check_miss_streak": self.soft_check_miss_streak,
            "soft_check_alerted": self.soft_check_alerted,
        }

    @staticmethod
    def from_dict(d: dict) -> "GoalExecutionSpec":
        return GoalExecutionSpec(
            version=int(d.get("version", 1)),
            goal_id=d.get("goal_id", ""),
            generated_at=float(d.get("generated_at", time.time())),
            confirmed=bool(d.get("confirmed", False)),
            confirmed_at=d.get("confirmed_at"),
            locked_fields=list(d.get("locked_fields", [])),
            deliverables=[Deliverable.from_dict(x) for x in d.get("deliverables", [])],
            handoff_fields=[HandoffField.from_dict(x) for x in d.get("handoff_fields", [])],
            sub_directories=[SubDirectory.from_dict(x) for x in d.get("sub_directories", [])],
            per_cycle_criteria=[Criterion.from_dict(x) for x in d.get("per_cycle_criteria", [])],
            overall_completion_criteria=[
                Criterion.from_dict(x) for x in d.get("overall_completion_criteria", [])
            ],
            special_constraints=list(d.get("special_constraints", [])),
            generation_error=d.get("generation_error"),
            soft_check_miss_streak=int(d.get("soft_check_miss_streak", 0)),
            soft_check_alerted=bool(d.get("soft_check_alerted", False)),
        )

    def is_empty(self) -> bool:
        """全部字段为空 == 等价于"沿用 output_workspace.py 通用行为"。"""
        return not (
            self.deliverables or self.handoff_fields or self.sub_directories
            or self.per_cycle_criteria or self.overall_completion_criteria
            or self.special_constraints
        )

    def render_summary_for_user(self) -> str:
        """渲染成给用户展示确认用的可读文本（看板/CLI 共用）。"""
        lines = [f"执行规范（第 {self.version} 版，{'已确认' if self.confirmed else '草稿'}）："]
        if self.generation_error:
            lines.append(f"⚠️ 上次生成存在问题：{self.generation_error}")
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
                lines.append(f"  - {s.name}：{s.purpose}")
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
        if self.deliverables:
            lines.append("本轮应产出：")
            for d in self.deliverables:
                req = "（每轮必需）" if d.required_every_cycle else "（可选）"
                pattern = f"，命名遵循：{d.naming_pattern}" if d.naming_pattern else ""
                lines.append(f"  - {d.name}{req}：{d.description}{pattern}")
        if self.sub_directories:
            lines.append("子目录组织：")
            for s in self.sub_directories:
                lines.append(f"  - {s.name}：{s.purpose}")
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
    """把执行规范写入独立文件。返回写入路径。"""
    spec.goal_id = goal_id
    d = _spec_dir(paths)
    d.mkdir(parents=True, exist_ok=True)
    p = _spec_path(paths, goal_id)
    atomic_write_json(p, spec.to_dict())
    return p


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


def _extract_json(text: str) -> Optional[dict]:
    m = _JSON_FENCE_RE.search(text or "")
    candidate = m.group(1) if m else None
    if candidate is None:
        m2 = _BRACE_RE.search(text or "")
        candidate = m2.group(0) if m2 else None
    if candidate is None:
        return None
    try:
        return json.loads(candidate)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.perception.goal_execution_spec._extract_json")
        return None


def _spec_from_llm_data(data: dict, goal_id: str, version: int, locked_fields: Optional[list[str]] = None) -> GoalExecutionSpec:
    return GoalExecutionSpec(
        version=version,
        goal_id=goal_id,
        confirmed=False,
        locked_fields=list(locked_fields or []),
        deliverables=[Deliverable.from_dict(x) for x in (data.get("deliverables") or [])],
        handoff_fields=[HandoffField.from_dict(x) for x in (data.get("handoff_fields") or [])],
        sub_directories=[SubDirectory.from_dict(x) for x in (data.get("sub_directories") or [])],
        per_cycle_criteria=[Criterion.from_dict(x) for x in (data.get("per_cycle_criteria") or [])],
        overall_completion_criteria=[
            Criterion.from_dict(x) for x in (data.get("overall_completion_criteria") or [])
        ],
        special_constraints=list(data.get("special_constraints") or []),
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
        - "auto"  → 用关键词规则粗略判断 `detection_text`（build_draft 传
          title+description，revise 传用户反馈文本）是否提到"参考/沿用
          项目已有内容"类诉求，命中则走 agent 路径，否则走 llm 路径。

        与 `GoalSpecBuilder._run_builder` 的"auto"路径的一处简化：这里没有
        做"LLM 自报 needs_project_context 后二次重生成"那层兜底——
        `goal_execution_spec_builder.md` 的输出 schema 目前不包含这个字段，
        规则漏判时不会自动补救，只能显式传 `mode="agent"` 绕过。见实施
        记录里这一 Stage 的取舍说明。
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
        self.last_effective_path = "llm"
        return self._run_llm(prompt)

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
        data = _extract_json(raw)
        if data is None:
            reason = self.last_error or "LLM 返回内容解析失败（未找到合法 JSON）"
            return _empty_draft(goal_id, 1, reason)

        spec = _spec_from_llm_data(data, goal_id, version=1)
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
        data = _extract_json(raw)
        if data is None:
            # 失败时保留上一版内容（不是清空），只是版本号不变、附上错误说明——
            # 修订失败不应该让用户已经确认过的字段凭空丢失。
            fallback = GoalExecutionSpec.from_dict(prior_spec.to_dict())
            fallback.confirmed = False
            fallback.generation_error = self.last_error or "LLM 返回内容解析失败（未找到合法 JSON）"
            return fallback

        new_spec = _spec_from_llm_data(data, prior_spec.goal_id, version=prior_spec.version + 1, locked_fields=locked)

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
    ) -> dict:
        """[goal_execution_spec_generation_plan.md §5 第二段 /
        implementation_record.md 未实施清单第 5 项] 判断"整个一次性 Goal
        是否可以整体关闭"——只在调用方已经确认"全部子 Objective 均已进入
        终态"且 `spec.overall_completion_criteria` 非空时才有意义调用，
        本方法自己不做这两条前置判断（由 `GoalBacklog.maybe_close_goal_
        by_overall_criteria()` 负责，纯函数与调用时机分开职责）。

        children：`[(title, status), ...]`，全部子 Objective 的标题与终态。
        manifests：该 Goal 历史全部轮次的 manifest dict 列表（通常来自
        `output_workspace.read_all_manifests()`），用于给判官提供"实际产出
        了什么"的证据，而不是只凭标题空判断。

        返回 `{"decision": "close"|"continue", "reasoning": str}`；LLM 调用
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

        prompt = pm.render(
            "user/goal_overall_completion_request",
            goal_title=goal_title,
            goal_description=goal_description or "（无）",
            criteria_lines=criteria_lines,
            children_lines=children_lines,
            manifest_block=manifest_block,
        )

        raw = self._run_judge_llm(prompt)
        data = _extract_json(raw)
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
    "list_templates",
    "load_template",
    "suggest_template",
    "soft_check_manifest",
    "get_handoff_data",
]
