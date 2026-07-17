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
    prior_checklist_lines: str = "",
    verification_result: Optional[dict] = None,
) -> str:
    """构建 GoalJudge 的核查 prompt（模板见 prompts/user/goal_judge_request.md）。

    prior_checklist_lines：[改造项三] 上一轮各条验收标准通过情况的文本行
    （由调用方基于 GoalState.criteria_status 拼装），空字符串时不生成
    prior_checklist_block（等价于该功能关闭或尚无历史记录）。

    verification_result：[goal_mode_stuck_compact_plan.md §2.2] GoalRunner
    程序化执行 verification_command 后的结果字典
    {"command": str, "returncode": int, "stdout_tail": str, "stderr_tail": str}，
    None 表示未设置验证命令、执行失败或功能关闭——不生成 verification_result_block。
    """
    criteria_lines = "\n".join(
        f"{i+1}. {c}" for i, c in enumerate(goal_spec.acceptance_criteria)
    )
    prior_feedback_block = ""
    if prior_feedback:
        prior_feedback_block = "\n" + pm.fragment(
            "goal_mode", "PRIOR_FEEDBACK_BLOCK", feedback=prior_feedback
        )

    prior_checklist_block = ""
    if prior_checklist_lines:
        prior_checklist_block = "\n" + pm.fragment(
            "goal_mode", "PRIOR_CHECKLIST_BLOCK", checklist_lines=prior_checklist_lines
        )

    verification_result_block = ""
    if verification_result:
        verification_result_block = "\n" + pm.fragment(
            "goal_mode", "VERIFICATION_RESULT_BLOCK",
            verification_command=verification_result.get("command", ""),
            returncode=verification_result.get("returncode", ""),
            stdout_tail=verification_result.get("stdout_tail", "") or "（无输出）",
            stderr_tail=verification_result.get("stderr_tail", "") or "（无输出）",
        )

    return pm.render(
        "user/goal_judge_request",
        round_no=round_no,
        goal_text=goal_spec.goal_text,
        criteria_lines=criteria_lines,
        agent_output=agent_output,
        prior_feedback_block=prior_feedback_block,
        prior_checklist_block=prior_checklist_block,
        verification_result_block=verification_result_block,
    )


def run_goal_judge(
    profile: "AgentProfile",
    base_cfg: "AppConfig",
    goal_spec: "GoalSpec",
    agent_output: str,
    round_no: int = 1,
    prior_feedback: str = "",
    extended_output_enabled: bool = False,
    prior_checklist_lines: str = "",
    verification_result: Optional[dict] = None,
    process_integrity_enabled: bool = False,
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

    extended_output_enabled：[goal_mode_completion_improvement_plan 改造项一/三]
        True 时在 system prompt 里额外拼接 progress/progress_reason/checklist
        的输出要求（GOAL_JUDGE_EXTENDED_OUTPUT_INSTRUCTIONS），对应
        cfg.goal_mode.progress_judge_mode == "llm"。False 时行为与升级前完全
        一致，不会要求也不会解析这些额外字段。
    prior_checklist_lines：透传给 build_goal_judge_prompt，供 GoalJudge 参考
        上一轮各条验收标准的通过情况（见改造项三）。
    verification_result：透传给 build_goal_judge_prompt（见 goal_mode_stuck_compact_plan.md §2.2）。
    process_integrity_enabled：[goal_mode_stuck_compact_plan.md §2.1] True 时在 system prompt
        里额外拼接"过程正当性判断"指令（PROCESS_INTEGRITY_INSTRUCTIONS），要求判官额外输出
        process_flags 字段标记投机行为（测试被弱化/检查被绕过/结果被伪造等）。对应
        cfg.goal_mode.process_integrity_check_enabled，与 extended_output_enabled
        （progress/checklist）是两个独立开关，可任意组合。False 时行为与升级前完全一致，
        不会要求也不会解析 process_flags。
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
            extended_output_instructions=(
                (
                    pm.fragment("goal_mode", "GOAL_JUDGE_EXTENDED_OUTPUT_INSTRUCTIONS")
                    if extended_output_enabled else ""
                )
                + (
                    ("\n" if extended_output_enabled else "")
                    + pm.fragment("goal_mode", "PROCESS_INTEGRITY_INSTRUCTIONS")
                    if process_integrity_enabled else ""
                )
            ),
        ),
        max_turns=int(getattr(goal_cfg_block, "judge_max_turns", 40)),
        tools_enabled=tools_enabled,
        allowed_tools=list(getattr(goal_cfg_block, "judge_allowed_tools", []) or []),
        allowed_tool_groups=list(getattr(goal_cfg_block, "judge_allowed_tool_groups", []) or []),
    )

    prompt = build_goal_judge_prompt(
        goal_spec=goal_spec,
        agent_output=agent_output,
        round_no=round_no,
        prior_feedback=prior_feedback,
        prior_checklist_lines=prior_checklist_lines,
        verification_result=verification_result,
    )

    result = run_judge_turn(
        judge_agent, prompt, failure_role_label="GoalJudgeAgent",
        profile_name=profile.name if profile else "goal_judge",
    )

    if result.ok and result.raw_output and result.raw_output.strip():
        return result.raw_output

    import json as _json
    if result.ok:
        # [BUGFIX] result.ok=True 但 raw_output 为空/空白：GoalJudge 挂了
        # 只读工具（judge_tools_enabled=True）时，可能在 max_turns 内一直在
        # 调用工具验证（read_file/grep/bash 等），始终没有在最后一轮收敛成
        # 最终 JSON 文本判定。此前这种情况会被当成"正常成功"直接返回空
        # 字符串，导致上层 extract_goal_status/parse_judge_verdict 拿到空
        # 文本、静默兜底成 CONTINUE，且没有任何可展示的判定内容——用户只能
        # 看到状态行，看不到"为什么没完成"。这里显式识别出来，走同一条
        # "判定失败"兜底路径，附带明确原因。
        fallback_msg = (
            "GoalJudgeAgent 在允许的轮次内未产出最终文本判定（可能一直在调用"
            "工具验证、没有收敛到结论），保守判定为需继续。建议检查 "
            "cfg.goal_mode.judge_tools_enabled 及判官 max_turns 配置，或打开 "
            "judge_show_prompt 排查判官具体在做什么。"
        )
        try:
            import mini_agent.ui.renderer as R
            R.print_warning(f"[GoalJudgeAgent] 命中空输出兜底：{fallback_msg}")
        except Exception:
            pass
        return _json.dumps({
            "status": "CONTINUE",
            "feedback": fallback_msg,
        }, ensure_ascii=False)

    # 判定失败时保守返回 CONTINUE，绝不能让异常被当成 DONE。
    # 兜底文本本身也是合法 JSON，保持与正常输出一致的可解析契约。
    return _json.dumps({
        "status": "CONTINUE",
        "feedback": f"[GoalJudgeAgent 运行失败: {result.error}]，保守判定为需继续。",
    }, ensure_ascii=False)
