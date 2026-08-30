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

生成路径（cfg.goal_mode.spec_builder_mode，见 config/models.py::GoalModeConfig）：
  "llm"   — 始终单轮裸 chat completion（LLMHelper.ask，不挂工具）。
  "agent" — 始终构造一个只读、有限工具的受限 Agent（skill_list / show_workflow /
            list_workflows / read_file / list_dir / tree_summary / grep / glob），
            可以先查证项目里实际存在的 skill/workflow 定义再产出验收标准。
  "auto"  — 默认。规则先判断目标是否涉及项目内部信息（关键词 + 已知 skill/
            workflow 名称命中）；命中走 "agent"，否则走 "llm"。规则未命中时，
            仍允许 "llm" 路径通过输出里的 needs_project_context 字段自报"需要
            读项目"，命中后改用 "agent" 路径重新生成一次。
  详见 docs/goal-mode-guide.md 「验收标准生成路径」一节。
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

    def validate_verifiability(self) -> list[str]:
        """[next_doc/autonomous_execution_stability_and_self_learning_integration_plan.md
        方案 B] 验收标准可验证性自检：纯启发式规则，不引入 LLM 语义判断
        （与本文件其它启发式函数如 `_looks_like_verbatim_echo` 保持一致的
        设计取舍）。

        返回一份"可能难以判定通过与否"的验收标准清单（每项是一句面向用户
        的警告文本），空列表表示没有发现明显问题。这只是提前暴露风险的
        自检，不会阻止用户冻结 GoalSpec——GoalJudge 运行时依然会正常核查，
        本函数只是把"运行时才会发现的一类问题"尽量提前到冻结前。

        判定规则：
          - 整份 GoalSpec 设置了 verification_method="run_command" 且
            verification_command 非空时，视为"有客观验证手段兜底"，不逐条
            检查（GoalJudge 可以直接跑这条命令验证，措辞是否精确没那么关键）。
          - 否则逐条检查每条验收标准文本，是否包含至少一个"可判断依据"类
            关键词（如"通过""完成""生成""输出""确认""验证""存在""无""不再"
            等，覆盖常见的可核查表述）。完全不包含这类关键词、且文本很短
            （容易是空泛描述）的条目会被标记为警告。
        """
        warnings: list[str] = []
        if self.verification_method == "run_command" and self.verification_command:
            return warnings

        evidence_keywords = (
            "通过", "完成", "生成", "输出", "确认", "验证", "存在", "不再",
            "达到", "包含", "返回", "成功", "失败", "无", "为", "是",
        )
        for i, criterion in enumerate(self.acceptance_criteria):
            text = (criterion or "").strip()
            if not text:
                warnings.append(f"第 {i+1} 条验收标准为空文本，无法核查。")
                continue
            has_keyword = any(kw in text for kw in evidence_keywords)
            if not has_keyword and len(text) < 12:
                warnings.append(
                    f"第 {i+1} 条「{text}」看起来比较空泛，缺少明确的可核查依据"
                    "（比如具体产出物、可观察的状态变化、可运行的验证命令），"
                    "GoalJudge 后续可能难以判定是否通过。"
                )
        return warnings


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_BRACE_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> Optional[dict]:
    """从 LLM 输出中提取第一个 JSON 对象，容忍 markdown 代码块包裹。

    正则抠出的片段哪怕能被 `json.loads` 成功解析，也不保证结果是
    dict——LLM 偶尔会在正文里夹带一段本身也合法但类型不对的 JSON（字符串/
    数组），被误抠中解析成功后，下游 `data.get(...)` 会直接因类型不对崩掉
    （镜像 `goal_execution_spec.py::_extract_json` 修的同一类问题，这里
    同步修）。非 dict 一律按"解析失败"处理，交由调用方已有的兜底逻辑
    （沿用 prior_spec / 保守值）接管。"""
    m = _JSON_FENCE_RE.search(text)
    candidate = m.group(1) if m else None
    if candidate is None:
        m2 = _BRACE_RE.search(text)
        candidate = m2.group(0) if m2 else None
    if candidate is None:
        return None
    try:
        parsed = json.loads(candidate)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.goal_mode.spec._extract_json')
        return None
    if not isinstance(parsed, dict):
        from mini_agent.errors import log_exception
        log_exception(
            TypeError(f"LLM JSON 解析结果类型不是 dict，而是 {type(parsed).__name__}：{candidate[:200]!r}"),
            where='mini_agent.goal_mode.spec._extract_json',
        )
        return None
    return parsed


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


