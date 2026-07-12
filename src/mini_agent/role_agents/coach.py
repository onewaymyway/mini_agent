"""
role_agents/coach.py — CoachAgent

职责：
  - 在特定工具调用后（trigger_on: tool_use:bash 等）提供策略建议
  - 扮演"教练/导师"角色，指出潜在问题和更好的方向
  - 建议注入主 Agent（一般用 system_reminder），让主 Agent 调整后续行为

与 EvaluatorAgent 的区别：
  - Evaluator 在事后评估最终输出（事后质检）
  - Coach 在过程中介入，在特定操作后给出前瞻性建议（过程指导）
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from mini_agent.orchestrator.agent_profiles import AgentProfile


DEFAULT_COACH_SYSTEM = """你是一位经验丰富的 AI 助手教练。
你的职责是在 AI 助手执行某个操作后，提供简洁的策略建议。

原则：
1. 建议要具体可操作，不要泛泛而谈
2. 重点关注潜在风险和更好的替代方案
3. 保持简洁，控制在 150 字以内
4. 用肯定语气，指出方向而非批评

输出格式：
**观察**：[对刚才操作的简短观察]
**建议**：[1-3 条具体建议]
**注意**：[可选，重要风险提示]"""


def build_coach_prompt(
    tool_name: str,
    tool_input: dict,
    tool_output: str,
    context: str,
    profile: "AgentProfile",
) -> str:
    """构建教练 prompt。"""
    import json
    tool_input_str = json.dumps(tool_input, ensure_ascii=False, indent=2)
    # 截断过长的输出
    if len(tool_output) > 1000:
        tool_output = tool_output[:1000] + "\n...[输出已截断]"

    return f"""AI 助手刚刚执行了以下操作，请提供建议。

**触发工具：** {tool_name}
**工具输入：**
```
{tool_input_str}
```
**工具输出：**
```
{tool_output}
```
**当前任务上下文：**
{context}

请按照你的角色提供简洁的策略建议。"""


def run_coach(
    profile: "AgentProfile",
    base_cfg: "AppConfig",
    tool_name: str,
    tool_input: dict,
    tool_output: str,
    context: str = "",
) -> str:
    """
    运行 CoachAgent，返回建议文本。
    """
    from mini_agent.config import load_config
    from mini_agent.agent import Agent
    from mini_agent.permissions import PermissionGuard

    coach_cfg = load_config(
        project_root=base_cfg.project_root,
        verbose=False,
        sandbox=base_cfg.sandbox,
        auto_approve=True,
        model=profile.model or base_cfg.model,
        llm_provider=profile.provider or base_cfg.llm_provider,
        llm_base_url=base_cfg.llm_base_url,
        # [BUGFIX] 同 evaluator.py：继承 base_cfg 的 --debug-llm，而不是硬编码 False。
        debug_llm=getattr(base_cfg, "debug_llm", False),
        debug_llm_console=getattr(base_cfg, "debug_llm_console", False),
    )
    coach_cfg.api_key = base_cfg.api_key
    coach_cfg.max_turns = 2
    coach_cfg.stream = False
    coach_cfg.system_extra = profile.system_prompt if profile.system_prompt.strip() else DEFAULT_COACH_SYSTEM
    # [SYS-TURN-JUDGE][BUGFIX] 防止内部 Agent 对自己触发 TurnJudge 造成无限递归核查
    from mini_agent.config.models import TurnJudgeConfig as _TurnJudgeConfig
    coach_cfg.turn_judge = _TurnJudgeConfig(enabled=False)

    guard = PermissionGuard(
        auto_approve=True,
        sandbox=base_cfg.sandbox,
        project_root=base_cfg.project_root,
    )

    from mini_agent.tools import get_default_registry
    empty_registry = get_default_registry().filtered(names=[], groups=[])
    coach_agent = Agent(cfg=coach_cfg, guard=guard, registry=empty_registry, is_subagent=True)

    prompt = build_coach_prompt(
        tool_name=tool_name,
        tool_input=tool_input,
        tool_output=tool_output,
        context=context,
        profile=profile,
    )

    try:
        result = coach_agent.run_turn(prompt)
        return result
    except Exception as e:
        return f"[CoachAgent 运行失败: {e}]"
