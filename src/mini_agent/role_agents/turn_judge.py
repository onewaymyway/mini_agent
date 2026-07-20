"""
role_agents/turn_judge.py — TurnJudgeAgent

职责：
  - 在每一轮对话结束、即将进入"等待真人用户输入"之前介入核查一次：
    这到底是主 Agent 真的完成了当前请求、需要人类给出新指示，
    还是主 Agent 遇到了技术性问题（模型输出格式有问题、撞到 max_turns
    硬顶需要 compact 等），本不该打扰真人，应该由系统自动代替用户反馈，
    让主 Agent 继续处理。
  - 输出结构化判定：TURN_STATUS: NEED_USER | AUTO_CONTINUE | NEED_COMPACT
  - AUTO_CONTINUE 时给出具体、可执行的反馈文本，作为"自动用户输入"注入下一轮。

与 GoalJudgeAgent 的区别：
  - GoalJudge 是 Goal 模式专属的"目标达成"核查，对照验收标准清单判定
  - TurnJudge 是通用机制（不依赖 GoalSpec），任何一轮对话结束时都可以启用，
    只判断"是否需要真人介入"这一件事

设计取舍（与 goal_judge 一致）：
  - 判定失败（异常）时保守返回 NEED_USER，绝不能让异常被当成 AUTO_CONTINUE
    （AUTO_CONTINUE 出错的代价是"该给用户看的东西被吞掉/循环失控"，
    比多打扰用户一次严重得多）
  - 涉及主观决策 / 需要人类确认的场景，一律 NEED_USER
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from mini_agent.prompts import pm

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from mini_agent.orchestrator.agent_profiles import AgentProfile


def build_turn_judge_prompt(
    assistant_output: str,
    recent_history: str,
    auto_round_no: int,
    max_auto_rounds: int,
    hit_max_turns: bool = False,
) -> str:
    """构建 TurnJudge 的核查 prompt（模板见 prompts/user/turn_judge_request.md）。"""
    hit_max_turns_line = ""
    if hit_max_turns:
        hit_max_turns_line = (
            "\n[系统提示] 本轮主助手撞到了单轮最大轮数上限（max_turns），"
            "循环是因为预算耗尽而被强制打断的，不代表任务已经完成。\n"
        )

    return pm.render(
        "user/turn_judge_request",
        auto_round_no=auto_round_no,
        max_auto_rounds=max_auto_rounds,
        hit_max_turns_line=hit_max_turns_line,
        assistant_output=assistant_output or "（本轮没有产出最终文本）",
        recent_history=recent_history or "（无历史）",
    )


def run_turn_judge(
    profile: "AgentProfile",
    base_cfg: "AppConfig",
    assistant_output: str,
    recent_history: str,
    auto_round_no: int = 1,
    max_auto_rounds: int = 3,
    hit_max_turns: bool = False,
    parent_session_id: Optional[str] = None,
) -> str:
    """
    运行 TurnJudgeAgent，返回判定文本（含 TURN_STATUS 行）。

    始终以纯文本方式判定（不挂载任何工具），因为这是一个高频触发点
    （每轮对话结束都可能跑一次），必须足够轻量、快速、零副作用。
    """
    # [Phase 3 重构] 样板逻辑收敛到 judge_factory.spawn_judge_agent /
    # run_judge_turn。函数签名和返回值保持完全不变。
    from mini_agent.role_agents.judge_factory import spawn_judge_agent, run_judge_turn
    tj_cfg_block = getattr(base_cfg, "turn_judge", None)

    judge_agent = spawn_judge_agent(
        profile=profile,
        base_cfg=base_cfg,
        role_cfg_block=tj_cfg_block,
        # [SYS-TURN-JUDGE] 给 TurnJudge 内部 Agent 一个专属的显示名，方便用户在
        # 打印输出中一眼看出这是自动核查而非主 Agent 本身在说话。
        display_name="🧭 TurnJudge",
        system_prompt=pm.render(
            "system/turn_judge",
            json_output_instructions=pm.fragment(
                "judge_json_output", "JSON_OUTPUT_INSTRUCTIONS",
                valid_statuses="NEED_USER | AUTO_CONTINUE | NEED_COMPACT",
                feedback_hint="先说明观察到的现象和依据，AUTO_CONTINUE 时结尾给出具体下一步指令",
                example_status="NEED_USER",
                example_feedback="助手已完整回答用户问题，正在正常等待下一步指示。",
            ),
        ),
        max_turns=2,
        tools_enabled=False,   # 纯文本判定，不挂载任何工具（最小权限、最低延迟）
        parent_session_id=parent_session_id,
    )

    prompt = build_turn_judge_prompt(
        assistant_output=assistant_output,
        recent_history=recent_history,
        auto_round_no=auto_round_no,
        max_auto_rounds=max_auto_rounds,
        hit_max_turns=hit_max_turns,
    )

    result = run_judge_turn(
        judge_agent, prompt, failure_role_label="TurnJudgeAgent",
        profile_name=profile.name if profile else "turn_judge",
    )

    if result.ok:
        return result.raw_output
    # 判定失败时保守返回 NEED_USER，绝不能让异常被当成 AUTO_CONTINUE。
    # 兜底文本本身也是合法 JSON，保持与正常输出一致的可解析契约。
    import json as _json
    return _json.dumps({
        "status": "NEED_USER",
        "feedback": f"[TurnJudgeAgent 运行失败: {result.error}]，保守判定为需要用户输入。",
    }, ensure_ascii=False)
