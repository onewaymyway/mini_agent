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
) -> str:
    """
    运行 EvaluatorAgent，返回评估文本。
    使用同步的 Agent.run_turn() 在当前线程运行（不起后台线程）。
    """
    from mini_agent.config import load_config
    from mini_agent.agent import Agent
    from mini_agent.permissions import PermissionGuard

    eval_cfg = load_config(
        project_root=base_cfg.project_root,
        verbose=False,
        sandbox=base_cfg.sandbox,
        auto_approve=True,   # 评估不需要审批
        model=profile.model or base_cfg.model,
        llm_provider=profile.provider or base_cfg.llm_provider,
        llm_base_url=base_cfg.llm_base_url,
        # [BUGFIX] 之前硬编码 False，导致 --debug-llm 对这个一次性内部 Agent 调用
        # 完全不生效——一旦这里的 LLM 调用失败，看不到任何调试日志。改为继承
        # base_cfg（外层 --debug-llm 传下来的配置）。
        debug_llm=getattr(base_cfg, "debug_llm", False),
        debug_llm_console=getattr(base_cfg, "debug_llm_console", False),
    )
    eval_cfg.api_key = base_cfg.api_key
    eval_cfg.max_turns = 3       # 评估只需要少量轮次
    eval_cfg.stream = False
    # 用 profile 的 system_prompt，如果没设置则用默认的
    eval_cfg.system_extra = profile.system_prompt if profile.system_prompt.strip() else DEFAULT_EVALUATOR_SYSTEM
    # [SYS-TURN-JUDGE][BUGFIX] 防止内部 Agent 对自己触发 TurnJudge 造成无限递归核查
    from mini_agent.config.models import TurnJudgeConfig as _TurnJudgeConfig
    eval_cfg.turn_judge = _TurnJudgeConfig(enabled=False)

    guard = PermissionGuard(
        auto_approve=True,
        sandbox=base_cfg.sandbox,
        project_root=base_cfg.project_root,
    )

    # 评估 agent 不需要任何工具（纯文本输出）
    from mini_agent.tools import get_default_registry
    # 只给一个空注册表（无工具），让评估 agent 只做文本推理
    empty_registry = get_default_registry().filtered(names=[], groups=[])

    eval_agent = Agent(cfg=eval_cfg, guard=guard, registry=empty_registry, is_subagent=True)

    prompt = build_evaluator_prompt(
        original_request=original_request,
        agent_output=agent_output,
        profile=profile,
        iteration=iteration,
    )

    try:
        result = eval_agent.run_turn(prompt)
        return result
    except Exception as e:
        return f"[EvaluatorAgent 运行失败: {e}]"
