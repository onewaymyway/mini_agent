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
    # [Phase 3 重构] 样板逻辑收敛到 judge_factory.spawn_judge_agent /
    # run_judge_turn。函数签名和返回值保持完全不变。
    from mini_agent.role_agents.judge_factory import spawn_judge_agent, run_judge_turn

    goal_cfg_block = getattr(base_cfg, "goal_mode", None)
    tools_enabled = bool(getattr(goal_cfg_block, "judge_tools_enabled", False))

    judge_agent = spawn_judge_agent(
        profile=profile,
        base_cfg=base_cfg,
        role_cfg_block=goal_cfg_block,
        # [SYS-GOAL-MODE] 给 GoalJudge 内部 Agent 一个专属的显示名，而不是沿用主
        # Agent 的 cfg.agent_name（默认都是同一个名字，会导致 print_assistant_prefix
        # 打印出来的前缀跟主 Agent 说话一模一样，看不出这是评估者的输出）。
        display_name="🎯 GoalJudge",
        system_prompt=pm.render(
            "system/goal_judge",
            json_output_instructions=pm.fragment(
                "judge_json_output", "JSON_OUTPUT_INSTRUCTIONS",
                valid_statuses="DONE | CONTINUE | NEED_COMPACT",
                feedback_hint="先列出每条验收标准的通过情况，CONTINUE 时结尾给出具体下一步指令",
                example_status="CONTINUE",
                example_feedback="标准1（xxx）未通过，因为...；请先做 A，再做 B。",
            ),
        ),
        max_turns=6 if tools_enabled else 2,   # 挂工具时允许多跑几轮验证命令
        tools_enabled=tools_enabled,
        allowed_tools=list(getattr(goal_cfg_block, "judge_allowed_tools", []) or []),
        allowed_tool_groups=list(getattr(goal_cfg_block, "judge_allowed_tool_groups", []) or []),
    )

    prompt = build_goal_judge_prompt(
        goal_spec=goal_spec,
        agent_output=agent_output,
        round_no=round_no,
        prior_feedback=prior_feedback,
    )

    result = run_judge_turn(
        judge_agent, prompt, failure_role_label="GoalJudgeAgent",
        profile_name=profile.name if profile else "goal_judge",
    )

    if result.ok:
        return result.raw_output
    # 判定失败时保守返回 CONTINUE，绝不能让异常被当成 DONE。
    # 兜底文本本身也是合法 JSON，保持与正常输出一致的可解析契约。
    import json as _json
    return _json.dumps({
        "status": "CONTINUE",
        "feedback": f"[GoalJudgeAgent 运行失败: {result.error}]，保守判定为需继续。",
    }, ensure_ascii=False)
