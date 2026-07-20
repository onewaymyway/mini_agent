"""
role_agents/evaluator.py — EvaluatorAgent

职责：
  - 对主 Agent 的输出进行质量评估
  - 返回评分（0-1）和改进建议
  - 支持与主 Agent 的多轮修订循环（由 Dispatcher 控制轮数）

原理：
  用独立的 Agent 实例，以「主 Agent 输出 + 评估 prompt」为输入，
  输出评分和具体改进意见，反馈给主 Agent。
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from mini_agent.orchestrator.agent_profiles import AgentProfile


DEFAULT_EVALUATOR_SYSTEM = """你是一个严格而专业的质量评估专家。
你的任务是评估 AI 助手的输出质量，并给出具体的改进建议。

评估维度：
1. 准确性：内容是否正确、无错误
2. 完整性：是否完整回答了用户需求
3. 清晰度：表达是否清晰、有条理
4. 实用性：是否真正对用户有帮助

输出格式（必须严格遵守）：
---
**评估维度分析**
- 准确性：[评价]
- 完整性：[评价]
- 清晰度：[评价]
- 实用性：[评价]

**主要问题**
[列举具体问题，如无问题则写"无明显问题"]

**改进建议**
[具体可操作的建议，如无需改进则写"输出质量良好，无需修订"]

SCORE: [分数]/10
---

请严格按照上述格式输出，SCORE 行必须在最后。"""


def build_evaluator_prompt(
    original_request: str,
    agent_output: str,
    profile: "AgentProfile",
    iteration: int = 1,
) -> str:
    """构建评估 prompt。"""
    extra = ""
    if iteration > 1:
        extra = f"\n\n注意：这是第 {iteration} 轮评估，请重点关注上轮建议是否被采纳。"

    return f"""请评估以下 AI 助手的输出质量。

**用户原始请求：**
{original_request}

**AI 助手的输出：**
{agent_output}
{extra}

请按照你的评估标准进行全面评估。"""


def run_evaluator(
    profile: "AgentProfile",
    base_cfg: "AppConfig",
    original_request: str,
    agent_output: str,
    iteration: int = 1,
    parent_session_id: Optional[str] = None,
) -> str:
    """
    运行 EvaluatorAgent，返回评估文本。
    使用同步的 Agent.run_turn() 在当前线程运行（不起后台线程）。

    [Phase 3 重构] 构造受限内部 Agent + 异常兜底的样板逻辑已收敛到
    judge_factory.spawn_judge_agent / run_judge_turn，本函数只保留
    evaluator 专属的 prompt 组装。函数签名和返回值保持完全不变。
    """
    import os
    from mini_agent.config.models import DEFAULT_AGENT_NAME
    from mini_agent.role_agents.judge_factory import spawn_judge_agent, run_judge_turn

    eval_agent = spawn_judge_agent(
        profile=profile,
        base_cfg=base_cfg,
        role_cfg_block=None,   # evaluator 没有专属配置块，model/provider 走 profile/base_cfg 两层
        # [行为保持] evaluator 此前从未显式设置 agent_name，等价于 load_config 的默认值
        display_name=os.environ.get("AGENT_NAME", DEFAULT_AGENT_NAME),
        system_prompt=DEFAULT_EVALUATOR_SYSTEM,
        parent_session_id=parent_session_id,
        max_turns=3,           # 评估只需要少量轮次
        tools_enabled=False,   # 评估 agent 不需要任何工具（纯文本输出）
    )

    prompt = build_evaluator_prompt(
        original_request=original_request,
        agent_output=agent_output,
        profile=profile,
        iteration=iteration,
    )

    result = run_judge_turn(eval_agent, prompt, failure_role_label="EvaluatorAgent", profile_name=profile.name)
    return result.raw_output