# ── [SYS-GOAL-MODE-DUAL-PATH] auto 模式的规则判定 ───────────────────────────
# 只是"锦上添花"的启发式信号：命中就升级到 agent 路径（多花点时间但更可靠），
# 漏判也不是致命问题——build_initial 里裸 LLM 输出的 needs_project_context
# 自报字段是第二道保险。因此这里的实现故意保持"便宜、宽松、宁可多召回"，
# 不追求精确，任何一步失败都直接忽略，不影响主流程。
_PROJECT_CONTEXT_KEYWORD_RE = re.compile(
    r"skill|技能|workflow|工作流|工作流程|流水线|pipeline",
    re.IGNORECASE,
)


def _collect_known_skill_workflow_names(cfg: "AppConfig") -> list[str]:
    """尽力从磁盘直接收集已知的 skill / workflow 名称，用于关键词命中判断。

    刻意不复用 SkillLoader/WorkflowStore 的构造逻辑（它们的初始化参数和副作用
    比这里需要的多得多），直接按项目里的约定目录扫描文件/目录名——足够覆盖
    "用户提到了一个真实存在的 skill/workflow 名字"这个场景，任何路径不存在/
    扫描出错都直接忽略。
    """
    from pathlib import Path

    names: list[str] = []
    project_root = getattr(cfg, "project_root", None)
    if not project_root:
        return names
    project_root = Path(project_root)

    # skills：cfg.skills_dir（若配置）+ 项目内约定的 .claude/skills 目录，
    # 兼容"目录形式"（<name>/SKILL.md）和"平铺形式"（<name>.md）两种布局。
    skill_dirs = []
    cfg_skills_dir = getattr(cfg, "skills_dir", None)
    if cfg_skills_dir:
        skill_dirs.append(Path(cfg_skills_dir))
    skill_dirs.append(project_root / ".claude" / "skills")
    for d in skill_dirs:
        try:
            if not d.is_dir():
                continue
            for entry in d.iterdir():
                if entry.is_dir() and (entry / "SKILL.md").exists():
                    names.append(entry.name)
                elif entry.is_file() and entry.suffix == ".md":
                    names.append(entry.stem)
        except Exception:
            continue

    # workflows：与 workflow/store.py::WorkflowStore.WORKFLOWS_DIR 保持一致，
    # 同时兼容单文件模式（<name>.yaml）和文件夹模式（<name>/workflow.yaml）。
    try:
        wf_dir = project_root / ".agent" / "workflows"
        if wf_dir.is_dir():
            for entry in wf_dir.iterdir():
                if entry.is_dir() and (entry / "workflow.yaml").exists():
                    names.append(entry.name)
                elif entry.is_file() and entry.suffix in (".yaml", ".yml"):
                    names.append(entry.stem)
    except Exception:
        pass

    return names


def _rule_based_needs_project_context(text: str, cfg: "AppConfig") -> bool:
    """判断一段目标/反馈文本是否"看起来"需要读项目内容才能写出可核查的验收标准。

    命中任一条件即可：
      1. 文本里出现 skill/workflow 相关关键词（中英文均覆盖）。
      2. 文本里提到了项目里已知存在的某个 skill 或 workflow 名称——哪怕用户
         没有说"skill"/"workflow"这两个词本身（比如直接喊出 skill 名字）。
    """
    if not text:
        return False
    if _PROJECT_CONTEXT_KEYWORD_RE.search(text):
        return True
    try:
        for name in _collect_known_skill_workflow_names(cfg):
            if name and len(name) >= 2 and name.lower() in text.lower():
                return True
    except Exception:
        pass
    return False


