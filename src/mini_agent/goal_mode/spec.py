"""
goal_mode/spec.py — GoalSpec 定义 + GoalSpecBuilder

GoalSpec：结构化的"目标 + 验收标准"，是 GoalRunner / GoalJudge 的唯一依据。

验收标准的确定流程（对应设计方案）：
  1. 用户给出一句自然语言目标
  2. GoalSpecBuilder 生成第 1 版 GoalSpec（结构化 JSON）
  3. 展示给用户，用户可以：
       - 提出修改意见 → GoalSpecBuilder 基于「上一版 + 反馈」重新生成（版本号 +1）
       - 确认（/confirm）→ GoalSpec.confirmed = True，冻结，进入 GoalRunner
       - 取消 → 整个协商过程放弃
  4. 这个协商过程是独立的会话态，不进主 Agent 的历史（用独立的一次性 Agent 调用，
     不占用主 Agent 的上下文/轮次预算）

verification_method 取值：
  run_command    — 可以通过运行某条命令验证（优先，最可靠）
  file_check     — 通过检查文件内容/存在性验证
  manual_review  — 只能靠阅读/主观判断验证（兜底，尽量少用）
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.config import AppConfig


@dataclass
class GoalSpec:
    """结构化的目标 + 验收标准。"""
    goal_text: str
    acceptance_criteria: list[str] = field(default_factory=list)
    verification_method: str = "manual_review"   # run_command | file_check | manual_review
    verification_command: str = ""                # verification_method=run_command 时的具体命令（可选）
    version: int = 1
    confirmed: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # 协商历史：[(version, source, text), ...]，source = "builder" | "user_feedback"
    negotiation_log: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "GoalSpec":
        return GoalSpec(
            goal_text=d.get("goal_text", ""),
            acceptance_criteria=list(d.get("acceptance_criteria", [])),
            verification_method=d.get("verification_method", "manual_review"),
            verification_command=d.get("verification_command", ""),
            version=int(d.get("version", 1)),
            confirmed=bool(d.get("confirmed", False)),
            created_at=float(d.get("created_at", time.time())),
            updated_at=float(d.get("updated_at", time.time())),
            negotiation_log=list(d.get("negotiation_log", [])),
        )

    def render_context_block(self) -> str:
        """渲染成"钉住"消息内容（配合 history.entry.make_goal_context 使用）。

        每次 compact / 跨 session 恢复后都要重新附加一份，防止目标信息被
        摘要策略稀释或丢弃。
        """
        criteria_lines = "\n".join(
            f"{i+1}. {c}" for i, c in enumerate(self.acceptance_criteria)
        )
        return (
            "[Goal 模式 — 目标与验收标准（此消息会在每次压缩历史后重新附加，请始终以此为准）]\n"
            f"目标：{self.goal_text}\n\n"
            f"验收标准：\n{criteria_lines}\n\n"
            "请持续朝这个目标推进，直到所有验收标准都满足为止。"
        )

    def render_summary_for_user(self) -> str:
        """渲染成给用户展示确认用的可读文本。"""
        lines = [f"目标（第 {self.version} 版）：{self.goal_text}", "", "验收标准："]
        for i, c in enumerate(self.acceptance_criteria):
            lines.append(f"  {i+1}. {c}")
        lines.append("")
        lines.append(f"验证方式：{self.verification_method}")
        if self.verification_command:
            lines.append(f"验证命令：{self.verification_command}")
        return "\n".join(lines)


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_BRACE_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> Optional[dict]:
    """从 LLM 输出中提取第一个 JSON 对象，容忍 markdown 代码块包裹。"""
    m = _JSON_FENCE_RE.search(text)
    candidate = m.group(1) if m else None
    if candidate is None:
        m2 = _BRACE_RE.search(text)
        candidate = m2.group(0) if m2 else None
    if candidate is None:
        return None
    try:
        return json.loads(candidate)
    except Exception:
        return None


DEFAULT_SPEC_BUILDER_SYSTEM = """你是一个「目标澄清助手」。你的任务是把用户模糊的自然语言目标，
转化为结构化、可验证的验收标准清单。

原则：
1. 验收标准要尽量具体、可核查（优先能通过运行命令验证，比如"pytest 全部通过"）
2. 标准数量控制在 2-6 条，不要过度分解也不要过于笼统
3. 如果用户的目标本身模糊（比如"提升性能"），要给出你理解的具体化解读，而不是原样照抄
4. 只输出 JSON，不要有任何 JSON 之外的文字，不要用 markdown 代码块包裹

