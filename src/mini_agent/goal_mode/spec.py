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
import mini_agent.ui.renderer as R


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
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.goal_mode.spec._extract_json')
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
) -> tuple[str, bool, bool]:
    """从 Agent.history 中提取可读的对话摘录，供 build_from_history() 使用。

    只保留 role in (user, assistant) 且带纯文本 content 的消息（跳过纯工具调用/
    工具结果消息——那些对"归纳用户目标"贡献很小，反而会占用大量篇幅），按时间
    正序拼接。为控制 prompt 体积：
      1. 先只取最近 max_messages 条候选消息
      2. 再对拼接结果做 max_chars 截断（保留最后部分，最近的内容优先级更高）

    对 `/compact` 产生的两类特殊条目做专门处理（依赖 history/entry.py 写入的
    `_type` 字段，仅在 active history 中保留，不会发给 LLM）：
      - `session_resume`：占位符消息，默认内容就是字面的
        "[Previous session summary]"，本身不带信息，直接跳过，不占用篇幅。
      - `compact_summary`：`/compact` 生成的结构化摘要（包含 Goal / Current
        State / Pending 等分节），信息密度远高于普通对话轮次，是判断"当前
        未完成任务"的最佳来源。标注成 `[历史摘要（/compact 生成）]` 而不是
        普通的 `[assistant]`，让下游 prompt 能识别出这是压缩摘要而不是随口
        的一句话，从而在生成目标时更放心地引用其中的 Pending/Current State
        内容（这属于结构化事实，不是"照抄用户随口一句话"）。

    返回 (transcript_text, truncated, has_compact_summary)。truncated 表示是否
    发生了截断（消息条数或字符数任一超限）；has_compact_summary 表示摘录里是否
    包含 `/compact` 摘要，供调用方选择更贴合的 prompt 措辞。
    """
    candidates: list[str] = []
    has_compact_summary = False
    for msg in history:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        entry_type = msg.get("_type")
        if entry_type == "session_resume":
            # 默认占位符文案本身没有信息量，直接跳过；万一未来被改成携带
            # 真实内容，仍然会走下面的通用文本提取逻辑（不在此处特判丢弃）。
            content = msg.get("content")
            if isinstance(content, str) and content.strip() == "[Previous session summary]":
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

        if entry_type == "compact_summary":
            has_compact_summary = True
            candidates.append(f"[历史摘要（/compact 生成）]\n{text}")
        else:
            candidates.append(f"[{role}] {text}")

    truncated = len(candidates) > max_messages
    if truncated:
        candidates = candidates[-max_messages:]

    # 摘录里包含 /compact 摘要时，它本身信息密度高（结构化的 Goal/Current
    # State/Pending 等分节），值得放宽字符预算，避免被普通对话截断挤掉。
    effective_max_chars = max_chars * 2 if has_compact_summary else max_chars

    transcript = "\n\n".join(candidates)
    if len(transcript) > effective_max_chars:
        truncated = True
        transcript = transcript[-effective_max_chars:]

    return transcript, truncated, has_compact_summary


