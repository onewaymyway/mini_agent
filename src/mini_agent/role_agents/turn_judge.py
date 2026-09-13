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
    from pathlib import Path
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
    parent_session_dir: Optional["Path"] = None,
) -> str:
    """
    运行 TurnJudgeAgent，返回判定文本（含 TURN_STATUS 行）。

    始终以纯文本方式判定（不挂载任何工具），因为这是一个高频触发点
    （每轮对话结束都可能跑一次），必须足够轻量、快速、零副作用。
    """
    # [Phase 3 重构] 样板逻辑收敛到 judge_factory.spawn_judge_agent /
    # run_judge_turn。函数签名和返回值保持完全不变。
    from mini_agent.role_agents.judge_factory import spawn_judge_agent, run_judge_turn
    import mini_agent.ui.renderer as R
    tj_cfg_block = getattr(base_cfg, "turn_judge", None)

    # [SYS-TURN-JUDGE-LOGGING] 进入/退出判官子会话的明显提示：便于和主
    # Agent 自己的 [max-turns]/compact 日志区分开——排查"是不是卡在
    # TurnJudge 内部出不来"时，这两行日志能直接确认判官子会话是否正常
    # 结束、以及结束时的判定结果，而不需要靠猜测中间那些 "🧭 TurnJudge ❯"
    # 打印到底是不是同一次调用内部的中间轮次。
    R.print_info(
        f"┌─ [TurnJudge] 进入判官子会话（第 {auto_round_no}/{max_auto_rounds} 次核查）"
    )

    _judge_max_turns = getattr(tj_cfg_block, "judge_max_turns", 6) if tj_cfg_block else 6

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
            # [next_doc/autonomous_execution_stability_and_self_learning_integration_plan.md
            # 方案 C 分级响应] 仅在开关开启时拼接 confidence 字段指令，关闭时
            # 渲染为空字符串，system prompt 与升级前完全一致。
            confidence_instructions=(
                pm.fragment("turn_judge", "CONFIDENCE_INSTRUCTIONS")
                if getattr(tj_cfg_block, "auto_continue_with_note_enabled", False) else ""
            ),
        ),
        # [BUGFIX/需求变更] 此前硬编码为 2，改为读 turn_judge.judge_max_turns
        # 配置项（默认 6），见 config/models.py::TurnJudgeConfig.judge_max_turns
        # 的注释。
        max_turns=_judge_max_turns,
        tools_enabled=False,   # 纯文本判定，不挂载任何工具（最小权限、最低延迟）
        parent_session_id=parent_session_id,
        parent_session_dir=parent_session_dir,
    )

    prompt = build_turn_judge_prompt(
        assistant_output=assistant_output,
        recent_history=recent_history,
        auto_round_no=auto_round_no,
        max_auto_rounds=max_auto_rounds,
        hit_max_turns=hit_max_turns,
    )

    from mini_agent.role_agents.verdict import parse_judge_verdict
    import json as _json

    _valid_statuses = ["NEED_USER", "AUTO_CONTINUE", "NEED_COMPACT"]
    _parse_retry_count = max(0, int(getattr(tj_cfg_block, "parse_retry_count", 2) or 0))

    last_raw = ""
    for _attempt in range(1, _parse_retry_count + 2):  # 首次尝试 + parse_retry_count 次重试
        result = run_judge_turn(
            judge_agent, prompt, failure_role_label="TurnJudgeAgent",
            profile_name=profile.name if profile else "turn_judge",
        )

        if not result.ok:
            # 运行本身抛异常（网络/超时等），不是"输出格式解析不了"，重跑同一个
            # 子会话意义不大，直接走既有的保守兜底，不占用 parse_retry_count。
            R.print_info(f"└─ [TurnJudge] 退出判官子会话，status=NEED_USER（运行失败兜底）")
            return _json.dumps({
                "status": "NEED_USER",
                "feedback": f"[TurnJudgeAgent 运行失败: {result.error}]，保守判定为需要用户输入。",
            }, ensure_ascii=False)

        last_raw = result.raw_output
        verdict = parse_judge_verdict(result.raw_output, valid_statuses=_valid_statuses, fallback_status="")
        if verdict.parse_ok:
            _retry_note = f"（第 {_attempt} 次尝试成功）" if _attempt > 1 else ""
            R.print_info(f"└─ [TurnJudge] 退出判官子会话，status={verdict.status}{_retry_note}")
            return result.raw_output

        if _attempt <= _parse_retry_count:
            R.print_warning(
                f"[TurnJudge] 第 {_attempt} 次输出解析失败（JSON 解析与兜底正则均未命中 "
                f"status 字段），原始输出前 200 字：{result.raw_output[:200]!r}，正在重新生成"
                f"（还剩 {_parse_retry_count - _attempt} 次重试机会）…"
            )

    # 连续多次都解析失败，才最终保守判定 NEED_USER，绝不能让解析失败被当成
    # AUTO_CONTINUE。兜底文本本身也是合法 JSON，保持与正常输出一致的可解析契约。
    R.print_warning(
        f"[TurnJudge] 连续 {_parse_retry_count + 1} 次输出均解析失败，"
        "放弃重试，保守判定为 NEED_USER，交还真人用户输入。"
    )
    R.print_info(f"└─ [TurnJudge] 退出判官子会话，status=NEED_USER（连续解析失败兜底）")
    return _json.dumps({
        "status": "NEED_USER",
        "feedback": (
            f"[TurnJudgeAgent 连续 {_parse_retry_count + 1} 次输出解析失败]，保守判定为需要用户输入。"
            f" 最后一次原始输出前 500 字：{last_raw[:500]}"
        ),
    }, ensure_ascii=False)