输出格式（严格遵守，只输出这一个 JSON 对象）：
{
  "goal_text": "对目标的清晰复述",
  "acceptance_criteria": ["标准1", "标准2", "..."],
  "verification_method": "run_command | file_check | manual_review",
  "verification_command": "如果 verification_method 是 run_command，给出具体命令；否则留空字符串"
}"""


class GoalSpecBuilder:
    """把自然语言目标转化为结构化 GoalSpec，支持基于用户反馈的多轮修订。

    每次调用都是独立的一次性 Agent 调用（不占用主 Agent 的历史/上下文），
    参考 role_agents/evaluator.py 的调用方式。
    """

    def __init__(self, cfg: "AppConfig") -> None:
        self._cfg = cfg

    def _run_builder(self, prompt: str) -> str:
        from mini_agent.config import load_config
        from mini_agent.agent import Agent
        from mini_agent.permissions import PermissionGuard
        from mini_agent.tools import get_default_registry

        gm = getattr(self._cfg, "goal_mode", None)
        builder_cfg = load_config(
            project_root=self._cfg.project_root,
            verbose=False,
            sandbox=self._cfg.sandbox,
            auto_approve=True,
            model=(getattr(gm, "spec_builder_model", None) or self._cfg.model),
            llm_provider=(getattr(gm, "spec_builder_provider", None) or self._cfg.llm_provider),
            llm_base_url=self._cfg.llm_base_url,
            debug_llm=False,
        )
        builder_cfg.api_key = self._cfg.api_key
        builder_cfg.max_turns = 2
        builder_cfg.stream = False
        builder_cfg.system_extra = DEFAULT_SPEC_BUILDER_SYSTEM

        guard = PermissionGuard(
            auto_approve=True,
            sandbox=self._cfg.sandbox,
            project_root=self._cfg.project_root,
        )
        empty_registry = get_default_registry().filtered(names=[], groups=[])
        builder_agent = Agent(cfg=builder_cfg, guard=guard, registry=empty_registry)

        try:
            return builder_agent.run_turn(prompt)
        except Exception as e:
            return f'{{"goal_text": "", "acceptance_criteria": [], "verification_method": "manual_review", "verification_command": "", "_error": "{e}"}}'

    def build_initial(self, user_goal_text: str) -> GoalSpec:
        """根据用户的自然语言目标生成第 1 版 GoalSpec。"""
        prompt = f"用户的目标：\n{user_goal_text}\n\n请生成结构化的验收标准。"
        raw = self._run_builder(prompt)
        data = _extract_json(raw) or {}

        spec = GoalSpec(
            goal_text=data.get("goal_text") or user_goal_text,
            acceptance_criteria=list(data.get("acceptance_criteria") or []),
            verification_method=data.get("verification_method") or "manual_review",
            verification_command=data.get("verification_command") or "",
            version=1,
        )
        if not spec.acceptance_criteria:
            # 兜底：解析失败时至少给一条最朴素的标准，避免空验收标准导致 Judge 无从判断
            spec.acceptance_criteria = [f"完成用户描述的目标：{user_goal_text}"]

        spec.negotiation_log.append({
            "version": 1,
            "source": "builder",
            "text": raw[:2000],
        })
        return spec

    def revise(self, prior_spec: GoalSpec, user_feedback: str) -> GoalSpec:
        """基于用户对上一版 GoalSpec 的反馈重新生成新版本。"""
        prompt = (
            f"这是当前的验收标准草案（第 {prior_spec.version} 版）：\n"
            f"{prior_spec.render_summary_for_user()}\n\n"
            f"用户对这版草案的修改意见：\n{user_feedback}\n\n"
            "请基于以上反馈生成修订后的新版本 JSON。"
        )
        raw = self._run_builder(prompt)
        data = _extract_json(raw)

        if data is None:
            # 解析失败：保留上一版，只追加协商记录，不静默丢弃用户反馈
            new_spec = GoalSpec.from_dict(prior_spec.to_dict())
            new_spec.negotiation_log.append({
                "version": prior_spec.version,
                "source": "user_feedback_parse_failed",
                "text": user_feedback[:2000],
            })
            new_spec.updated_at = time.time()
            return new_spec

        new_spec = GoalSpec(
            goal_text=data.get("goal_text") or prior_spec.goal_text,
            acceptance_criteria=list(data.get("acceptance_criteria") or prior_spec.acceptance_criteria),
            verification_method=data.get("verification_method") or prior_spec.verification_method,
            verification_command=data.get("verification_command", prior_spec.verification_command),
            version=prior_spec.version + 1,
            negotiation_log=list(prior_spec.negotiation_log),
        )
        new_spec.negotiation_log.append({
            "version": prior_spec.version,
            "source": "user_feedback",
            "text": user_feedback[:2000],
        })
        new_spec.negotiation_log.append({
            "version": new_spec.version,
            "source": "builder",
            "text": raw[:2000],
        })
        return new_spec

    def diff_summary(self, old_spec: GoalSpec, new_spec: GoalSpec) -> str:
        """生成简单的版本 diff 展示（供 CLI 展示用），只做粗粒度的增删对比。"""
        old_set = set(old_spec.acceptance_criteria)
        new_set = set(new_spec.acceptance_criteria)
        added = [c for c in new_spec.acceptance_criteria if c not in old_set]
        removed = [c for c in old_spec.acceptance_criteria if c not in new_set]

        lines = [f"第 {old_spec.version} 版 → 第 {new_spec.version} 版："]
        if old_spec.goal_text != new_spec.goal_text:
            lines.append(f"  目标描述已更新：{new_spec.goal_text}")
        for c in added:
            lines.append(f"  + 新增标准：{c}")
        for c in removed:
            lines.append(f"  - 移除标准：{c}")
        if not added and not removed and old_spec.goal_text == new_spec.goal_text:
            lines.append("  （内容基本未变，仅措辞调整）")
        return "\n".join(lines)
