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
) -> str:
    """
    运行 TurnJudgeAgent，返回判定文本（含 TURN_STATUS 行）。

    始终以纯文本方式判定（不挂载任何工具），因为这是一个高频触发点
    （每轮对话结束都可能跑一次），必须足够轻量、快速、零副作用。
    """
    from mini_agent.config import load_config
    from mini_agent.agent import Agent
    from mini_agent.permissions import PermissionGuard
    from mini_agent.tools import get_default_registry

    tj_cfg_block = getattr(base_cfg, "turn_judge", None)

    from mini_agent.role_agents.model_resolution import resolve_role_model
    judge_model, judge_provider = resolve_role_model(profile, tj_cfg_block, base_cfg)

    judge_cfg = load_config(
        project_root=base_cfg.project_root,
        verbose=False,
        sandbox=base_cfg.sandbox,
        auto_approve=True,
        model=judge_model,
        llm_provider=judge_provider,
        llm_base_url=base_cfg.llm_base_url,
        debug_llm=False,
    )
    judge_cfg.api_key = base_cfg.api_key
    judge_cfg.max_turns = 2
    judge_cfg.stream = False
    judge_cfg.system_extra = profile.system_prompt if profile.system_prompt.strip() else pm.render("system/turn_judge")
    # [SYS-TURN-JUDGE][BUGFIX] load_config() 会重新从同一个 agent_config.json 读取配置，
    # 这意味着 judge_cfg.turn_judge.enabled 也会是 True——如果不显式关掉，TurnJudgeAgent
    # 自己跑 run_turn() 时会对自己再触发一次 TurnJudge 核查，无限递归自我核查，
    # 表现为一直卡在 "🧭 TurnJudge ❯" 反复核查、永远不把控制权交还真人。
    # 这里必须显式禁用，不能只依赖下面的 is_subagent 标记（那只是第二道保险）。
    from mini_agent.config.models import TurnJudgeConfig as _TurnJudgeConfig
    judge_cfg.turn_judge = _TurnJudgeConfig(enabled=False)
    # [SYS-TURN-JUDGE] 给 TurnJudge 内部 Agent 一个专属的显示名，风格与 GoalJudge 一致，
    # 方便用户在打印输出中一眼看出这是自动核查而非主 Agent 本身在说话。
    judge_cfg.agent_name = "🧭 TurnJudge"

    guard = PermissionGuard(
        auto_approve=True,
        sandbox=base_cfg.sandbox,
        project_root=base_cfg.project_root,
    )

    # 纯文本判定，不挂载任何工具（最小权限、最低延迟）
    registry = get_default_registry().filtered(names=[], groups=[])

    # [SYS-TURN-JUDGE][BUGFIX] 显式标记为 subagent，作为第二道保险：即使未来
    # judge_cfg.turn_judge 的禁用逻辑被误删，agent.py::_maybe_run_turn_judge()
    # 里的 `self._is_subagent` 检查依然会拦住嵌套触发。
    judge_agent = Agent(cfg=judge_cfg, guard=guard, registry=registry, is_subagent=True)

    prompt = build_turn_judge_prompt(
        assistant_output=assistant_output,
        recent_history=recent_history,
        auto_round_no=auto_round_no,
        max_auto_rounds=max_auto_rounds,
        hit_max_turns=hit_max_turns,
    )

    try:
        result = judge_agent.run_turn(prompt)
        return result
    except Exception as e:
        # 判定失败时保守返回 NEED_USER，绝不能让异常被当成 AUTO_CONTINUE
        return f"**结论**\n[TurnJudgeAgent 运行失败: {e}]，保守判定为需要用户输入。\n\nTURN_STATUS: NEED_USER"
