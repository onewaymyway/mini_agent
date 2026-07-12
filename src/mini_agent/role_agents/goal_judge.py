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

from mini_agent.prompts import pm

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from mini_agent.orchestrator.agent_profiles import AgentProfile
    from mini_agent.goal_mode.spec import GoalSpec


def build_goal_judge_prompt(
    goal_spec: "GoalSpec",
    agent_output: str,
    round_no: int,
    prior_feedback: str = "",
) -> str:
    """构建 GoalJudge 的核查 prompt（模板见 prompts/user/goal_judge_request.md）。"""
    criteria_lines = "\n".join(
        f"{i+1}. {c}" for i, c in enumerate(goal_spec.acceptance_criteria)
    )
    prior_feedback_block = ""
    if prior_feedback:
        prior_feedback_block = "\n" + pm.fragment(
            "goal_mode", "PRIOR_FEEDBACK_BLOCK", feedback=prior_feedback
        )

    return pm.render(
        "user/goal_judge_request",
        round_no=round_no,
        goal_text=goal_spec.goal_text,
        criteria_lines=criteria_lines,
        agent_output=agent_output,
        prior_feedback_block=prior_feedback_block,
    )


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
      True         → 按 judge_allowed_tools / judge_allowed_tool_groups 白名单挂载只读工具；
                     默认仍强制 sandbox=True（工具调用会被拦截，只显示
                     "would have executed"，不会真的跑）。
                     如果需要 GoalJudge 真的执行命令来验证（比如跑 pytest/运行脚本），
                     再额外打开 base_cfg.goal_mode.judge_yes_mode，此时会以
                     auto_approve=True + 不走 sandbox 的方式真实执行（等价于人工
                     一直按 --yes 放行），不会逐条弹确认。
    """
    from mini_agent.config import load_config
    from mini_agent.agent import Agent
    from mini_agent.permissions import PermissionGuard
    from mini_agent.tools import get_default_registry

    goal_cfg_block = getattr(base_cfg, "goal_mode", None)
    tools_enabled = bool(getattr(goal_cfg_block, "judge_tools_enabled", False))
    yes_mode = bool(getattr(goal_cfg_block, "judge_yes_mode", False))
    # tools_enabled 且未开 yes_mode 时强制 sandbox=True（拦截真实执行，只能看到
    # "would have executed"）；开了 yes_mode 则真实执行，等价于始终 --yes 放行。
    judge_sandbox = (not yes_mode) if tools_enabled else base_cfg.sandbox

    from mini_agent.role_agents.model_resolution import resolve_role_model
    judge_model, judge_provider = resolve_role_model(profile, goal_cfg_block, base_cfg)

    judge_cfg = load_config(
        project_root=base_cfg.project_root,
        verbose=base_cfg.verbose,
        sandbox=judge_sandbox,
        auto_approve=True,
        model=judge_model,
        llm_provider=judge_provider,
        llm_base_url=base_cfg.llm_base_url,
        # [BUGFIX] 同 evaluator.py：继承 base_cfg 的 --debug-llm，而不是硬编码 False。
        debug_llm=getattr(base_cfg, "debug_llm", False),
        debug_llm_console=getattr(base_cfg, "debug_llm_console", False),
    )
    judge_cfg.api_key = base_cfg.api_key
    judge_cfg.max_turns = 6 if tools_enabled else 2   # 挂工具时允许多跑几轮验证命令
    judge_cfg.stream = False
    judge_cfg.system_extra = profile.system_prompt if profile.system_prompt.strip() else pm.render("system/goal_judge")
    # [SYS-GOAL-MODE] 给 GoalJudge 内部 Agent 一个专属的显示名，而不是沿用主 Agent 的
    # cfg.agent_name（默认都是同一个名字，会导致 print_assistant_prefix 打印出来的前缀
    # 跟主 Agent 说话一模一样，看不出这是评估者的输出）。
    judge_cfg.agent_name = "🎯 GoalJudge"
    # [SYS-TURN-JUDGE][BUGFIX] load_config() 会从同一份 agent_config.json 重新加载配置，
    # 若其中开启了 turn_judge，会导致 GoalJudge 这个内部 Agent 自己跑 run_turn() 时
    # 对自己触发一次 TurnJudge 核查，引发无限递归自我核查。显式禁用，不能只依赖
    # 下面的 is_subagent 标记（那只是第二道保险）。
    from mini_agent.config.models import TurnJudgeConfig as _TurnJudgeConfig
    judge_cfg.turn_judge = _TurnJudgeConfig(enabled=False)

    guard = PermissionGuard(
        auto_approve=True,
        sandbox=judge_sandbox,
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

    judge_agent = Agent(cfg=judge_cfg, guard=guard, registry=registry, is_subagent=True)

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
