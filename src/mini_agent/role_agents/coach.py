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
    from pathlib import Path
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
    parent_session_id: Optional[str] = None,
    parent_session_dir: Optional["Path"] = None,
) -> str:
    """
    运行 CoachAgent，返回建议文本。

    [Phase 3 重构] 样板逻辑收敛到 judge_factory.spawn_judge_agent /
    run_judge_turn，函数签名和返回值保持完全不变。
    """
    import os
    from mini_agent.config.models import DEFAULT_AGENT_NAME
    from mini_agent.role_agents.judge_factory import spawn_judge_agent, run_judge_turn

    coach_agent = spawn_judge_agent(
        profile=profile,
        base_cfg=base_cfg,
        role_cfg_block=None,
        # [行为保持] coach 此前从未显式设置 agent_name，等价于 load_config 的默认值
        display_name=os.environ.get("AGENT_NAME", DEFAULT_AGENT_NAME),
        system_prompt=DEFAULT_COACH_SYSTEM,
        max_turns=2,
        tools_enabled=False,
        parent_session_id=parent_session_id,
        parent_session_dir=parent_session_dir,
    )

    prompt = build_coach_prompt(
        tool_name=tool_name,
        tool_input=tool_input,
        tool_output=tool_output,
        context=context,
        profile=profile,
    )

    result = run_judge_turn(coach_agent, prompt, failure_role_label="CoachAgent", profile_name=profile.name)
    return result.raw_output