class GoalSpecBuilder:
    """把自然语言目标转化为结构化 GoalSpec，支持基于用户反馈的多轮修订。

    每次调用都是独立的一次性 Agent 调用（不占用主 Agent 的历史/上下文），
    参考 role_agents/evaluator.py 的调用方式。
    """

    _VALID_MODES = ("llm", "agent", "auto")

    def __init__(self, cfg: "AppConfig", parent_session_id: Optional[str] = None,
                 parent_session_dir=None, llm_helper=None, mode: Optional[str] = None) -> None:
        self._cfg = cfg
        # session 挂载信息：mode="agent"（或 auto 命中 agent 路径）时会真正
        # 构造一个子 Agent（见 _run_builder_agent），此时会用到这两个参数把
        # 子 session 落在主 session 目录下；mode="llm" 时仍然用不到，但保留
        # 在签名里不受影响。
        self._parent_session_id = parent_session_id
        self._parent_session_dir = parent_session_dir
        # 复用调用方（通常是主 Agent）已经持有的 LLMHelper，天然跟随
        # /model、/provider 的实时切换；调用方拿不到活跃 Agent 实例时
        # （如独立工具函数、无 agent 的后台任务）传 None，_run_builder_llm 会
        # 用 LLMHelper.from_config(cfg) 兜底现建一条单链 client。
        self._llm_helper = llm_helper
        # 上一次 _run_builder* 调用的诊断信息，供 build_initial/build_from_history
        # 在命中 _fallback_criteria 时打印具体原因，而不是让用户只看到一份
        # "看起来像拼出来的"验收标准却不知道为什么。
        self.last_error: Optional[str] = None

        # [SYS-GOAL-MODE-DUAL-PATH] 生成路径：显式传入的 mode 参数（如
        # `/goal --mode=agent <文本>`）优先于 cfg.goal_mode.spec_builder_mode，
        # 都没有时兜底 "auto"。非法值（拼写错误等）视为 "auto" 并打印警告，
        # 而不是让整个 builder 因为一个坏配置项而崩溃。
        gm = getattr(cfg, "goal_mode", None)
        raw_mode = (mode or getattr(gm, "spec_builder_mode", None) or "auto")
        normalized = str(raw_mode).strip().lower()
        if normalized not in self._VALID_MODES:
            R.print_warning(
                f"[GoalSpecBuilder] 未知的 spec_builder_mode={raw_mode!r}，"
                f"已回退为 \"auto\"（合法值：{', '.join(self._VALID_MODES)}）。"
            )
            normalized = "auto"
        self.mode = normalized
        # 记录最近一次实际生效的路径（"llm" | "agent"），供上层展示/调试用，
        # 与 self.mode（用户配置的策略）区分开——"auto" 本身不是一次实际路径。
        self.last_effective_path: Optional[str] = None

    def _resolve_spec_builder_model(self):
        """解析 GoalSpecBuilder 用的 model/provider（复用三层优先级）。"""
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
        return resolve_role_model(None, role_cfg_block, self._cfg), role_cfg_block

    @staticmethod
    def _empty_result_json(reason: str) -> str:
        return (
            '{"goal_text": "", "acceptance_criteria": [], '
            '"verification_method": "manual_review", "verification_command": "", '
            f'"_error": {json.dumps(reason)}}}'
        )

    def _run_builder(self, prompt: str, *, detection_text: Optional[str] = None) -> str:
        """按 self.mode 分诊到具体生成路径，是 build_initial/from_history/revise

        的唯一入口。

        - "llm"   → 直接走 `_run_builder_llm`。
        - "agent" → 直接走 `_run_builder_agent`。
        - "auto"  → 先用规则判断 `detection_text`（缺省用 prompt 本身）是否
          涉及项目内部信息（skill/workflow 关键词、或命中已知 skill/workflow
          名称），命中则走 agent 路径；否则先走 llm 路径，若其输出的 JSON 里
          `needs_project_context` 为 true（模型自报"这道题目我答不好，需要
          先看看项目"），则丢弃这次结果，改用 agent 路径重新生成一次——这样
          规则漏判的情况仍有一次纠正机会，而不会两头都不覆盖。
        """
        if self.mode == "agent":
            self.last_effective_path = "agent"
            return self._run_builder_agent(prompt)

        if self.mode == "llm":
            self.last_effective_path = "llm"
            return self._run_builder_llm(prompt)

        # mode == "auto"
        text_for_rule = detection_text if detection_text is not None else prompt
        if _rule_based_needs_project_context(text_for_rule, self._cfg):
            self.last_effective_path = "agent"
            return self._run_builder_agent(prompt)

        raw = self._run_builder_llm(prompt)
        self.last_effective_path = "llm"
        data = _extract_json(raw) or {}
        if data.get("needs_project_context") is True:
            R.print_info(
                "[GoalSpecBuilder] 规则未命中，但模型自报需要读取项目内容"
                "（skill/workflow 等）才能写好验收标准，改用受限 Agent 路径重新生成…"
            )
            self.last_effective_path = "agent"
            return self._run_builder_agent(prompt)
        return raw

    def _run_builder_llm(self, prompt: str) -> str:
        """直接调用大模型生成/修订 GoalSpec 草案（不经过 Agent/工具循环）。

        这是历史上唯一的路径（见 spec.py 模块头部"生成路径"说明的演进过程），
        现在是 mode="llm" 时的固定路径，也是 mode="auto" 的默认路径（规则未
        判定为需要项目上下文时）。

        生成验收标准这件事在不需要读项目文件时本质上是"单轮、无工具、只要
        一段文本"的任务，不需要多轮工具调用/环境探索——通过 LLMHelper.ask()
        发起一次裸的 chat completion：不注册任何工具 schema、不连接任何 MCP
        server、没有"允许几轮"的概念，模型唯一能做的事就是把 JSON 写出来。
        当目标确实涉及项目内部信息时，应该用 mode="agent"/"auto"（见
        `_run_builder_agent`），而不是让这条纯文本路径瞎编项目细节。

        model/provider 解析：复用 role_agents/model_resolution.py 的三层
        优先级（GoalModeConfig.spec_builder_model/provider > 主模型），通过
        LLMHelper.ask 的 override_model/override_provider 一次性指定。
        """
        from mini_agent.llm.service import LLMHelper

        (model, provider), _ = self._resolve_spec_builder_model()

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
            log_exception(e, where='mini_agent.goal_mode.spec.GoalSpecBuilder._run_builder_llm')
            self.last_error = str(e)
            R.print_warning(f"[GoalSpecBuilder] LLM 调用失败，将使用兜底验收标准。原因：{self.last_error}")
            return self._empty_result_json(self.last_error)

        if text and text.strip():
            self.last_error = None
            return text

        # LLM 调用没抛异常，但没产出任何文本（比如被安全过滤器拦截返回空）。
        self.last_error = "GoalSpecBuilder 未产出任何文本输出"
        R.print_warning(f"[GoalSpecBuilder] LLM 调用失败，将使用兜底验收标准。原因：{self.last_error}")
        return self._empty_result_json(self.last_error)

    def _run_builder_agent(self, prompt: str) -> str:
        """构造一个只读、有限工具的受限 Agent 来生成/修订 GoalSpec 草案。

        用于目标涉及项目本身信息（skill、workflow 等）的场景：这类目标写出
        "可核查"的验收标准，前提是先确认项目里实际存在的 skill/workflow 定义
        （名称、步骤、产出物），而不是凭 LLM 的先验知识编造一个不存在的路径
        或命令。

        与早期（[REFACTOR 2] 之前）的受限 Agent 方案的关键区别，是这次真的
        给了工具（`cfg.goal_mode.spec_builder_agent_allowed_tools/_groups`，
        默认 skill_list / list_workflows / show_workflow / read_file /
        list_dir / tree_summary / grep / glob），而不是"注册表为空却让模型
        以为自己在通用 Agent 环境里"——避免重蹈"每轮都 Unknown tool 报错、
        max_turns 耗尽仍说不出 JSON"的覆辙。工具白名单刻意只读：不包含
        bash、不包含任何写文件/写 workflow 的工具，builder 只需要"看"。
        """
        from mini_agent.role_agents.judge_factory import spawn_judge_agent, run_judge_turn

        gm = getattr(self._cfg, "goal_mode", None)
        _, role_cfg_block = self._resolve_spec_builder_model()

        allowed_tools = list(getattr(gm, "spec_builder_agent_allowed_tools", None) or [])
        allowed_tool_groups = list(getattr(gm, "spec_builder_agent_allowed_tool_groups", None) or [])
        max_turns = int(getattr(gm, "spec_builder_agent_max_turns", None) or 6)

        agent_system_prompt = (
            pm.render("system/goal_spec_builder")
            + "\n\n"
            + pm.render("system/goal_spec_builder_agent_addendum")
        )

        try:
            agent = spawn_judge_agent(
                profile=None,
                base_cfg=self._cfg,
                role_cfg_block=role_cfg_block,
                display_name="🎯 GoalSpecBuilder(agent)",
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
            log_exception(e, where='mini_agent.goal_mode.spec.GoalSpecBuilder._run_builder_agent')
            self.last_error = f"构造受限 Agent 失败：{e}"
            R.print_warning(f"[GoalSpecBuilder] {self.last_error}，将使用兜底验收标准。")
            return self._empty_result_json(self.last_error)

        result = run_judge_turn(agent, prompt, failure_role_label="GoalSpecBuilder(agent)")
        if not result.ok:
            self.last_error = result.error or "GoalSpecBuilder(agent) 运行失败"
            R.print_warning(f"[GoalSpecBuilder] {self.last_error}，将使用兜底验收标准。")
            return self._empty_result_json(self.last_error)

        if result.raw_output and result.raw_output.strip():
            self.last_error = None
            return result.raw_output

        self.last_error = "GoalSpecBuilder(agent) 未产出任何文本输出（可能一直在调用只读工具探索，未在 max_turns 内收敛）"
        R.print_warning(f"[GoalSpecBuilder] {self.last_error}，将使用兜底验收标准。")
        return self._empty_result_json(self.last_error)

    def build_initial(self, user_goal_text: str) -> GoalSpec:
        """根据用户的自然语言目标生成第 1 版 GoalSpec。"""
        prompt = pm.render("user/goal_spec_initial_request", user_goal_text=user_goal_text)
        raw = self._run_builder(prompt, detection_text=user_goal_text)
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
        raw = self._run_builder(prompt, detection_text=transcript)
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
        raw = self._run_builder(
            prompt, detection_text=f"{prior_spec.goal_text}\n{user_feedback}"
        )
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