class GoalSpecBuilder:
    """把自然语言目标转化为结构化 GoalSpec，支持基于用户反馈的多轮修订。

    每次调用都是独立的一次性 Agent 调用（不占用主 Agent 的历史/上下文），
    参考 role_agents/evaluator.py 的调用方式。
    """

    def __init__(self, cfg: "AppConfig", parent_session_id: Optional[str] = None,
                 parent_session_dir=None, llm_helper=None) -> None:
        self._cfg = cfg
        # [REMOVED: agent 机制] parent_session_id / parent_session_dir 曾经是给
        # spawn_judge_agent 用的"把子 Agent session 挂到主 session 目录下"的
        # 参数。GoalSpecBuilder 不再构造任何子 Agent（见 _run_builder 的重构
        # 说明），这两个参数已经没有实际作用；仍然保留在签名里只是为了不
        # 破坏现有调用方（goal_mode_cmd.py / runner.py 里已经在传），避免
        # 一次性改动过多调用点。
        self._parent_session_id = parent_session_id
        self._parent_session_dir = parent_session_dir
        # 复用调用方（通常是主 Agent）已经持有的 LLMHelper，天然跟随
        # /model、/provider 的实时切换；调用方拿不到活跃 Agent 实例时
        # （如独立工具函数、无 agent 的后台任务）传 None，_run_builder 会
        # 用 LLMHelper.from_config(cfg) 兜底现建一条单链 client。
        self._llm_helper = llm_helper
        # 上一次 _run_builder 调用的诊断信息，供 build_initial/build_from_history
        # 在命中 _fallback_criteria 时打印具体原因，而不是让用户只看到一份
        # "看起来像拼出来的"验收标准却不知道为什么。
        self.last_error: Optional[str] = None

    def _run_builder(self, prompt: str) -> str:
        """直接调用大模型生成/修订 GoalSpec 草案（不经过 Agent/工具循环）。

        [REFACTOR 2] 之前这里复用 judge_factory.spawn_judge_agent 构造一个
        "tools_enabled=False、max_turns=2 的受限 Agent"来跑这一轮。实测暴露
        出一个新的、比"llm_fallback_chain 未清空"更根本的问题：

          即使 tools_enabled=False（注册表为空），Agent 构造流程仍然会按
          正常主循环的方式连接已配置的 MCP server（如用户环境里的
          `time_server`），并且系统提示词模板本身没有为"这是一个不该有
          任何工具"的场景做裁剪。模型看到自己是在一个通用 Agent 里，会
          按惯常做法先尝试 `skill_list`/`bash` 摸底环境，但这些工具压根
          不在（空）注册表里，于是每一轮都以 `Unknown tool` 报错收场，在
          `max_turns=2` 的预算内根本没有机会把 JSON 草案说出口——表现为
          "GoalSpecBuilder 未产出任何文本输出"，退回通用兜底标准。

        生成验收标准这件事本质上是"单轮、无工具、只要一段文本"的任务，
        不需要多轮工具调用/环境探索，Agent 循环带来的只有额外的失败面
        （MCP 连接、工具幻觉、轮次预算）而没有额外的能力。因此改为直接
        通过 LLMHelper.ask() 发起一次裸的 chat completion：不注册任何
        工具 schema、不连接任何 MCP server、没有"允许几轮"的概念，模型
        唯一能做的事就是把 JSON 写出来。

        model/provider 解析：仍然复用 role_agents/model_resolution.py 的
        三层优先级（GoalModeConfig.spec_builder_model/provider > 主模型），
        通过 LLMHelper.ask 的 override_model/override_provider 一次性指定，
        等价于原来 spawn_judge_agent 里"清空 llm_fallback_chain、只用单条
        client"的效果，不需要为此再构造一个 AppConfig/Agent。
        """
        from mini_agent.llm.service import LLMHelper
        from mini_agent.role_agents.model_resolution import resolve_role_model
        from types import SimpleNamespace

        gm = getattr(self._cfg, "goal_mode", None)
        # GoalModeConfig 用的是 spec_builder_model/spec_builder_provider 字段
        # （不是 judge_model/judge_provider），这里包一层轻量适配对象，复用
        # resolve_role_model 期望的 role_cfg_block.judge_model/.judge_provider
        # 接口，避免另写一份三层模型优先级解析逻辑。
        role_cfg_block = SimpleNamespace(
            judge_model=getattr(gm, "spec_builder_model", None),
            judge_provider=getattr(gm, "spec_builder_provider", None),
        )
        model, provider = resolve_role_model(None, role_cfg_block, self._cfg)

        helper = self._llm_helper or LLMHelper.from_config(self._cfg)
        system_prompt = pm.render("system/goal_spec_builder")

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
            log_exception(e, where='mini_agent.goal_mode.spec.GoalSpecBuilder._run_builder')
            self.last_error = str(e)
            R.print_warning(f"[GoalSpecBuilder] LLM 调用失败，将使用兜底验收标准。原因：{self.last_error}")
            return (
                '{"goal_text": "", "acceptance_criteria": [], '
                '"verification_method": "manual_review", "verification_command": "", '
                f'"_error": {json.dumps(self.last_error)}}}'
            )

        if text and text.strip():
            self.last_error = None
            return text

        # LLM 调用没抛异常，但没产出任何文本（比如被安全过滤器拦截返回空）。
        self.last_error = "GoalSpecBuilder 未产出任何文本输出"
        R.print_warning(f"[GoalSpecBuilder] LLM 调用失败，将使用兜底验收标准。原因：{self.last_error}")
        return (
            '{"goal_text": "", "acceptance_criteria": [], '
            '"verification_method": "manual_review", "verification_command": "", '
            f'"_error": {json.dumps(self.last_error)}}}'
        )

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
            # getattr 兜底：self.last_error 是 __init__ 里设置的诊断字段，
            # 若实例是通过 __new__ 构造（跳过 __init__，测试里常见）或来自
            # 其他不经过 __init__ 的构造路径，直接访问会抛 AttributeError，
            # 把"兜底展示原因"这个本该是纯展示的分支变成了一次真正的崩溃。
            reason = getattr(self, "last_error", None) or "LLM 返回内容解析后 acceptance_criteria 字段为空"
            R.print_warning(
                f"[GoalSpecBuilder] 未能从 LLM 输出中获得有效验收标准，"
                f"已使用通用兜底标准代替。原因：{reason}\n"
                f"LLM 原始输出（截断）：{raw[:500]!r}"
            )
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
        transcript, truncated, has_compact_summary = _extract_history_transcript(history)
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
        summary_note = (
            "\n（提示：上面标注为「历史摘要（/compact 生成）」的部分是系统自动"
            "生成的结构化压缩摘要，通常包含 Goal / Current State / Pending 等"
            "分节，信息是可靠的事实陈述而非用户随口的一句话——请优先依据其中的"
            "Current State（当前进展）和 Pending / Next Steps（待办事项）来判断"
            "当前尚未完成的任务，可以直接引用其中的具体事实，不需要为了\"避免"
            "照抄\"而回避这些结构化信息；'照抄'规则针对的是把用户一句话原样"
            "当成验收标准，不适用于对这类结构化摘要的合理引用。）"
            if has_compact_summary else ""
        )
        prompt = pm.render(
            "user/goal_spec_from_history_request",
            history_transcript=transcript,
            truncated_note=truncated_note + summary_note,
        )
        R.print_debug_block(
            f"/goal from-history 发送给 GoalSpecBuilder 的输入"
            f"（history_transcript {len(transcript)} 字符，"
            f"has_compact_summary={has_compact_summary}，truncated={truncated}）",
            prompt,
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
            R.print_debug_block("/goal from-history 重试输入（上次输出无法解析为 JSON）", retry_prompt)
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
            # getattr 兜底：理由同上（build_initial 分支）——self.last_error
            # 可能因为实例未走 __init__ 而不存在。
            reason = getattr(self, "last_error", None) or "LLM 返回内容解析后 acceptance_criteria 字段为空"
            R.print_warning(
                f"[GoalSpecBuilder] from-history 未能获得有效验收标准，"
                f"已使用通用兜底标准代替。原因：{reason}\n"
                f"LLM 原始输出（截断）：{raw[:500]!r}"
            )
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
