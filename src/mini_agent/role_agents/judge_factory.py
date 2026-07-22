"""
role_agents/judge_factory.py — 判官类内部 Agent 的统一构造工厂

背景：evaluator / coach / turn_judge / goal_judge / dispatcher._run_custom_role
此前各自手写了一份几乎一模一样的"构造一个受限内部 Agent"样板代码
（load_config 三层 model/provider 优先级解析、显式禁用递归 TurnJudge、
按 tools_enabled 决定注册表、is_subagent=True 标记、try/except 兜底）。
本模块把这件事收敛成一个函数 + 一条统一的失败识别/上报路径，后续新增
判官类型只需要写 prompt + 调这个工厂，不需要再碰这份样板。
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
    from mini_agent.config import AppConfig
    from mini_agent.agent import Agent
    from mini_agent.orchestrator.agent_profiles import AgentProfile


@dataclass
class JudgeResult:
    """判官内部 Agent 一次运行的结果，统一给调用方消费。"""
    ok: bool
    raw_output: str = ""
    error: Optional[str] = None


def spawn_judge_agent(
    *,
    profile: Optional["AgentProfile"],
    base_cfg: "AppConfig",
    role_cfg_block=None,          # 如 cfg.goal_mode / cfg.turn_judge，提供 judge_model/judge_provider
    display_name: str,            # 如 "🎯 GoalJudge"，仅用于打印前缀
    system_prompt: str,
    max_turns: int = 2,
    tools_enabled: bool = False,
    allowed_tools: Optional[list] = None,
    allowed_tool_groups: Optional[list] = None,
    force_sandbox_when_tools: bool = True,
    parent_session_id: Optional[str] = None,
    parent_session_dir: Optional["Path"] = None,
) -> "Agent":
    """按统一规则构造一个受限的内部判官 Agent 实例。

    收敛了此前 evaluator.py / coach.py / turn_judge.py / goal_judge.py /
    dispatcher._run_custom_role 里重复的：
      - load_config 三层 model/provider 优先级解析（复用 model_resolution.py）
      - 显式禁用 judge_cfg.turn_judge，防止内部 Agent 对自己递归触发 TurnJudge
      - 按 tools_enabled 开关决定空注册表 or 过滤注册表
      - is_subagent=True 标记（第二道防递归保险）

    parent_session_id：调用方（主 Agent / 上层判官）当前的 session id。
    传入后，本判官内部 Agent 的 session 会落在
    <project_root>/.agent/sessions/<parent_session_id>/<自己的 session_id>/
    下，而不是和主 session 平级——避免子 agent session 散落在
    sessions_dir 根目录，与主 session 无法区分归属。
    """
    from mini_agent.config import load_config
    from mini_agent.agent import Agent
    from mini_agent.permissions import PermissionGuard
    from mini_agent.tools import get_default_registry
    from mini_agent.config.models import TurnJudgeConfig as _TurnJudgeConfig
    from mini_agent.role_agents.model_resolution import resolve_role_model

    judge_model, judge_provider = resolve_role_model(profile, role_cfg_block, base_cfg)

    yes_mode = bool(getattr(role_cfg_block, "judge_yes_mode", False)) if tools_enabled else False
    judge_sandbox = (
        (not yes_mode) if (tools_enabled and force_sandbox_when_tools) else base_cfg.sandbox
    )

    judge_cfg = load_config(
        project_root=base_cfg.project_root,
        verbose=base_cfg.verbose,
        sandbox=judge_sandbox,
        auto_approve=True,
        model=judge_model,
        llm_provider=judge_provider,
        llm_base_url=base_cfg.llm_base_url,
        debug_llm=getattr(base_cfg, "debug_llm", False),
        debug_llm_console=getattr(base_cfg, "debug_llm_console", False),
    )
    judge_cfg.api_key = base_cfg.api_key

    # [子 agent session 嵌套] 若调用方提供了 parent_session_id/parent_session_dir，
    # 让本判官内部 Agent 的 session 落在主 session 目录下的子目录，而不是与主
    # session 平级散落在 sessions_dir 根目录下。
    #
    # [BUGFIX] 优先使用调用方显式传入的 parent_session_dir（调用方通过
    # Agent._current_session_dir() 取得的真实落盘目录）。只有 parent_session_id
    # 而没有 parent_session_dir 时，才退回"用 AgentPaths 在 sessions_dir 根目录
    # 下按 id 拼路径"这种平级假设——当调用方自身就是一个嵌套的子 agent（例如
    # SubAgent 内部再触发 TurnJudge）时，这个假设是错的：调用方的 session 本来
    # 就不在 sessions_dir 根目录下，按 id 重新拼出来的路径会指向一个不存在的
    # 平级目录，导致本判官的 llm_debug.jsonl 等和它真正的 history.json 对不上。
    if parent_session_dir:
        judge_cfg.session.dir = parent_session_dir
    elif parent_session_id:
        from mini_agent.storage.paths import AgentPaths
        judge_cfg.session.dir = AgentPaths(base_cfg.project_root).session_dir(parent_session_id)

    # [BUGFIX] load_config() 的 model=/llm_provider= 参数只影响 judge_cfg 的
    # 顶层 model/llm_provider 字段；但 Agent.__init__ 里真正决定"实际用哪个
    # client"的是 LLMClientPool.from_config(cfg)——只要 judge_cfg.llm_fallback_chain
    # 非空（项目配置了多 provider 故障转移链，很常见），就会完全无视顶层
    # model/llm_provider，直接用 chain[0]（配置文件里写死的模型）构造 client，
    # 把上面 resolve_role_model() 精心解析出的 judge_model/judge_provider
    # （未显式配置 judge_model 时会回退到主 Agent 当前正在用的模型，随
    # /model、/provider switch 实时变化）整个覆盖掉，退化成"单纯从配置文件
    # 读取模型"——这正是本函数要避免的情况。
    #
    # 判官内部 Agent 是轻量一次性调用，不需要主 Agent 那条多 provider 故障
    # 转移链，因此这里直接清空 fallback chain，强制 LLMClientPool 退化为单
    # 条主配置（取 judge_cfg.model / judge_cfg.llm_provider / judge_cfg.api_key），
    # 确保 resolve_role_model() 解析出的模型才是最终实际生效的模型。
    judge_cfg.llm_fallback_chain = []

    judge_cfg.max_turns = max_turns
    judge_cfg.stream = False
    judge_cfg.system_extra = (
        profile.system_prompt if (profile and profile.system_prompt.strip()) else system_prompt
    )
    judge_cfg.agent_name = display_name
    judge_cfg.turn_judge = _TurnJudgeConfig(enabled=False)  # 防递归，唯一权威开关点

    guard = PermissionGuard(
        auto_approve=True, sandbox=judge_sandbox, project_root=base_cfg.project_root,
    )

    if tools_enabled:
        allowed_tools = list(allowed_tools or [])
        allowed_groups = list(allowed_tool_groups or [])
        if profile and profile.tools:
            allowed_tools = [t for t in profile.tools if t in allowed_tools] or profile.tools
        if profile and profile.tool_groups:
            allowed_groups = [g for g in profile.tool_groups if g in allowed_groups] or profile.tool_groups
        registry = get_default_registry().filtered(names=allowed_tools, groups=allowed_groups)
    else:
        # [BUGFIX] 此前这里写的是 filtered(names=[], groups=[])，两个参数
        # 都是空列表时会被 filtered() 当成"未筛选"返回全量工具，导致
        # tools_enabled=False 完全没有生效——GoalSpecBuilder/GoalJudge 等
        # 声称"不给工具"的内部 Agent，实际拿到了包括 tree_summary/list_dir/
        # bash 在内的全部工具，会在 max_turns 有限的情况下把轮次耗在探索
        # 项目结构上，导致真正该产出的文本内容没有机会生成。
        registry = get_default_registry().empty()

    return Agent(cfg=judge_cfg, guard=guard, registry=registry, is_subagent=True)


def run_judge_turn(
    agent: "Agent",
    prompt: str,
    *,
    failure_role_label: str,
    profile_name: Optional[str] = None,
) -> JudgeResult:
    """统一的"跑一轮判官 Agent + 异常兜底"逻辑，替代四处重复的 try/except。

    [Phase 4] 若传入 profile_name，运行结束后会自动调用一次
    `report_judge_outcome`，把这次判官运行的成败上报给 auto_quarantine——
    这样 evaluator/coach/goal_judge/turn_judge/custom 五类判官全部自动获得
    保护，不需要各自记得接入。不传 profile_name（如某些一次性/测试场景）
    则跳过上报，行为等价于此前完全没有这条能力。
    """
    try:
        raw = agent.run_turn(prompt)
        result = JudgeResult(ok=True, raw_output=raw)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.role_agents.judge_factory.run_judge_turn')
        result = JudgeResult(ok=False, error=str(e), raw_output=f"[{failure_role_label} 运行失败: {e}]")

    if profile_name:
        report_judge_outcome(result, profile_name)

    return result


# ── [Phase 4] 失败识别 + auto_quarantine 上报统一 ────────────────────────────
# 此前只有走 RoleAgentDispatcher 路径的 evaluator/coach/custom 会通过正则识别
# "[XxxAgent 运行失败: ...]" 字符串上报 auto_quarantine，GoalJudge/TurnJudge
# 完全没接这条能力。有了 JudgeResult.ok 之后，失败识别不再需要依赖约定俗成的
# 字符串前缀，直接基于类型化的布尔字段判断即可，这里统一收口。

# 仍然保留这个正则，兼容尚未切换到 spawn_judge_agent/JudgeResult 的旧调用方
# （目前应该已经没有，但保留作为过渡期的安全网，不额外造成开销）。
_ROLE_FAILURE_RE = _re.compile(r"^\[\w+Agent 运行失败: (.*)\]$", _re.DOTALL)


def report_judge_outcome(result: JudgeResult, profile_name: str) -> None:
    """基于 JudgeResult.ok 上报一次判官 Agent 的运行结果给 auto_quarantine。

    ok=True  → record_success（清零该 profile 的历史失败计数）
    ok=False → record_failure（累计失败计数，达到阈值后自动屏蔽该 profile）

    auto_quarantine 总开关默认关闭，关闭时下面两个 record_* 调用都是 no-op，
    这里的调用本身零成本。
    """
    try:
        from mini_agent.auto_quarantine import get_quarantine_store
        store = get_quarantine_store()
        if result.ok:
            store.record_success("agent", profile_name)
            return

        from mini_agent.perception.observability import classify_error
        import mini_agent.ui.renderer as R
        cat = classify_error(result.raw_output or result.error or "")
        just_q = store.record_failure("agent", profile_name, cat, result.error or result.raw_output)
        if just_q:
            R.print_warning(
                f"[quarantine] agent profile '{profile_name}' 在当前平台连续失败达到阈值"
                f"（{cat}），已自动屏蔽。使用 /quarantine remove agent:{profile_name} 可解除。"
            )
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.role_agents.judge_factory')