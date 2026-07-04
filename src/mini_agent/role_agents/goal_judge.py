"""
role_agents/goal_judge.py — GoalJudgeAgent

职责：
  - 对照 GoalSpec（目标 + 验收标准）核查主 Agent 当前产出是否达成目标
  - 输出结构化判定：GOAL_STATUS: DONE | CONTINUE | NEED_COMPACT
  - CONTINUE 时给出具体、可操作的下一步反馈（不是泛泛的"继续加油"）
  - 可选挂载受限只读工具（bash/read_file/grep 等），自己跑一遍验证命令，
    而不是单纯"读文字判断"（由 cfg.goal_mode.judge_tools_enabled 开关控制）

与 EvaluatorAgent 的区别：
  - Evaluator 判断"质量好不好"（打分）
  - GoalJudge 判断"目标达成没有"（对照验收标准清单逐条核查 + 状态机）

与 CoachAgent 的区别：
  - Coach 是过程中的策略建议，不做终局判定
  - GoalJudge 是每轮外层循环结束时的"关卡"，决定 GoalRunner 下一步动作
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from mini_agent.orchestrator.agent_profiles import AgentProfile
    from mini_agent.goal_mode.spec import GoalSpec


DEFAULT_GOAL_JUDGE_SYSTEM = """你是一位严格的目标达成核查员（Goal Judge）。
你的唯一职责是对照「验收标准清单」逐条核查 AI 助手是否已经达成用户设定的目标。

核查原则：
1. 逐条核查每一条验收标准，给出"通过 / 不通过"，并说明依据（不是主观印象，是具体证据）
2. 如果你被授予了工具权限，优先通过实际运行命令（如测试、lint）来验证，而不是单纯相信 AI 助手的自述
3. 只要有一条标准不通过，整体状态就不能判为 DONE
4. 如果你怀疑 AI 助手是因为历史上下文混乱、反复卡在同一处、或者上下文已经很臃肿导致失去焦点，
   可以判定为 NEED_COMPACT，建议压缩历史后重新聚焦
5. CONTINUE 时反馈必须具体可执行：明确指出"哪条标准没过 + 大概该怎么做"，
   不要说"请继续完善"这种空话

输出格式（必须严格遵守，GOAL_STATUS 行必须存在且在最后）：
---
**验收核查**
- [标准1 摘要]：通过 / 不通过 —— 依据
- [标准2 摘要]：通过 / 不通过 —— 依据
（每条标准都要核查，不要遗漏）

**结论**
简要说明整体情况。

**反馈**
（仅当 CONTINUE 时必填：给 AI 助手的具体下一步指令）

GOAL_STATUS: DONE
---
（GOAL_STATUS 只能是 DONE / CONTINUE / NEED_COMPACT 三者之一）"""


def build_goal_judge_prompt(
    goal_spec: "GoalSpec",
    agent_output: str,
    round_no: int,
    prior_feedback: str = "",
) -> str:
    """构建 GoalJudge 的核查 prompt。"""
    criteria_lines = "\n".join(
        f"{i+1}. {c}" for i, c in enumerate(goal_spec.acceptance_criteria)
    )
    prior_block = ""
    if prior_feedback:
        prior_block = f"\n\n**上一轮给出的反馈（用于判断本轮是否已解决）：**\n{prior_feedback}"

    return f"""请核查 AI 助手是否已经达成以下目标。这是第 {round_no} 轮核查。

**目标：**
{goal_spec.goal_text}

**验收标准清单：**
{criteria_lines}

**AI 助手本轮的产出（含过程与最终回复）：**
{agent_output}
{prior_block}

请严格按照你的核查原则和输出格式进行判定。"""


def run_goal_judge(
    profile: "AgentProfile",
    base_cfg: "AppConfig",
    goal_spec: "GoalSpec",
    agent_output: str,
    round_no: int = 1,
    prior_feedback: str = "",
) -> str:
    """
    运行 GoalJudgeAgent，返回判定文本（含 GOAL_STATUS 行）。

    工具权限由 base_cfg.goal_mode.judge_tools_enabled 控制：
      False（默认）→ 空工具注册表，纯文本判定（与现有 evaluator 行为一致，零风险）
      True         → 按 judge_allowed_tools / judge_allowed_tool_groups 白名单挂载只读工具，
                     且强制 sandbox=True，防止 GoalJudge 越权修改代码
    """
    from mini_agent.config import load_config
    from mini_agent.agent import Agent
    from mini_agent.permissions import PermissionGuard
    from mini_agent.tools import get_default_registry

    goal_cfg_block = getattr(base_cfg, "goal_mode", None)
    tools_enabled = bool(getattr(goal_cfg_block, "judge_tools_enabled", False))

    judge_cfg = load_config(
        project_root=base_cfg.project_root,
        verbose=False,
        sandbox=True if tools_enabled else base_cfg.sandbox,
        auto_approve=True,
        model=profile.model or (getattr(goal_cfg_block, "judge_model", None)) or base_cfg.model,
        llm_provider=profile.provider or (getattr(goal_cfg_block, "judge_provider", None)) or base_cfg.llm_provider,
        llm_base_url=base_cfg.llm_base_url,
        debug_llm=False,
    )
    judge_cfg.api_key = base_cfg.api_key
    judge_cfg.max_turns = 6 if tools_enabled else 2   # 挂工具时允许多跑几轮验证命令
    judge_cfg.stream = False
    judge_cfg.system_extra = profile.system_prompt if profile.system_prompt.strip() else DEFAULT_GOAL_JUDGE_SYSTEM

    guard = PermissionGuard(
        auto_approve=True,
        sandbox=True if tools_enabled else base_cfg.sandbox,
        project_root=base_cfg.project_root,
    )

    if tools_enabled:
        allowed_tools = list(getattr(goal_cfg_block, "judge_allowed_tools", []) or [])
        allowed_groups = list(getattr(goal_cfg_block, "judge_allowed_tool_groups", []) or [])
        # profile 自己声明的 tools/tool_groups 若非空，取交集收窄（profile 更具体，优先级更高）
        if profile.tools:
            allowed_tools = [t for t in profile.tools if t in allowed_tools] or profile.tools
        if profile.tool_groups:
            allowed_groups = [g for g in profile.tool_groups if g in allowed_groups] or profile.tool_groups
        registry = get_default_registry().filtered(names=allowed_tools, groups=allowed_groups)
    else:
        registry = get_default_registry().filtered(names=[], groups=[])

    judge_agent = Agent(cfg=judge_cfg, guard=guard, registry=registry)

    prompt = build_goal_judge_prompt(
        goal_spec=goal_spec,
        agent_output=agent_output,
        round_no=round_no,
        prior_feedback=prior_feedback,
    )

    try:
        result = judge_agent.run_turn(prompt)
        return result
    except Exception as e:
        # 判定失败时保守返回 CONTINUE，绝不能让异常被当成 DONE
        return f"**结论**\n[GoalJudgeAgent 运行失败: {e}]，保守判定为需继续。\n\nGOAL_STATUS: CONTINUE"
