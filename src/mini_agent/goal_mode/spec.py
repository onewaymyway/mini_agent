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

from mini_agent.prompts import pm


class GoalSpecBuildError(RuntimeError):
    """GoalSpecBuilder 生成草案时的不可恢复失败（如多次重试仍解析不出合法 JSON）。

    与"LLM 正常解析后主动判断历史里没有明确任务"是两码事——后者不算错误，
    只是 goal_text 为空字符串；这个异常专门用来把"builder 本身失败了"和
    "确实没有可归纳的目标"区分开，调用方需要分别给出不同的提示。
    """
    pass

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
        摘要策略稀释或丢弃。模板见 prompts/user/goal_context.md。
        """
        criteria_lines = "\n".join(
            f"{i+1}. {c}" for i, c in enumerate(self.acceptance_criteria)
        )
        return pm.render(
            "user/goal_context",
            goal_text=self.goal_text,
            criteria_lines=criteria_lines,
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


_NON_WORD_RE = re.compile(r"[\s,，。.！!？?；;:：\-_/\\'\"“”‘’()（）\[\]【】]+")


def _normalize_for_compare(text: str) -> str:
    """归一化文本用于粗粒度相似度比较：去空白/标点，转小写。"""
    return _NON_WORD_RE.sub("", text or "").lower()


def _is_near_duplicate(a: str, b: str) -> bool:
    """粗粒度判断两段文本是否"基本是同一句话"（照抄/仅做微小改写）。

    不追求精确的编辑距离算法，只做一个足够便宜、足够保守的启发式：
    归一化后互为子串，或者字符集合的重叠度很高，就认为是照抄。
    """
    na, nb = _normalize_for_compare(a), _normalize_for_compare(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(shorter) >= 6 and shorter in longer:
        return True
    # 字符级 Jaccard 相似度兜底（应对语序打乱但用词几乎相同的情况）
    set_a, set_b = set(na), set(nb)
    if not set_a or not set_b:
        return False
    overlap = len(set_a & set_b) / len(set_a | set_b)
    return overlap > 0.85


def _looks_like_verbatim_echo(acceptance_criteria: list[str], user_goal_text: str) -> bool:
    """判断生成的验收标准是否"几乎原封不动"地照抄了用户目标原文。

    只要有任意一条标准与原始目标高度雷同，就认为加工不充分——正常的加工结果
    应该比原句更长、更具体，不会和原句"本质上是一句话"。
    """
    if not acceptance_criteria:
        return False
    return any(_is_near_duplicate(c, user_goal_text) for c in acceptance_criteria)


def _fallback_criteria(user_goal_text: str) -> list[str]:
    """LLM 解析失败时的兜底验收标准。

    相比"完成用户描述的目标：{user_goal_text}"这种纯复述，这里至少从
    "交付物是否存在 / 是否按目标工作 / 是否引入新问题"三个通用维度给出
    可核查的表述，避免空验收标准导致 Judge 无从判断，也避免直接照抄原文。
    """
    goal = (user_goal_text or "").strip() or "用户描述的目标"
    return [
        f"围绕「{goal}」产出的内容/改动是否已实际存在于对应文件或产出物中（而非仅在对话中描述）",
        f"围绕「{goal}」的核心诉求，功能或结果是否按预期工作，可通过实际运行/查看产出来核实",
        "本次改动是否未引入新的报错、测试失败或明显破坏原有功能（如适用，运行现有测试全部通过）",
    ]




def _extract_history_transcript(
    history: list[dict],
    max_messages: int = 40,
    max_chars: int = 6000,
) -> tuple[str, bool]:
    """从 Agent.history 中提取可读的对话摘录，供 build_from_history() 使用。

    只保留 role in (user, assistant) 且带纯文本 content 的消息（跳过纯工具调用/
    工具结果消息——那些对"归纳用户目标"贡献很小，反而会占用大量篇幅），按时间
    正序拼接。为控制 prompt 体积：
      1. 先只取最近 max_messages 条候选消息
      2. 再对拼接结果做 max_chars 截断（保留最后部分，最近的内容优先级更高）

    返回 (transcript_text, truncated)。truncated 表示是否发生了截断（消息条数
    或字符数任一超限），用于在提示词里附加"这只是部分历史"的说明，避免模型
    误以为这就是完整上下文。
    """
    candidates: list[str] = []
    for msg in history:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content")
        # content 可能是字符串，也可能是多模态/结构化 list（工具调用等），
        # 这里只提取其中的纯文本部分，忽略图片/工具调用块。
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            text = "\n".join(p.strip() for p in parts if p.strip())
        else:
            text = ""
        if not text:
            continue
        candidates.append(f"[{role}] {text}")

    truncated = len(candidates) > max_messages
    if truncated:
        candidates = candidates[-max_messages:]

    transcript = "\n\n".join(candidates)
    if len(transcript) > max_chars:
        truncated = True
        transcript = transcript[-max_chars:]

    return transcript, truncated


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
        builder_cfg.system_extra = pm.render("system/goal_spec_builder")
        # [SYS-GOAL-MODE] 同理，给 GoalSpecBuilder 一个专属显示名，避免和主 Agent 混淆
        builder_cfg.agent_name = "📋 GoalSpecBuilder"
        # [SYS-TURN-JUDGE][BUGFIX] 防止内部 Agent 对自己触发 TurnJudge 造成无限递归核查
        from mini_agent.config.models import TurnJudgeConfig as _TurnJudgeConfig
        builder_cfg.turn_judge = _TurnJudgeConfig(enabled=False)

        guard = PermissionGuard(
            auto_approve=True,
            sandbox=self._cfg.sandbox,
            project_root=self._cfg.project_root,
        )
        empty_registry = get_default_registry().filtered(names=[], groups=[])
        builder_agent = Agent(cfg=builder_cfg, guard=guard, registry=empty_registry, is_subagent=True)

        try:
            return builder_agent.run_turn(prompt)
        except Exception as e:
            return f'{{"goal_text": "", "acceptance_criteria": [], "verification_method": "manual_review", "verification_command": "", "_error": "{e}"}}'

    def build_initial(self, user_goal_text: str) -> GoalSpec:
        """根据用户的自然语言目标生成第 1 版 GoalSpec。"""
        prompt = pm.render("user/goal_spec_initial_request", user_goal_text=user_goal_text)
        raw = self._run_builder(prompt)
        data = _extract_json(raw) or {}

        criteria = list(data.get("acceptance_criteria") or [])
        goal_text = data.get("goal_text") or user_goal_text

        # 质量兜底：如果模型几乎原封不动地照抄了用户原话（未做具体化/拆解加工），
        # 用更强的纠正提示重试一次，而不是默默接受一份"没加工"的验收标准。
        if criteria and _looks_like_verbatim_echo(criteria, user_goal_text):
            retry_prompt = (
                prompt
                + "\n\n【纠正】你上一次的输出几乎是把用户原话直接当成了验收标准，"
                "这不符合要求。请重新生成：每一条标准都必须比用户原话更具体、"
                "更可核查（给出可观察的条件/结果，或可执行的验证方式），"
                "不能是原句的复述或近义替换。"
            )
            retry_raw = self._run_builder(retry_prompt)
            retry_data = _extract_json(retry_raw) or {}
            retry_criteria = list(retry_data.get("acceptance_criteria") or [])
            if retry_criteria and not _looks_like_verbatim_echo(retry_criteria, user_goal_text):
                raw = retry_raw
                data = retry_data
                criteria = retry_criteria
                goal_text = data.get("goal_text") or goal_text

        spec = GoalSpec(
            goal_text=goal_text,
            acceptance_criteria=criteria,
            verification_method=data.get("verification_method") or "manual_review",
            verification_command=data.get("verification_command") or "",
            version=1,
        )
        if not spec.acceptance_criteria:
            # 兜底：解析失败/模型未返回标准时，用分维度的通用标准兜底，
            # 避免空验收标准导致 Judge 无从判断，也避免直接照抄原文。
            spec.acceptance_criteria = _fallback_criteria(user_goal_text)

        spec.negotiation_log.append({
            "version": 1,
            "source": "builder",
            "text": raw[:2000],
        })
        return spec

    def build_from_history(self, history: list[dict]) -> GoalSpec:
        """根据当前 session 的历史对话，归纳出用户当前任务，生成第 1 版 GoalSpec。

        供 `/goal from-history` 使用：不要求用户重新用一句话描述目标，而是
        从 Agent.history（当前 session 已有的 user/assistant 对话）里自动
        归纳。与 build_initial() 共用同一个 system prompt（加工方法论），
        只是 user 消息换成"对话摘录 + 归纳要求"，一次 LLM 调用内完成
        "归纳任务 + 生成验收标准"，不额外多打一轮总结请求。

        两种"没结果"的情况需要严格区分，不能混为一谈：
          1. LLM 正常返回了合法 JSON，但判断历史里确实没有明确任务（比如刚
             开始、或者都是闲聊）——这是正常结果，goal_text 为空字符串，
             调用方据此提示用户改用 `/goal <目标文本>`。
          2. LLM 返回的内容压根解析不出合法 JSON（比如输出格式跑偏，常见于
             历史被 /compact 压缩过、结构比较特殊的情况）——这是 builder
             本身的失败，不代表"没有任务"，会先带纠正提示重试一次；仍然
             解析失败则抛出 GoalSpecBuildError，调用方应提示"生成失败，
             请重试或手动指定"，而不是"没有明确目标"。
        """
        transcript, truncated = _extract_history_transcript(history)
        if not transcript:
            # 没有任何可用的文本历史，直接返回一个空目标，交给调用方处理，
            # 不必浪费一次 LLM 调用。这属于情况 1（正常的"无目标"）。
            spec = GoalSpec(goal_text="", acceptance_criteria=[])
            return spec

        truncated_note = (
            "\n（注意：以上只是当前 session 历史中最近的一部分，更早的内容因篇幅"
            "限制未包含在内，请仅根据可见部分归纳。）"
            if truncated else ""
        )
        prompt = pm.render(
            "user/goal_spec_from_history_request",
            history_transcript=transcript,
            truncated_note=truncated_note,
        )
        raw = self._run_builder(prompt)
        data = _extract_json(raw)

        if data is None:
            # 情况 2：解析失败，带纠正提示重试一次，而不是直接判定"无目标"。
            retry_prompt = (
                prompt
                + "\n\n【纠正】你上一次的输出不是合法的 JSON，无法被解析。"
                "请只输出一个 JSON 对象（不要有任何前后缀文字或代码块标记），"
                "字段为 goal_text / acceptance_criteria / verification_method / "
                "verification_command。如果确实判断不出明确任务，goal_text 用"
                "空字符串 \"\"，acceptance_criteria 用空数组 []，但整体仍必须是"
                "合法 JSON。"
            )
            retry_raw = self._run_builder(retry_prompt)
            retry_data = _extract_json(retry_raw)
            if retry_data is None:
                raise GoalSpecBuildError(
                    "根据历史生成目标草案失败：LLM 两次输出都无法解析为合法 JSON。"
                )
            raw = retry_raw
            data = retry_data

        goal_text = (data.get("goal_text") or "").strip()
        criteria = list(data.get("acceptance_criteria") or [])

        spec = GoalSpec(
            goal_text=goal_text,
            acceptance_criteria=criteria,
            verification_method=data.get("verification_method") or "manual_review",
            verification_command=data.get("verification_command") or "",
            version=1,
        )
        if goal_text and not spec.acceptance_criteria:
            # 归纳出了目标但没有标准（模型漏填）——用通用兜底，避免空验收标准。
            spec.acceptance_criteria = _fallback_criteria(goal_text)

        spec.negotiation_log.append({
            "version": 1,
            "source": "builder_from_history",
            "text": raw[:2000],
        })
        return spec

    def revise(self, prior_spec: GoalSpec, user_feedback: str) -> GoalSpec:
        """基于用户对上一版 GoalSpec 的反馈重新生成新版本。"""
        prompt = pm.render(
            "user/goal_spec_revise_request",
            prior_version=prior_spec.version,
            prior_summary=prior_spec.render_summary_for_user(),
            user_feedback=user_feedback,
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

        new_criteria = list(data.get("acceptance_criteria") or prior_spec.acceptance_criteria)
        if new_criteria and _looks_like_verbatim_echo(new_criteria, user_feedback):
            # 模型把用户反馈原话直接塞成了一条"新标准"——保留其余未受影响的标准，
            # 剔除照抄的那几条，而不是整体接受一份加工不充分的结果。
            new_criteria = [c for c in new_criteria if not _is_near_duplicate(c, user_feedback)] or new_criteria

        new_spec = GoalSpec(
            goal_text=data.get("goal_text") or prior_spec.goal_text,
            acceptance_criteria=new_criteria,
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
